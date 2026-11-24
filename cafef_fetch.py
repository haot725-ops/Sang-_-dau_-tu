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
CẤU HÌNH:  tickers.json, ví dụ {"NVL": "bds", "VNM": "chung", "HPG": "chung"}
           ngành hỗ trợ: "bds" (bất động sản), "chung" (sản xuất, bán lẻ, dịch vụ...). Ngân hàng chưa hỗ trợ.
CHẠY:      python cafef_fetch.py                     # đọc tickers.json
           python cafef_fetch.py --tickers NVL VNM   # chỉ lấy vài mã (ngành "chung" nếu không có trong tickers.json)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
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


class FetchError(Exception):
    pass


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
    """Gộp dữ liệu thành {kỳ: {mã chỉ tiêu: giá trị}}. Báo cáo có nhóm thì dữ liệu nằm trong từng nhóm."""
    by_period: dict[str, dict[str, float]] = {}

    def ingest(entries: list[dict]) -> None:
        for ky in entries or []:
            bucket = by_period.setdefault(str(ky.get("time")), {})
            for it in ky.get("data") or []:
                v = it.get("value")
                if isinstance(v, (int, float)):
                    bucket[str(it.get("code"))] = v

    if grouped:
        for nhom in data or []:
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
            if not js.get("isSuccess"):
                raise FetchError(f"API báo lỗi: {js.get('errors')}")
            val = js.get("value") or {}
            by_period = _collect_values(val.get("data") or [], grouped)
            periods = sorted(by_period, key=_period_key)
            if not periods:
                raise FetchError("API không trả về kỳ nào")
            tmpl = _flatten_template(val.get("templace") or [])
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
            return {"periods": periods, "items": items}
        except (requests.RequestException, ValueError, FetchError) as e:
            last = e
            log.warning("%s/%s lần %d/%d lỗi: %s", ticker, kind, attempt, retries, e)
            time.sleep(2 ** attempt)
    raise FetchError(f"{ticker}/{kind}: {last}")


def fetch_ticker(ticker: str, sector: str, session: requests.Session) -> dict[str, Any]:
    out = {"ticker": ticker, "sector": sector, "unit": "VND",
           "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "source": "cafef.vn"}
    for kind in ("bs", "is", "cf"):
        out[kind] = fetch_report(ticker, kind, session)
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
            (a.out / f"{t}.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            log.info("%s: đã lưu %d kỳ", t, len(data["is"]["periods"]))
        except FetchError as e:
            failed += 1
            log.error("Bỏ qua %s, giữ dữ liệu cũ: %s", t, e)
    # danh mục để app biết có những mã nào
    have = sorted(p.stem for p in a.out.glob("*.json") if p.stem != "index")
    (a.out / "index.json").write_text(json.dumps({"tickers": have}, ensure_ascii=False), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
