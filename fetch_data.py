#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_data.py - Lấy báo cáo tài chính (CafeF) và giá lịch sử (nhiều nguồn dự phòng) cho các mã trong tickers.json.

Kết quả:
  data/<MÃ>.json          báo cáo tài chính theo quý (định dạng của cafef_fetch.py)
  data/prices/<MÃ>.json   giá ngày: {"t","src","asof","d":[ngày],"o","h","l","c","v"}  (giá đơn vị đồng)
  data/index.json         danh sách mã có báo cáo / có giá
  data/status.json        nhật ký từng nguồn: nguồn nào chạy được, lỗi gì (để biết sửa ở đâu)

Các địa chỉ nguồn giá là API không chính thức, được nhớ từ các dự án mã nguồn mở, có thể đổi hoặc chặn bất cứ lúc nào.
Chương trình thử lần lượt từng nguồn, nguồn nào cho đủ dữ liệu thì dừng. Nếu mọi nguồn lỗi, giữ nguyên file cũ.

CÀI ĐẶT:  pip install requests
CHẠY:     python fetch_data.py                      # đọc tickers.json
          python fetch_data.py --tickers FPT HPG    # chỉ vài mã
          python fetch_data.py --only prices        # chỉ giá  (hoặc --only bctc)
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import logging
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

import requests

import cafef_fetch

log = logging.getLogger("data")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
      "Accept": "application/json, text/plain, */*"}
KEEP = 260          # số phiên giữ lại (đủ cho MA50 và 9 tháng biểu đồ)
MIN_BARS = 60       # ít hơn mức này coi là nguồn lỗi
UNIVERSE_FILE = Path("universe.json")
UNIVERSE_BATCH_SIZE = 200
UNIVERSE_RETRY_LIMIT = 50
UNIVERSE_WORKERS = 3  # giới hạn request đồng thời; mỗi worker vẫn nghỉ giữa các API CafeF

# Đồng bộ với nhóm phân loại tài chính trong index.html. Chỉ định kiểu báo cáo
# chuyên biệt khi có mã được nhận diện rõ; mã khác giữ nhóm doanh nghiệp chung.
BANK_TICKERS = set("VCB BID CTG TCB MBB ACB VPB HDB STB SHB TPB LPB MSB OCB EIB VIB SSB ABB BAB BVB KLB NAB NVB PGB SGB VAB".split())
BROKER_TICKERS = set("SSI VND VCI HCM SHS MBS FTS BSI CTS AGR VDS ORS TVS APS BVS VIX SBS DSC PSI WSS".split())
REAL_ESTATE_TICKERS = set("NVL VHM VIC VRE KDH DXG DIG CEO PDR NLG KBC IDC SZC HDG HDC TCH SCR NTL CRE LDG QCG HQC ITC AGG NBB SJS TDH DXS KHG L14 D2D SIP BCM".split())


def report_sector(ticker: str, configured: str | None = None) -> str:
    """Chọn schema CafeF theo metadata đã biết, không suy diễn từ nhãn Finance chung."""
    if configured in {"nganhang", "ck", "bds", "chung"}:
        return configured
    if ticker in BANK_TICKERS:
        return "nganhang"
    if ticker in BROKER_TICKERS:
        return "ck"
    if ticker in REAL_ESTATE_TICKERS:
        return "bds"
    return "chung"


def select_universe_batch(universe_file: Path, core: set[str], shard_size: int,
                          shard_index: int) -> tuple[list[str], int]:
    """Trả về lô xoay vòng mã ngoài tickers.json và tổng số lô."""
    data = json.loads(universe_file.read_text(encoding="utf-8"))
    all_tickers = sorted({str(x.get("t", "")).upper() for x in data.get("stocks", [])
                          if isinstance(x, dict) and x.get("t")})
    extra = [t for t in all_tickers if t not in core]
    count = max(1, (len(extra) + shard_size - 1) // shard_size)
    idx = shard_index % count
    return extra[idx * shard_size:(idx + 1) * shard_size], count


class SourceError(Exception):
    pass


def _num(x: Any) -> float | None:
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def normalize_bars(rows: list[dict]) -> dict[str, list]:
    """rows: [{d:'YYYY-MM-DD', o,h,l,c,v}] -> mảng song song, tăng dần theo ngày, giá đổi về đồng nếu nguồn trả nghìn đồng."""
    clean: dict[str, dict] = {}
    for r in rows:
        o, h, l, c, v = (_num(r.get(k)) for k in "ohlcv")
        d = str(r.get("d") or "")[:10]
        if not d or c is None or c <= 0:
            continue
        o = o or c
        h = max(x for x in (h, o, c) if x is not None)
        l = min(x for x in (l, o, c) if x is not None and x > 0)
        clean[d] = {"o": o, "h": h, "l": l, "c": c, "v": v or 0.0}
    days = sorted(clean)[-KEEP:]
    if days and max(clean[d]["c"] for d in days) < 500:   # giá nguồn trả theo nghìn đồng (vd 128.5) -> đồng
        for d in days:
            for k in "ohlc":
                clean[d][k] *= 1000
    return {"d": days, **{k: [clean[d][k] for d in days] for k in "ohlcv"}}


# ---- Parser từng nguồn: nhận JSON đã giải mã, trả list[{d,o,h,l,c,v}] ----
def parse_vnd(js: Any) -> list[dict]:
    return [{"d": x.get("date"), "o": x.get("open"), "h": x.get("high"), "l": x.get("low"), "c": x.get("close"),
             "v": x.get("nmVolume", x.get("volume"))} for x in (js or {}).get("data", [])]


def parse_tcbs(js: Any) -> list[dict]:
    return [{"d": str(x.get("tradingDate", ""))[:10], "o": x.get("open"), "h": x.get("high"), "l": x.get("low"),
             "c": x.get("close"), "v": x.get("volume")} for x in (js or {}).get("data", [])]


def parse_dnse(js: Any) -> list[dict]:
    t = (js or {}).get("t") or []
    out = []
    for i, ts in enumerate(t):
        d = datetime.fromtimestamp(int(ts), timezone(timedelta(hours=7))).strftime("%Y-%m-%d")
        out.append({"d": d, **{k: (js.get(k) or [None] * len(t))[i] for k in "ohlcv"}})
    return out


def parse_vci(js: Any) -> list[dict]:
    if not isinstance(js, list) or not js:
        return []
    x = js[0]
    out = []
    for i, ts in enumerate(x.get("t") or []):
        d = datetime.fromtimestamp(int(ts), timezone(timedelta(hours=7))).strftime("%Y-%m-%d")
        out.append({"d": d, **{k: (x.get(k) or [None] * len(x["t"]))[i] for k in "ohlcv"}})
    return out


def sources(sym: str, today: date) -> list[tuple[str, Callable[[requests.Session], Any], Callable[[Any], list[dict]]]]:
    start = today - timedelta(days=430)
    t0, t1 = int(datetime.combine(start, datetime.min.time()).timestamp()), int(time.time()) + 86400

    def get(url: str, **kw):
        def run(s: requests.Session):
            r = s.get(url, headers=UA, timeout=30, **kw)
            r.raise_for_status()
            return r.json()
        return run

    def post_vci(s: requests.Session):
        r = s.post("https://trading.vietcap.com.vn/api/chart/OHLCChart/gap-chart", headers={**UA, "Content-Type": "application/json"},
                   json={"timeFrame": "ONE_DAY", "symbols": [sym], "to": t1, "countBack": KEEP + 10}, timeout=30)
        r.raise_for_status()
        return r.json()

    return [
        ("vndirect", get("https://finfo-api.vndirect.com.vn/v4/stock_prices",
                         params={"sort": "date", "size": KEEP + 20, "q": f"code:{sym}~date:gte:{start.isoformat()}"}), parse_vnd),
        ("tcbs", get("https://apipub.tcbs.com.vn/stock-insight/v1/stock/bars-long-term",
                     params={"ticker": sym, "type": "stock", "resolution": "D", "from": t0, "to": t1}), parse_tcbs),
        ("dnse", get("https://services.entrade.com.vn/chart-api/v2/ohlcs/stock",
                     params={"symbol": sym, "resolution": "1D", "from": t0, "to": t1}), parse_dnse),
        ("vietcap", post_vci, parse_vci),
    ]


def fetch_prices(sym: str, session: requests.Session, status: dict) -> dict | None:
    for name, call, parse in sources(sym, date.today()):
        try:
            bars = normalize_bars(parse(call(session)))
            if len(bars["d"]) < MIN_BARS:
                raise SourceError(f"chỉ có {len(bars['d'])} phiên")
            status.setdefault("prices", {}).setdefault(name, {"ok": 0, "fail": 0})["ok"] += 1
            return {"t": sym, "src": name, "asof": bars["d"][-1], "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **bars}
        except (requests.RequestException, ValueError, SourceError, KeyError, TypeError, IndexError) as e:
            st = status.setdefault("prices", {}).setdefault(name, {"ok": 0, "fail": 0})
            st["fail"] += 1
            st["last_error"] = f"{sym}: {type(e).__name__}: {e}"[:200]
            log.warning("%s nguồn %s lỗi: %s", sym, name, e)
        time.sleep(0.5)
    return None


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Lấy BCTC và giá cho app Sàng")
    ap.add_argument("--tickers", nargs="*")
    ap.add_argument("--config", type=Path, default=Path("tickers.json"))
    ap.add_argument("--out", type=Path, default=Path("data"))
    ap.add_argument("--only", choices=["bctc", "prices"])
    ap.add_argument("--universe-batch", action="store_true",
                    help="Lấy thêm một lô BCTC xoay vòng từ universe.json")
    ap.add_argument("--universe-file", type=Path, default=UNIVERSE_FILE)
    ap.add_argument("--batch-size", type=int, default=UNIVERSE_BATCH_SIZE)
    ap.add_argument("--universe-workers", type=int, default=UNIVERSE_WORKERS,
                    help="Số mã universe xử lý đồng thời (mặc định 3)")
    ap.add_argument("--merge-status", action="store_true",
                    help="Gộp status nhóm lõi vừa chạy trong cùng workflow")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")

    cfg: dict[str, str] = {}
    if a.config.exists():
        try:
            cfg = {k.upper(): v for k, v in json.loads(a.config.read_text(encoding="utf-8")).items()}
        except (OSError, ValueError) as e:
            log.error("Không đọc được %s: %s", a.config, e)
            return 2
    tickers = list(dict.fromkeys(t.upper() for t in a.tickers)) if a.tickers else list(cfg)
    if a.universe_batch and not a.tickers:
        tickers = []
    if not tickers and not a.universe_batch:
        log.error("Chưa có mã nào. Tạo tickers.json hoặc dùng --tickers")
        return 2
    if a.batch_size < 1:
        log.error("--batch-size phải lớn hơn 0")
        return 2
    if a.universe_workers < 1:
        log.error("--universe-workers phải lớn hơn 0")
        return 2

    (a.out / "prices").mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    status: dict[str, Any] = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "bctc": {"ok": 0, "fail": 0, "errors": []}}
    core = set(cfg) if a.universe_batch else set(tickers)
    cursor_path = a.out / "state" / "universe_cursor.json"
    retry_path = a.out / "state" / "universe_retry.json"
    shard_tickers: list[str] = []
    shard_index = 0
    shard_count = 0
    retries: list[str] = []
    deferred_retries: list[str] = []
    if a.universe_batch:
        try:
            cursor = json.loads(cursor_path.read_text(encoding="utf-8")) if cursor_path.exists() else {}
            shard_index = max(0, int(cursor.get("next_shard", 0)))
            shard_tickers, shard_count = select_universe_batch(a.universe_file, core, a.batch_size, shard_index)
            if retry_path.exists():
                raw_retry = json.loads(retry_path.read_text(encoding="utf-8"))
                retry_queue = list(dict.fromkeys(str(t).upper() for t in raw_retry
                                                  if isinstance(t, str) and t.upper() not in core))
                retries = retry_queue[:UNIVERSE_RETRY_LIMIT]
                deferred_retries = retry_queue[UNIVERSE_RETRY_LIMIT:]
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as e:
            log.error("Không tạo được lô universe: %s", e)
            return 2
        shard_tickers = list(dict.fromkeys(retries + [t for t in shard_tickers if t not in retries]))
        deferred_retries = [t for t in deferred_retries if t not in shard_tickers]
        status["universe_batch"] = {"index": shard_index % shard_count, "count": shard_count,
                                    "batch_size": a.batch_size, "scheduled_symbols": len(shard_tickers),
                                    "retry_symbols": len(retries), "ok": 0, "fail": 0, "errors": []}
    done = 0
    for t in tickers:
        if a.only != "prices":
            try:
                data = cafef_fetch.fetch_ticker(t, report_sector(t, cfg.get(t)), session)
                (a.out / f"{t}.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                status["bctc"]["ok"] += 1
                done += 1
            except cafef_fetch.FetchError as e:
                status["bctc"]["fail"] += 1
                status["bctc"]["errors"].append(str(e)[:200])
                log.error("BCTC %s lỗi, giữ dữ liệu cũ: %s", t, e)
        if a.only != "bctc":
            p = fetch_prices(t, session, status)
            if p:
                (a.out / "prices" / f"{t}.json").write_text(json.dumps(p, separators=(",", ":")), encoding="utf-8")
                log.info("%s: %d phiên giá từ %s", t, len(p["d"]), p["src"])
                done += 1
            else:
                log.error("Giá %s: mọi nguồn đều lỗi, giữ dữ liệu cũ", t)
        time.sleep(0.5)

    failed_universe: list[str] = []
    if shard_tickers:
        def fetch_universe_ticker(ticker: str) -> dict[str, Any]:
            # Session riêng cho mỗi mã; không chia sẻ requests.Session giữa các thread.
            with requests.Session() as worker_session:
                return cafef_fetch.fetch_ticker(ticker, report_sector(ticker), worker_session)

        with ThreadPoolExecutor(max_workers=a.universe_workers) as pool:
            futures = {pool.submit(fetch_universe_ticker, t): t for t in shard_tickers}
            for future in as_completed(futures):
                t = futures[future]
                try:
                    data = future.result()
                    (a.out / f"{t}.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
                    status["bctc"]["ok"] += 1
                    status["universe_batch"]["ok"] += 1
                    done += 1
                    log.info("universe %s: đã lưu %d kỳ", t, len(data["is"]["periods"]))
                except Exception as e:
                    failed_universe.append(t)
                    err = str(e)[:200]
                    status["bctc"]["fail"] += 1
                    status["bctc"]["errors"].append(err)
                    status["universe_batch"]["fail"] += 1
                    status["universe_batch"]["errors"].append(err)
                    log.error("Bỏ qua universe %s, giữ dữ liệu cũ: %s", t, e)
    if a.universe_batch:
        cursor_path.parent.mkdir(parents=True, exist_ok=True)
        cursor_path.write_text(json.dumps({"next_shard": (shard_index + 1) % shard_count,
                                           "last_shard": shard_index % shard_count,
                                           "shards": shard_count,
                                           "updated_at": status["run_at"]}, ensure_ascii=False, indent=2), encoding="utf-8")
        retry_next = list(dict.fromkeys(failed_universe + deferred_retries))
        retry_path.write_text(json.dumps(retry_next, ensure_ascii=False, indent=2), encoding="utf-8")
        status["universe_batch"]["retry_next_run"] = len(retry_next)

    have_f = sorted(p.stem for p in a.out.glob("*.json") if p.stem not in ("index", "status"))
    have_p = sorted(p.stem for p in (a.out / "prices").glob("*.json"))
    (a.out / "index.json").write_text(json.dumps({"tickers": have_f, "prices": have_p, "updated": status["run_at"]}, ensure_ascii=False), encoding="utf-8")
    status_path = a.out / "status.json"
    if a.merge_status and status_path.exists():
        try:
            previous = json.loads(status_path.read_text(encoding="utf-8"))
            prev_at = datetime.fromisoformat(str(previous.get("run_at", "")).replace("Z", "+00:00"))
            now_at = datetime.fromisoformat(status["run_at"])
            if -timedelta(minutes=2) <= now_at - prev_at <= timedelta(hours=2):
                old_bctc = previous.get("bctc", {})
                status["bctc"]["ok"] += int(old_bctc.get("ok", 0))
                status["bctc"]["fail"] += int(old_bctc.get("fail", 0))
                status["bctc"]["errors"] = (old_bctc.get("errors", []) + status["bctc"]["errors"])[:300]
                status["prices"] = previous.get("prices", {})
                status["core_run_at"] = previous.get("run_at")
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            log.warning("Không thể gộp status lượt trước; ghi status batch hiện tại")
    status_path.write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if done else 1   # chỉ báo lỗi khi không lấy được gì, để Action vẫn lưu phần đã lấy


if __name__ == "__main__":
    sys.exit(main())
