#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Audit các kỳ LCTT toàn 0 trong snapshot và đối chiếu API CafeF.

Script chỉ đọc data/<TICKER>.json, chỉ ghi báo cáo audit; tuyệt đối không sửa
hoặc ghi đè snapshot tài chính. Khi CafeF trả nhiều type (ví dụ M/H) khác nhau,
script giữ nguyên cả hai ứng viên và đánh dấu xung đột, không tự chọn.

Cách chạy từ thư mục repo:
  python3 audit_zero_cf.py
  python3 audit_zero_cf.py --offline
  python3 audit_zero_cf.py --tickers ACB IDC --report /tmp/lctt-audit.json
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

ENDPOINT = "https://apiweb.cafef.vn/api/v1/BCTC/GetReportLCTT"
HEADERS = {"User-Agent": "Mozilla/5.0", "Accept": "application/json"}
SKIP_FILES = {"index.json", "status.json"}


def is_number(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def read_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def scan_snapshot(data_dir: Path, selected: set[str] | None = None) -> tuple[list[dict], list[dict], int]:
    """Trả về kỳ all-zero, lỗi cấu trúc và số file BCTC được quét."""
    suspects: list[dict] = []
    errors: list[dict] = []
    scanned = 0

    for path in sorted(data_dir.glob("*.json")):
        if path.name in SKIP_FILES:
            continue
        ticker = path.stem.upper()
        if selected and ticker not in selected:
            continue
        scanned += 1
        try:
            doc = read_json(path)
        except (OSError, json.JSONDecodeError) as exc:
            errors.append({"file": str(path), "error": f"Không đọc được JSON: {exc}"})
            continue

        if not isinstance(doc, dict) or not isinstance(doc.get("cf"), dict):
            errors.append({"file": str(path), "error": "Thiếu object cf"})
            continue
        report = doc["cf"]
        periods = report.get("periods")
        items = report.get("items")
        if not isinstance(periods, list) or not isinstance(items, list):
            errors.append({"ticker": ticker, "error": "cf.periods hoặc cf.items sai schema"})
            continue

        for item in items:
            if not isinstance(item, dict):
                errors.append({"ticker": ticker, "error": "Có item LCTT không phải object"})
                continue
            if item.get("group"):
                continue
            values = item.get("values")
            if not isinstance(values, list) or len(values) != len(periods):
                errors.append({
                    "ticker": ticker,
                    "code": item.get("code"),
                    "error": "Độ dài values không khớp periods",
                })

        for i, period in enumerate(periods):
            numeric_values: list[int | float] = []
            missing_count = 0
            invalid_count = 0
            line_count = 0
            for item in items:
                if not isinstance(item, dict) or item.get("group"):
                    continue
                values = item.get("values")
                if not isinstance(values, list) or i >= len(values):
                    invalid_count += 1
                    continue
                line_count += 1
                value = values[i]
                if value is None:
                    missing_count += 1
                elif is_number(value):
                    numeric_values.append(value)
                else:
                    invalid_count += 1

            # Chỉ gọi là all-zero nếu mọi dòng cấp chỉ tiêu đều có số hợp lệ,
            # không thiếu/null, không lỗi kiểu dữ liệu và tất cả đúng bằng 0.
            if line_count and numeric_values and not missing_count and not invalid_count and all(v == 0 for v in numeric_values):
                suspects.append({
                    "ticker": ticker,
                    "period": str(period),
                    "snapshot_line_items": line_count,
                    "snapshot_nonzero_items": 0,
                    "snapshot_status": "ALL_ZERO",
                    "cafef_status": "NOT_CHECKED",
                    "cafef_candidates": [],
                    "decision": "NO_AUTOMATIC_REPAIR",
                })

    return suspects, errors, scanned


def extract_cafef_candidates(payload: Any, wanted_periods: set[str]) -> tuple[dict[str, dict[str, dict[str, float]]], list[str]]:
    """Gộp raw CafeF theo kỳ/type/mã chỉ tiêu, phát hiện mâu thuẫn trong cùng type."""
    if not isinstance(payload, dict) or not payload.get("isSuccess"):
        raise ValueError("CafeF không báo isSuccess")
    value = payload.get("value")
    groups = value.get("data") if isinstance(value, dict) else None
    if not isinstance(groups, list):
        raise ValueError("CafeF thiếu value.data dạng list")

    candidates: dict[str, dict[str, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    internal_conflicts: list[str] = []
    for group in groups:
        if not isinstance(group, dict) or not isinstance(group.get("data"), list):
            raise ValueError("Nhóm LCTT CafeF sai schema")
        for period_record in group["data"]:
            if not isinstance(period_record, dict):
                continue
            period = str(period_record.get("time") or "")
            if period not in wanted_periods:
                continue
            report_type = str(period_record.get("type") or "UNKNOWN").strip().upper()
            rows = period_record.get("data")
            if not isinstance(rows, list):
                raise ValueError(f"Dữ liệu dòng sai schema tại {period}/{report_type}")
            target = candidates[period][report_type]
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError(f"Dòng chỉ tiêu sai schema tại {period}/{report_type}")
                code = str(row.get("code") or "")
                raw = row.get("value")
                if not code or raw is None:
                    continue
                if not is_number(raw):
                    raise ValueError(f"Giá trị không phải số hữu hạn tại {period}/{report_type}/{code}")
                if code in target and target[code] != raw:
                    internal_conflicts.append(f"{period}/{report_type}/{code}")
                else:
                    target[code] = float(raw)
    return candidates, internal_conflicts


def compare_candidate_types(types: dict[str, dict[str, float]]) -> str:
    """Phân loại nguồn, không đưa ra lựa chọn thay thế."""
    if not types:
        return "API_PERIOD_NOT_RETURNED"
    if len(types) == 1:
        only = next(iter(types.values()))
        if only and all(value == 0 for value in only.values()):
            return "API_SINGLE_TYPE_ALL_ZERO"
        return "API_SINGLE_TYPE_HAS_NONZERO"
    maps = list(types.values())
    if all(candidate == maps[0] for candidate in maps[1:]):
        return "API_TYPES_IDENTICAL"
    return "API_TYPE_CONFLICT"


def summarize_candidates(types: dict[str, dict[str, float]]) -> list[dict]:
    out = []
    for report_type in sorted(types):
        values = types[report_type]
        out.append({
            "type": report_type,
            "line_items": len(values),
            "nonzero_items": sum(value != 0 for value in values.values()),
            "all_zero": bool(values) and all(value == 0 for value in values.values()),
        })
    return out


def check_cafef(ticker: str, periods: set[str], session: requests.Session, timeout: float) -> tuple[dict, list[str]]:
    response = session.get(
        ENDPOINT,
        params={"symbol": ticker, "pageIndex": 1, "pageSize": 20, "reportType": "ALL", "TypeTime": "QUY"},
        headers=HEADERS,
        timeout=timeout,
    )
    response.raise_for_status()
    candidates, conflicts = extract_cafef_candidates(response.json(), periods)
    return candidates, conflicts


def run_audit(args: argparse.Namespace) -> dict:
    selected = {ticker.upper() for ticker in args.tickers} if args.tickers else None
    suspects, errors, scanned = scan_snapshot(args.data_dir, selected)
    api_errors: list[dict] = []

    if not args.offline and suspects:
        by_ticker: dict[str, list[dict]] = defaultdict(list)
        for row in suspects:
            by_ticker[row["ticker"]].append(row)

        session = requests.Session()
        tickers = sorted(by_ticker)
        for n, ticker in enumerate(tickers):
            periods = {row["period"] for row in by_ticker[ticker]}
            try:
                candidates, internal_conflicts = check_cafef(ticker, periods, session, args.timeout)
                for row in by_ticker[ticker]:
                    types = candidates.get(row["period"], {})
                    row["cafef_status"] = compare_candidate_types(types)
                    row["cafef_candidates"] = summarize_candidates(types)
                    if internal_conflicts:
                        related = [c for c in internal_conflicts if c.startswith(row["period"] + "/")]
                        if related:
                            row["cafef_status"] = "API_INTERNAL_CONFLICT"
                            row["cafef_internal_conflicts"] = related
            except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
                message = f"{type(exc).__name__}: {exc}"
                for row in by_ticker[ticker]:
                    row["cafef_status"] = "API_ERROR"
                    row["cafef_error"] = message[:500]
                api_errors.append({"ticker": ticker, "error": message[:500]})
            if n + 1 < len(tickers) and args.delay > 0:
                time.sleep(args.delay)

    status_counts = Counter(row["cafef_status"] for row in suspects)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": "CafeF public LCTT API" if not args.offline else "Local snapshot only",
        "data_dir": str(args.data_dir),
        "scanned_bctc_files": scanned,
        "all_zero_periods": len(suspects),
        "tickers_with_all_zero_periods": len({row["ticker"] for row in suspects}),
        "cafef_status_counts": dict(sorted(status_counts.items())),
        "api_errors": api_errors,
        "snapshot_errors": errors,
        "automatic_repair": "disabled; financial snapshot files are never modified",
        "findings": suspects,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit LCTT periods whose statement line items are all zero.")
    parser.add_argument("--data-dir", type=Path, default=Path("data"), help="Thư mục snapshot (mặc định: data)")
    parser.add_argument("--report", type=Path, default=Path("reports/lctt_zero_audit.json"), help="File JSON báo cáo")
    parser.add_argument("--offline", action="store_true", help="Không gọi API CafeF; chỉ quét snapshot")
    parser.add_argument("--tickers", nargs="*", help="Chỉ quét các mã được liệt kê")
    parser.add_argument("--timeout", type=float, default=20.0, help="Timeout mỗi request CafeF, giây")
    parser.add_argument("--delay", type=float, default=1.0, help="Nghỉ giữa các mã khi gọi CafeF, giây")
    args = parser.parse_args(argv)

    if args.timeout <= 0 or args.delay < 0:
        parser.error("--timeout phải > 0 và --delay phải >= 0")
    if not args.data_dir.is_dir():
        parser.error(f"Không tìm thấy thư mục dữ liệu: {args.data_dir}")

    report = run_audit(args)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with args.report.open("w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write("\n")

    print(
        f"Đã quét {report['scanned_bctc_files']} file BCTC; "
        f"phát hiện {report['all_zero_periods']} kỳ LCTT toàn 0 "
        f"ở {report['tickers_with_all_zero_periods']} mã. "
        f"Không sửa snapshot. Báo cáo: {args.report}"
    )
    if report["snapshot_errors"] or report["api_errors"]:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
