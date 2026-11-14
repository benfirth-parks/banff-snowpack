/* Snowpack Translator: draws a manual snow profile (hand hardness bars by depth, grain form and size columns,
   temperature line, stability test call-outs) as a list of shapes, then writes that list as SVG for the screen or as a
   vector PDF. No libraries and no network: the PDF writer below is complete and uses the built-in Helvetica fonts. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory(require("./model.js"));
  else root.SnowDraw = factory(root.SnowModel);
})(typeof self !== "undefined" ? self : this, function (M) {
  "use strict";

  const THEMES = {
    dark: { bg: "#121214", grid: "#34343a", gridBold: "#85858c", ink: "#f2f2f2", muted: "#a4a8ae", bar: "#6e6e70", barStroke: "#dcdcdc",
      concern: "#8f1a10", concernStroke: "#f0b9b2", temp: "#f0453a", test: "#f0a818", hs: "#f0a818", pit: "#4aa8ff", table: "#5c5c63", sel: "#ffffff", empty: "#2a2a2f" },
    light: { bg: "#ffffff", grid: "#cfcfcf", gridBold: "#5a5a5a", ink: "#111111", muted: "#555555", bar: "#ababab", barStroke: "#6a6a6a",
      concern: "#ec8f85", concernStroke: "#b5281c", temp: "#d93025", test: "#e8a21a", hs: "#d98e00", pit: "#1e88e5", table: "#9a9a9a", sel: "#d9520b", empty: "#ececec" }
  };

  /* ---------- Helvetica metrics (for centring and right-aligning text in the PDF) ---------- */
  const W_N = [278,278,355,556,556,889,667,191,333,333,389,584,278,333,278,278,556,556,556,556,556,556,556,556,556,556,278,278,584,584,584,556,1015,667,667,722,722,667,611,778,722,278,500,667,556,833,722,778,667,778,722,667,611,722,667,944,667,667,611,278,278,278,469,556,333,556,556,500,556,556,278,556,556,222,222,500,222,833,556,556,556,556,333,500,278,556,500,722,500,500,500,334,260,334,584];
  const W_B = [278,333,474,556,556,889,722,238,333,333,389,584,278,333,278,278,556,556,556,556,556,556,556,556,556,556,333,333,584,584,584,611,975,722,722,722,722,667,611,778,722,278,556,722,611,833,722,778,667,778,722,667,611,722,667,944,667,667,611,333,278,333,584,556,333,556,611,556,611,556,333,611,611,278,278,556,278,889,611,611,611,611,389,556,333,611,556,778,556,556,500,389,280,389,584];
  function tw(s, size, font) {
    const T = font === "b" ? W_B : W_N; let w = 0;
    for (const ch of String(s)) { const c = ch.charCodeAt(0); w += c >= 32 && c <= 126 ? T[c - 32] : c === 176 ? 400 : c === 8211 ? 556 : 556; }
    return w * size / 1000;
  }
  function fit(s, maxW, size, font) { // shorten with an ellipsis-free cut so it stays inside a cell
    s = String(s || ""); if (tw(s, size, font) <= maxW) return s;
    while (s.length > 1 && tw(s + "..", size, font) > maxW) s = s.slice(0, -1);
    return s.replace(/\s+$/, "") + "..";
  }
  function wrap(s, maxW, size, font, maxLines) {
    const out = []; let line = "";
    String(s || "").split(/\s+/).forEach(w => {
      const t = line ? line + " " + w : w;
      if (tw(t, size, font) <= maxW || !line) line = t; else { out.push(line); line = w; }
    });
    if (line) out.push(line);
    if (maxLines && out.length > maxLines) { out.length = maxLines; out[maxLines - 1] = fit(out[maxLines - 1] + " ...", maxW, size, font); }
    return out;
  }

  /* ---------- grain form symbols (IACS 2009 main classes, drawn as shapes so they need no font) ---------- */
  const MAIN = c => /^(PP|MM|DF|RG|FC|DH|SH|MF|IF)/.exec(c || "") ? RegExp.$1 : "";
  function glyph(L, code, cx, cy, s, col) {
    const sw = Math.max(1, s * 0.24), ln = (x1, y1, x2, y2) => L.push({ t: "line", x1: cx + x1 * s, y1: cy + y1 * s, x2: cx + x2 * s, y2: cy + y2 * s, stroke: col, sw });
    const ring = (x, y, r, fill) => L.push({ t: "circle", cx: cx + x * s, cy: cy + y * s, r: r * s, fill: fill || null, stroke: col, sw });
    const key = code === "FCxr" || code === "MFcr" || code === "RGxf" || code === "PPgp" ? code : MAIN(code);
    switch (key) {
      case "PP": ln(-1, 0, 1, 0); ln(0, -1, 0, 1); break;
      case "PPgp": L.push({ t: "poly", pts: [[cx - s, cy - 0.8 * s], [cx + s, cy - 0.8 * s], [cx, cy + 0.9 * s]], close: true, stroke: col, sw }); L.push({ t: "circle", cx, cy: cy - 0.25 * s, r: 0.22 * s, fill: col }); break;
      case "MM": ring(0, 0, 1); ring(0, 0, 0.45); break;
      case "DF": ln(-0.8, 0.9, 0.8, -0.9); break;
      case "RG": L.push({ t: "circle", cx, cy, r: 0.62 * s, fill: col }); break;
      case "RGxf": L.push({ t: "circle", cx, cy, r: 0.5 * s, fill: col }); L.push({ t: "rect", x: cx - 0.95 * s, y: cy - 0.95 * s, w: 1.9 * s, h: 1.9 * s, stroke: col, sw: sw * 0.8 }); break;
      case "FC": L.push({ t: "rect", x: cx - 0.85 * s, y: cy - 0.85 * s, w: 1.7 * s, h: 1.7 * s, stroke: col, sw }); break;
      case "FCxr": {
        const pts = [[cx - 0.85 * s, cy - 0.15 * s]];
        for (let k = 0; k <= 8; k++) { const a = Math.PI * (1 - k / 8); pts.push([cx + 0.85 * s * Math.cos(a), cy - 0.15 * s - 0.8 * s * Math.sin(a)]); }
        pts.push([cx + 0.85 * s, cy + 0.9 * s], [cx - 0.85 * s, cy + 0.9 * s]);
        L.push({ t: "poly", pts, close: true, stroke: col, sw }); ln(-0.85, -0.15, 0.85, -0.15); break;
      }
      case "DH": ln(-0.9, 0.85, 0, -0.85); ln(0, -0.85, 0.9, 0.85); break;
      case "SH": ln(-0.9, -0.85, 0, 0.85); ln(0, 0.85, 0.9, -0.85); break;
      case "MF": ring(0, 0, 0.85); break;
      case "MFcr": ring(-0.6, 0, 0.62); ring(0.6, 0, 0.62); ln(-1.3, 1, 1.3, 1); break;
      case "IF": L.push({ t: "rect", x: cx - s, y: cy - 0.42 * s, w: 2 * s, h: 0.84 * s, fill: col }); break;
      default: return false;
    }
    return true;
  }
  // grain form with its secondary form in brackets, centred on cx; returns the width used
  function grainMark(L, l, cx, cy, s, col, fs) {
    if (!l || !l.grain) return 0;
    if (!MAIN(l.grain)) { L.push({ t: "text", x: cx, y: cy + fs * 0.35, s: l.grain, size: fs, fill: col, anchor: "middle" }); return tw(l.grain, fs); }
    if (!l.grain2 || !MAIN(l.grain2)) { glyph(L, l.grain, cx, cy, s, col); return 2 * s; }
    const gap = s * 0.9, pw = tw("(", fs), total = 2 * s + gap + pw + 2 * s + pw + 2; let x = cx - total / 2;
    glyph(L, l.grain, x + s, cy, s, col); x += 2 * s + gap;
    L.push({ t: "text", x, y: cy + fs * 0.35, s: "(", size: fs, fill: col }); x += pw + 1;
    glyph(L, l.grain2, x + s, cy, s, col); x += 2 * s + 1;
    L.push({ t: "text", x, y: cy + fs * 0.35, s: ")", size: fs, fill: col });
    return total;
  }

  function niceStep(range, target) {
    const raw = range / target, p = Math.pow(10, Math.floor(Math.log10(raw)));
    for (const k of [1, 2, 5, 10]) if (k * p >= raw) return k * p;
    return 10 * p;
  }
  // table row boundaries: as close to the layer boundaries as possible while every row stays tall enough to read
  function spread(ys, minRow, top, bottom) {
    const n = ys.length - 1; if (n < 1) return ys.slice();
    const mr = Math.min(minRow, (bottom - top) / n), t = ys.slice();
    t[0] = Math.max(top, t[0]);
    for (let i = 1; i <= n; i++) t[i] = Math.max(t[i], t[i - 1] + mr);
    t[n] = Math.min(t[n], bottom);
    for (let i = n - 1; i >= 0; i--) t[i] = Math.min(t[i], t[i + 1] - mr);
    return t;
  }

  /* ---------- the profile graph ---------- */
  function graph(L, m, G, C) {
    const fs = G.fs || 10, hasT = m.temps.length > 0, fsS = fs - 1.5;
    const wN = G.notes ? Math.max(70, Math.min(140, G.w * 0.2)) : 0, wF = G.w < 420 ? 44 : 56, wS = G.w < 420 ? 44 : 52, tableW = wF + wS + wN;
    const axisW = G.w < 420 ? 34 : 42, headH = 26, bottom = M.pitBottom(m), hs = M.hsOf(m), hsKnown = M.num(m.surf.hs) != null;
    const B = bottom > 0 ? bottom : (hsKnown ? hs : 100), partial = hsKnown && bottom > 0 && hs - bottom > 0.5, breakH = partial ? 20 : 0;
    // mirror: bars grow from the right with the depth axis and the table on the right, as on the phone recorder
    const mir = !!G.mirror, pw = G.w - tableW - axisW - 8, px = mir ? G.x + 6 : G.x + tableW + axisW, py = G.y + headH, ph = G.h - headH - 32 - breakH;
    const base = mir ? px + pw : px, dir = mir ? -1 : 1, lx = base - dir * 6; // lx: where the depth labels sit
    const Y = d => py + d / B * ph, height = m.ref === "height";
    const ice = m.layers.some(l => l.hardTop === "I" || l.hardBot === "I"), hMin = 1 / 3, hMax = (ice ? 6 : 5) + 1 / 3;
    const X = h => base + dir * (Math.max(hMin, Math.min(hMax, h)) - hMin) / (hMax - hMin) * pw;

    L.push({ t: "rect", x: px, y: py, w: pw, h: ph, fill: C.bg });
    // vertical grid: thirds of a hardness step, bold on the named steps
    for (let k = 1; k <= Math.round((hMax - hMin) * 3); k++) {
      const h = hMin + k / 3, main = Math.abs(h - Math.round(h)) < 0.01;
      if (h < hMax - 0.01) L.push({ t: "line", x1: X(h), y1: py, x2: X(h), y2: py + ph, stroke: main ? C.gridBold : C.grid, sw: main ? 0.9 : 0.5 });
    }
    // depth ticks
    const step = niceStep(B, Math.max(4, Math.min(16, ph / 26))), ticks = [];
    const unscaled = height && !hsKnown && bottom === 0; // heights have no numbers until the snowpack height is recorded
    if (unscaled) { for (let d = 0; d <= B + 1e-6; d += step) ticks.push({ d, label: null }); }
    else if (height) { for (let v = Math.ceil((hs - B) / step - 1e-9) * step; v <= hs + 1e-6; v += step) ticks.push({ d: hs - v, label: v }); }
    else for (let d = 0; d <= B + 1e-6; d += step) ticks.push({ d, label: d });
    ticks.forEach(k => L.push({ t: "line", x1: px, y1: Y(k.d), x2: px + pw, y2: Y(k.d), stroke: C.grid, sw: 0.5 }));

    // table frame and rows
    const tx = mir ? px + pw + axisW : G.x, yb = m.layers.length ? [Y(m.layers[0].top)].concat(m.layers.map(l => Y(l.bottom))) : [];
    const rows = spread(yb, 13, py, py + ph);
    L.push({ t: "rect", x: tx, y: py - headH, w: tableW, h: headH, stroke: C.table, sw: 0.7 });
    L.push({ t: "line", x1: tx, y1: py, x2: tx, y2: py + ph, stroke: C.table, sw: 0.7 }, { t: "line", x1: tx + wF, y1: py - headH, x2: tx + wF, y2: py + ph, stroke: C.table, sw: 0.7 },
      { t: "line", x1: tx + wF + wS, y1: py - headH, x2: tx + wF + wS, y2: py + ph, stroke: C.table, sw: 0.7 });
    if (wN) L.push({ t: "line", x1: tx + tableW, y1: py, x2: tx + tableW, y2: py + ph, stroke: C.table, sw: 0.7 });
    const narrow = wF < 50, hy = py - headH / 2;
    if (narrow) L.push({ t: "text", x: tx + wF / 2, y: hy - 0.5, s: "Grain", size: fsS, fill: C.ink, anchor: "middle" }, { t: "text", x: tx + wF / 2, y: hy + fsS - 1, s: "Form", size: fsS, fill: C.ink, anchor: "middle" });
    else L.push({ t: "text", x: tx + wF / 2, y: hy + fsS * 0.35, s: "Grain Form", size: fsS, fill: C.ink, anchor: "middle" });
    L.push({ t: "text", x: tx + wF + wS / 2, y: hy - 0.5, s: narrow ? "Size" : "Grain Size", size: fsS, fill: C.ink, anchor: "middle" });
    L.push({ t: "text", x: tx + wF + wS / 2, y: hy + fsS - 1, s: "(mm)", size: fsS - 1.5, font: "i", fill: C.ink, anchor: "middle" });
    if (wN) L.push({ t: "text", x: tx + wF + wS + wN / 2, y: py - headH / 2 + fsS * 0.35, s: "Notes", size: fsS, fill: C.ink, anchor: "middle" });

    // layer bars
    m.layers.forEach((l, i) => {
      const y1 = Y(l.top), y2 = Y(l.bottom), a = M.hIndex(l.hardTop), b = M.hIndex(l.hardBot || l.hardTop);
      const sel = G.sel === i, attr = G.live ? { class: "lay", "data-i": i } : null;
      if (!a) L.push({ t: "rect", x: px, y: y1, w: pw, h: y2 - y1, fill: C.empty, stroke: C.gridBold, sw: 0.7, dash: [3, 3], attr });
      else L.push({ t: "poly", pts: [[base, y1], [X(a), y1], [X(b || a), y2], [base, y2]], close: true, fill: l.concern ? C.concern : C.bar, stroke: l.concern ? C.concernStroke : C.barStroke, sw: 0.9, attr });
      if (sel) L.push({ t: "rect", x: px + 1, y: y1 + 1, w: pw - 2, h: Math.max(1, y2 - y1 - 2), stroke: C.sel, sw: 1.6, dash: [5, 3] });
    });
    L.push({ t: "rect", x: px, y: py, w: pw, h: ph, stroke: C.gridBold, sw: 0.9 });

    // leaders from each layer boundary to its table row, then the rows
    rows.forEach((ty, i) => {
      L.push({ t: "line", x1: tx, y1: ty, x2: tx + tableW, y2: ty, stroke: C.table, sw: 0.7 });
      const te = mir ? tx : tx + tableW; // the table edge that faces the plot
      L.push({ t: "poly", pts: [[te, ty], [te - dir * 8, ty], [base - dir * 8, yb[i]], [base, yb[i]]], stroke: C.table, sw: 0.6 });
    });
    m.layers.forEach((l, i) => {
      const cy = (rows[i] + rows[i + 1]) / 2, rh = rows[i + 1] - rows[i], f = Math.min(fsS, Math.max(6.5, rh - 3)), gs = Math.min(4.6, Math.max(2.6, (rh - 4) / 2));
      grainMark(L, l, tx + wF / 2, cy, gs, C.ink, f);
      const sz = M.sizeLabel(l); if (sz) L.push({ t: "text", x: tx + wF + wS / 2, y: cy + f * 0.35, s: sz, size: f, fill: C.ink, anchor: "middle" });
      const note = [l.note, l.wet && l.wet !== "D" ? M.WET[l.wet] : "", l.density ? l.density + " kg/m3" : ""].filter(Boolean).join(", ");
      if (wN && note) L.push({ t: "text", x: tx + wF + wS + wN / 2, y: cy + f * 0.35, s: fit(note, wN - 8, f - 0.5), size: f - 0.5, fill: C.ink, anchor: "middle" });
    });

    // depth axis labels (drawn over the leaders on a small patch of background)
    const lab = (y, s, col, bold) => {
      const w = tw(s, fs, bold ? "b" : "n");
      L.push({ t: "rect", x: (mir ? lx : lx - w) - 1.5, y: y - fs * 0.5, w: w + 3, h: fs, fill: C.bg });
      L.push({ t: "text", x: lx, y: y + fs * 0.35, s, size: fs, font: bold ? "b" : "n", fill: col, anchor: mir ? "start" : "end" });
    };
    const special = []; // y positions taken by HS / PIT labels
    if (height) special.push(Y(0)); if (partial) special.push(Y(B)); if (!height && !partial && hsKnown) special.push(Y(B));
    ticks.forEach(k => { const y = Y(k.d); if (k.label != null && !special.some(sy => y > sy - fs * 2 && y < sy + fs * 1.1)) lab(y, M.fmt(k.label), C.ink); });
    const tag = (y, word, val, col) => { lab(y, M.fmt(val), col, true); L.push({ t: "text", x: lx, y: y - fs * 0.72, s: word, size: fs - 3.5, font: "b", fill: col, anchor: mir ? "start" : "end" }); };
    if (unscaled) { /* nothing to label yet */ } else if (height) tag(Y(0), "HS", hs, C.hs);
    else if (hsKnown && !partial) tag(Y(B), "HS", hs, C.hs);
    if (partial) {
      const yP = Y(B), yG = yP + breakH;
      L.push({ t: "line", x1: px, y1: yP, x2: px + pw, y2: yP, stroke: C.pit, sw: 1.2 });
      tag(yP, "PIT", height ? hs - B : B, C.pit);
      const zig = []; for (let x = px - 4, k = 0; x <= px + pw + 4; x += 7, k++) zig.push([Math.min(x, px + pw + 4), yP + breakH / 2 + (k % 2 ? -2.6 : 2.6)]);
      L.push({ t: "poly", pts: zig, stroke: C.hs, sw: 1 });
      L.push({ t: "line", x1: px, y1: yG, x2: px + pw, y2: yG, stroke: C.gridBold, sw: 0.9 });
      if (height) lab(yG, "0", C.ink, true); else tag(yG, "HS", hs, C.hs);
    }
    L.push({ t: "text", x: mir ? tx - 3 : G.x + tableW + 9, y: py + ph - 6, s: height ? "Height (cm)" : "Depth (cm)", size: fs - 2, fill: C.muted, rot: -90 });

    // hardness axis
    const yA = py + ph + breakH, names = ["F", "4F", "1F", "P", "K"].concat(ice ? ["I"] : []);
    names.forEach((n, i) => {
      L.push({ t: "line", x1: X(i + 1), y1: yA, x2: X(i + 1), y2: yA + 4, stroke: C.gridBold, sw: 0.9 });
      L.push({ t: "text", x: X(i + 1), y: yA + 5 + fs, s: n, size: fs, fill: C.ink, anchor: "middle" });
    });
    L.push({ t: "text", x: px + pw / 2, y: yA + 8 + 2 * fs, s: "Hand Hardness", size: fs, fill: C.ink, anchor: "middle" });

    // temperature line and its axis along the top
    if (hasT) {
      const tMin = Math.min.apply(null, m.temps.map(t => t.t)), range = tMin < -20 ? Math.ceil(-tMin / 10) * 10 : tMin < -10 ? 20 : 10, tStep = range > 20 ? 5 : range > 10 ? 2 : 1;
      const XT = t => base + dir * Math.max(0, Math.min(range, -t)) / range * pw;
      for (let v = 0; v <= range; v += tStep) {
        if (G.w < 420 && tStep === 1 && v % 2) { L.push({ t: "line", x1: XT(-v), y1: py - 3, x2: XT(-v), y2: py, stroke: C.gridBold, sw: 0.7 }); continue; }
        L.push({ t: "line", x1: XT(-v), y1: py - 4, x2: XT(-v), y2: py, stroke: C.gridBold, sw: 0.9 });
        L.push({ t: "text", x: XT(-v), y: py - 7, s: v ? "-" + v : "0", size: fs - 1.5, font: "b", fill: C.temp, anchor: "middle" });
      }
      L.push({ t: "text", x: mir ? px : px + pw, y: py - 7 - fs, s: "Snow temperature (\u00b0C)", size: fs - 2.5, fill: C.temp, anchor: mir ? "start" : "end" });
      if (m.temps.length > 1) L.push({ t: "poly", pts: m.temps.map(t => [XT(t.t), Y(t.depth)]), stroke: C.temp, sw: 1.5 });
      m.temps.forEach(t => L.push({ t: "circle", cx: XT(t.t), cy: Y(t.depth), r: 3.4, fill: C.temp }));
    }

    // stability tests: a line at the failure depth with a call-out box at the right edge
    const groups = [];
    m.tests.filter(t => M.num(t.depth) != null).sort((a, b) => a.depth - b.depth).forEach(t => {
      const g = groups.find(g => Math.abs(g.depth - t.depth) < 0.5); if (g) g.tests.push(t); else groups.push({ depth: t.depth, tests: [t] });
    });
    const fT = fs + 1, rowH = fT + 4, maxBox = pw * 0.82;
    groups.forEach(g => {
      const on = M.layerAt(m, g.depth);
      g.rows = g.tests.map(t => {
        const label = M.testLabel(t), parts = [{ s: label, font: "b", size: fT }];
        if (on && on.grain) { parts.push({ s: " on ", size: fT - 2 }); parts.push({ glyph: on.grain, w: MAIN(on.grain) ? 10 : tw(on.grain, fT - 1) + 2 }); const sz = M.sizeLabel(on); if (sz) parts.push({ s: " " + sz, size: fT - 2 }); }
        const note = t.note || (on && on.note) || ""; if (note) parts.push({ s: " " + note, font: "i", size: fT - 2 });
        let w = parts.reduce((a, p) => a + (p.glyph ? p.w : tw(p.s, p.size, p.font)), 0);
        const last = parts[parts.length - 1];
        if (w > maxBox - 14 && last.font === "i") { const room = maxBox - 14 - (w - tw(last.s, last.size, "i")); if (room > 20) last.s = " " + fit(last.s.trim(), room - 4, last.size, "i"); else parts.pop(); w = parts.reduce((a, p) => a + (p.glyph ? p.w : tw(p.s, p.size, p.font)), 0); }
        return { parts, w };
      });
      g.w = Math.min(maxBox, Math.max.apply(null, g.rows.map(r => r.w)) + 14); g.h = g.rows.length * rowH + 6; g.y = Y(g.depth) - g.h / 2;
    });
    for (let i = 0; i < groups.length; i++) { const g = groups[i]; g.y = Math.max(py + 2, g.y); if (i && g.y < groups[i - 1].y + groups[i - 1].h + 3) g.y = groups[i - 1].y + groups[i - 1].h + 3; }
    for (let i = groups.length - 1, lim = py + ph + breakH - 2; i >= 0; i--) { const g = groups[i]; if (g.y + g.h > lim) g.y = lim - g.h; lim = g.y - 3; }
    groups.forEach(g => {
      const yL = Y(g.depth), bx = mir ? px + 3 : px + pw - g.w - 3, mid = g.y + g.h / 2, be = mir ? bx + g.w : bx; // be: the box edge the line meets
      if (Math.abs(mid - yL) < 1.5) L.push({ t: "line", x1: base, y1: yL, x2: be, y2: yL, stroke: C.test, sw: 1.8 });
      else L.push({ t: "poly", pts: [[base, yL], [be - dir * 14, yL], [be, mid]], stroke: C.test, sw: 1.8 });
      L.push({ t: "rect", x: bx, y: g.y, w: g.w, h: g.h, r: 4.5, fill: C.bg, stroke: C.test, sw: 1.6 });
      g.rows.forEach((r, k) => {
        let x = bx + 7; const by = g.y + 3 + k * rowH + rowH / 2;
        r.parts.forEach(p => {
          if (p.glyph) { if (MAIN(p.glyph)) glyph(L, p.glyph, x + 5, by, 3.6, C.ink); else L.push({ t: "text", x: x + 1, y: by + (fT - 1) * 0.35, s: p.glyph, size: fT - 1, fill: C.ink }); x += p.w; }
          else { L.push({ t: "text", x, y: by + p.size * 0.35, s: p.s.replace(/^ /, ""), size: p.size, font: p.font || "n", fill: C.ink, lead: /^ /.test(p.s) ? tw(" ", p.size, p.font) : 0 }); x += tw(p.s, p.size, p.font); }
        });
      });
    });
    return { px, py, pw, ph };
  }
  // text pieces that began with a space carry it as "lead" so both writers place them identically
  function settle(L) { L.forEach(p => { if (p.t === "text" && p.lead) { p.x += p.lead; delete p.lead; } }); return L; }

  /* ---------- screen ---------- */
  function screen(m, W, H, theme, opts) {
    const C = THEMES[theme] || THEMES.dark, L = [{ t: "rect", x: 0, y: 0, w: W, h: H, fill: C.bg }];
    graph(L, m, Object.assign({ x: 6, y: 18, w: W - 10, h: H - 22, fs: W < 420 ? 10 : 11, notes: W >= 640, live: true }, opts || {}), C);
    return { w: W, h: H, list: settle(L) };
  }

  /* ---------- PDF pages (US Letter) ---------- */
  function dateLong(iso) { const x = /^(\d{4}-\d\d-\d\d)(?:T(\d\d:\d\d))?/.exec(iso || ""); return x ? x[1] + (x[2] ? " " + x[2] : "") : ""; }
  function summaryLines(m) {
    const fl = M.flags(m).max, word = m.ref === "height" ? "height" : "depth", out = [];
    if (fl) { const names = { size: "grain size > 1 mm", hard: "hardness below 1F", type: "persistent grain type", dSize: "grain size difference > 0.5 mm", dHard: "hardness difference > 1 step", depth: "interface 20 to 85 cm deep" };
      out.push(["Maximum yellow flags: ", `${fl.n} of 6 at ${M.fmt(M.toRef(m, fl.depth))} cm ${word} (${Object.keys(fl.f).filter(k => fl.f[k]).map(k => names[k]).join("; ")})`]); }
    if (m.tests.length) out.push(["Stability tests: ", m.tests.map(t => { const on = M.layerAt(m, t.depth); return M.testLabel(t) + (M.num(t.depth) != null ? ` at ${M.fmt(M.toRef(m, t.depth))} cm` : "") + (on && on.grain ? ` on ${M.grainLabel(on)}` + (M.sizeLabel(on) ? ` ${M.sizeLabel(on)} mm` : "") : "") + (t.note ? ` (${t.note})` : ""); }).join("; ")]);
    if (m.avs) out.push(["Avalanche observations: ", m.avs]);
    return out;
  }
  function pages(m, o) {
    const C = THEMES.light, PW = 612, PH = 792, mx = 40, L = [], s = m.site, w = m.wx, f = m.surf, n = M.num;
    const title = m.name || s.location || "Snow profile";
    L.push({ t: "text", x: mx, y: 50, s: "Profile Name:", size: 10.5, fill: C.muted });
    L.push({ t: "text", x: mx + tw("Profile Name: ", 10.5), y: 50, s: fit(title, 360, 10.5, "b"), size: 10.5, font: "b", fill: C.ink });
    L.push({ t: "text", x: PW - mx, y: 50, s: dateLong(s.date), size: 10.5, fill: C.muted, anchor: "end" });
    L.push({ t: "line", x1: mx, y1: 60, x2: PW - mx, y2: 60, stroke: C.ink, sw: 0.8 });
    graph(L, m, { x: mx, y: 74, w: PW - 2 * mx, h: 452, fs: 10, notes: true, mirror: !!(o && o.mirror) }, C);
    L.push({ t: "line", x1: mx, y1: 540, x2: PW - mx, y2: 540, stroke: C.ink, sw: 0.8 });
    const sg = M.surfaceGrain(m), d = v => v === "" || v == null ? "--" : v;
    const cols = [
      ["General", [["Date", dateLong(s.date)], ["Observer", s.observer], ["Org", s.org], ["Site", s.location]]],
      ["Location", [["Elevation", n(s.elev) != null ? M.fmt(s.elev) + " " + s.elevUnit : ""], ["Aspect", s.aspect], ["Slope", n(s.slope) != null ? M.fmt(s.slope) + "\u00b0" : ""], ["Lat/Lng", n(s.lat) != null && n(s.lng) != null ? `${(+s.lat).toFixed(4)}, ${(+s.lng).toFixed(4)}` : ""]]],
      ["Weather", [["Air Temperature", n(w.air) != null ? M.fmt(w.air) + " \u00b0C" : ""], ["Sky Cover", w.sky ? M.SKY[w.sky] || w.sky : ""], ["Precipitation", w.precip ? (w.precip === "NIL" ? "None" : w.precip) : ""],
        ["Wind", w.windSpd ? (M.WIND[w.windSpd] || w.windSpd) + (w.windDir ? ", " + w.windDir : "") : ""], ["Blowing Snow", w.blow ? (M.BLOW[w.blow] || w.blow) + (w.blowDir && w.blow !== "NIL" ? ", " + w.blowDir : "") : ""]]],
      ["Snow Conditions", [["Total Snow Depth (HS)", n(f.hs) != null ? M.fmt(f.hs) + " cm" : ""], ["Surface Grain", sg.code ? (g => tw("Surface Grain: " + g, 8.5, "b") <= 146 ? g : sg.code + (sg.size ? ", " + sg.size + " mm" : ""))((M.GRAINS[sg.code] || sg.code) + (sg.size ? " " + sg.size + " mm" : "")) : ""], ["Foot Pen", n(f.foot) != null ? M.fmt(f.foot) + " cm" : ""], ["Ski Pen", n(f.ski) != null ? M.fmt(f.ski) + " cm" : ""],
        ["New Snow", [n(w.hn24) != null ? "HN24 " + M.fmt(w.hn24) + " cm" : "", n(w.hst) != null ? "HST " + M.fmt(w.hst) + " cm" : ""].filter(Boolean).join(", ")]]]
    ];
    const cx = [mx, mx + 126, mx + 252, mx + 386], cw = [122, 122, 130, 146];
    cols.forEach((c, i) => {
      L.push({ t: "text", x: cx[i], y: 556, s: c[0], size: 9.5, font: "b", fill: C.ink });
      c[1].forEach((r, k) => {
        const y = 570 + k * 13, lw = tw(r[0] + ": ", 8.5);
        L.push({ t: "text", x: cx[i], y, s: r[0] + ":", size: 8.5, fill: C.ink });
        L.push({ t: "text", x: cx[i] + lw, y, s: fit(d(r[1]), cw[i] - lw, 8.5, "b"), size: 8.5, font: "b", fill: C.ink });
      });
    });
    L.push({ t: "line", x1: mx, y1: 640, x2: PW - mx, y2: 640, stroke: C.ink, sw: 0.8 });
    let y = 656;
    const para = (label, text, maxLines) => {
      const lw = tw(label, 9, "b"), lines = wrap(text, PW - 2 * mx - lw, 9, "n", maxLines);
      L.push({ t: "text", x: mx, y, s: label, size: 9, font: "b", fill: C.ink });
      lines.forEach((ln, k) => L.push({ t: "text", x: mx + lw, y: y + k * 12, s: ln, size: 9, fill: C.ink }));
      y += Math.max(1, lines.length) * 12 + 3;
    };
    summaryLines(m).forEach(r => para(r[0], r[1], 2));
    para("Notes: ", m.notes || "", Math.max(1, Math.floor((748 - y) / 12)));
    L.push({ t: "line", x1: mx, y1: 754, x2: PW - mx, y2: 754, stroke: C.grid, sw: 0.6 });
    L.push({ t: "text", x: mx, y: 767, s: "Field observation recorded by the observer. Decision support, not an avalanche forecast.", size: 7.5, fill: C.muted });
    L.push({ t: "text", x: PW - mx, y: 767, s: "SNOWPACK TRANSLATOR  |  MANUAL SNOW PROFILE", size: 8, font: "b", fill: C.muted, anchor: "end" });
    const out = [{ w: PW, h: PH, list: settle(L) }];

    if (m.layers.length || m.temps.length || m.tests.length) { // page 2: the numbers behind the graph
      const D = [], word = m.ref === "height" ? "Height" : "Depth"; let yy = 50;
      D.push({ t: "text", x: mx, y: yy, s: "Profile data: ", size: 10.5, fill: C.muted }, { t: "text", x: mx + tw("Profile data: ", 10.5), y: yy, s: fit(title, 380, 10.5, "b"), size: 10.5, font: "b", fill: C.ink });
      D.push({ t: "line", x1: mx, y1: 60, x2: PW - mx, y2: 60, stroke: C.ink, sw: 0.8 }); yy = 80;
      const table = (head, widths, rowsData) => {
        let x = mx; head.forEach((h, i) => { D.push({ t: "text", x, y: yy, s: h, size: 8, font: "b", fill: C.muted }); x += widths[i]; });
        D.push({ t: "line", x1: mx, y1: yy + 4, x2: PW - mx, y2: yy + 4, stroke: C.grid, sw: 0.6 }); yy += 16;
        rowsData.forEach(r => { if (yy > 740) return; let x2 = mx; r.forEach((c, i) => { D.push({ t: "text", x: x2, y: yy, s: fit(c, widths[i] - 6, 9), size: 9, fill: C.ink }); x2 += widths[i]; }); yy += 13; });
        yy += 14;
      };
      if (m.layers.length) table(["#", `Top (cm ${word.toLowerCase()})`, "Bottom", "Hardness", "Grain form", "Size (mm)", "Wetness", "Density", "Notes"], [22, 78, 48, 62, 118, 54, 52, 44, 54],
        m.layers.map((l, i) => [String(i + 1), M.fmt(M.toRef(m, l.top)), M.fmt(M.toRef(m, l.bottom)), M.hardLabel(l), l.grain ? (M.GRAINS[l.grain] || l.grain) + (l.grain2 ? ` (${l.grain2})` : "") : "", M.sizeLabel(l), l.wet ? M.WET[l.wet] : "", l.density ? String(l.density) : "", (l.concern ? "Concern. " : "") + (l.note || "")]));
      if (m.temps.length) table([`${word} (cm)`, "Snow temperature (\u00b0C)"], [90, 200], m.temps.map(t => [M.fmt(M.toRef(m, t.depth)), M.fmt(t.t)]));
      if (m.tests.length) table(["Test", `${word} (cm)`, "On", "Note"], [110, 80, 150, 190], m.tests.map(t => { const on = M.layerAt(m, t.depth); return [M.testLabel(t), M.num(t.depth) != null ? M.fmt(M.toRef(m, t.depth)) : "--", on && on.grain ? (M.GRAINS[on.grain] || on.grain) : "", t.note || ""]; }));
      const fl = M.flags(m).list.filter(o => o.n >= 3);
      if (fl.length) table([`Interface ${word.toLowerCase()} (cm)`, "Yellow flags (of 6)"], [130, 200], fl.map(o => [M.fmt(M.toRef(m, o.depth)), String(o.n)]));
      D.push({ t: "text", x: mx, y: 767, s: "Yellow flags follow the Jamieson and Schweizer (2005) profile checklist. Decision support, not an avalanche forecast.", size: 7.5, fill: C.muted });
      out.push({ w: PW, h: PH, list: D });
    }
    return out;
  }

  /* ---------- SVG writer ---------- */
  const esc = s => String(s == null ? "" : s).replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  const f2 = v => +(+v).toFixed(2);
  function svg(page, label) {
    const FONT = { n: "", b: ' font-weight="700"', i: ' font-style="italic"' };
    let o = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 ${page.w} ${page.h}" width="${page.w}" height="${page.h}" role="img" aria-label="${esc(label || "Snow profile")}" font-family="Helvetica, Arial, sans-serif">`;
    page.list.forEach(p => {
      const st = `fill="${p.fill || "none"}"` + (p.stroke ? ` stroke="${p.stroke}" stroke-width="${p.sw || 1}"` : "") + (p.dash ? ` stroke-dasharray="${p.dash.join(" ")}"` : "");
      const at = p.attr ? Object.keys(p.attr).map(k => ` ${k}="${esc(p.attr[k])}"`).join("") : "";
      if (p.t === "rect") o += `<rect x="${f2(p.x)}" y="${f2(p.y)}" width="${f2(Math.max(0, p.w))}" height="${f2(Math.max(0, p.h))}"${p.r ? ` rx="${p.r}"` : ""} ${st}${at}/>`;
      else if (p.t === "line") o += `<line x1="${f2(p.x1)}" y1="${f2(p.y1)}" x2="${f2(p.x2)}" y2="${f2(p.y2)}" ${st}/>`;
      else if (p.t === "poly") o += `<${p.close ? "polygon" : "polyline"} points="${p.pts.map(q => f2(q[0]) + "," + f2(q[1])).join(" ")}" ${st} stroke-linejoin="round"${at}/>`;
      else if (p.t === "circle") o += `<circle cx="${f2(p.cx)}" cy="${f2(p.cy)}" r="${f2(p.r)}" ${st}/>`;
      else if (p.t === "text") o += `<text x="${f2(p.x)}" y="${f2(p.y)}" font-size="${p.size}"${FONT[p.font || "n"]} fill="${p.fill}"${p.anchor && p.anchor !== "start" ? ` text-anchor="${p.anchor}"` : ""}${p.rot ? ` transform="rotate(${p.rot} ${f2(p.x)} ${f2(p.y)})"` : ""}>${esc(p.s)}</text>`;
    });
    return o + "</svg>";
  }

  /* ---------- PDF writer ---------- */
  const WIN = { 8211: 150, 8212: 151, 8216: 145, 8217: 146, 8220: 147, 8221: 148, 8226: 149, 8230: 133 };
  function pdfStr(s) {
    let o = "";
    for (const ch of String(s)) {
      let c = ch.charCodeAt(0); if (c > 255) c = WIN[c] || 63;
      if (c === 40 || c === 41 || c === 92) o += "\\" + String.fromCharCode(c);
      else if (c < 32) o += " "; else o += String.fromCharCode(c);
    }
    return "(" + o + ")";
  }
  function rgb(hex) { const v = parseInt(hex.slice(1), 16); return [(v >> 16) / 255, ((v >> 8) & 255) / 255, (v & 255) / 255].map(x => +x.toFixed(3)).join(" "); }
  function pdf(pgs, meta) {
    const objs = [null], addObj = body => { objs.push(body); return objs.length - 1; };
    const cat = addObj(""), pagesId = addObj("");
    const fN = addObj("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>");
    const fB = addObj("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>");
    const fI = addObj("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Oblique /Encoding /WinAnsiEncoding >>");
    const FN = { n: "F1", b: "F2", i: "F3" }, kids = [];
    pgs.forEach(pg => {
      const c = [`1 0 0 -1 0 ${pg.h} cm`, "1 j 0 J"];
      const paint = p => (p.fill && p.stroke ? "B" : p.fill ? "f" : "S");
      const style = p => { let s = ""; if (p.fill) s += rgb(p.fill) + " rg "; if (p.stroke) s += rgb(p.stroke) + " RG " + (p.sw || 1) + " w "; s += p.dash ? `[${p.dash.join(" ")}] 0 d` : "[] 0 d"; return s; };
      pg.list.forEach(p => {
        if (p.t === "text") {
          if (!p.s) return;
          const w = tw(p.s, p.size, p.font), off = p.anchor === "middle" ? -w / 2 : p.anchor === "end" ? -w : 0, th = (p.rot || 0) * Math.PI / 180, cs = Math.cos(th), sn = Math.sin(th);
          c.push(`BT /${FN[p.font || "n"]} ${p.size} Tf ${rgb(p.fill || "#000000")} rg ${f2(cs)} ${f2(sn)} ${f2(sn)} ${f2(-cs)} ${f2(p.x + off * cs)} ${f2(p.y + off * sn)} Tm ${pdfStr(p.s)} Tj ET`);
          return;
        }
        if (!p.fill && !p.stroke) return;
        c.push(style(p));
        if (p.t === "rect") {
          const w = Math.max(0, p.w), h = Math.max(0, p.h);
          if (p.r) { const r = Math.min(p.r, w / 2, h / 2), k = r * 0.5523, x = p.x, y = p.y;
            c.push(`${f2(x + r)} ${f2(y)} m ${f2(x + w - r)} ${f2(y)} l ${f2(x + w - r + k)} ${f2(y)} ${f2(x + w)} ${f2(y + r - k)} ${f2(x + w)} ${f2(y + r)} c ${f2(x + w)} ${f2(y + h - r)} l ${f2(x + w)} ${f2(y + h - r + k)} ${f2(x + w - r + k)} ${f2(y + h)} ${f2(x + w - r)} ${f2(y + h)} c ${f2(x + r)} ${f2(y + h)} l ${f2(x + r - k)} ${f2(y + h)} ${f2(x)} ${f2(y + h - r + k)} ${f2(x)} ${f2(y + h - r)} c ${f2(x)} ${f2(y + r)} l ${f2(x)} ${f2(y + r - k)} ${f2(x + r - k)} ${f2(y)} ${f2(x + r)} ${f2(y)} c h ${paint(p)}`);
          } else c.push(`${f2(p.x)} ${f2(p.y)} ${f2(w)} ${f2(h)} re ${paint(p)}`);
        } else if (p.t === "line") c.push(`${f2(p.x1)} ${f2(p.y1)} m ${f2(p.x2)} ${f2(p.y2)} l S`);
        else if (p.t === "poly") c.push(p.pts.map((q, i) => `${f2(q[0])} ${f2(q[1])} ${i ? "l" : "m"}`).join(" ") + (p.close ? " h " : " ") + (p.close ? paint(p) : "S"));
        else if (p.t === "circle") { const r = p.r, k = r * 0.5523, x = p.cx, y = p.cy;
          c.push(`${f2(x + r)} ${f2(y)} m ${f2(x + r)} ${f2(y + k)} ${f2(x + k)} ${f2(y + r)} ${f2(x)} ${f2(y + r)} c ${f2(x - k)} ${f2(y + r)} ${f2(x - r)} ${f2(y + k)} ${f2(x - r)} ${f2(y)} c ${f2(x - r)} ${f2(y - k)} ${f2(x - k)} ${f2(y - r)} ${f2(x)} ${f2(y - r)} c ${f2(x + k)} ${f2(y - r)} ${f2(x + r)} ${f2(y - k)} ${f2(x + r)} ${f2(y)} c h ${paint(p)}`); }
      });
      const stream = c.join("\n"), cid = addObj(`<< /Length ${stream.length} >>\nstream\n${stream}\nendstream`);
      kids.push(addObj(`<< /Type /Page /Parent ${pagesId} 0 R /MediaBox [0 0 ${pg.w} ${pg.h}] /Resources << /Font << /F1 ${fN} 0 R /F2 ${fB} 0 R /F3 ${fI} 0 R >> >> /Contents ${cid} 0 R >>`));
    });
    objs[cat] = `<< /Type /Catalog /Pages ${pagesId} 0 R >>`;
    objs[pagesId] = `<< /Type /Pages /Kids [${kids.map(k => k + " 0 R").join(" ")}] /Count ${kids.length} >>`;
    const mt = meta || {}, info = addObj(`<< /Title ${pdfStr(mt.title || "Snow profile")} /Creator ${pdfStr("Snowpack Translator")} /Producer ${pdfStr("Snowpack Translator")}${mt.date ? " /CreationDate " + pdfStr(mt.date) : ""} >>`);
    let out = "%PDF-1.4\n%\u00e2\u00e3\u00cf\u00d3\n"; const offs = [0];
    for (let i = 1; i < objs.length; i++) { offs[i] = out.length; out += `${i} 0 obj\n${objs[i]}\nendobj\n`; }
    const xref = out.length;
    out += `xref\n0 ${objs.length}\n0000000000 65535 f \n` + offs.slice(1).map(o => String(o).padStart(10, "0") + " 00000 n \n").join("");
    out += `trailer\n<< /Size ${objs.length} /Root ${cat} 0 R /Info ${info} 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
    const bytes = new Uint8Array(out.length); for (let i = 0; i < out.length; i++) bytes[i] = out.charCodeAt(i) & 255;
    return bytes;
  }
  function pdfDate(d) { const p = n => String(n).padStart(2, "0"); return `D:${d.getFullYear()}${p(d.getMonth() + 1)}${p(d.getDate())}${p(d.getHours())}${p(d.getMinutes())}${p(d.getSeconds())}`; }
  function profilePdf(m, now, o) { return pdf(pages(m, o), { title: m.name || m.site.location || "Snow profile", date: pdfDate(now || new Date()) }); }

  return { THEMES, tw, fit, wrap, glyph, graph, screen, pages, svg, pdf, profilePdf, spread, summaryLines };
});
