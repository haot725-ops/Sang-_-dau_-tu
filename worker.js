/**
 * Cloudflare Worker cho app Sàng: cầu nối để trình duyệt lấy giá và báo cáo tài chính không bị chặn CORS.
 * Triển khai: dash.cloudflare.com > Workers & Pages > Create > Hello World > Edit code > dán file này > Deploy.
 * Sau đó dán địa chỉ https://....workers.dev vào mục Cài đặt trong app.
 *
 * Đường dẫn:
 *   /?symbol=FPT                  báo cáo tài chính 3 loại từ CafeF   -> {bs,is,cf}
 *   /quote?symbols=FPT,HPG        giá gần thời gian thực từ VPS        -> {quotes:[{t,price,ref,change,changePct,vol}]}
 *   /history?symbol=FPT           giá ngày, thử lần lượt các nguồn      -> {t,src,d,o,h,l,c,v}
 * Các nguồn là API không chính thức, có thể đổi hoặc chặn bất cứ lúc nào.
 */
const ALLOW = ["https://haot725-ops.github.io"]; // thêm địa chỉ trang của ông nếu khác
const UA = { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36", Accept: "application/json, text/plain, */*" };
const CAFEF = {
  bs: ["https://apiweb.cafef.vn/api/v2/BCTC/GetReportCDKT", "ALL"],
  is: ["https://apiweb.cafef.vn/api/v1/BCTC/GetReportDetail", "KQKD"],
  cf: ["https://apiweb.cafef.vn/api/v1/BCTC/GetReportLCTT", "ALL"],
};
const KEEP = 260;

const num = (x) => { const v = Number(x); return Number.isFinite(v) ? v : null; };
const ymd = (ts) => new Date((Number(ts) + 7 * 3600) * 1000).toISOString().slice(0, 10);

function normalizeBars(rows) {
  const m = new Map();
  for (const r of rows) {
    const c = num(r.c), d = String(r.d || "").slice(0, 10);
    if (!d || c == null || c <= 0) continue;
    const o = num(r.o) || c;
    m.set(d, { o, h: Math.max(num(r.h) || 0, o, c), l: Math.min(...[num(r.l), o, c].filter((x) => x != null && x > 0)), c, v: num(r.v) || 0 });
  }
  const days = [...m.keys()].sort().slice(-KEEP);
  const k = days.length && Math.max(...days.map((d) => m.get(d).c)) < 500 ? 1000 : 1; // nguồn trả nghìn đồng
  const out = { d: days, o: [], h: [], l: [], c: [], v: [] };
  for (const d of days) { const b = m.get(d); out.o.push(b.o * k); out.h.push(b.h * k); out.l.push(b.l * k); out.c.push(b.c * k); out.v.push(b.v); }
  return out;
}
const P = {
  vndirect: (js) => (js.data || []).map((x) => ({ d: x.date, o: x.open, h: x.high, l: x.low, c: x.close, v: x.nmVolume ?? x.volume })),
  tcbs: (js) => (js.data || []).map((x) => ({ d: String(x.tradingDate || "").slice(0, 10), o: x.open, h: x.high, l: x.low, c: x.close, v: x.volume })),
  dnse: (js) => (js.t || []).map((t, i) => ({ d: ymd(t), o: js.o?.[i], h: js.h?.[i], l: js.l?.[i], c: js.c?.[i], v: js.v?.[i] })),
};
async function history(sym) {
  const now = Math.floor(Date.now() / 1000), from = now - 430 * 86400;
  const start = new Date(from * 1000).toISOString().slice(0, 10);
  const tries = [
    ["vndirect", `https://finfo-api.vndirect.com.vn/v4/stock_prices?sort=date&size=${KEEP + 20}&q=${encodeURIComponent(`code:${sym}~date:gte:${start}`)}`],
    ["tcbs", `https://apipub.tcbs.com.vn/stock-insight/v1/stock/bars-long-term?ticker=${sym}&type=stock&resolution=D&from=${from}&to=${now + 86400}`],
    ["dnse", `https://services.entrade.com.vn/chart-api/v2/ohlcs/stock?symbol=${sym}&resolution=1D&from=${from}&to=${now + 86400}`],
  ];
  const errs = [];
  for (const [src, url] of tries) {
    try {
      const r = await fetch(url, { headers: UA });
      if (!r.ok) throw new Error("HTTP " + r.status);
      const b = normalizeBars(P[src](await r.json()));
      if (b.d.length < 60) throw new Error("chỉ có " + b.d.length + " phiên");
      return { t: sym, src, asof: b.d[b.d.length - 1], ...b };
    } catch (e) { errs.push(src + ": " + (e.message || e)); }
  }
  throw new Error(errs.join(" | "));
}
async function quotes(symbols) {
  const r = await fetch("https://bgapidatafeed.vps.com.vn/getliststockdata/" + symbols.join(","), { headers: UA });
  if (!r.ok) throw new Error("VPS HTTP " + r.status);
  const js = await r.json();
  if (!Array.isArray(js)) throw new Error("VPS trả dữ liệu lạ");
  const k = (p) => (p > 0 && p < 1000 ? p * 1000 : p); // VPS trả nghìn đồng
  return js.map((x) => {
    const ref = k(num(x.r)), last = k(num(x.lastPrice));
    const price = last > 0 ? last : ref;
    if (!x.sym || !(price > 0)) return null;
    return { t: String(x.sym).toUpperCase(), price, ref, change: ref ? price - ref : null, changePct: ref ? ((price - ref) / ref) * 100 : null, vol: num(x.lot) };
  }).filter(Boolean);
}
async function bctc(sym) {
  const out = {};
  await Promise.all(Object.entries(CAFEF).map(async ([k, [url, type]]) => {
    const u = new URL(url);
    u.search = new URLSearchParams({ symbol: sym, pageIndex: "1", pageSize: "12", reportType: type, TypeTime: "QUY" }).toString();
    const r = await fetch(u, { headers: UA });
    if (!r.ok) throw new Error("CafeF " + k + " trả " + r.status);
    out[k] = await r.json();
  }));
  return out;
}

async function cached(ctx, key, ttl, make) {
  const cache = caches.default, req = new Request("https://cache.local/" + key);
  const hit = await cache.match(req);
  if (hit) return hit.text();
  const body = JSON.stringify(await make());
  ctx.waitUntil(cache.put(req, new Response(body, { headers: { "Cache-Control": "max-age=" + ttl } })));
  return body;
}

export default {
  async fetch(req, env, ctx) {
    const origin = req.headers.get("Origin") || "";
    const cors = { "Access-Control-Allow-Origin": ALLOW.includes(origin) ? origin : ALLOW[0], "Access-Control-Allow-Methods": "GET, OPTIONS", Vary: "Origin" };
    const send = (body, status = 200) => new Response(typeof body === "string" ? body : JSON.stringify(body), { status, headers: { ...cors, "Content-Type": "application/json; charset=utf-8" } });
    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    const u = new URL(req.url), path = u.pathname.replace(/\/+$/, "") || "/";
    try {
      if (path === "/quote") {
        const list = [...new Set((u.searchParams.get("symbols") || "").toUpperCase().split(/[\s,]+/))].filter((s) => /^[A-Z0-9]{2,10}$/.test(s)).slice(0, 80);
        if (!list.length) return send({ error: "symbols không hợp lệ" }, 400);
        return send(await cached(ctx, "q/" + list.join(","), 15, async () => ({ quotes: await quotes(list), at: new Date().toISOString() })));
      }
      const sym = (u.searchParams.get("symbol") || "").toUpperCase();
      if (!/^[A-Z0-9]{2,10}$/.test(sym)) return send({ error: "symbol không hợp lệ" }, 400);
      if (path === "/history") return send(await cached(ctx, "h/" + sym, 1800, () => history(sym)));
      if (path === "/" || path === "/bctc") return send(await cached(ctx, "b/" + sym, 21600, () => bctc(sym)));
      return send({ error: "không có đường dẫn này" }, 404);
    } catch (e) {
      return send({ error: String((e && e.message) || e) }, 502);
    }
  },
};
