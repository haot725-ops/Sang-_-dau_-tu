/**
 * Cloudflare Worker cho app Sàng: cầu nối để trình duyệt lấy giá và báo cáo tài chính không bị chặn CORS.
 * Triển khai: dash.cloudflare.com > Workers & Pages > Create > Hello World > Edit code > dán file này > Deploy.
 * Sau đó dán địa chỉ https://....workers.dev vào mục Cài đặt trong app.
 *
 * Đường dẫn:
 *   /?symbol=FPT                  báo cáo tài chính 3 loại từ CafeF   -> {bs,is,cf}
 *   /quote?symbols=FPT,HPG        giá gần thời gian thực từ VPS        -> {quotes:[{t,price,ref,change,changePct,vol}]}
 *   /history?symbol=FPT           giá ngày, thử lần lượt các nguồn      -> {t,src,d,o,h,l,c,v}
 *   /movers?ex=HOSE&n=10          top tăng/giảm trong ngày theo sàn     -> {ex,at,up:[],down:[],stats}
 *                                 ex = HOSE | HNX | UPCOM | ALL; n = 1..30; minVal = giá trị khớp tối thiểu (đồng), mặc định 500 triệu
 *   /heatmap?ex=HOSE&n=150        bản đồ nhiệt cả sàn                   -> {ex,at,basis,capSrc,rows:[[mã,giá,%,giá trị khớp,vốn hóa tỷ]]}
 *   /profile?symbols=FPT,HPG      ngành và số cổ phiếu lưu hành từ Vietstock A-Z (tối đa 30 mã), dự phòng TCBS/VNDirect -> {rows:[[mã,ngành,số cổ phiếu]]}
 *   /news                          tiêu đề tin chứng khoán Vietstock RSS, chỉ gồm ngày và link bài gốc
 *   /vnindex                       giá gần nhất và 130 nến ngày VN-Index từ biểu đồ DNSE
 *   /caps?probe=1                 kiểm tra nguồn vốn hóa (để chẩn đoán)
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
const SITE = "https://haot725-ops.github.io/Sang-_-dau_-tu/"; // nơi có universe.json (danh sách mã theo sàn)

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

function dnseIndexBars(js) {
  if (!js || !Array.isArray(js.t) || !Array.isArray(js.c)) return [];
  return js.t.map((t, i) => {
    const ts = num(t), c = num(js.c[i]);
    return ts && c > 0 ? { t: ts, d: ymd(ts), c } : null;
  }).filter(Boolean).sort((a, b) => a.t - b.t);
}

async function vnIndex() {
  const now = Math.floor(Date.now() / 1000), base = "https://services.entrade.com.vn/chart-api/v2/ohlcs/index?symbol=VNINDEX";
  const dailyUrl = `${base}&from=${now - 280 * 86400}&to=${now + 86400}&resolution=1D`;
  const minuteUrl = `${base}&from=${now - 7 * 86400}&to=${now + 86400}&resolution=1`;
  const [dailyRes, minuteRes] = await Promise.all([
    fetch(dailyUrl, { headers: UA }),
    fetch(minuteUrl, { headers: UA }).catch(() => null),
  ]);
  if (!dailyRes.ok) throw new Error("DNSE lịch sử chỉ số trả " + dailyRes.status);
  const daily = dnseIndexBars(await dailyRes.json());
  if (daily.length < 2) throw new Error("DNSE chưa trả đủ nến VN-Index");
  let minute = [];
  if (minuteRes && minuteRes.ok) {
    try { minute = dnseIndexBars(await minuteRes.json()); } catch (_) {}
  }
  const lastDaily = daily[daily.length - 1], priorDaily = daily[daily.length - 2];
  const lastMinute = minute[minute.length - 1];
  let price = lastDaily.c, prevClose = priorDaily.c, at = lastDaily.t, asof = lastDaily.d;
  if (lastMinute && lastMinute.d >= lastDaily.d) {
    price = lastMinute.c; at = lastMinute.t; asof = lastMinute.d;
    if (lastMinute.d > lastDaily.d) { prevClose = lastDaily.c; daily.push(lastMinute); }
    else daily[daily.length - 1].c = price;
  }
  const change = price - prevClose;
  return {
    symbol: "VNINDEX", source: "DNSE", price, prevClose, change,
    changePct: prevClose ? change / prevClose * 100 : null,
    asof, at: new Date(at * 1000).toISOString(),
    bars: daily.slice(-130).map(({ d, c }) => ({ d, c })),
  };
}

async function universe(ctx) {
  return JSON.parse(await cached(ctx, "u/all", 86400, async () => {
    const r = await fetch(SITE + "universe.json", { headers: UA });
    if (!r.ok) throw new Error("không đọc được universe.json (" + r.status + ")");
    const j = await r.json();
    return (j.stocks || []).map((s) => [s.t, s.x, s.n]);
  }));
}
async function movers(ctx, ex, n, minVal) {
  const all = await universe(ctx);
  const list = all.filter((r) => (ex === "ALL" || r[1] === ex) && /^[A-Z0-9]{2,10}$/.test(r[0]));
  if (!list.length) throw new Error("không có mã nào thuộc sàn " + ex);
  const name = new Map(list.map((r) => [r[0], r[2]]));
  const chunks = [];
  for (let i = 0; i < list.length; i += 80) chunks.push(list.slice(i, i + 80).map((r) => r[0]));
  const rows = [];
  let failed = 0;
  for (let i = 0; i < chunks.length; i += 6) {
    await Promise.all(chunks.slice(i, i + 6).map(async (c) => {
      try { rows.push(...(await quotes(c))); } catch (e) { failed++; }
    }));
  }
  if (!rows.length) throw new Error("nguồn giá không trả dữ liệu");
  const clean = rows.filter((q) => q.changePct != null && Number.isFinite(q.changePct)).map((q) => {
    const sh = (q.vol || 0) * 10; // VPS trả khối lượng theo lô 10 cổ phiếu
    return { t: q.t, n: name.get(q.t) || "", price: q.price, changePct: Math.round(q.changePct * 100) / 100, vol: sh, val: Math.round(q.price * sh) };
  });
  const liquid = clean.filter((q) => q.val >= minVal);
  const up = liquid.filter((q) => q.changePct > 0).sort((a, b) => b.changePct - a.changePct || b.val - a.val).slice(0, n);
  const down = liquid.filter((q) => q.changePct < 0).sort((a, b) => a.changePct - b.changePct || b.val - a.val).slice(0, n);
  const stats = { up: clean.filter((q) => q.changePct > 0).length, down: clean.filter((q) => q.changePct < 0).length, flat: clean.filter((q) => q.changePct === 0).length, total: clean.length, listed: list.length };
  return { ex, at: new Date().toISOString(), n, minVal, up, down, stats, failed };
}

/* ---- Vốn hóa: thử lần lượt các nguồn, nhớ kết quả 12 giờ ---- */
const CAP_TRIES = [
  ["vndirect-ratios-q", "https://finfo-api.vndirect.com.vn/v4/ratios/latest?order=reportDate&fields=code,ratioCode,value,reportDate&size=5000&q=" + encodeURIComponent("ratioCode:MARKETCAP")],
  ["vndirect-ratios-filter", "https://finfo-api.vndirect.com.vn/v4/ratios/latest?order=reportDate&fields=code,ratioCode,value,reportDate&size=5000&filter=" + encodeURIComponent("ratioCode:MARKETCAP")],
  ["vndirect-ratios-item", "https://finfo-api.vndirect.com.vn/v4/ratios/latest?order=reportDate&fields=code,itemCode,value,reportDate&size=5000&filter=" + encodeURIComponent("itemCode:51003")],
];
function parseCaps(js) {
  const rows = Array.isArray(js) ? js : js && Array.isArray(js.data) ? js.data : [];
  const m = {};
  for (const r of rows) {
    const t = String(r.code || r.ticker || r.symbol || "").toUpperCase(), v = num(r.value ?? r.marketCap ?? r.mc);
    if (/^[A-Z0-9]{2,10}$/.test(t) && v > 0) m[t] = v;
  }
  const vals = Object.values(m).sort((a, b) => a - b);
  if (vals.length < 100) return null;
  const k = vals[Math.floor(vals.length / 2)] > 1e8 ? 1e9 : 1; // VND -> tỷ; nếu đã là tỷ thì giữ
  const out = {};
  for (const t in m) out[t] = Math.round((m[t] / k) * 10) / 10;
  return out;
}
async function caps(ctx, probe) {
  const dbg = [];
  for (const [src, url] of CAP_TRIES) {
    try {
      const r = await fetch(url, { headers: UA });
      const text = await r.text();
      let m = null;
      try { m = parseCaps(JSON.parse(text)); } catch (e) { /* không phải JSON */ }
      dbg.push({ src, status: r.status, count: m ? Object.keys(m).length : 0, sample: probe ? text.slice(0, 300) : undefined });
      if (m && !probe) return { src, map: m };
      if (m && probe) return { src, map: m, dbg };
    } catch (e) { dbg.push({ src, error: String((e && e.message) || e) }); }
  }
  return { src: null, map: {}, dbg };
}
async function heatmap(ctx, ex, n) {
  const all = await universe(ctx);
  const list = all.filter((r) => (ex === "ALL" || r[1] === ex) && /^[A-Z0-9]{2,10}$/.test(r[0]));
  if (!list.length) throw new Error("không có mã nào thuộc sàn " + ex);
  const capRes = JSON.parse(await cached(ctx, "caps/v1", (v) => (v.src ? 43200 : 300), async () => { const c = await caps(ctx, false); return { src: c.src, map: c.map }; }));
  const chunks = [];
  for (let i = 0; i < list.length; i += 80) chunks.push(list.slice(i, i + 80).map((r) => r[0]));
  const rows = [];
  let failed = 0;
  for (let i = 0; i < chunks.length; i += 6) {
    await Promise.all(chunks.slice(i, i + 6).map(async (c) => {
      try { rows.push(...(await quotes(c))); } catch (e) { failed++; }
    }));
  }
  if (!rows.length) throw new Error("nguồn giá không trả dữ liệu");
  const clean = rows.filter((q) => q.changePct != null && Number.isFinite(q.changePct)).map((q) => {
    const val = Math.round(q.price * (q.vol || 0) * 10);
    return [q.t, q.price, Math.round(q.changePct * 100) / 100, val, capRes.map[q.t] || null];
  });
  const haveCap = clean.filter((r) => r[4] > 0).length >= Math.min(30, clean.length * 0.3);
  const basis = haveCap ? "cap" : "val";
  const top = clean.filter((r) => (haveCap ? r[4] > 0 : r[3] > 0)).sort((a, b) => (haveCap ? b[4] - a[4] : b[3] - a[3])).slice(0, n);
  return { ex, at: new Date().toISOString(), basis, capSrc: capRes.src, total: clean.length, listed: list.length, failed, rows: top };
}
const PROFILE_SOURCES = [
  ["tcbs-pub", (t) => "https://apipub.tcbs.com.vn/tcanalysis/v1/ticker/" + t + "/overview", { ...UA, Origin: "https://tcinvest.tcbs.com.vn", Referer: "https://tcinvest.tcbs.com.vn/", "Accept-Language": "vi-VN,vi;q=0.9" }, (j) => j],
  ["tcbs-aws", (t) => "https://apipubaws.tcbs.com.vn/tcanalysis/v1/ticker/" + t + "/overview", { ...UA, Origin: "https://tcinvest.tcbs.com.vn", Referer: "https://tcinvest.tcbs.com.vn/", "Accept-Language": "vi-VN,vi;q=0.9" }, (j) => j],
  ["vnd-profile", (t) => "https://finfo-api.vndirect.com.vn/v4/company_profiles?q=code:" + t, { ...UA, Origin: "https://dstock.vndirect.com.vn", Referer: "https://dstock.vndirect.com.vn/" }, (j) => j.data?.[0] || null],
  ["vnd-stock", (t) => "https://finfo-api.vndirect.com.vn/v4/stocks?q=code:" + t, { ...UA, Origin: "https://dstock.vndirect.com.vn", Referer: "https://dstock.vndirect.com.vn/" }, (j) => j.data?.[0] || null],
];
function profileRow(sym, data) {
  if (!data) return null;
  const pick = (keys) => { for (const key of keys) if (data[key] != null && data[key] !== "") return data[key]; return null; };
  const ind = String(pick(["IndustryName", "industry", "industryName", "icbName", "icbIndustry", "industryEn", "industryNameVi", "sectorName"]) || "").trim();
  let shares = num(pick(["TotalShares", "outstandingShare", "outstandingShares", "issueShare", "listedShare", "listedShares", "shareOutstanding"]));
  if (shares != null && shares > 0 && shares < 1e5) shares = Math.round(shares * 1e6); // nguồn TCBS có thể trả theo triệu cổ phiếu
  return ind || shares > 0 ? [sym, ind, shares > 0 ? shares : null] : null;
}
function vietstockCookieHeader(headers) {
  const lines = typeof headers.getSetCookie === "function" ? headers.getSetCookie() : [headers.get("set-cookie") || ""];
  const found = new Map();
  for (const line of lines) {
    for (const match of line.matchAll(/(?:^|[,\s])(ASP\.NET_SessionId|__RequestVerificationToken|language)=([^;,\s]+)/g)) found.set(match[1], match[2]);
  }
  return [...found.entries()].map(([name, value]) => name + "=" + value).join("; ");
}
async function vietstockSession() {
  const pageUrl = "https://finance.vietstock.vn/doanh-nghiep-a-z?page=1";
  const r = await fetch(pageUrl, { headers: { ...UA, Accept: "text/html,application/xhtml+xml", Referer: "https://finance.vietstock.vn/" } });
  if (!r.ok) throw new Error("Vietstock A-Z HTTP " + r.status);
  const html = await r.text();
  const tokenInput = html.match(/<input\b[^>]*\bname=["']?__RequestVerificationToken["']?[^>]*>/i)?.[0] || "";
  const token = tokenInput.match(/\bvalue=["']?([^"'\s>]+)/i)?.[1] || "";
  const cookie = vietstockCookieHeader(r.headers);
  if (!token || !cookie.includes("ASP.NET_SessionId=")) throw new Error("Vietstock A-Z thiếu token phiên hoặc cookie");
  return { token, cookie, pageUrl };
}
async function vietstockProfile(sym, session) {
  const body = new URLSearchParams({
    catID: "0", industryID: "0", page: "1", pageSize: "20", type: "0", code: sym,
    businessTypeID: "0", orderBy: "Code", orderDir: "ASC", __RequestVerificationToken: session.token,
  });
  const r = await fetch("https://finance.vietstock.vn/data/corporateaz", {
    method: "POST",
    headers: {
      ...UA, Accept: "application/json, text/javascript, */*; q=0.01",
      "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
      "X-Requested-With": "XMLHttpRequest", Origin: "https://finance.vietstock.vn",
      Referer: session.pageUrl, Cookie: session.cookie,
    },
    body: body.toString(),
  });
  const text = await r.text();
  if (!r.ok) throw new Error("Vietstock A-Z HTTP " + r.status);
  let records;
  try { records = JSON.parse(text); } catch (_) { throw new Error("Vietstock A-Z không trả JSON"); }
  if (!Array.isArray(records)) throw new Error("Vietstock A-Z trả dữ liệu lạ");
  const data = records.find((item) => String(item.Code || "").trim().toUpperCase() === sym) || null;
  return { row: profileRow(sym, data), src: "vietstock-az", name: "Vietstock A-Z", status: r.status, sample: text.slice(0, 250) };
}
async function vietstockProfiles(list) {
  const session = await vietstockSession();
  const rows = [];
  for (let i = 0; i < list.length; i += 6) {
    const batch = await Promise.all(list.slice(i, i + 6).map(async (sym) => {
      try { return (await vietstockProfile(sym, session)).row; } catch (_) { return null; }
    }));
    rows.push(...batch.filter(Boolean));
  }
  return { source: "Vietstock A-Z", at: new Date().toISOString(), rows, failed: list.length - rows.length };
}
async function profileFrom(sym, index) {
  const [name, url, headers, unwrap] = PROFILE_SOURCES[index];
  const r = await fetch(url(sym), { headers });
  const text = await r.text();
  if (!r.ok) throw new Error(name + " HTTP " + r.status);
  let data;
  try { data = unwrap(JSON.parse(text)); } catch (e) { throw new Error(name + " JSON không hợp lệ"); }
  return { row: profileRow(sym, data), src: index, name, status: r.status, sample: text.slice(0, 250) };
}
async function profile(sym, probe = false, sourceIndex = null) {
  if (probe) {
    const diagnostics = [];
    try {
      const result = await vietstockProfile(sym, await vietstockSession());
      diagnostics.push({ src: result.name, status: result.status, ok: !!result.row, row: result.row, sample: result.sample });
    } catch (e) { diagnostics.push({ src: "Vietstock A-Z", error: String(e?.message || e) }); }
    return diagnostics.concat(await legacyProfile(sym, true, sourceIndex));
  }
  return legacyProfile(sym, false, sourceIndex);
}
async function legacyProfile(sym, probe = false, sourceIndex = null) {
  const indexes = sourceIndex == null ? PROFILE_SOURCES.map((_, i) => i) : [sourceIndex];
  const diagnostics = [];
  for (const i of indexes) {
    try {
      const result = await profileFrom(sym, i);
      if (probe) diagnostics.push({ src: result.name, status: result.status, ok: !!result.row, row: result.row, sample: result.sample });
      else if (result.row) return result;
    } catch (e) {
      if (probe) diagnostics.push({ src: PROFILE_SOURCES[i][0], error: String(e?.message || e) });
    }
  }
  if (probe) return diagnostics;
  throw new Error("không nguồn nào trả hồ sơ " + sym);
}
async function legacyProfiles(list) {
  let first = null;
  for (const t of list) {
    try { first = await profile(t); break; } catch (_) {}
  }
  if (!first) return { at: new Date().toISOString(), rows: [], failed: list.length };
  const rest = await Promise.all(list.filter((t) => t !== first.row[0]).map((t) => profile(t, false, first.src).catch(() => null)));
  const rows = [first, ...rest].filter(Boolean).map((x) => x.row);
  return { at: new Date().toISOString(), rows, failed: list.length - rows.length };
}
async function profiles(list) {
  try {
    const result = await vietstockProfiles(list);
    if (result.rows.length) return result;
  } catch (_) { /* Vietstock có thể đổi/chặn API; giữ nguồn dự phòng cũ. */ }
  return legacyProfiles(list);
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
  const val = await make(), body = JSON.stringify(val);
  ctx.waitUntil(cache.put(req, new Response(body, { headers: { "Cache-Control": "max-age=" + (typeof ttl === "function" ? ttl(val) : ttl) } })));
  return body;
}

function xmlText(value) {
  return String(value || "").replace(/<!\[CDATA\[([\s\S]*?)\]\]>/g, "$1").replace(/<[^>]*>/g, " ")
    .replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos);/gi, (_, code) => {
      if (code[0] === "#") { const n = code[1].toLowerCase() === "x" ? parseInt(code.slice(2), 16) : parseInt(code.slice(1), 10); return Number.isFinite(n) && n > 0 && n <= 0x10ffff ? String.fromCodePoint(n) : ""; }
      return ({ amp: "&", lt: "<", gt: ">", quot: '"', apos: "'" })[code.toLowerCase()] || "";
    }).replace(/\s+/g, " ").trim();
}

function rssField(item, name) {
  const m = item.match(new RegExp("<" + name + "(?:\\s[^>]*)?>([\\s\\S]*?)<\\/" + name + "\\s*>", "i"));
  return m ? xmlText(m[1]) : "";
}

async function vietstockNews() {
  const r = await fetch("https://vietstock.vn/144/chung-khoan.rss", {
    headers: { "User-Agent": "Mozilla/5.0 (compatible; SangNewsReader/1.0)", Accept: "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8" },
  });
  if (!r.ok) throw new Error("Vietstock RSS trả " + r.status);
  const xml = await r.text();
  const items = [...xml.matchAll(/<item\b[^>]*>([\s\S]*?)<\/item>/gi)].slice(0, 30).map((m) => {
    const title = rssField(m[1], "title"), rawUrl = rssField(m[1], "link"), date = rssField(m[1], "pubDate");
    let url = "";
    try {
      const u = new URL(rawUrl);
      if ((u.protocol === "http:" || u.protocol === "https:") && (u.hostname === "vietstock.vn" || u.hostname.endsWith(".vietstock.vn"))) {
        u.protocol = "https:";
        url = u.href;
      }
    } catch (_) {}
    const publishedAt = new Date(date);
    return title && url && Number.isFinite(publishedAt.getTime()) ? { title, url, publishedAt: publishedAt.toISOString() } : null;
  }).filter(Boolean);
  if (!items.length) throw new Error("Vietstock RSS không có tin hợp lệ");
  return { source: "Vietstock", at: new Date().toISOString(), items };
}

export default {
  async fetch(req, env, ctx) {
    const origin = req.headers.get("Origin") || "";
    const cors = { "Access-Control-Allow-Origin": ALLOW.includes(origin) ? origin : ALLOW[0], "Access-Control-Allow-Methods": "GET, OPTIONS", Vary: "Origin" };
    const send = (body, status = 200, extraHeaders = {}) => new Response(typeof body === "string" ? body : JSON.stringify(body), { status, headers: { ...cors, "Content-Type": "application/json; charset=utf-8", ...extraHeaders } });
    if (req.method === "OPTIONS") return new Response(null, { status: 204, headers: cors });
    const u = new URL(req.url), path = u.pathname.replace(/\/+$/, "") || "/";
    const ip = req.headers.get("CF-Connecting-IP") || "unknown";
    const weight = path === "/movers" ? (u.searchParams.get("ex") || "HOSE").toUpperCase() === "ALL" ? 30 : 10 : path === "/heatmap" ? 10 : path === "/profile" ? 5 : path === "/news" ? 2 : 1;
    try {
      const { success } = await env.SANG_API_LIMITER.limit({ key: ip, delta: weight });
      if (!success) return send({ error: "Đã vượt giới hạn truy vấn. Vui lòng thử lại sau.", retryAfter: 60 }, 429, { "Retry-After": "60" });
    } catch (e) {
      return send({ error: "Bộ giới hạn truy vấn chưa sẵn sàng." }, 503);
    }
    try {
      if (path === "/quote") {
        const list = [...new Set((u.searchParams.get("symbols") || "").toUpperCase().split(/[\s,]+/))].filter((s) => /^[A-Z0-9]{2,10}$/.test(s)).slice(0, 80);
        if (!list.length) return send({ error: "symbols không hợp lệ" }, 400);
        return send(await cached(ctx, "q/" + list.join(","), 15, async () => ({ quotes: await quotes(list), at: new Date().toISOString() })));
      }
      if (path === "/movers") {
        const ex = (u.searchParams.get("ex") || "HOSE").toUpperCase();
        if (!["HOSE", "HNX", "UPCOM", "ALL"].includes(ex)) return send({ error: "ex phải là HOSE, HNX, UPCOM hoặc ALL" }, 400);
        const n = Math.min(30, Math.max(1, parseInt(u.searchParams.get("n") || "10", 10) || 10));
        const mv = Number(u.searchParams.get("minVal"));
        const minVal = Number.isFinite(mv) && u.searchParams.get("minVal") !== null ? Math.min(1e11, Math.max(0, mv)) : 5e8;
        return send(await cached(ctx, "m/" + ex + "/" + n + "/" + minVal, 30, () => movers(ctx, ex, n, minVal)));
      }
      if (path === "/heatmap") {
        const ex = (u.searchParams.get("ex") || "HOSE").toUpperCase();
        if (!["HOSE", "HNX", "UPCOM", "ALL"].includes(ex)) return send({ error: "ex phải là HOSE, HNX, UPCOM hoặc ALL" }, 400);
        const n = Math.min(300, Math.max(20, parseInt(u.searchParams.get("n") || "150", 10) || 150));
        return send(await cached(ctx, "hm/" + ex + "/" + n, 15, () => heatmap(ctx, ex, n)));
      }
      if (path === "/profile") {
        const list = [...new Set((u.searchParams.get("symbols") || "").toUpperCase().split(/[\s,]+/))].filter((x) => /^[A-Z0-9]{2,10}$/.test(x)).slice(0, 30);
        if (!list.length) return send({ error: "symbols không hợp lệ" }, 400);
        if (u.searchParams.get("probe")) return send(await profile(list[0], true));
        return send(await cached(ctx, "p/" + list.join(","), (v) => (v.rows.length === list.length ? 604800 : v.rows.length ? 15 : 60), () => profiles(list)));
      }
      if (path === "/caps") {
        const c = await caps(ctx, true);
        return send({ src: c.src, count: Object.keys(c.map).length, tries: c.dbg });
      }
      if (path === "/news") return send(await cached(ctx, "news/vietstock-securities", 300, vietstockNews));
      if (path === "/vnindex") return send(await cached(ctx, "vni/current", 30, vnIndex));
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
