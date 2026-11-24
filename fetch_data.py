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

    (a.out / "prices").mkdir(parents=True, exist_ok=True)
    session = requests.Session()
    status: dict[str, Any] = {"run_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), "bctc": {"ok": 0, "fail": 0, "errors": []}}
    done = 0
    for t in tickers:
        if a.only != "prices":
            try:
                data = cafef_fetch.fetch_ticker(t, cfg.get(t, "chung"), session)
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

    have_f = sorted(p.stem for p in a.out.glob("*.json") if p.stem not in ("index", "status"))
    have_p = sorted(p.stem for p in (a.out / "prices").glob("*.json"))
    (a.out / "index.json").write_text(json.dumps({"tickers": have_f, "prices": have_p, "updated": status["run_at"]}, ensure_ascii=False), encoding="utf-8")
    (a.out / "status.json").write_text(json.dumps(status, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0 if done else 1   # chỉ báo lỗi khi không lấy được gì, để Action vẫn lưu phần đã lấy


if __name__ == "__main__":
    sys.exit(main())
