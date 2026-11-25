#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
cafef_fetch.py - Lấy báo cáo tài chính theo quý từ API công khai của CafeF, lưu thành data/<MÃ>.json cho app đọc.

Lấy 3 báo cáo: cân đối kế toán (bs), kết quả kinh doanh (is), lưu chuyển tiền tệ (cf).
Đơn vị trong file là ĐỒNG (VND), nguyên như CafeF trả về.

LƯU Ý:
- Các địa chỉ API dưới đây là API không chính thức của CafeF (lấy từ dự án mã nguồn mở), CafeF có thể đổi hoặc chặn
  bất cứ lúc nào. Hãy xem điều khoản sử dụng của CafeF và chỉ dùng cho mục đích cá nhân, tần suất thấp.
- Chương trình chỉ ghi đè file khi cả 3 báo cáo đều lấy được, nên lỗi mạng không làm hỏng dữ liệu cũ.

CÀI ĐẶT:   pip install requests
CẤU HÌNH:  tickers.json, ví dụ {"NVL": "bds", "VNM": "chung", "VCB": "nganhang", "SSI": "ck"}
           nhóm hỗ trợ: "bds", "chung", "nganhang" (ngân hàng), "ck" (công ty chứng khoán).
CHẠY:      python cafef_fetch.py                     # đọc tickers.json
           python cafef_fetch.py --tickers NVL VNM   # chỉ lấy vài mã (ngành "chung" nếu không có trong tickers.json)
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

log = logging.getLogger("cafef")

HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
ENDPOINTS = {
    # khoá: (URL, tham số reportType, báo cáo có chia nhóm con hay không)
    "bs": ("https://apiweb.cafef.vn/api/v2/BCTC/GetReportCDKT", "ALL", True),
    "is": ("https://apiweb.cafef.vn/api/v1/BCTC/GetReportDetail", "KQKD", False),
    "cf": ("https://apiweb.cafef.vn/api/v1/BCTC/GetReportLCTT", "ALL", True),
}
PAGE_SIZE = 12  # 12 quý gần nhất (API cho tối đa 20)
QUARTER_RE = re.compile(r"^Q[1-4]-\d{4}$")


class FetchError(Exception):
    pass


def atomic_json_write(path: Path, value: Any, *, indent: int | None = None) -> None:
    """Ghi JSON vào file tạm cùng thư mục rồi thay thế nguyên tử; giữ file cũ nếu ghi lỗi."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", suffix=".tmp", delete=False) as f:
            temp_path = Path(f.name)
            kwargs: dict[str, Any] = {"ensure_ascii": False, "allow_nan": False}
            if indent is None:
                kwargs["separators"] = (",", ":")
            else:
                kwargs["indent"] = indent
            json.dump(value, f, **kwargs)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
    except Exception:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
        raise


def _period_key(p: str) -> tuple[int, int]:
    """'Q3-2026' -> (2026, 3); '2025' -> (2025, 0). Dùng để sắp xếp kỳ tăng dần theo thời gian."""
    try:
        if p.startswith("Q"):
            q, y = p[1:].split("-")
            return int(y), int(q)
        return int(p), 0
    except (ValueError, IndexError):
        return (0, 0)


def _flatten_template(nodes: list[dict], depth: int = 0) -> list[dict]:
    """Làm phẳng danh sách tên chỉ tiêu (có thể lồng nhau), giữ thứ tự và cấp để hiển thị bảng."""
    out = []
    for n in nodes or []:
        children = n.get("data") or []
        out.append({"code": str(n.get("code", "")), "name": n.get("name", ""), "lv": depth, "group": bool(children)})
        if children:
            out.extend(_flatten_template(children, depth + 1))
    return out


def _collect_values(data: list[dict], grouped: bool) -> dict[str, dict[str, float]]:
    """Gộp theo kỳ; hàng cùng kỳ trùng khớp được hợp nhất, số liệu xung đột thì từ chối."""
    by_period: dict[str, dict[str, float]] = {}

    def ingest(entries: list[dict]) -> None:
        if not isinstance(entries, list):
            raise FetchError("API trả danh sách kỳ sai schema")
        for ky in entries:
            if not isinstance(ky, dict):
                raise FetchError("API trả kỳ báo cáo sai schema")
            period = str(ky.get("time") or "")
            values = ky.get("data") or []
            if not isinstance(values, list):
                raise FetchError(f"API trả chỉ tiêu sai schema tại kỳ {period}")
            bucket = by_period.setdefault(period, {})
            for it in values:
                if not isinstance(it, dict):
                    raise FetchError(f"API trả dòng chỉ tiêu sai schema tại kỳ {period}")
                v = it.get("value")
                if v is None:
                    continue
                if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
                    raise FetchError("API trả giá trị chỉ tiêu không phải số hữu hạn")
                code = str(it.get("code"))
                if code and code in bucket and bucket[code] != v:
                    raise FetchError(f"API trả mã chỉ tiêu {code} xung đột tại kỳ {period}")
                bucket[code] = v

    if grouped:
        for nhom in data:
            if not isinstance(nhom, dict):
                raise FetchError("API trả nhóm chỉ tiêu sai schema")
            ingest(nhom.get("data") or [])
    else:
        ingest(data)
    return by_period


def fetch_report(ticker: str, kind: str, session: requests.Session, retries: int = 3) -> dict[str, Any]:
    """Gọi một API, trả về {"periods": [...], "items": [{code, name, lv, group, values}]}."""
    url, rtype, grouped = ENDPOINTS[kind]
    params = {"symbol": ticker, "pageIndex": 1, "pageSize": PAGE_SIZE, "reportType": rtype, "TypeTime": "QUY"}
    last: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            r = session.get(url, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            js = r.json()
            if not isinstance(js, dict):
                raise FetchError("API trả về cấu trúc không phải object")
            if not js.get("isSuccess"):
                raise FetchError(f"API báo lỗi: {js.get('errors')}")
            val = js.get("value")
            if not isinstance(val, dict) or not isinstance(val.get("data"), list):
                raise FetchError("API thiếu value.data hoặc sai schema")
            by_period = _collect_values(val["data"], grouped)
            periods = sorted(by_period, key=_period_key)
            if not periods:
                raise FetchError("API không trả về kỳ nào")
            if any(not QUARTER_RE.fullmatch(p) or not 1900 <= _period_key(p)[0] <= 2100 for p in periods):
                raise FetchError("API trả kỳ quý sai định dạng")
            template = val.get("templace")
            if not isinstance(template, list) or not template:
                raise FetchError("API thiếu danh mục chỉ tiêu báo cáo")
            tmpl = _flatten_template(template)
            names = {t["code"] for t in tmpl}
            # mã có dữ liệu nhưng không có trong mẫu tên: vẫn giữ, để không mất số
            for code in sorted({c for b in by_period.values() for c in b} - names):
                tmpl.append({"code": code, "name": "", "lv": 0, "group": False})
            items = []
            for t in tmpl:
                vals = [by_period[p].get(t["code"]) for p in periods]
                if t["group"] and all(v is None for v in vals):
                    items.append({**t, "values": vals})  # giữ tiêu đề nhóm
                elif any(v is not None for v in vals):
                    items.append({**t, "values": vals})
            if not any(not item["group"] and any(v is not None for v in item["values"]) for item in items):
                raise FetchError("Báo cáo không có dòng chỉ tiêu chứa dữ liệu")
            if any(len(item["values"]) != len(periods) for item in items):
                raise FetchError("Số giá trị không khớp số kỳ báo cáo")
            return {"periods": periods, "items": items}
        except (requests.RequestException, ValueError, TypeError, KeyError, AttributeError, FetchError) as e:
            last = e
            log.warning("%s/%s lần %d/%d lỗi: %s", ticker, kind, attempt, retries, e)
            if attempt < retries:
                time.sleep(2 ** attempt)
    raise FetchError(f"{ticker}/{kind}: {last}")


def fetch_ticker(ticker: str, sector: str, session: requests.Session) -> dict[str, Any]:
    ticker = str(ticker).upper()
    if not re.fullmatch(r"[A-Z0-9]{2,10}", ticker):
        raise FetchError(f"Mã chứng khoán không hợp lệ: {ticker!r}")
    if sector not in {"bds", "chung", "nganhang", "ck"}:
        sector = "chung"
    out = {"ticker": ticker, "sector": sector, "unit": "VND",
           "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": "cafef.vn"}
    for i, kind in enumerate(("bs", "is", "cf")):
        out[kind] = fetch_report(ticker, kind, session)
        if i < 2:
            time.sleep(1.0)  # nghỉ giữa các lần gọi để không làm phiền máy chủ
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Lấy BCTC theo quý từ CafeF")
    ap.add_argument("--tickers", nargs="*", help="Chỉ lấy các mã này")
    ap.add_argument("--config", type=Path, default=Path("tickers.json"))
    ap.add_argument("--out", type=Path, default=Path("data"))
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    cfg: dict[str, str] = {}
    if a.config.exists():
        try:
            cfg = {k.upper(): v for k, v in json.loads(a.config.read_text(encoding="utf-8")).items()}
        except (OSError, ValueError) as e:
            log.error("Không đọc được %s: %s", a.config, e)
            return 2
    tickers = [t.upper() for t in a.tickers] if a.tickers else list(cfg)
    if not tickers:
        log.error("Chưa có mã nào. Tạo tickers.json hoặc dùng --tickers")
        return 2

    a.out.mkdir(parents=True, exist_ok=True)
    session, failed = requests.Session(), 0
    for t in tickers:
        try:
            data = fetch_ticker(t, cfg.get(t, "chung"), session)
            atomic_json_write(a.out / f"{t}.json", data)
            log.info("%s: đã lưu %d kỳ", t, len(data["is"]["periods"]))
        except FetchError as e:
            failed += 1
            log.error("Bỏ qua %s, giữ dữ liệu cũ: %s", t, e)
    # danh mục để app biết có những mã nào
    have = sorted(p.stem for p in a.out.glob("*.json") if p.stem != "index")
    atomic_json_write(a.out / "index.json", {"tickers": have})
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
