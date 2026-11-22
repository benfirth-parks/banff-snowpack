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
// ADR-102: where the run's snowfall came from (the snow depth sensor up to a day, then precipitation)
const snowfallNote = () => {
  const sf = S.data && S.data.snowfall;
  if (!sf) return "";
  if (sf.source !== "snow depth sensor") return ", snowfall from precipitation (too few snow-depth readings)";
  return S.t && S.t > new Date(sf.until_utc + ":00Z") ? ", snowfall from precipitation after the last snow-depth reading"
    : ", snowfall from the snow-depth sensor";
};
// ADR-137: the main model (an evolved lab column model, "main") comes first when the season has its run; standard
// SNOWPACK's inputs follow, each labelled as SNOWPACK
const MODES = [["nowcast", "Standard SNOWPACK: measured weather (nowcast)"],
  ["free", "Standard SNOWPACK: measured weather, without pit updates"],
  ["precip", "Standard SNOWPACK, previous version: snowfall from precipitation"],
  ["fc1", "Standard SNOWPACK forecast, lead up to 24 h"], ["fc2", "Standard SNOWPACK forecast, lead 24–48 h"],
  ["fc3", "Standard SNOWPACK forecast, lead 48–72 h"]];
// ADR-116: a forecast run on HRDPS (then GFS for day 3) carries `weather.source`; older ones are GFS alone.
const fcModel = (rec) => (rec && rec.weather && rec.weather.source === "HRDPS+GFS" ? "HRDPS (day 3 GFS)" :
  rec && rec.weather && rec.weather.source === "HRDPS" ? "HRDPS" : "GFS");

const S = { sites: null, site: null, seasonMeta: null, data: null, fc: null, t: null, mode: "main",
  cache: new Map(), nowIndex: null, fcIndex: null, agentIndex: null, agentData: null, agentIdx: new Map(),
  mainInfo: null, mainData: null, mainIdx: null };
// the main model's run of the selected plot and season (ADR-137), or null: the page then shows standard SNOWPACK first
const mainRun = () => (S.mainData && S.mainData.main && S.mainData.main.nowcast && S.mainData.main.nowcast.length ? S.mainData.main : null);
const firstMode = () => (mainRun() ? "main" : "nowcast");
// experimental evolved agents (ADR-084): chosen as a weather input "agent:<id>", never the default
const isAgent = (m) => typeof m === "string" && m.startsWith("agent:");
function currentAgent() {
  if (!isAgent(S.mode) || !S.agentData) return null;
  return S.agentData.agents.find((a) => a.id === S.mode.slice(6)) || null;
}

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
  S.precipIndex = S.data.nowcast_precip ? new Map(S.data.nowcast_precip.map((p) => [p.t, p])) : null;
  S.fcIndex = S.fc ? new Map(S.fc.issues.filter((i) => i.P).map((i) => [i.issue, i])) : null;
  S.agentIdx = new Map((S.agentData ? S.agentData.agents : []).map((a) => [a.id, new Map(a.nowcast.map((p) => [p.t, p]))]));
  S.mainIdx = mainRun() ? new Map(mainRun().nowcast.map((p) => [p.t, p])) : null;
}
function leadFor(t, mode) {
  const n = { fc1: 1, fc2: 2, fc3: 3 }[mode];
  const h = t.getUTCHours();
  return (h === 0 ? 24 : h) + 24 * (n - 1);
}
function simulated() {
  const k = keyOf(S.t);
  if (isAgent(S.mode)) {
    const a = currentAgent();
    if (!a) return { err: "This experimental agent has no run for this plot and season." };
    const p = S.agentIdx.get(a.id).get(k);
    return p ? { p, kind: "agent", agent: a } : { err: `No profile from ${a.name} at this time (it runs the live season only, from measured weather).` };
  }
  if (S.mode === "main") {
    const m = mainRun();
    if (!m) return { err: "The main model has no run for this plot and season. Choose a standard SNOWPACK input." };
    const p = S.mainIdx.get(k);
    if (p) return { p, kind: "main", main: m };
    const last = fromKey(m.nowcast[m.nowcast.length - 1].t);
    return { err: S.t > last ? `${m.name}: measured weather reaches ${fmtMST(last)} so far. For later times choose a standard SNOWPACK forecast.` :
      `No profile from ${m.name} at this time.` };
  }
  if (S.mode === "free" && S.freeIndex) {
    const p = S.freeIndex.get(k);
    return p ? { p, kind: "free" } : { err: "No simulated profile at this time." };
  }
  if (S.mode === "precip" && S.precipIndex) {  // ADR-102: the previous standard, for comparison
    const p = S.precipIndex.get(k);
    return p ? { p, kind: "precip" } : { err: "No simulated profile at this time." };
  }
  if (S.mode === "nowcast") {
    const p = S.nowIndex.get(k);
    if (p) return { p, kind: "nowcast" };
    const sk = (S.data.skipped_profiles || []).find((x) => x.t === k);
    const last = S.data.nowcast.length ? fromKey(S.data.nowcast[S.data.nowcast.length - 1].t) : null;
    if (S.data.live && last && S.t > last) {
      return { err: `Measured weather reaches ${fmtMST(last)} so far. For later times choose a forecast weather input.` };
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

// ------------------------------------------------------------------ new-snow check (ADR-104)
// The lab's new-snow check (ADR-103) on the main forecast: SNOWPACK draws facets, depth hoar and surface hoar in snow
// that fell since the last pit far more often than observers find them there, so those weak-layer-flag layers are
// marked unlikely (flag bit 4). Layers, depths and scores are unchanged. Kept free of the page so a test can run it.
const NEW_SNOW_FRAC = 0.9;  // higher than 90% of the last pit's snow depth = snow since that pit (old snow settles)
const dayAfter = (d) => new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate() + 1));
// The latest pit with layers the profile could know of: for a forecast any pit before its issue time; for a nowcast a
// pit once its pit update has run (00 UTC the next day; the profile at that hour is still from before the update).
function newSnowPit(pits, ref, forecast) {
  let best = null;
  for (const pit of pits || []) {
    if (!pit.L || !pit.L.length) continue;
    const t = new Date(pit.t + ":00Z");
    if (forecast ? t >= ref : dayAfter(t) >= ref) continue;
    if (!best || t > best.t) best = { pit, t };
  }
  if (!best) return null;
  const hs = best.pit.hs > 0 ? best.pit.hs : Math.max(...best.pit.L.map((l) => l[0] || 0));
  return hs > 0 ? { pit: best.pit, hs } : null;
}
function markNewSnow(layers, hs) {
  const floor = NEW_SNOW_FRAC * hs;
  return layers.map((L) => ((L[7] & 2) && (L[0] + L[1]) / 2 > floor ? [...L.slice(0, 7), L[7] | 4] : L));
}
// ------------------------------------------------------------------ end new-snow check

// ------------------------------------------------------------------ profile chart
function drawProfile(box, layers, opt) {
  box.replaceChildren();
  // both profile charts use the narrower box's width, so side by side they render at exactly the same size
  const widths = ["sim-profile", "obs-profile"].map((id) => $(id) && $(id).clientWidth).filter((w) => w > 0);
  const W = Math.max(320, (widths.length ? Math.min(...widths) : box.clientWidth) || 560), H = 430;
  const m = { l: 50, r: 10, t: 14, b: 36 };
  const tempW = Math.max(84, Math.round(W * 0.24)), gap = 18;
  const hw = W - m.l - m.r - tempW - gap;
  const svg = sv("svg", { viewBox: `0 0 ${W} ${H}`, role: "img", "aria-label": opt.aria, style: `max-width:${W}px` });
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
    const unlikely = L[7] & 4;  // new-snow check: faded, dashed outline
    const path = sv("path", { d, fill: grainColor(g), "fill-opacity": h == null ? 0.45 : unlikely ? 0.3 : 1,
      ...(unlikely ? { stroke: grainColor(g), "stroke-width": 1.2, "stroke-dasharray": "3 2" } : {}) });
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
    if (f & 2) rows.push({ label: f & 4 ? "candidate weak layer, but unlikely: it formed in snow that fell since the last pit (new-snow check)"
      : "candidate weak layer (grain-form flag, not stability)" });
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
        el("td", {}, [(L[7] & 1) ? "crust" : "", (L[7] & 2) ? ((L[7] & 4) ? "weak-layer flag, unlikely (new snow)" : "weak-layer flag") : ""]
          .filter(Boolean).join(", ") || "–"));
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
        const flush = () => { if (seg.length > 1) svg.append(sv("polyline", { points: seg.join(" "), fill: "none", stroke: s.color, "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round", ...(s.dash ? { "stroke-dasharray": "6 4" } : {}) })); seg = []; };
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
  if (sim.p && (sim.kind === "nowcast" || sim.kind === "forecast")) {  // new-snow check on the main forecast (ADR-104)
    const fc = sim.kind === "forecast";
    sim.check = newSnowPit(S.data.pits, fromKey(fc ? sim.rec.issue : sim.p.t), fc);
  }
  const layers = sim.p ? (sim.check ? markNewSnow(sim.p.L, sim.check.hs) : sim.p.L) : [];
  const near = nearestPit(S.t, 24);
  const hsMax = Math.ceil((Math.max(sim.p ? sim.p.hs : 0, near && near.pit.hs ? near.pit.hs : 0,
    ...(near ? near.pit.L.map((l) => l[0] || 0) : [0])) + 10) / 10) * 10;
  // simulated
  $("sim-when").textContent = fmtMST(S.t);
  if (sim.p) {
    const done = S.data.steer ? S.data.steer.updates.filter((u) => !u.note && new Date(u.time_utc) <= S.t) : [];
    const nUpd = done.length, lay = done.some((u) => u.method === "layers");
    const upd = nUpd ? `, ${lay ? "restarted from" : "depth updated from"} ${nUpd} earlier pit${nUpd === 1 ? "" : "s"}` : "";
    const mainWhat = () => {
      const m = sim.main, r = (m.restarts && m.restarts.pits) || [];
      const n = r.filter((x) => new Date(x.t + ":00Z") <= S.t).length;
      const snow = m.snowfall && m.snowfall.source === "snow depth sensor" ? ", snowfall from the snow-depth sensor" : "";
      return `MAIN MODEL “${m.name}”: evolved lab column model, no SNOWPACK · measured weather${snow}` +
        (n ? `, restarted from the latest of ${n} earlier pit${n === 1 ? "" : "s"}` : ", from bare ground (no pit yet)");
    };
    const what = sim.kind === "main" ? mainWhat() :
      sim.kind === "agent" ? `EXPERIMENTAL evolved agent “${sim.agent.name}”, not validated · measured weather, no pit updates` :
      sim.kind === "free" ? `measured weather${snowfallNote()}, no pit updates` :
      sim.kind === "precip" ? `previous standard: snowfall from precipitation, measured weather${upd}` :
      sim.kind === "nowcast" ? (S.data.mode === "live" ? `measured weather (GFS fill until ERA5 is published)${snowfallNote()}${upd}` :
        measured() ? `measured weather${snowfallNote()}${upd}` : "ERA5 reanalysis weather") :
      `${fcModel(sim.rec)} run of ${fmtMST(sim.issue)} (lead ${sim.lead} h)`;
    const nl = layers.length, nwl = layers.filter((l) => l[7] & 2).length, ncr = layers.filter((l) => l[7] & 1).length;
    const nun = layers.filter((l) => l[7] & 4).length;
    const unl = nun ? ` (${nun} unlikely: in snow since the pit of ${fmtMST(new Date(sim.check.pit.t + ":00Z"))})` : "";
    $("sim-meta").textContent = `${what} · HS ${sim.p.hs} cm · SWE ${sim.p.swe} mm · ${nl} layers · ${ncr} crust, ${nwl} weak-layer-flag layers${unl}`;
    drawProfile($("sim-profile"), layers, { hsMax, temps: layers.map((l) => [(l[0] + l[1]) / 2, l[5]]), kind: "model",
      aria: `Simulated profile, ${S.site}, ${fmtMST(S.t)}` });
    layerTable($("sim-table"), layers, "model");
    $("sim-trace").textContent = traceLine(sim);
    $("sim-exp").hidden = sim.kind !== "agent";
    $("sim-main").hidden = sim.kind !== "main";
  } else {
    $("sim-exp").hidden = !isAgent(S.mode);
    $("sim-main").hidden = S.mode !== "main";
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
  renderTimeline();
  $("pit").value = near && near.dt <= 3 ? near.pit.id : "";
  writeURL();
  const lv = S.data.live;
  status(lv ? `${site.name} · live season ${S.data.season} · weather through ${fmtMST(new Date(lv.weather_through))} · latest GFS run ${lv.latest_issue ? fmtMST(fromKey(lv.latest_issue)) : "none yet"} · updated ${fmtMST(new Date(lv.generated_utc))} · ${S.data.pits.length} observations` :
    `${site.name} · ${S.data.season} · ${measured() ? "measured station weather" : "ERA5 reanalysis (no station record)"} · ${S.data.nowcast.length} simulated profiles · ${S.data.pits.length} observations`);
}
function renderMainBrief() {
  // the brief and the banner name the main model from data/main_model.json, so swapping the model changes no page code
  const m = S.mainInfo && S.mainInfo.model;
  $("main-brief").hidden = !m;
  if (!m) return;
  $("main-brief-name").textContent = m.name;
  if (S.mainInfo.label) $("sim-main").textContent = S.mainInfo.label;
}
function traceLine(sim) {
  // provenance of the profile shown: engine build, run, configuration and forcing hashes
  const eng = S.data.engine || {}, h = (x) => (x ? x.slice(0, 8) : "–");
  const ver = eng.version ? eng.version.split(" (")[0] : "SNOWPACK";
  if (sim.kind === "main") {
    const m = sim.main, me = m.engine || {};
    const ids = ((m.restarts && m.restarts.pits) || []).filter((x) => new Date(x.t + ":00Z") <= S.t).map((x) => x.id);
    const gname = m.source && m.source.genome_name && m.source.genome_name !== m.name ? `${m.source.genome_name}, ` : "";
    return `${me.version || "lab column model"} · main model ${m.name} (${gname}${m.id}, genome ${h(m.genome_hash)})` +
      ` · run ${m.run_id} · code ${h(me.code_version)} · forcing ${h(m.forcing_hash)}` +
      (ids.length ? ` · restarted from pit ${ids[ids.length - 1]}` : "");
  }
  if (sim.kind === "agent") {
    const a = sim.agent, ae = a.engine || {};
    return `${ae.version ? ae.version.split(" (")[0] : ver} · experimental agent ${a.name} (${a.id}, genome ${h(a.genome_hash)})` +
      ` · run ${a.run_id} · config ${h(ae.config_hash)} · forcing ${h(a.forcing_hash)}`;
  }
  const chk = sim.check ? ` · new-snow check from pit ${sim.check.pit.id} (HS ${sim.check.hs} cm)` : "";
  if (sim.kind === "forecast") {
    const fe = (S.fc && S.fc.engine) || {};
    return `${ver} · forecast config ${h(fe.config_hash)} · forcing ${h(sim.rec.forcing_hash)} (${fcModel(sim.rec)} ${sim.rec.issue}Z)` +
      ` · initial state from run ${S.data.run_id || "–"}${chk}`;
  }
  return `${ver} · run ${S.data.run_id || "–"} · config ${h(eng.config_hash)} · forcing ${h(S.data.forcing_hash)}${chk}`;
}
function renderScores(sim, near) {
  const box = $("scores"); box.replaceChildren();
  const note = $("scores-note");
  if (!near || !sim.p) { note.textContent = "Scores appear when a simulated and an observed profile are both shown."; return; }
  const pit = near.pit;
  let sc = null;
  if (sim.kind === "nowcast" && pit.nowcast && pit.nowcast.t === sim.p.t) sc = pit.nowcast;
  if (sim.kind === "free" && pit.nowcast_free && pit.nowcast_free.t === sim.p.t) sc = pit.nowcast_free;
  if (sim.kind === "precip" && pit.nowcast_precip && pit.nowcast_precip.t === sim.p.t) sc = pit.nowcast_precip;
  if (sim.kind === "agent") { const a = sim.agent.pits.find((x) => x.id === pit.id); if (a && a.t === sim.p.t && "boundary_f1" in a) sc = a; }
  if (sim.kind === "main") { const a = sim.main.pits.find((x) => x.id === pit.id); if (a && a.t === sim.p.t && "boundary_f1" in a) sc = a; }
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
  legend($("inputs-legend"), fc ? [{ color: c1, label: lab1 }, { color: c2, label: `${fc ? fcModel(sim.rec) : "GFS"} forecast used for this simulation` }] : [{ color: c1, label: lab1 }]);
  const mk = (k) => [{ color: c1, label: "measured", pts: meas[k] }].concat(fc ? [{ color: c2, label: fcModel(sim.rec), pts: fc[k] }] : []);
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
  const mr = mainRun();
  const std = mr ? "standard SNOWPACK" : "simulated";
  const model = { color: mr ? "var(--series-4)" : "var(--series-1)", label: dly.hs_model_free ? `${std} (pit-updated)` : std, pts: pts(dly.hs_model) };
  const series = [];
  if (mr) {  // ADR-137: the main model's depth first
    const m0 = new Date(mr.daily.d0 + "T00:00:00Z").getTime();
    series.push({ color: "var(--series-1)", label: `${mr.name} (main model)`,
      pts: mr.daily.hs_model.map((v, i) => [new Date(m0 + i * 86400e3 + 18 * 3600e3), v]) });
  }
  series.push(model);
  if (dly.hs_model_free) series.push({ color: "var(--text-muted)", label: `${std} without pit updates`, pts: pts(dly.hs_model_free) });
  if (dly.hs_model_precip) series.push({ color: "var(--series-5)", label: "previous standard (snowfall from precipitation)", dash: true, pts: pts(dly.hs_model_precip) });
  const ag = currentAgent();
  if (ag) {
    const a0 = new Date(ag.daily.d0 + "T00:00:00Z").getTime();
    series.push({ color: "var(--series-4)", label: `${ag.name} (experimental)`, dash: true,
      pts: ag.daily.hs_model.map((v, i) => [new Date(a0 + i * 86400e3 + 18 * 3600e3), v]) });
  }
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

// ------------------------------------------------------------------ season timeline (ADR-114)
// Every simulated profile of the season side by side: time across, height above ground up, colour by grain form.
// Uses the profiles the page already holds; nothing new is computed by the model. Kept free of the page so a test
// can run it. Layers are [top, bottom, grain, hardness, density, temperature, water, flags] as in the profiles.
function timelineCols(profiles, pits, check) {
  // one column per profile, each spanning to the next profile's time (the last one keeps the step before it);
  // check: apply the new-snow check (main forecast only), with the pit a nowcast at that time could know of
  const ts = profiles.map((p) => new Date(p.t + ":00:00Z").getTime());
  return profiles.map((p, i) => {
    const step = i + 1 < ts.length ? ts[i + 1] - ts[i] : i > 0 ? ts[i] - ts[i - 1] : 6 * 3600e3;
    const ns = check ? newSnowPit(pits, new Date(ts[i]), false) : null;
    return { t: ts[i], t1: ts[i] + step, hs: p.hs, L: ns ? markNewSnow(p.L, ns.hs) : p.L };
  });
}
function colAt(cols, tms) {
  // the column covering a time (binary search; columns are in time order), or null outside the run
  let lo = 0, hi = cols.length - 1;
  if (!cols.length || tms < cols[0].t || tms >= cols[hi].t1) return null;
  while (lo < hi) { const mid = (lo + hi + 1) >> 1; if (cols[mid].t <= tms) lo = mid; else hi = mid - 1; }
  return cols[lo];
}
const layerAt = (layers, cm) => layers.find((L) => L[0] != null && L[1] != null && cm <= L[0] && cm >= L[1]) || null;
// ------------------------------------------------------------------ end season timeline

function timelineSource() {
  // the run the timeline shows follows the weather input where it is a whole-season run; forecasts show the nowcast
  const a = currentAgent();
  if (a) return { profiles: a.nowcast, check: false, what: `${a.name} (experimental, not validated), measured weather, no pit updates` };
  const m = S.mode === "main" ? mainRun() : null;
  if (m) return { profiles: m.nowcast, check: false, what: `${m.name} (main model, evolved lab column model), measured weather, restarted from each pit` };
  if (S.mode === "free" && S.data.nowcast_free) return { profiles: S.data.nowcast_free, check: false, what: "measured weather, without pit updates" };
  if (S.mode === "precip" && S.data.nowcast_precip) return { profiles: S.data.nowcast_precip, check: false, what: "previous standard: snowfall from precipitation" };
  return { profiles: S.data.nowcast, check: true, what: measured() ? "standard SNOWPACK with measured weather, pit-updated" : "standard SNOWPACK, ERA5 reanalysis weather, pit-updated" };
}
function renderTimeline() {
  const box = $("timeline-chart");
  const src = timelineSource();
  const key = `${S.site}|${S.data.season}|${S.mode}`;
  if (!S.tl || S.tl.key !== key) S.tl = { key, cols: timelineCols(src.profiles, S.data.pits, src.check) };
  const cols = S.tl.cols;
  $("timeline-what").textContent = src.what;
  box.replaceChildren();
  if (!cols.length) { box.append(el("p", { class: "empty" }, "No simulated profiles in this season yet.")); return; }
  // same time axis and side margins as the snow depth chart below, so the two line up
  const dly = S.data.daily, d0 = new Date(dly.d0 + "T00:00:00Z").getTime();
  const t0 = d0, t1 = d0 + dly.hs_model.length * 86400e3;
  const W = Math.max(320, box.clientWidth || 900), H = 300;
  const m = { l: 46, r: 12, t: 26, b: 26 };
  const pits = S.data.pits.filter((p) => p.L && p.L.length);
  const top = Math.max(50, ...cols.map((c) => c.hs || 0), ...pits.map((p) => Math.max(p.hs || 0, ...p.L.map((l) => l[0] || 0))));
  const yMax = Math.ceil((top + 10) / 25) * 25;
  const x = (tms) => m.l + (W - m.l - m.r) * (tms - t0) / (t1 - t0);
  const y = (cm) => m.t + (H - m.t - m.b) * (1 - cm / yMax);
  const cv = el("canvas", { role: "img", "aria-label": `Simulated layers through the season, ${S.site} ${S.data.season}` });
  const dpr = window.devicePixelRatio || 1;
  cv.width = Math.round(W * dpr); cv.height = Math.round(H * dpr);
  cv.style.width = "100%"; cv.style.maxWidth = `${W}px`; cv.style.aspectRatio = `${W} / ${H}`; cv.style.cursor = "pointer";
  const g = cv.getContext("2d"); g.scale(dpr, dpr);
  const css = getComputedStyle(document.documentElement);
  const v = (n) => css.getPropertyValue(n).trim();
  g.font = "11px system-ui, -apple-system, 'Segoe UI', sans-serif";
  // grid
  const step = yMax > 250 ? 50 : yMax > 120 ? 25 : 20;
  g.textAlign = "right"; g.textBaseline = "middle";
  for (let c = 0; c <= yMax; c += step) {
    g.strokeStyle = v("--grid"); g.lineWidth = 1; g.beginPath(); g.moveTo(m.l, y(c) + 0.5); g.lineTo(W - m.r, y(c) + 0.5); g.stroke();
    g.fillStyle = v("--text-muted"); g.fillText(String(c), m.l - 6, y(c));
  }
  // layers: one column per profile (unlikely layers from the new-snow check faded, as in the profile chart)
  for (const c of cols) {
    const xa = x(c.t), w = Math.max(0.6, x(c.t1) - xa + 0.4);
    for (const L of c.L) {
      if (L[0] == null || L[1] == null) continue;
      g.globalAlpha = L[7] & 4 ? 0.3 : 1;
      g.fillStyle = grainColor(L[2]);
      g.fillRect(xa, y(L[0]), w, Math.max(0.6, y(L[1]) - y(L[0])));
    }
  }
  g.globalAlpha = 1;
  // observed pits: a narrow strip of the pit's own layers, outlined, with a marker above the chart
  const pw = 7;
  for (const p of pits) {
    const xp = x(new Date(p.t + ":00Z").getTime());
    for (const L of p.L) {
      if (L[0] == null || L[1] == null) continue;
      g.fillStyle = grainColor(L[2]); g.fillRect(xp - pw / 2, y(L[0]), pw, Math.max(0.6, y(L[1]) - y(L[0])));
    }
    const ph = Math.max(p.hs || 0, ...p.L.map((l) => l[0] || 0));
    g.strokeStyle = v("--text-primary"); g.lineWidth = 1.2; g.strokeRect(xp - pw / 2, y(ph), pw, y(0) - y(ph));
    g.fillStyle = v("--text-primary"); g.beginPath(); g.moveTo(xp - 5, m.t - 12); g.lineTo(xp + 5, m.t - 12); g.lineTo(xp, m.t - 4); g.closePath(); g.fill();
  }
  // baseline, axis titles, month ticks
  g.strokeStyle = v("--axis"); g.lineWidth = 1; g.beginPath(); g.moveTo(m.l, y(0) + 0.5); g.lineTo(W - m.r, y(0) + 0.5); g.stroke();
  g.fillStyle = v("--text-secondary"); g.textAlign = "left"; g.textBaseline = "alphabetic"; g.fillText("Height above ground (cm)", m.l, 12);
  g.fillStyle = v("--text-muted"); g.textAlign = "center";
  let lastX = -1e9;
  for (let d = new Date(Date.UTC(new Date(t0).getUTCFullYear(), new Date(t0).getUTCMonth() + 1, 1, MST_H)); d.getTime() <= t1; d = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth() + 1, 1, MST_H))) {
    if (x(d.getTime()) - lastX < 46) continue;
    lastX = x(d.getTime());
    g.fillText(MONTHS[toMST(d).getUTCMonth()], lastX, H - 8);
  }
  // selected time
  if (S.t) { g.strokeStyle = v("--text-primary"); g.lineWidth = 1.5; g.setLineDash([4, 3]); g.beginPath(); g.moveTo(x(S.t.getTime()), m.t); g.lineTo(x(S.t.getTime()), y(0)); g.stroke(); g.setLineDash([]); }
  // hover: what is at that height and time; click: open that profile
  const at = (evt) => {
    const r = cv.getBoundingClientRect();
    const px = (evt.clientX - r.left) * W / r.width, py = (evt.clientY - r.top) * H / r.height;
    return { tms: t0 + (t1 - t0) * (px - m.l) / (W - m.l - m.r), cm: yMax * (1 - (py - m.t) / (H - m.t - m.b)), px };
  };
  cv.addEventListener("pointermove", (evt) => {
    const a = at(evt);
    const pit = pits.find((p) => Math.abs(x(new Date(p.t + ":00Z").getTime()) - a.px) <= pw / 2 + 1);
    if (pit) {
      const L = layerAt(pit.L, a.cm);
      showTip(evt, [{ value: `Observed pit, ${fmtMST(new Date(pit.t + ":00Z"))}` }, ...(L ? layerTip(L, "observed") : [{ label: `HS ${pit.hs ?? "–"} cm` }]), { label: "click to open this pit" }]);
      return;
    }
    const c = colAt(cols, a.tms);
    if (!c) { hideTip(); return; }
    const L = layerAt(c.L, a.cm);
    showTip(evt, [{ value: fmtMST(new Date(c.t)) }, ...(L ? layerTip(L, "model") : [{ label: `above the snow (HS ${c.hs} cm)` }])]);
  });
  cv.addEventListener("pointerleave", hideTip);
  cv.addEventListener("click", (evt) => {
    const a = at(evt);
    const pit = pits.find((p) => Math.abs(x(new Date(p.t + ":00Z").getTime()) - a.px) <= pw / 2 + 1);
    if (pit) { goToPit(pit); return; }
    const c = colAt(cols, a.tms);
    if (c) setTarget(new Date(c.t));
  });
  box.append(cv);
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
  renderWarnings(st);
  const line = (k, v) => { const li = el("li"); li.append(el("span", { class: "tk" }, `${k}: `), document.createTextNode(v)); box.append(li); };
  line("Last update", fmtMST(new Date(st.generated_utc)));
  const nw = (st.warnings || []).filter((w) => w.level !== "info").length;
  if (st.warnings) line("Data warnings", nw ? `${nw} (listed at the top of the page)` : "none");
  for (const [k, v] of Object.entries(st.weather || {})) line(k, v ? fmtMST(new Date(v)) : "no data");
  if (st.min) line("MIN reports near the plots", `${st.min.reports} archived; last scan ${fmtMST(new Date(st.min.last_scan_utc))}`);
  const ib = $("inbox-list"); ib.replaceChildren();
  const items = (st.inbox && st.inbox.items) || [];
  if (!items.length) ib.append(el("p", { class: "note" }, "Nothing uploaded yet."));
  for (const it of items.slice(0, 20)) {
    ib.append(el("div", { class: "inbox-item" }, `${it.received_utc ? fmtMST(new Date(it.received_utc), true) : ""} · ${it.name} · ${it.site || "site not given"} · ${it.status}${it.note ? ` (${it.note})` : ""}`));
  }
}

// A missed daily update: status.json older than stale_after_h.update (36 h; ADR-044). Hours since, or null.
function updateLateH(st, now = new Date()) {
  const limit = (st.stale_after_h && st.stale_after_h.update) || 36;
  const age = (now - new Date(st.generated_utc)) / 3600e3;
  return Number.isFinite(age) && age > limit ? age : null;
}

// Stale or failed inputs found by the daily update (status.json "warnings"; levels error, warning, info), and a
// missed update.
function renderWarnings(st) {
  const box = $("data-warnings"); if (!box) return;
  box.replaceChildren();
  const rank = { error: 0, warning: 1, info: 2 };
  const ws = [...(st.warnings || [])].sort((a, b) => (rank[a.level] ?? 1) - (rank[b.level] ?? 1));
  const late = updateLateH(st);
  box.hidden = !ws.length && late === null;
  if (box.hidden) return;
  const serious = late !== null || ws.some((w) => w.level !== "info");
  box.className = `data-warnings${serious ? "" : " quiet"}`;
  if (late !== null) {
    box.append(el("p", { class: "dw-head" }, `No daily update for ${Math.round(late)} h (last ${fmtMST(new Date(st.generated_utc))}; one is expected every day): the weather, simulations and notes on this page may be out of date.`));
  }
  if (!ws.length) return;
  box.append(el("p", { class: "dw-head" }, ws.some((w) => w.level !== "info")
    ? `Input data warnings at the last update (${fmtMST(new Date(st.generated_utc))}): the simulations below may use filled or older data.`
    : `Input data notes at the last update (${fmtMST(new Date(st.generated_utc))}):`));
  const ul = el("ul");
  const tag = { error: "Error", warning: "Warning", info: "Note" };
  for (const w of ws) {
    const li = el("li", { class: `dw-${w.level}` });
    li.append(el("strong", {}, `${tag[w.level] || "Warning"}: `), document.createTextNode(w.message));
    ul.append(li);
  }
  box.append(ul);
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
  const mr = mainRun();
  if (mr) sel.append(el("option", { value: "main" }, `${mr.name}: main model (evolved, no SNOWPACK)`));
  for (const [v, label] of MODES) {
    if (v === "free" && !S.data.nowcast_free) continue;
    if (v === "precip" && !S.data.nowcast_precip) continue;
    if (v.startsWith("fc") && !S.seasonMeta.forecasts) continue;
    sel.append(el("option", { value: v }, v === "nowcast" && !measured() ? "Standard SNOWPACK: ERA5 reanalysis (no station record)" : label));
  }
  for (const a of (S.agentData ? S.agentData.agents : [])) {
    sel.append(el("option", { value: `agent:${a.id}` }, `Experimental agent: ${a.name} (not validated)`));
  }
  S.mode = [...sel.options].some((o) => o.value === keep) ? keep : firstMode();
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
  if (S.mode === "main" || S.mode === "nowcast" || S.mode === "free" || S.mode === "precip" || isAgent(S.mode) || !S.seasonMeta.forecasts) { S.fc = null; buildIndexes(); return; }
  status("Loading archived GFS forecasts…");
  S.fc = await getJSON(S.seasonMeta.forecasts);
  buildIndexes();
}
async function loadSeason(seasonName, target) {
  S.seasonMeta = seasonFiles(S.site).find((s) => s.season === seasonName);
  status(`Loading ${seasonName}…`);
  S.data = await getJSON(S.seasonMeta.file);
  S.agentData = await loadAgents(S.site, seasonName);
  S.mainData = await loadMain(S.seasonMeta);
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
async function loadMain(meta) {
  // optional (ADR-137): without the model index or the season's run, the page shows standard SNOWPACK first
  if (!S.mainInfo || !meta || !meta.main) return null;
  try {
    const d = await getJSON(meta.main);
    return d && d.main && Array.isArray(d.main.nowcast) ? d : null;
  } catch (e) { return null; }
}
async function loadAgents(site, season) {
  // optional: a missing or broken agents file never stops the page
  const f = S.agentIndex && S.agentIndex.plots && S.agentIndex.plots[site] && S.agentIndex.plots[site][season];
  if (!f) return null;
  try {
    const d = await getJSON(`data/${site}/${f}`);
    return d && Array.isArray(d.agents) && d.agents.length ? d : null;
  } catch (e) { return null; }
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
  try { S.agentIndex = await getJSON("data/agents.json"); } catch (e) { S.agentIndex = null; }
  try { S.mainInfo = await getJSON("data/main_model.json"); } catch (e) { S.mainInfo = null; }
  renderMainBrief();
  const gl = $("grain-legend");
  for (const [k, [c, n]] of Object.entries(GRAIN)) { const key = el("span", { class: "key" }); const sw = el("span", { class: "swatch" }); sw.style.background = c; key.append(sw, document.createTextNode(`${k} · ${n}`)); gl.append(key); }
  $("build-info").textContent = `Data generated ${S.sites.generated_utc}. ${S.sites.label}`;
  const q = new URLSearchParams(location.search);
  const site = S.sites.sites.some((s) => s.id === q.get("site")) ? q.get("site") : S.sites.sites[0].id;
  if (["main", "nowcast", "free", "precip", "fc1", "fc2", "fc3"].includes(q.get("mode")) || /^agent:[\w-]{1,80}$/.test(q.get("mode") || "")) S.mode = q.get("mode");
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
    S.mode = firstMode(); $("mode").value = S.mode;
    setTarget(fromKey(S.data.nowcast[S.data.nowcast.length - 1].t));
  });
  initUpload();
  renderStatus();
  $("pit").addEventListener("change", () => { const p = S.data.pits.find((x) => x.id === $("pit").value); if (p) goToPit(p); });
  let rt = null;
  window.addEventListener("resize", () => { clearTimeout(rt); rt = setTimeout(render, 150); });
}
init();
