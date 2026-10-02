"use strict";
/* Banff snowpack site tool (static). Reads data/sites.json and the per-season files written by
   snowagent.web.build. All text from data is inserted with textContent. Times: data in UTC; shown in MST
   (UTC-7, the station-log convention, no daylight saving). */

const MST_H = 7;
const NS = "http://www.w3.org/2000/svg";
const $ = (id) => document.getElementById(id);

const GRAIN = {
  PP: ["#00ff00", "Precipitation particles"], PPgp: ["#808080", "Graupel"], MM: ["#ffd700", "Machine-made"],
  DF: ["#228b22", "Decomposing and fragmented"], RG: ["#ffb6c1", "Rounded grains"],
  FC: ["#add8e6", "Faceted crystals"], FCxr: ["#6495ed", "Rounding faceted"], DH: ["#0000ff", "Depth hoar"],
  SH: ["#ff00ff", "Surface hoar"], MF: ["#ff0000", "Melt forms"], MFcr: ["#8b0000", "Melt-freeze crust"],
  IF: ["#00ffff", "Ice formation"],
};
const HARD = ["", "F", "4F", "1F", "P", "K", "I"];
const STATIONS = { sunshine_village_ab_env: "Sunshine Village AB station sensor (2 km W of the plot)",
  bow_summit: "Bow Summit station sensor (at the plot)", simpson_lower: "Simpson Lower station sensor (at the plot)" };
function median(a) { const w = a.filter((x) => x != null).sort((x, y) => x - y); return w.length ? w[Math.floor(w.length / 2)] : null; }
function maskSpikes(vals, half = 3, tol = 50, jump = 100) {
  // display only: drop daily values > tol cm from the centred 7-day median, or > jump cm above the previous
  // week's median (stuck/echo readings; no snowfall adds a metre in a day)
  return vals.map((v, i) => {
    if (v == null) return null;
    const c = median(vals.slice(Math.max(0, i - half), i + half + 1));
    const prev = median(vals.slice(Math.max(0, i - 7), i));
    if (c != null && Math.abs(v - c) > tol) return null;
    if (prev != null && v - prev > jump) return null;
    return v;
  });
}
const measured = () => !!S.data && S.data.mode !== "era5";  // station or live season
const MODES = [["nowcast", "Measured weather (nowcast)"], ["free", "Measured weather, without pit updates"], ["fc1", "GFS forecast, lead up to 24 h"],
  ["fc2", "GFS forecast, lead 24–48 h"], ["fc3", "GFS forecast, lead 48–72 h"]];

const S = { sites: null, site: null, seasonMeta: null, data: null, fc: null, t: null, mode: "nowcast",
  cache: new Map(), nowIndex: null, fcIndex: null };

// ------------------------------------------------------------------ helpers
function grainKey(code) {
  if (!code) return null;
  for (const k of ["MFcr", "FCxr", "PPgp"]) if (code.startsWith(k)) return k;
  if (GRAIN[code]) return code;
  return GRAIN[code.slice(0, 2)] ? code.slice(0, 2) : null;
}
const grainColor = (c) => { const k = grainKey(c); return k ? GRAIN[k][0] : "#c3c2b7"; };
const grainName = (c) => { const k = grainKey(c); return k ? `${c} · ${GRAIN[k][1]}` : (c || "not recorded"); };
function hardLabel(h) {
  if (h == null || Number.isNaN(h)) return "–";
  const b = Math.min(6, Math.max(1, Math.round(h))); const d = h - b;
  return HARD[b] + (d > 0.15 ? "+" : d < -0.15 ? "−" : "");
}
const pad = (n) => String(n).padStart(2, "0");
const keyOf = (d) => `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T${pad(d.getUTCHours())}`;
const fromKey = (k) => new Date(`${k}:00:00Z`);
const toMST = (d) => new Date(d.getTime() - MST_H * 3600e3);
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
function fmtMST(d, withYear = true) {
  const m = toMST(d);
  return `${m.getUTCDate()} ${MONTHS[m.getUTCMonth()]}${withYear ? " " + m.getUTCFullYear() : ""}, ${pad(m.getUTCHours())}:${pad(m.getUTCMinutes())} MST`;
}
const localDateStr = (d) => { const m = toMST(d); return `${m.getUTCFullYear()}-${pad(m.getUTCMonth() + 1)}-${pad(m.getUTCDate())}`; };
function el(tag, attrs = {}, text) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) { if (k === "class") e.className = v; else e.setAttribute(k, v); }
  if (text != null) e.textContent = text;
  return e;
}
function sv(tag, attrs = {}, text) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (text != null) e.textContent = text;
  return e;
}
async function getJSON(url) {
  if (S.cache.has(url)) return S.cache.get(url);
  const r = await fetch(url);
  if (!r.ok) throw new Error(`${url}: HTTP ${r.status}`);
  const j = await r.json();
  S.cache.set(url, j);
  return j;
}
function status(msg) { $("status").textContent = msg; }

// tooltip -----------------------------------------------------------
const tip = $("tooltip");
function showTip(evt, rows) {
  tip.replaceChildren();
  for (const r of rows) {
    const row = el("div", { class: "row" });
    if (r.color) { const s = el("span", { class: "line" }); s.style.background = r.color; row.append(s); }
    if (r.value != null) row.append(el("span", { class: "tv" }, r.value));
    if (r.label != null) row.append(el("span", { class: "tk" }, r.label));
    tip.append(row);
  }
  tip.hidden = false;
  const x = Math.min(window.innerWidth - tip.offsetWidth - 8, evt.clientX + 14);
  const y = Math.min(window.innerHeight - tip.offsetHeight - 8, evt.clientY + 14);
  tip.style.left = `${Math.max(8, x)}px`; tip.style.top = `${Math.max(8, y)}px`;
}
const hideTip = () => { tip.hidden = true; };

// ------------------------------------------------------------------ data selection
function seasonFiles(site) { return S.sites.sites.find((s) => s.id === site).seasons; }
function buildIndexes() {
  S.nowIndex = new Map(S.data.nowcast.map((p) => [p.t, p]));
  S.freeIndex = S.data.nowcast_free ? new Map(S.data.nowcast_free.map((p) => [p.t, p])) : null;
  S.fcIndex = S.fc ? new Map(S.fc.issues.filter((i) => i.P).map((i) => [i.issue, i])) : null;
}
function leadFor(t, mode) {
  const n = { fc1: 1, fc2: 2, fc3: 3 }[mode];
  const h = t.getUTCHours();
  return (h === 0 ? 24 : h) + 24 * (n - 1);
}
function simulated() {
  const k = keyOf(S.t);
  if (S.mode === "free" && S.freeIndex) {
    const p = S.freeIndex.get(k);
    return p ? { p, kind: "free" } : { err: "No simulated profile at this time." };
  }
  if (S.mode === "nowcast") {
    const p = S.nowIndex.get(k);
    if (p) return { p, kind: "nowcast" };
    const sk = (S.data.skipped_profiles || []).find((x) => x.t === k);
    const last = S.data.nowcast.length ? fromKey(S.data.nowcast[S.data.nowcast.length - 1].t) : null;
    if (S.data.live && last && S.t > last) {
      return { err: `Measured weather reaches ${fmtMST(last)} so far. For later times choose a GFS forecast weather input.` };
    }
    return { err: sk ? `The simulated profile at this time failed a physical check and is not shown (${sk.reason}).` :
      "No simulated profile at this time (outside the season run)." };
  }
  if (!S.fcIndex) return { err: "GFS forecasts exist only for Nov–Apr of seasons 2021-22 onward." };
  const lead = leadFor(S.t, S.mode);
  const issue = new Date(S.t.getTime() - lead * 3600e3);
  const rec = S.fcIndex.get(keyOf(issue));
  if (!rec) return { err: `No archived GFS run for ${fmtMST(issue)} (forecasts cover Nov–Apr).` };
  const p = rec.P.find((x) => x.t === k);
  if (!p) return { err: "This forecast has no profile at the selected time." };
  return { p, kind: "forecast", issue, lead, rec };
}
function nearestPit(t, maxH) {
  let best = null;
  for (const pit of S.data.pits) {
    const dt = Math.abs(new Date(pit.t + ":00Z").getTime() - t.getTime()) / 3600e3;
    if (dt <= maxH && (!best || dt < best.dt)) best = { pit, dt };
  }
  return best;
}

// ------------------------------------------------------------------ profile chart
function drawProfile(box, layers, opt) {
  box.replaceChildren();
  const W = Math.max(320, box.clientWidth || 560), H = 430;
  const m = { l: 50, r: 10, t: 14, b: 36 };
  const tempW = Math.max(84, Math.round(W * 0.24)), gap = 18;
  const hw = W - m.l - m.r - tempW - gap;
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opt.aria });
  const yMax = opt.hsMax > 0 ? opt.hsMax : 100;
  const y = (cm) => m.t + (H - m.t - m.b) * (1 - cm / yMax);
  const x0 = m.l + hw; // hardness baseline (fist side) on the right, harder to the left
  const xh = (h) => x0 - hw * Math.max(0, Math.min(6, h)) / 6;
  // grid + axes
  const step = yMax > 250 ? 50 : yMax > 120 ? 25 : 20;
  for (let v = 0; v <= yMax; v += step) {
    svg.append(sv("line", { class: "gridline", x1: m.l, x2: m.l + hw, y1: y(v), y2: y(v) }));
    svg.append(sv("text", { class: "tick", x: m.l - 6, y: y(v) + 4, "text-anchor": "end" }, String(v)));
  }
  for (let i = 1; i <= 6; i++) {
    svg.append(sv("line", { class: "gridline", x1: xh(i), x2: xh(i), y1: m.t, y2: H - m.b }));
    svg.append(sv("text", { class: "tick", x: xh(i), y: H - m.b + 14, "text-anchor": "middle" }, HARD[i]));
  }
  svg.append(sv("line", { class: "baseline", x1: m.l, x2: x0, y1: y(0), y2: y(0) }));
  svg.append(sv("text", { class: "axis-title", x: m.l + hw / 2, y: H - 4, "text-anchor": "middle" }, "hand hardness"));
  svg.append(sv("text", { class: "axis-title", x: 11, y: m.t, transform: `rotate(-90 11 ${m.t})`, "text-anchor": "end" }, "height above ground (cm)"));
  // bars
  for (const L of layers) {
    const [top, bot, g, h] = L;
    if (top == null || bot == null) continue;
    const yt = y(top), yb = y(bot);
    const hh = Math.max(0.6, yb - yt - (yb - yt > 3 ? 1 : 0));
    const xl = xh(h == null ? 1 : h), w = Math.max(1, x0 - xl), r = Math.min(4, hh / 2, w / 2);
    const d = `M${x0},${yt} H${xl + r} Q${xl},${yt} ${xl},${yt + r} V${yt + hh - r} Q${xl},${yt + hh} ${xl + r},${yt + hh} H${x0} Z`;
    const path = sv("path", { d, fill: grainColor(g), "fill-opacity": h == null ? 0.45 : 1 });
    path.addEventListener("pointermove", (e) => showTip(e, layerTip(L, opt.kind)));
    path.addEventListener("pointerleave", hideTip);
    svg.append(path);
  }
  // tests (observed)
  for (const t of opt.tests || []) {
    if (t.height_cm == null) continue;
    const ty = y(t.height_cm);
    const g = sv("g", {});
    g.append(sv("line", { x1: x0 - 2, x2: x0 + 8, y1: ty, y2: ty, stroke: "var(--text-primary)", "stroke-width": 2 }));
    g.append(sv("rect", { x: x0 - 6, y: ty - 8, width: 18, height: 16, fill: "transparent" }));
    const lab = [t.result || t.type, t.fracture_character].filter(Boolean).join(" ");
    g.addEventListener("pointermove", (e) => showTip(e, [{ value: lab || "test" }, { label: `failure at ${t.height_cm} cm` }]));
    g.addEventListener("pointerleave", hideTip);
    svg.append(g);
  }
  // temperature panel (own x scale, separate panel: no dual axis)
  const tx0 = m.l + hw + gap, tw = tempW;
  const temps = (opt.temps || []).filter((p) => p[0] != null && p[1] != null);
  const tmin = Math.min(-10, Math.floor(Math.min(0, ...temps.map((p) => p[1])) / 5) * 5);
  const xt = (T) => tx0 + tw * (1 - Math.min(0, Math.max(tmin, T)) / tmin);
  for (let v = 0; v >= tmin; v -= tmin <= -20 ? 10 : 5) {
    svg.append(sv("line", { class: "gridline", x1: xt(v), x2: xt(v), y1: m.t, y2: H - m.b }));
    svg.append(sv("text", { class: "tick", x: xt(v), y: H - m.b + 14, "text-anchor": "middle" }, String(v)));
  }
  svg.append(sv("line", { class: "baseline", x1: tx0, x2: tx0 + tw, y1: y(0), y2: y(0) }));
  svg.append(sv("text", { class: "axis-title", x: tx0 + tw / 2, y: H - 4, "text-anchor": "middle" }, "temperature (°C)"));
  if (temps.length > 1) {
    const pts = temps.slice().sort((a, b) => a[0] - b[0]).map((p) => `${xt(p[1]).toFixed(1)},${y(p[0]).toFixed(1)}`);
    svg.append(sv("polyline", { points: pts.join(" "), fill: "none", stroke: "var(--series-1)", "stroke-width": 2,
      "stroke-linejoin": "round", "stroke-linecap": "round" }));
  }
  if (opt.kind === "observed") {
    for (const p of temps) svg.append(sv("circle", { cx: xt(p[1]), cy: y(p[0]), r: 4, fill: "var(--series-1)", stroke: "var(--surface-1)", "stroke-width": 2 }));
  }
  if (!temps.length) svg.append(sv("text", { class: "mark-label", x: tx0 + tw / 2, y: m.t + 14, "text-anchor": "middle" }, "no temperatures"));
  box.append(svg);
}
function layerTip(L, kind) {
  const [top, bot, g, h, rho, T, w, f] = L;
  const rows = [{ value: grainName(g) }, { value: `${top}–${bot} cm`, label: `(${(top - bot).toFixed(1)} cm)` },
    { value: hardLabel(h), label: h == null ? "hardness not recorded" : `hand hardness (${h.toFixed(1)})` }];
  if (rho != null) rows.push({ value: `${rho} kg/m³`, label: "density" });
  if (kind !== "observed") {
    if (T != null) rows.push({ value: `${T} °C`, label: "temperature" });
    if (w) rows.push({ value: `${w} %`, label: "liquid water" });
    if (f & 1) rows.push({ label: "melt-freeze crust" });
    if (f & 2) rows.push({ label: "candidate weak layer (grain-form flag, not stability)" });
  }
  return rows;
}
function layerTable(box, layers, kind, tests) {
  box.replaceChildren();
  const t = el("table");
  const head = kind === "observed" ? ["Top (cm)", "Bottom", "Grain form", "Hardness", "Density"] :
    ["Top (cm)", "Bottom", "Grain form", "Hardness", "Density", "Temp (°C)", "Water (%)", "Flags"];
  const tr = el("tr"); head.forEach((h, i) => tr.append(el("th", i === 2 ? { class: "l" } : {}, h))); t.append(tr);
  for (const L of layers) {
    const r = el("tr");
    const g = el("td", { class: "l" }); const sw = el("span", { class: "swatch" }); sw.style.background = grainColor(L[2]);
    g.append(sw, document.createTextNode(L[2] || "–"));
    r.append(el("td", {}, String(L[0])), el("td", {}, String(L[1])), g, el("td", {}, hardLabel(L[3])),
      el("td", {}, L[4] == null ? "–" : String(L[4])));
    if (kind !== "observed") {
      r.append(el("td", {}, String(L[5])), el("td", {}, String(L[6])),
        el("td", {}, [(L[7] & 1) ? "crust" : "", (L[7] & 2) ? "weak-layer flag" : ""].filter(Boolean).join(", ") || "–"));
    }
    t.append(r);
  }
  box.append(t);
  if (tests && tests.length) {
    const tt = el("table"); tt.style.marginTop = "8px";
    const h = el("tr"); ["Test", "Height (cm)", "Fracture"].forEach((x, i) => h.append(el("th", i === 0 ? { class: "l" } : {}, x))); tt.append(h);
    for (const x of tests) {
      const r = el("tr");
      r.append(el("td", { class: "l" }, x.result || x.type || "–"), el("td", {}, x.height_cm == null ? "–" : String(x.height_cm)),
        el("td", {}, x.fracture_character || "–"));
      tt.append(r);
    }
    box.append(tt);
  }
}

// ------------------------------------------------------------------ line charts
function drawSeries(box, cfg) {
  // cfg: {x0, x1 (Date), panels:[{title, unit, series:[{color, label, pts:[[Date, v]], bars}]}], zone:[Date,Date], sel: Date, onClick}
  box.replaceChildren();
  const W = Math.max(320, box.clientWidth || 900), ph = cfg.panelH || 92, gapP = 30;
  const m = { l: 46, r: 12, t: 20, b: 26 };
  const H = m.t + cfg.panels.length * ph + (cfg.panels.length - 1) * gapP + m.b;
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": cfg.aria });
  const t0 = cfg.x0.getTime(), t1 = cfg.x1.getTime();
  const x = (d) => m.l + (W - m.l - m.r) * (d.getTime() - t0) / (t1 - t0);
  if (cfg.zone) svg.append(sv("rect", { class: "forecast-zone", x: x(cfg.zone[0]), y: m.t, width: Math.max(0, x(cfg.zone[1]) - x(cfg.zone[0])), height: H - m.t - m.b }));
  const scales = [];
  cfg.panels.forEach((p, i) => {
    const top = m.t + i * (ph + gapP), bot = top + ph;
    const vals = p.series.flatMap((s) => s.pts.map((q) => q[1])).filter((v) => v != null && !Number.isNaN(v));
    let lo = p.min != null ? p.min : Math.min(...vals), hi = p.max != null ? p.max : Math.max(...vals);
    if (!vals.length) { lo = 0; hi = 1; }
    if (p.zero) lo = Math.min(0, lo);
    if (hi - lo < (p.minSpan || 1)) hi = lo + (p.minSpan || 1);
    const yv = (v) => bot - (bot - top) * (v - lo) / (hi - lo);
    scales.push({ yv, p });
    for (const v of niceTicks(lo, hi, 3)) {
      svg.append(sv("line", { class: "gridline", x1: m.l, x2: W - m.r, y1: yv(v), y2: yv(v) }));
      svg.append(sv("text", { class: "tick", x: m.l - 6, y: yv(v) + 4, "text-anchor": "end" }, fmtNum(v)));
    }
    svg.append(sv("text", { class: "axis-title", x: m.l, y: top - 8 }, `${p.title} (${p.unit})`));
    for (const s of p.series) {
      if (s.bars) {
        const bw = Math.max(1, (W - m.l - m.r) / ((t1 - t0) / 3600e3) - 1);
        for (const [d, v] of s.pts) if (v > 0) svg.append(sv("rect", { x: x(d) - bw, y: yv(v), width: bw, height: Math.max(0.5, yv(lo) - yv(v)), fill: s.color }));
      } else {
        let seg = [];
        const flush = () => { if (seg.length > 1) svg.append(sv("polyline", { points: seg.join(" "), fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" })); seg = []; };
        for (const [d, v] of s.pts) { if (v == null || Number.isNaN(v)) { flush(); continue; } seg.push(`${x(d).toFixed(1)},${yv(v).toFixed(1)}`); }
        flush();
      }
      for (const mk of s.markers || []) {
        const c = sv("circle", { cx: x(mk.d), cy: yv(mk.v), r: 4, fill: s.color, stroke: "var(--surface-1)", "stroke-width": 2 });
        svg.append(c);
      }
    }
    svg.append(sv("line", { class: "baseline", x1: m.l, x2: W - m.r, y1: bot, y2: bot }));
  });
  // x ticks (days)
  const days = (t1 - t0) / 86400e3;
  const every = days > 120 ? 30 : days > 40 ? 7 : 1;
  let lastLabelX = -1e9;
  for (let d = new Date(Math.ceil(t0 / 86400e3) * 86400e3 + MST_H * 3600e3); d.getTime() <= t1; d = new Date(d.getTime() + every * 86400e3)) {
    if (every === 30 && toMST(d).getUTCDate() !== 1) { d = new Date(Date.UTC(toMST(d).getUTCFullYear(), toMST(d).getUTCMonth() + 1, 1, MST_H)); d = new Date(d.getTime() - every * 86400e3); continue; }
    const md = toMST(d);
    if (x(d) - lastLabelX < 46) continue;  // keep tick labels apart on narrow screens
    lastLabelX = x(d);
    svg.append(sv("text", { class: "tick", x: x(d), y: H - 8, "text-anchor": "middle" }, every === 30 ? MONTHS[md.getUTCMonth()] : `${md.getUTCDate()} ${MONTHS[md.getUTCMonth()]}`));
  }
  if (cfg.sel) svg.append(sv("line", { class: "sel", x1: x(cfg.sel), x2: x(cfg.sel), y1: m.t, y2: H - m.b }));
  // crosshair + tooltip + click
  const cross = sv("line", { class: "cross", y1: m.t, y2: H - m.b, visibility: "hidden" });
  svg.append(cross);
  const hit = sv("rect", { x: m.l, y: m.t, width: W - m.l - m.r, height: H - m.t - m.b, fill: "transparent", style: cfg.onClick ? "cursor:pointer" : "" });
  const toDate = (evt) => {
    const rect = svg.getBoundingClientRect();
    const px = (evt.clientX - rect.left) * W / rect.width;
    return new Date(t0 + (t1 - t0) * (px - m.l) / (W - m.l - m.r));
  };
  hit.addEventListener("pointermove", (evt) => {
    const d = toDate(evt);
    const snap = new Date(Math.round(d.getTime() / cfg.snapMs) * cfg.snapMs);
    cross.setAttribute("x1", x(snap)); cross.setAttribute("x2", x(snap)); cross.setAttribute("visibility", "visible");
    const rows = [{ value: cfg.snapMs >= 86400e3 ? fmtMST(snap).split(",")[0] : fmtMST(snap) }];
    for (const p of cfg.panels) for (const s of p.series) {
      const v = nearestVal(s.pts, snap, cfg.snapMs);
      if (v != null) rows.push({ color: s.color, value: `${fmtNum(v)} ${p.unit}`, label: s.label });
      for (const mk of s.markers || []) if (Math.abs(mk.d - snap) < cfg.snapMs / 2 + 1) rows.push({ color: s.color, value: `${fmtNum(mk.v)} ${p.unit}`, label: mk.label });
    }
    showTip(evt, rows);
  });
  hit.addEventListener("pointerleave", () => { cross.setAttribute("visibility", "hidden"); hideTip(); });
  if (cfg.onClick) hit.addEventListener("click", (evt) => cfg.onClick(toDate(evt)));
  svg.append(hit);
  box.append(svg);
}
function nearestVal(pts, d, tol) {
  let best = null, bd = Infinity;
  for (const [t, v] of pts) { const dd = Math.abs(t - d); if (dd < bd) { bd = dd; best = v; } }
  return bd <= tol ? best : null;
}
function niceTicks(lo, hi, n) {
  const span = hi - lo, raw = span / n, mag = 10 ** Math.floor(Math.log10(raw));
  const stepv = [1, 2, 2.5, 5, 10].map((k) => k * mag).find((s) => span / s <= n + 1) || mag * 10;
  const out = []; for (let v = Math.ceil(lo / stepv) * stepv; v <= hi + 1e-9; v += stepv) out.push(+v.toFixed(6));
  return out;
}
const fmtNum = (v) => (Math.abs(v) >= 100 ? Math.round(v).toString() : (Math.round(v * 10) / 10).toString());
function legend(box, items) {
  box.replaceChildren();
  for (const it of items) {
    const k = el("span", { class: "key" });
    const s = el("span", { class: it.dot ? "dot" : "line" }); s.style.background = it.color;
    k.append(s, document.createTextNode(it.label));
    box.append(k);
  }
}

// ------------------------------------------------------------------ render
function render() {
  if (!S.data) return;
  const site = S.sites.sites.find((s) => s.id === S.site);
  const sim = simulated();
  const near = nearestPit(S.t, 24);
  const hsMax = Math.ceil((Math.max(sim.p ? sim.p.hs : 0, near && near.pit.hs ? near.pit.hs : 0,
    ...(near ? near.pit.L.map((l) => l[0] || 0) : [0])) + 10) / 10) * 10;
  // simulated
  $("sim-when").textContent = fmtMST(S.t);
  if (sim.p) {
    const done = S.data.steer ? S.data.steer.updates.filter((u) => !u.note && new Date(u.time_utc) <= S.t) : [];
    const nUpd = done.length, lay = done.some((u) => u.method === "layers");
    const upd = nUpd ? `, ${lay ? "restarted from" : "depth updated from"} ${nUpd} earlier pit${nUpd === 1 ? "" : "s"}` : "";
    const what = sim.kind === "free" ? "measured weather, no pit updates" :
      sim.kind === "nowcast" ? (S.data.mode === "live" ? `measured weather (GFS fill until ERA5 is published)${upd}` :
        measured() ? `measured weather${upd}` : "ERA5 reanalysis weather") :
      `GFS run of ${fmtMST(sim.issue)} (lead ${sim.lead} h)`;
    const nl = sim.p.L.length, nwl = sim.p.L.filter((l) => l[7] & 2).length, ncr = sim.p.L.filter((l) => l[7] & 1).length;
    $("sim-meta").textContent = `${what} · HS ${sim.p.hs} cm · SWE ${sim.p.swe} mm · ${nl} layers · ${ncr} crust, ${nwl} weak-layer-flag layers`;
    drawProfile($("sim-profile"), sim.p.L, { hsMax, temps: sim.p.L.map((l) => [(l[0] + l[1]) / 2, l[5]]), kind: "model",
      aria: `Simulated profile, ${S.site}, ${fmtMST(S.t)}` });
    layerTable($("sim-table"), sim.p.L, "model");
    $("sim-trace").textContent = traceLine(sim);
  } else {
    $("sim-trace").textContent = "";
    $("sim-meta").textContent = "";
    $("sim-profile").replaceChildren(el("p", { class: "empty" }, sim.err));
    $("sim-table").replaceChildren();
  }
  // observed
  if (near) {
    const pit = near.pit, pt = new Date(pit.t + ":00Z");
    $("obs-when").textContent = fmtMST(pt);
    const nt = pit.tests.length;
    $("obs-meta").textContent = `${pit.source === "exact" ? "exact file" : "transcribed from the field record"} · HS ${pit.hs ?? "–"} cm · ${pit.L.length} layers · ${nt} test${nt === 1 ? "" : "s"}` +
      (near.dt > 3 ? ` · ${Math.round(near.dt)} h from the selected time` : "");
    drawProfile($("obs-profile"), pit.L, { hsMax, temps: pit.temps, tests: pit.tests, kind: "observed", aria: `Observed profile ${pit.id}` });
    layerTable($("obs-table"), pit.L, "observed", pit.tests);
  } else {
    $("obs-when").textContent = ""; $("obs-meta").textContent = "";
    const box = $("obs-profile"); box.replaceChildren();
    const p = el("p", { class: "empty" }, "No observed profile within 24 h of this time. ");
    const any = nearestPit(S.t, 1e9);
    if (any) {
      const b = el("button", { type: "button" }, `Go to the nearest observation (${fmtMST(new Date(any.pit.t + ":00Z"))})`);
      b.addEventListener("click", () => goToPit(any.pit));
      p.append(b);
    }
    box.append(p); $("obs-table").replaceChildren();
  }
  renderScores(sim, near);
  renderPublic();
  renderInputs(sim);
  renderSeason();
  $("pit").value = near && near.dt <= 3 ? near.pit.id : "";
  writeURL();
  const lv = S.data.live;
  status(lv ? `${site.name} · live season ${S.data.season} · weather through ${fmtMST(new Date(lv.weather_through))} · latest GFS run ${lv.latest_issue ? fmtMST(fromKey(lv.latest_issue)) : "none yet"} · updated ${fmtMST(new Date(lv.generated_utc))} · ${S.data.pits.length} observations` :
    `${site.name} · ${S.data.season} · ${measured() ? "measured station weather" : "ERA5 reanalysis (no station record)"} · ${S.data.nowcast.length} simulated profiles · ${S.data.pits.length} observations`);
}
function traceLine(sim) {
  // provenance of the profile shown: engine build, run, configuration and forcing hashes
  const eng = S.data.engine || {}, h = (x) => (x ? x.slice(0, 8) : "–");
  const ver = eng.version ? eng.version.split(" (")[0] : "SNOWPACK";
  if (sim.kind === "forecast") {
    const fe = (S.fc && S.fc.engine) || {};
    return `${ver} · forecast config ${h(fe.config_hash)} · forcing ${h(sim.rec.forcing_hash)} (GFS ${sim.rec.issue}Z)` +
      ` · initial state from run ${S.data.run_id || "–"}`;
  }
  return `${ver} · run ${S.data.run_id || "–"} · config ${h(eng.config_hash)} · forcing ${h(S.data.forcing_hash)}`;
}
function renderScores(sim, near) {
  const box = $("scores"); box.replaceChildren();
  const note = $("scores-note");
  if (!near || !sim.p) { note.textContent = "Scores appear when a simulated and an observed profile are both shown."; return; }
  const pit = near.pit;
  let sc = null;
  if (sim.kind === "nowcast" && pit.nowcast && pit.nowcast.t === sim.p.t) sc = pit.nowcast;
  if (sim.kind === "free" && pit.nowcast_free && pit.nowcast_free.t === sim.p.t) sc = pit.nowcast_free;
  if (sim.kind === "forecast" && pit.forecast) { const f = pit.forecast[keyOf(sim.issue)]; if (f && f.t === sim.p.t) sc = f; }
  const tile = (k, v, d) => { const t = el("div", { class: "tile" }); t.append(el("div", { class: "k" }, k), el("div", { class: "v" }, v)); if (d) t.append(el("div", { class: "d" }, d)); box.append(t); };
  const diff = pit.hs != null ? sim.p.hs - pit.hs : null;
  tile("Snow depth", `${sim.p.hs} vs ${pit.hs ?? "–"} cm`, diff == null ? "" : `simulated ${diff >= 0 ? "+" : ""}${diff.toFixed(0)} cm`);
  if (sc) {
    tile("Grain class agreement", sc.grain_class_agreement == null ? "–" : `${Math.round(sc.grain_class_agreement * 100)} %`,
      sc.grain_class_agreement == null ? "grain forms not recorded" : "share of the column");
    tile("Hand hardness difference", sc.hardness_mae_index == null ? "–" : `${sc.hardness_mae_index.toFixed(2)} steps`,
      sc.hardness_bias_index == null ? "" : `simulated ${sc.hardness_bias_index >= 0 ? "harder" : "softer"} by ${Math.abs(sc.hardness_bias_index).toFixed(2)} on average`);
    tile("Layer boundaries matched", sc.boundary_f1 == null ? "–" : sc.boundary_f1.toFixed(2), "F1, within 2 cm");
    note.textContent = "Scores compare the observation with the simulated profile shown (nearest 6-hourly output to the observation time).";
  } else {
    note.textContent = "Structure scores are computed at the observation time. Use “Go to observation” to align the simulated time with the pit.";
  }
}
function hourlySlice(h, from, to) {
  const t0 = fromKey(h.t0).getTime(), out = { ta: [], rh: [], vw: [], iswr: [], psum: [] };
  const i0 = Math.max(0, Math.floor((from - t0) / 3600e3)), i1 = Math.min(h.ta.length - 1, Math.floor((to - t0) / 3600e3));
  for (let i = i0; i <= i1; i++) { const d = new Date(t0 + i * 3600e3); for (const k of Object.keys(out)) out[k].push([d, h[k][i]]); }
  return out;
}
function renderInputs(sim) {
  const box = $("inputs");
  const to = S.t, from = new Date(S.t.getTime() - 7 * 86400e3);
  let meas, fc = null, zone = null;
  if (sim.kind === "forecast") {
    meas = hourlySlice(S.data.hourly, from, sim.issue);
    fc = { ta: [], rh: [], vw: [], iswr: [], psum: [] };
    sim.rec.wx.ta.forEach((_, i) => { const d = new Date(sim.issue.getTime() + (i + 1) * 3600e3); if (d <= to) for (const k of Object.keys(fc)) fc[k].push([d, sim.rec.wx[k][i]]); });
    zone = [sim.issue, to];
  } else {
    meas = hourlySlice(S.data.hourly, from, to);
  }
  const c1 = "var(--series-1)", c2 = "var(--series-2)";
  const lab1 = S.data.mode === "live" ? "measured (stations; GFS wind and radiation until ERA5 is published)" :
    measured() ? "measured (stations; ERA5 wind and radiation)" : "ERA5 reanalysis";
  legend($("inputs-legend"), fc ? [{ color: c1, label: lab1 }, { color: c2, label: "GFS forecast used for this simulation" }] : [{ color: c1, label: lab1 }]);
  const mk = (k) => [{ color: c1, label: "measured", pts: meas[k] }].concat(fc ? [{ color: c2, label: "GFS", pts: fc[k] }] : []);
  const panels = [
    { title: "Air temperature", unit: "°C", series: mk("ta"), minSpan: 6 },
    { title: "Precipitation", unit: "mm/h", series: mk("psum").map((s) => ({ ...s, bars: true })), zero: true, minSpan: 1 },
    { title: "Wind speed", unit: "m/s", series: mk("vw"), zero: true, minSpan: 4 },
    { title: "Incoming shortwave", unit: "W/m²", series: mk("iswr"), zero: true, minSpan: 100 },
  ];
  drawSeries(box, { x0: from, x1: to, panels, zone, snapMs: 3600e3, aria: "Weather inputs for the 7 days before the selected time" });
}
function renderSeason() {
  const dly = S.data.daily, d0 = new Date(dly.d0 + "T00:00:00Z").getTime();
  const pts = (arr) => arr.map((v, i) => [new Date(d0 + i * 86400e3 + 18 * 3600e3), v]);
  const model = { color: "var(--series-1)", label: dly.hs_model_free ? "simulated (pit-updated)" : "simulated", pts: pts(dly.hs_model) };
  const series = [model];
  if (dly.hs_model_free) series.push({ color: "var(--text-muted)", label: "simulated without pit updates", pts: pts(dly.hs_model_free) });
  if (dly.hs_station.some((v) => v != null)) series.push({ color: "var(--series-2)", label: "station snow-depth sensor", pts: pts(maskSpikes(dly.hs_station)) });
  const pitS = { color: "var(--series-3)", label: "observed (pit)", pts: [], markers: S.data.pits.filter((p) => p.hs != null).map((p) => ({ d: new Date(p.t + ":00Z"), v: p.hs, label: "observed (pit)" })) };
  series.push(pitS);
  const items = series.map((s) => ({ color: s.color, label: s.label === "station snow-depth sensor" ? (STATIONS[dly.station] || "station snow-depth sensor") : s.label, dot: s === pitS }));
  legend($("season-legend"), items);
  const x0 = new Date(d0), x1 = new Date(d0 + dly.hs_model.length * 86400e3);
  drawSeries($("season-chart"), { x0, x1, panels: [{ title: "Snow depth", unit: "cm", series, zero: true, minSpan: 50 }], panelH: 180,
    sel: S.t, snapMs: 86400e3, aria: "Snow depth through the season",
    onClick: (d) => { const m = toMST(d); setTarget(new Date(Date.UTC(m.getUTCFullYear(), m.getUTCMonth(), m.getUTCDate(), currentLocalHour() + MST_H))); } });
}

// ------------------------------------------------------------------ public reports (Avalanche Canada MIN)
const PUB_DAYS = 3;
async function ensurePublic() {
  S.pub = null;
  if (!S.seasonMeta.public) return;
  try { S.pub = await getJSON(S.seasonMeta.public); } catch (e) { S.pub = null; }
}
const words = (a) => (a || []).map((x) => String(x).replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase()).join(", ");
function pubDetail(r) {
  const out = [];
  if (r.elev != null) out.push(`${r.elev} m`); else if (r.bands && r.bands.length) out.push(r.bands.join("/").toUpperCase());
  if (r.aspects && r.aspects.length) out.push(r.aspects.join("/"));
  if (r.hs != null) out.push(`HS ${r.hs} cm`);
  if (r.test) {
    const t = [r.test.i, r.test.f].filter(Boolean).join(" ");
    out.push(`test ${t || "result"}${r.test.d != null ? ` down ${r.test.d} cm` : ""}${r.test.c && r.test.c.length ? ` on ${words(r.test.c)}` : ""}`);
  }
  if (r.wh) out.push("whumpfing"); if (r.cr) out.push("cracking");
  if (r.hn24 != null) out.push(`${r.hn24} cm new snow in 24 h`);
  if (r.surface && r.surface.length) out.push(`surface: ${words(r.surface)}`);
  for (const a of r.av || []) out.push(`avalanche size ${a.size || "?"}${a.char && a.char.length ? ` ${words(a.char)}` : ""}${a.trig ? `, ${words([a.trig])}` : ""}${a.asp && a.asp.length ? `, ${a.asp.join("/")}` : ""}`);
  return out.join(" · ");
}
function renderPublic() {
  const box = $("public-list"), note = $("public-note");
  box.replaceChildren();
  if (!S.pub) { note.textContent = "No public reports archived for this season."; return; }
  const t = S.t.getTime(), win = PUB_DAYS * 86400e3;
  const rows = S.pub.reports.filter((r) => Math.abs(new Date(r.t).getTime() - t) <= win)
    .sort((a, b) => a.km - b.km || Math.abs(new Date(a.t) - t) - Math.abs(new Date(b.t) - t));
  note.textContent = `${rows.length} report${rows.length === 1 ? "" : "s"} within ${S.pub.radius_km} km of the plot and ${PUB_DAYS} days of the selected time. ` +
    "Avalanche Canada Mountain Information Network: public, unverified, and at other places, elevations and aspects than the plot.";
  for (const r of rows.slice(0, 15)) {
    const li = el("li");
    const a = el("a", { href: r.url, target: "_blank", rel: "noopener" }, r.title || "MIN report");
    li.append(a, el("span", { class: "pub-when" }, ` · ${fmtMST(new Date(r.t))} · ${r.km} km · ${r.types.join(", ")}`));
    const d = pubDetail(r);
    if (d) li.append(el("div", { class: "pub-detail" }, d));
    if (r.comment) li.append(el("div", { class: "pub-comment" }, r.comment));
    box.append(li);
  }
  if (rows.length > 15) box.append(el("li", { class: "pub-more" }, `${rows.length - 15} more not shown.`));
}

// ------------------------------------------------------------------ upload + data status
function initUpload() {
  const form = $("upload-form");
  if (!form) return;
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const msg = $("upload-status");
    const f = form.querySelector('input[type="file"]').files[0];
    if (!f) { msg.textContent = "Choose a file first."; return; }
    if (f.size > 8e6) { msg.textContent = "That file is over 8 MB; export a smaller PDF or image."; return; }
    msg.textContent = "Uploading…";
    try {
      const r = await fetch("/", { method: "POST", body: new FormData(form) });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      msg.textContent = `Received ${f.name}. It is added at the next daily update; its status appears under “Recently added”.`;
      form.reset();
    } catch (err) { msg.textContent = `Upload failed (${err.message}). Try again, or send the file to the project owner.`; }
  });
}
async function renderStatus() {
  let st = null;
  try { st = await getJSON("data/status.json"); } catch (e) { st = null; }
  const box = $("status-list"); if (!box) return;
  box.replaceChildren();
  if (!st) { box.append(el("li", {}, "No update has run yet.")); return; }
  const line = (k, v) => { const li = el("li"); li.append(el("span", { class: "tk" }, `${k}: `), document.createTextNode(v)); box.append(li); };
  line("Last update", fmtMST(new Date(st.generated_utc)));
  for (const [k, v] of Object.entries(st.weather || {})) line(k, v ? fmtMST(new Date(v)) : "no data");
  if (st.min) line("MIN reports near the plots", `${st.min.reports} archived; last scan ${fmtMST(new Date(st.min.last_scan_utc))}`);
  const ib = $("inbox-list"); ib.replaceChildren();
  const items = (st.inbox && st.inbox.items) || [];
  if (!items.length) ib.append(el("p", { class: "note" }, "Nothing uploaded yet."));
  for (const it of items.slice(0, 20)) {
    ib.append(el("div", { class: "inbox-item" }, `${it.received_utc ? fmtMST(new Date(it.received_utc), true) : ""} · ${it.name} · ${it.site || "site not given"} · ${it.status}${it.note ? ` (${it.note})` : ""}`));
  }
}

// ------------------------------------------------------------------ controls
function currentLocalHour() { return Number($("time").value || 11); }
function timeOptions() {
  const sel = $("time"); const keep = sel.value || "11"; sel.replaceChildren();
  const hours = measured() ? [5, 11, 17, 23] : [11];
  for (const h of hours) sel.append(el("option", { value: String(h) }, `${pad(h)}:00 MST`));
  sel.value = hours.includes(Number(keep)) ? keep : "11";
}
function modeOptions() {
  const sel = $("mode"); const keep = S.mode; sel.replaceChildren();
  for (const [v, label] of MODES) {
    if (v === "free" && !S.data.nowcast_free) continue;
    if (v !== "nowcast" && v !== "free" && !S.seasonMeta.forecasts) continue;
    sel.append(el("option", { value: v }, v === "nowcast" && !measured() ? "ERA5 reanalysis (no station record)" : label));
  }
  S.mode = [...sel.options].some((o) => o.value === keep) ? keep : "nowcast";
  sel.value = S.mode;
}
function pitOptions() {
  const sel = $("pit"); sel.replaceChildren(el("option", { value: "" }, `— ${S.data.pits.length} in this season —`));
  for (const p of S.data.pits) sel.append(el("option", { value: p.id }, `${fmtMST(new Date(p.t + ":00Z"), false)}${p.hs ? ` · HS ${p.hs}` : ""}`));
}
function snapToOutput(d) {
  // nearest available simulated time (6-hourly; daily 18 UTC for reanalysis seasons)
  const every = (S.data.nowcast_every_h || 6) * 3600e3, off = measured() ? 0 : 18 * 3600e3;
  return new Date(Math.round((d.getTime() - off) / every) * every + off);
}
function setTarget(d) {
  S.t = snapToOutput(d);
  $("date").value = localDateStr(S.t);
  const lh = toMST(S.t).getUTCHours();
  if ([...$("time").options].some((o) => Number(o.value) === lh)) $("time").value = String(lh);
  ensureForecast().then(render);
}
function goToPit(pit) { setTarget(new Date(pit.t + ":00Z")); }
async function ensureForecast() {
  if (S.mode === "nowcast" || S.mode === "free" || !S.seasonMeta.forecasts) { S.fc = null; buildIndexes(); return; }
  status("Loading archived GFS forecasts…");
  S.fc = await getJSON(S.seasonMeta.forecasts);
  buildIndexes();
}
async function loadSeason(seasonName, target) {
  S.seasonMeta = seasonFiles(S.site).find((s) => s.season === seasonName);
  status(`Loading ${seasonName}…`);
  S.data = await getJSON(S.seasonMeta.file);
  S.fc = null; buildIndexes();
  timeOptions(); modeOptions(); pitOptions();
  const first = fromKey(S.data.nowcast[0].t), last = fromKey(S.data.nowcast[S.data.nowcast.length - 1].t);
  const lv = S.data.live;
  // a live season reaches into the future through its latest GFS run (72 h)
  const maxT = lv && lv.latest_issue ? new Date(Math.max(last.getTime(), fromKey(lv.latest_issue).getTime() + 72 * 3600e3)) : last;
  $("date").min = localDateStr(first); $("date").max = localDateStr(maxT);
  $("now-btn").hidden = !lv;
  let t = target;
  if (!t || t < first || t > maxT) {
    const lastPit = S.data.pits[S.data.pits.length - 1];
    t = lv ? last : lastPit ? new Date(lastPit.t + ":00Z") :
      new Date(Math.min(last.getTime(), Date.UTC(first.getUTCFullYear() + 1, 1, 15, 18)));
  }
  await ensurePublic();
  setTarget(t);
}
async function selectSite(site, season, target) {
  S.site = site; $("site").value = site;
  const ss = seasonFiles(site).slice().reverse();
  const sel = $("season"); sel.replaceChildren();
  for (const s of ss) sel.append(el("option", { value: s.season }, `${s.season.replace(/-(\d\d)(\d\d)$/, "-$2")} · ${s.live ? "live" : s.mode === "era5" ? "ERA5" : "measured"}${s.forecasts ? " + GFS" : ""} · ${s.pits} pits`));
  const pick = ss.find((s) => s.season === season) ? season : ss[0].season;
  sel.value = pick;
  await loadSeason(pick, target);
}
function writeURL() {
  const q = new URLSearchParams({ site: S.site, season: S.data.season, t: keyOf(S.t), mode: S.mode });
  history.replaceState(null, "", `?${q}`);
}

async function init() {
  try {
    S.sites = await getJSON("data/sites.json");
  } catch (e) { status(`Could not load data/sites.json: ${e.message}`); return; }
  for (const s of S.sites.sites) $("site").append(el("option", { value: s.id }, s.name));
  const gl = $("grain-legend");
  for (const [k, [c, n]] of Object.entries(GRAIN)) { const key = el("span", { class: "key" }); const sw = el("span", { class: "swatch" }); sw.style.background = c; key.append(sw, document.createTextNode(`${k} · ${n}`)); gl.append(key); }
  $("build-info").textContent = `Data generated ${S.sites.generated_utc}. ${S.sites.label}`;
  const q = new URLSearchParams(location.search);
  const site = S.sites.sites.some((s) => s.id === q.get("site")) ? q.get("site") : S.sites.sites[0].id;
  if (["nowcast", "free", "fc1", "fc2", "fc3"].includes(q.get("mode"))) S.mode = q.get("mode");
  const t = q.get("t") && /^\d{4}-\d\d-\d\dT\d\d$/.test(q.get("t")) ? fromKey(q.get("t")) : null;
  await selectSite(site, q.get("season"), t);
  $("site").addEventListener("change", () => selectSite($("site").value, null, S.t));
  $("season").addEventListener("change", () => loadSeason($("season").value, null));
  $("date").addEventListener("change", () => {
    const [y, m, d] = $("date").value.split("-").map(Number);
    if (y) setTarget(new Date(Date.UTC(y, m - 1, d, currentLocalHour() + MST_H)));
  });
  $("time").addEventListener("change", () => {
    const [y, m, d] = $("date").value.split("-").map(Number);
    setTarget(new Date(Date.UTC(y, m - 1, d, currentLocalHour() + MST_H)));
  });
  $("mode").addEventListener("change", () => { S.mode = $("mode").value; ensureForecast().then(render); });
  $("now-btn").addEventListener("click", () => {
    if (!S.data) return;
    S.mode = "nowcast"; $("mode").value = "nowcast";
    setTarget(fromKey(S.data.nowcast[S.data.nowcast.length - 1].t));
  });
  initUpload();
  renderStatus();
  $("pit").addEventListener("change", () => { const p = S.data.pits.find((x) => x.id === $("pit").value); if (p) goToPit(p); });
  let rt = null;
  window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(render, 150); });
}
init();
