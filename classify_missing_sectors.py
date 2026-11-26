#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit missing universe industries and fill only classifications published by HOSE.

The script uses the public API called by HOSE's own GICS classification pages.
It does not infer sectors from company names. HNX/UPCoM symbols without a
verified ticker-level exchange classification are reported as unresolved.

Usage:
  python classify_missing_sectors.py                 # audit only; no file changes
  python classify_missing_sectors.py --report audit.json
  python classify_missing_sectors.py --apply          # explicitly update universe.json
"""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import sys
import tempfile
import time
from typing import Any

import requests

SECTORS_URL = "https://api.hsx.vn/l/api/v1/1/sectors"
SECTOR_MEMBERS_URL = "https://api.hsx.vn/l/api/v1/1/sector-report"
SOURCE_PAGE = "https://www.hsx.vn/vi/quan-ly-niem-yet/phan-nganh-niem-yet"
USER_AGENT = "SangSectorAudit/1.0 (public exchange classification; contact repository maintainer)"
TICKER_RE = re.compile(r"^[A-Z0-9]{3,6}$")


class SourceError(RuntimeError):
    """Raised if the exchange response is incomplete or has an unexpected schema."""


def _request_json(session: requests.Session, url: str, params: dict[str, Any]) -> Any:
    response = session.get(url, params=params, timeout=(10, 30))
    response.raise_for_status()
    try:
        return response.json()
    except ValueError as exc:
        raise SourceError(f"Phản hồi không phải JSON hợp lệ: {response.url}") from exc


def fetch_hose_classifications(session: requests.Session, pause: float = 0.15) -> dict[str, str]:
    """Return ticker -> official HOSE GICS top-level sector."""
    payload = _request_json(session, SECTORS_URL, {"pageIndex": 1, "pageSize": 1000})
    if not isinstance(payload, dict) or payload.get("success") is not True:
        raise SourceError("HOSE không trả success=true khi lấy danh sách ngành.")
    data = payload.get("data")
    sectors = data.get("list") if isinstance(data, dict) else None
    if not isinstance(sectors, list) or not sectors:
        raise SourceError("HOSE trả danh sách ngành rỗng hoặc sai cấu trúc.")

    mapping: dict[str, str] = {}
    conflicts: dict[str, set[str]] = {}
    for sector in sectors:
        if not isinstance(sector, dict):
            continue
        sector_id = sector.get("id")
        sector_name = str(sector.get("name") or "").strip()
        if not sector_id or not sector_name:
            continue
        members = _request_json(session, SECTOR_MEMBERS_URL, {
            "pageSize": 1000,
            "pageIndex": 1,
            "parentId": sector_id,
        })
        if not isinstance(members, dict) or members.get("success") is not True:
            raise SourceError(f"HOSE không trả success=true cho ngành {sector_name}.")
        rows = members.get("data")
        if not isinstance(rows, list):
            raise SourceError(f"Danh sách mã thuộc ngành {sector_name} sai cấu trúc.")
        for row in rows:
            if not isinstance(row, dict):
                continue
            ticker = str(row.get("code") or "").strip().upper()
            top_sector = str(row.get("parentName") or sector_name).strip()
            if not TICKER_RE.fullmatch(ticker) or not top_sector:
                continue
            if ticker in mapping and mapping[ticker] != top_sector:
                conflicts.setdefault(ticker, {mapping[ticker]}).add(top_sector)
            else:
                mapping[ticker] = top_sector
        if pause:
            time.sleep(pause)

    if conflicts:
        details = "; ".join(f"{t}: {', '.join(sorted(v))}" for t, v in sorted(conflicts.items()))
        raise SourceError(f"HOSE trả mã thuộc nhiều ngành cấp cao: {details}")
    if not mapping:
        raise SourceError("Không tìm thấy mã cổ phiếu nào trong dữ liệu phân ngành HOSE.")
    return mapping


def build_audit(universe: dict[str, Any], classifications: dict[str, str]) -> tuple[dict[str, Any], int]:
    stocks = universe.get("stocks")
    if not isinstance(stocks, list):
        raise ValueError("universe.json phải có trường stocks dạng danh sách.")
    missing = [x for x in stocks if isinstance(x, dict) and not str(x.get("i") or "").strip()]
    matched: list[dict[str, str]] = []
    unresolved: list[dict[str, str]] = []
    by_exchange = Counter()
    for item in missing:
        ticker = str(item.get("t") or "").strip().upper()
        exchange = str(item.get("x") or "").strip().upper()
        sector = classifications.get(ticker) if exchange == "HOSE" else None
        if sector:
            matched.append({"ticker": ticker, "exchange": exchange, "industry": sector})
        else:
            unresolved.append({"ticker": ticker, "exchange": exchange})
            by_exchange[exchange or "UNKNOWN"] += 1
    audit = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": SOURCE_PAGE,
        "source_api": SECTORS_URL,
        "mode": "official exchange data only; no company-name inference",
        "summary": {
            "universe_count": len(stocks),
            "missing_before": len(missing),
            "matched_from_exchange_data": len(matched),
            "unresolved": len(unresolved),
            "unresolved_by_exchange": dict(sorted(by_exchange.items())),
        },
        "matched": matched,
        "unresolved": unresolved,
    }
    return audit, len(matched)


def atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def apply_classifications(universe: dict[str, Any], classifications: dict[str, str]) -> int:
    count = 0
    for item in universe.get("stocks", []):
        if not isinstance(item, dict) or str(item.get("i") or "").strip():
            continue
        ticker = str(item.get("t") or "").strip().upper()
        if str(item.get("x") or "").strip().upper() == "HOSE" and ticker in classifications:
            item["i"] = classifications[ticker]
            count += 1
    return count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--universe", type=Path, default=Path("universe.json"), help="Đường dẫn universe.json")
    parser.add_argument("--report", type=Path, help="Ghi báo cáo audit JSON ra file (tùy chọn)")
    parser.add_argument("--apply", action="store_true", help="Ghi ngành có nguồn xác thực vào universe.json; mặc định chỉ audit")
    args = parser.parse_args(argv)

    try:
        universe = json.loads(args.universe.read_text(encoding="utf-8"))
        with requests.Session() as session:
            session.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json"})
            classifications = fetch_hose_classifications(session)
        audit, _ = build_audit(universe, classifications)
        if args.apply:
            changed = apply_classifications(universe, classifications)
            atomic_write_json(args.universe, universe)
            audit["summary"]["written"] = changed
        if args.report:
            atomic_write_json(args.report, audit)
    except (OSError, ValueError, requests.RequestException, SourceError) as exc:
        print(f"Lỗi: {exc}", file=sys.stderr)
        return 1

    summary = audit["summary"]
    action = f"Đã ghi {summary.get('written', 0)} mã vào {args.universe}." if args.apply else "Chế độ audit: không sửa universe.json."
    print(f"Tổng mã: {summary['universe_count']}; thiếu ngành ban đầu: {summary['missing_before']}; "
          f"ghép được từ HOSE: {summary['matched_from_exchange_data']}; chưa xác minh: {summary['unresolved']}.")
    print("Chưa xác minh theo sàn: " + ", ".join(f"{k}={v}" for k, v in summary["unresolved_by_exchange"].items()))
    unresolved_by_exchange: dict[str, list[str]] = {}
    for item in audit["unresolved"]:
        unresolved_by_exchange.setdefault(item["exchange"], []).append(item["ticker"])
    for exchange, tickers in sorted(unresolved_by_exchange.items()):
        preview = ", ".join(tickers[:20])
        suffix = f" … (+{len(tickers) - 20} mã; xem JSON report)" if len(tickers) > 20 else ""
        print(f"{exchange}: {preview}{suffix}")
    print(action)
    if args.report:
        print(f"Báo cáo: {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
