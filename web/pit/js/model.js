/* Snowpack Translator: the observation (site, weather, layers, temperatures, tests), how spoken actions change it,
   the structural yellow-flag count, and the compact satellite text. Plain rules only.
   Depths are stored in cm below the snow surface; "ref" says how the observer reads and speaks them. */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.SnowModel = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  const GRAINS = {
    PP: "Precipitation particles", PPgp: "Graupel", PPsd: "Stellar dendrites", MM: "Machine made", DF: "Decomposing fragments",
    RG: "Rounded grains", RGsr: "Small rounds", RGlr: "Large rounds", RGwp: "Wind packed", RGxf: "Faceted rounds",
    FC: "Faceted crystals", FCsf: "Near-surface facets", FCxr: "Rounding facets", DH: "Depth hoar", SH: "Surface hoar",
    MF: "Melt forms", MFcr: "Melt-freeze crust", IF: "Ice formation", IFrc: "Rain crust", IFsc: "Sun crust"
  };
  const HARD = ["F-", "F", "F+", "4F-", "4F", "4F+", "1F-", "1F", "1F+", "P-", "P", "P+", "K-", "K", "K+", "I"];
  const HBASE = { F: 1, "4F": 2, "1F": 3, P: 4, K: 5, I: 6 };
  const WET = { D: "Dry", M: "Moist", W: "Wet", V: "Very wet", S: "Soaked" };
  const SKY = { CLR: "Clear", FEW: "Few", SCT: "Scattered", BKN: "Broken", OVC: "Overcast", X: "Obscured" };
  const WIND = { C: "Calm", L: "Light", M: "Moderate", S: "Strong", X: "Extreme", VAR: "Variable" };
  const PRECIP = { NIL: "No precipitation", "S-1": "Snow, very light", S1: "Snow, light (1 cm/h)", S2: "Snow, moderate (2 cm/h)", S5: "Snow, heavy (5 cm/h)", S10: "Snow, very heavy (10 cm/h)", RV: "Rain, very light", RL: "Rain, light", RM: "Rain, moderate", RH: "Rain, heavy" };
  const BLOW = { NIL: "None", Prev: "Previous", L: "Light", M: "Moderate", I: "Intense" };

  function hIndex(h) {
    const m = /^(4F|1F|F|P|K|I)([+-]?)$/.exec(h || ""); if (!m) return 0;
    return HBASE[m[1]] + (m[2] === "+" ? 1 / 3 : m[2] === "-" ? -1 / 3 : 0);
  }
  const num = v => (v === "" || v == null || isNaN(+v)) ? null : +v;
  const fmt = v => { const n = num(v); return n == null ? "" : String(+n.toFixed(1)); };
  const fmtSize = v => { const n = num(v); return n == null ? "" : n.toFixed(n < 10 && Math.round(n * 10) !== n * 10 ? 2 : 1); };

  function blank() {
    return {
      v: 1, id: "o" + Date.now().toString(36) + Math.random().toString(36).slice(2, 6), name: "", ref: "depth", cur: null, uid: 1,
      site: { location: "", date: "", observer: "", org: "", elev: "", elevUnit: "m", aspect: "", slope: "", lat: "", lng: "" },
      wx: { air: "", tmax: "", tmin: "", sky: "", precip: "", windSpd: "", windDir: "", windKmh: "", blow: "", blowDir: "", hn24: "", hst: "" },
      surf: { hs: "", foot: "", ski: "", grain: "", size: "" },
      layers: [], temps: [], tests: [], avs: "", notes: ""
    };
  }

  /* ---------- geometry ---------- */
  function pitBottom(m) {
    let b = 0;
    m.layers.forEach(l => { if (l.bottom > b) b = l.bottom; });
    m.temps.forEach(t => { if (t.depth > b) b = t.depth; });
    m.tests.forEach(t => { if (num(t.depth) != null && t.depth > b) b = t.depth; });
    return b;
  }
  function hsOf(m) { const h = num(m.surf.hs); return h != null && h > 0 ? Math.max(h, pitBottom(m)) : pitBottom(m); }
  const toRef = (m, d) => m.ref === "height" ? +(hsOf(m) - d).toFixed(1) : +(+d).toFixed(1);
  const fromRef = (m, v) => m.ref === "height" ? hsOf(m) - v : v;
  function shiftAll(m, by) {
    if (!by) return;
    m.layers.forEach(l => { l.top += by; l.bottom += by; });
    m.temps.forEach(t => { t.depth += by; });
    m.tests.forEach(t => { if (num(t.depth) != null) t.depth += by; });
  }
  // In height mode the heights the observer spoke stay fixed, so a new HS moves the stored depths.
  function setHS(m, hs) {
    const old = num(m.surf.hs);
    if (m.ref === "height" && old != null && (m.layers.length || m.temps.length || m.tests.length)) shiftAll(m, hs - old);
    m.surf.hs = hs;
  }
  // In height mode a lower HS lifts every stored depth: the recorded height that would end up above the new surface, else null.
  function hsConflict(m, hs) {
    const old = num(m.surf.hs); if (m.ref !== "height" || old == null) return null;
    let top = null; const see = d => { if (num(d) != null && (top == null || d < top)) top = d; };
    m.layers.forEach(l => see(l.top)); m.temps.forEach(t => see(t.depth)); m.tests.forEach(t => see(t.depth));
    return top != null && top + (hs - old) < -0.01 ? +(old - top).toFixed(1) : null;
  }
  function setRef(m, ref) {
    if (ref === m.ref) return;
    if (ref === "height" && num(m.surf.hs) == null && pitBottom(m) > 0) m.surf.hs = pitBottom(m);
    m.ref = ref;
  }
  function spokenDepth(m, v) { // a spoken depth or height -> stored depth
    if (m.ref !== "height") return v;
    const hs = num(m.surf.hs);
    if (hs == null || v > hs) setHS(m, v);
    return num(m.surf.hs) - v;
  }

  /* ---------- labels ---------- */
  function hardLabel(l) { return !l.hardTop ? "" : l.hardBot && l.hardBot !== l.hardTop ? `${l.hardTop} to ${l.hardBot}` : l.hardTop; }
  function sizeLabel(l) {
    const a = num(l.size), b = num(l.sizeMax);
    if (a == null && b == null) return "";
    if (a != null && b != null && b !== a) return `${fmtSize(a)}-${fmtSize(b)}`;
    return fmtSize(a != null ? a : b);
  }
  function grainLabel(l) { return !l.grain ? "" : l.grain + (l.grain2 ? `(${l.grain2})` : ""); }
  function layerSay(m, l) {
    const bits = [hardLabel(l), l.grain ? (GRAINS[l.grain] || l.grain).toLowerCase() + (l.grain2 ? " with " + (GRAINS[l.grain2] || l.grain2).toLowerCase() : "") : "",
      sizeLabel(l) ? sizeLabel(l) + " mm" : "", l.wet && l.wet !== "D" ? WET[l.wet].toLowerCase() : "", l.density ? l.density + " kg/m³" : "",
      l.concern ? "layer of concern" : "", l.note ? `“${l.note}”` : ""].filter(Boolean);
    return `Layer ${fmt(toRef(m, l.top))}–${fmt(toRef(m, l.bottom))} cm` + (bits.length ? ": " + bits.join(", ") : "");
  }
  function testLabel(t) {
    if (t.type === "ECT") return "ECT" + (t.prop || "") + (t.prop === "X" || t.prop === "PV" || t.score == null ? "" : t.score);
    if (t.type === "PST") return "PST" + (t.cut != null && t.col != null ? `${t.cut}/${t.col}` : "") + (t.res ? `(${t.res})` : "");
    if (t.type === "RB") return "RB" + (t.score != null ? t.score : "") + (t.rel ? `(${t.rel})` : "");
    const base = t.type + (t.none ? "N" : t.score != null ? t.score : (t.band || ""));
    return base + (t.char || "") + (t.q ? (t.char ? " " : "") + t.q : "");
  }
  function layerAt(m, d) { // the layer a test at depth d sits on (the one below the interface)
    if (num(d) == null) return null;
    return m.layers.find(l => d >= l.top - 0.01 && d < l.bottom - 0.01) || m.layers.find(l => Math.abs(l.bottom - d) < 0.01) || null;
  }
  function surfaceGrain(m) {
    const top = m.layers[0];
    const g = m.surf.grain || (top && top.top < 0.5 ? top.grain : ""), s = num(m.surf.size) != null ? fmtSize(m.surf.size) : (top && top.top < 0.5 && !m.surf.grain ? sizeLabel(top) : "");
    return { code: g, size: s };
  }

  /* ---------- applying spoken actions ---------- */
  const FIELD = {
    "name": ["Profile name"], "site.location": ["Location"], "site.observer": ["Observer"], "site.org": ["Organization"],
    "site.aspect": ["Aspect"], "site.elev": ["Elevation"], "site.slope": ["Slope", "°"], "surf.hs": ["Snow depth (HS)", " cm"],
    "wx.air": ["Air temperature", " °C"], "wx.tmax": ["Max temperature", " °C"], "wx.tmin": ["Min temperature", " °C"],
    "wx.sky": ["Sky", "", SKY], "wx.precip": ["Precipitation", "", PRECIP], "wx.windSpd": ["Wind", "", WIND], "wx.windDir": ["Wind from"],
    "wx.blow": ["Blowing snow", "", BLOW], "wx.blowDir": ["Blowing snow from"], "wx.hn24": ["New snow, 24 h (HN24)", " cm"],
    "wx.hst": ["Storm snow (HST)", " cm"], "surf.foot": ["Foot penetration", " cm"], "surf.ski": ["Ski penetration", " cm"],
    "surf.grain": ["Surface grain", "", GRAINS], "surf.size": ["Surface grain size", " mm"]
  };

  function putLayer(m, top, bottom, attrs) {
    top = Math.max(0, +top.toFixed(1)); bottom = +bottom.toFixed(1);
    if (bottom < top) { const s = top; top = bottom; bottom = s; }
    if (bottom - top < 0.05) return { err: "That layer has no thickness." };
    let L = m.layers.find(l => Math.abs(l.top - top) < 0.25 && Math.abs(l.bottom - bottom) < 0.25), changed = 0;
    if (!L) {
      const keep = [];
      m.layers.forEach(l => {
        if (l.top >= top - 0.01 && l.bottom <= bottom + 0.01) { changed++; return; }
        if (l.top < top && l.bottom > bottom) { keep.push(Object.assign({}, l, { id: m.uid++, top: bottom })); l.bottom = top; changed++; }
        else if (l.top < top && l.bottom > top) { l.bottom = top; changed++; }
        else if (l.top < bottom && l.bottom > bottom) { l.top = bottom; changed++; }
        keep.push(l);
      });
      L = { id: m.uid++, top, bottom, hardTop: "", hardBot: "", grain: "", grain2: "", size: "", sizeMax: "", wet: "", density: "", note: "", concern: false };
      keep.push(L); m.layers = keep.sort((a, b) => a.top - b.top);
    }
    setAttrs(L, attrs); m.cur = L.id;
    return { layer: L, changed };
  }
  function setAttrs(L, a) {
    if (!a) return;
    if (a.hardTop) { L.hardTop = a.hardTop; L.hardBot = a.hardBot || ""; }
    if (a.grain) { L.grain = a.grain; L.grain2 = a.grain2 || ""; }
    if (a.size != null) { L.size = a.size; L.sizeMax = a.sizeMax != null ? a.sizeMax : ""; }
    if (a.wet) L.wet = a.wet;
    if (a.density != null) L.density = a.density;
    if (a.note) L.note = a.note;
    if (a.concern) L.concern = true;
  }

  function applyOne(m, a) {
    const bad = (say, hint) => ({ ok: false, say, hint });
    const extra = a.unknown && a.unknown.length ? ` Not understood: “${a.unknown.join(" ")}”.` : "";
    if (a.type === "unknown") return bad(`Not understood: “${a.text}”`, a.hint);
    if (a.type === "undo") return { ok: true, undo: true, say: "Undo" };
    if (a.type === "set") {
      const [obj, key] = a.key.indexOf(".") > 0 ? a.key.split(".") : [null, a.key];
      if (a.key === "surf.hs") {
        const over = hsConflict(m, a.value);
        if (over != null) return bad(`Snow depth ${fmt(a.value)} cm is lower than the ${fmt(over)} cm already recorded.`, "Fix that height first, or say the snow depth again.");
        setHS(m, a.value);
      } else if (obj) m[obj][key] = a.value; else m[key] = a.value;
      if (a.key === "wx.windSpd" && a.value === "C") m.wx.windDir = "";
      if (a.quiet) return { ok: true, quiet: true };
      const f = FIELD[a.key] || [a.key], shown = f[2] && f[2][a.value] ? `${a.value} (${f[2][a.value].toLowerCase()})` : a.value + (a.key === "site.elev" ? " " + m.site.elevUnit : (f[1] || ""));
      return { ok: true, say: `${f[0]}: ${shown}` };
    }
    if (a.type === "text") {
      m[a.key] = m[a.key] ? m[a.key].replace(/[.;\s]+$/, "") + "; " + a.text : a.text;
      return { ok: true, say: (a.key === "avs" ? "Avalanches: " : "Note: ") + a.text };
    }
    if (a.type === "temp") {
      const d = +spokenDepth(m, a.depth).toFixed(1);
      if (d < 0) return bad(`Temperature at ${a.depth} cm is above the snow surface.`);
      m.temps = m.temps.filter(t => Math.abs(t.depth - d) > 0.01); m.temps.push({ id: m.uid++, depth: d, t: a.t }); m.temps.sort((x, y) => x.depth - y.depth);
      return { ok: true, say: `Temperature ${fmt(a.t)} °C at ${fmt(a.depth)} cm` };
    }
    if (a.type === "test") {
      const t = { id: m.uid++, type: a.test, score: a.score != null ? a.score : null, depth: a.depth != null ? +spokenDepth(m, a.depth).toFixed(1) : null, note: a.note || "" };
      ["char", "q", "prop", "none", "band", "cut", "col", "res", "rel"].forEach(k => { if (a[k] != null) t[k] = a[k]; });
      if (a.test === "ECT" && !t.prop && t.score != null) { m.tests.push(t); return { ok: true, warn: true, say: `${testLabel(t)}: say “propagates” or “no propagation”.` + extra }; }
      if (a.test !== "ECT" && a.test !== "PST" && t.score == null && !t.none && !t.band) return bad(`Test not understood: “${a.test} ${(a.unknown || []).join(" ")}”`, "Say the score, for example “compression test 21 sudden planar down 88”.");
      m.tests.push(t);
      const on = layerAt(m, t.depth);
      return { ok: true, warn: t.depth == null || !!extra, say: testLabel(t) + (t.depth != null ? ` at ${fmt(a.depth)} cm` + (on && on.grain ? ` on ${(GRAINS[on.grain] || on.grain).toLowerCase()}` : "") : ": say “down” and the depth to place it on the graph.") + extra };
    }
    if (a.type === "dellayer") {
      const i = a.index != null ? a.index - 1 : m.layers.findIndex(l => l.id === m.cur);
      if (!m.layers[i]) return bad("There is no such layer to delete.");
      const L = m.layers.splice(i, 1)[0]; m.cur = null;
      return { ok: true, say: `Deleted layer ${fmt(toRef(m, L.top))}–${fmt(toRef(m, L.bottom))} cm` };
    }
    if (a.type === "layer") {
      const empty = !Object.keys(a.attrs || {}).length;
      if (a.mode === "current" || a.mode === "index") {
        const L = a.mode === "index" ? m.layers[a.index - 1] : (m.layers.find(l => l.id === m.cur) || m.layers[m.layers.length - 1]);
        if (!L) return bad(a.mode === "index" ? `There is no layer ${a.index}.` : "No layer yet. Start with its depths, for example “layer 0 to 35 fist rounds”.");
        if (empty) return bad("Nothing to change on that layer." + extra);
        setAttrs(L, a.attrs); m.cur = L.id;
        return { ok: true, warn: !!extra, say: layerSay(m, L) + extra + (a.heardFirst ? " (heard “first” as fist)" : "") };
      }
      let top, bottom;
      const prev = () => { const c = m.layers.find(l => l.id === m.cur) || m.layers[m.layers.length - 1]; return c ? c.bottom : 0; };
      if (a.mode === "range") {
        if (m.ref === "height") { spokenDepth(m, Math.max(a.a, a.b)); top = num(m.surf.hs) - a.a; bottom = num(m.surf.hs) - a.b; }
        else { top = a.a; bottom = a.b; }
      } else if (a.mode === "thick") { top = prev(); bottom = top + a.b; }
      else { // "to": from the last boundary to the spoken one
        if (m.ref === "height" && num(m.surf.hs) == null) return bad("Say the snow depth first, or give both heights: “layer 150 to 141”.");
        top = prev(); bottom = m.ref === "height" ? num(m.surf.hs) - a.b : a.b;
      }
      const r = putLayer(m, top, bottom, a.attrs);
      if (r.err) return bad(r.err);
      return { ok: true, warn: !!extra, say: layerSay(m, r.layer) + (r.changed ? " (adjusted the layers it overlaps)" : "") + extra + (a.heardFirst ? " (heard “first” as fist)" : "") };
    }
    return bad("Not understood.");
  }
  function apply(m, actions) { return actions.map(a => applyOne(m, a)).filter(r => !r.quiet); }

  /* ---------- structural yellow flags (Jamieson and Schweizer 2005 checklist) ---------- */
  const PERSISTENT = /^(FC|DH|SH)/;
  function layerHard(l) { const a = hIndex(l.hardTop), b = hIndex(l.hardBot || l.hardTop); return a ? (a + (b || a)) / 2 : 0; }
  function layerSize(l) { const a = num(l.size), b = num(l.sizeMax); return a != null && b != null ? (a + b) / 2 : a != null ? a : b; }
  function flags(m) {
    const out = [];
    for (let i = 0; i + 1 < m.layers.length; i++) {
      const up = m.layers[i], lo = m.layers[i + 1];
      if (Math.abs(up.bottom - lo.top) > 0.5) continue;
      const side = l => { const s = layerSize(l), h = layerHard(l); return [s != null && s > 1, h > 0 && h < 3 - 1e-6, PERSISTENT.test(l.grain || "") || PERSISTENT.test(l.grain2 || "")]; };
      const A = side(up), B = side(lo), pick = B.filter(Boolean).length >= A.filter(Boolean).length ? B : A;
      const s1 = layerSize(up), s2 = layerSize(lo), h1 = layerHard(up), h2 = layerHard(lo), d = up.bottom;
      const f = { size: pick[0], hard: pick[1], type: pick[2], dSize: s1 != null && s2 != null && Math.abs(s1 - s2) > 0.5 + 1e-6, dHard: h1 > 0 && h2 > 0 && Math.abs(h1 - h2) > 1 + 1e-6, depth: d >= 20 && d <= 85 };
      out.push({ depth: d, n: Object.keys(f).filter(k => f[k]).length, f });
    }
    let max = null; out.forEach(o => { if (!max || o.n > max.n) max = o; });
    return { list: out, max: max && max.n ? max : null };
  }

  /* ---------- satellite text ---------- */
  const MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  function shortDate(iso) {
    const m = /^(\d{4})-(\d\d)-(\d\d)(?:T(\d\d):(\d\d))?/.exec(iso || ""); if (!m) return "";
    return `${+m[3]}${MON[+m[2] - 1]}` + (m[4] ? ` ${m[4]}${m[5]}` : "");
  }
  const GSM = "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà";
  function plain(s) { // keep to characters every SMS path carries as one byte
    return String(s || "").replace(/[‘’]/g, "'").replace(/[“”]/g, '"').replace(/[–—−]/g, "-").replace(/°/g, "").replace(/³/g, "3")
      .replace(/\s+/g, " ").trim();
  }
  function textInfo(s) {
    let gsm = true, len = 0;
    for (const ch of s) { if (GSM.indexOf(ch) >= 0) len++; else if ("^{}\\[~]|€".indexOf(ch) >= 0) len += 2; else { gsm = false; } }
    if (!gsm) { const n = Array.from(s).length; return { chars: n, gsm: false, parts: n <= 70 ? 1 : Math.ceil(n / 67) }; }
    return { chars: len, gsm: true, parts: len <= 160 ? 1 : Math.ceil(len / 153) };
  }
  const sg = v => { const n = num(v); return n == null ? "" : (n > 0 ? "+" : "") + fmt(n); };
  function satText(m, o) {
    o = Object.assign({ pos: true, wx: true, snow: true, pit: true, avs: true, notes: true }, o || {});
    const s = m.site, w = m.wx, f = m.surf, parts = [];
    const head = [shortDate(s.date), s.location, num(s.elev) != null ? fmt(s.elev) + s.elevUnit : "", s.aspect, num(s.slope) != null ? fmt(s.slope) + "deg" : ""].filter(Boolean);
    if (o.pos && num(s.lat) != null && num(s.lng) != null) head.push(`${(+s.lat).toFixed(4)},${(+s.lng).toFixed(4)}`);
    if (head.length) parts.push(head.join(" "));
    if (o.wx) {
      const wind = w.windSpd ? "Wind " + w.windSpd + (w.windDir && w.windSpd !== "C" ? " " + w.windDir : "") : "";
      const x = [w.sky ? "Sky " + w.sky : "", w.precip ? "Pcp " + w.precip : "", wind, num(w.air) != null ? "Ta " + sg(w.air) : "",
        num(w.tmax) != null ? "Tx " + sg(w.tmax) : "", num(w.tmin) != null ? "Tn " + sg(w.tmin) : "",
        w.blow ? "BS " + w.blow + (w.blowDir && w.blow !== "NIL" ? " " + w.blowDir : "") : ""].filter(Boolean);
      if (x.length) parts.push(x.join(", "));
    }
    if (o.snow) {
      const sgn = surfaceGrain(m);
      const x = [num(f.hs) != null ? "HS " + fmt(f.hs) : "", num(w.hn24) != null ? "HN24 " + fmt(w.hn24) : "", num(w.hst) != null ? "HST " + fmt(w.hst) : "",
        num(f.foot) != null ? "PF " + fmt(f.foot) : "", num(f.ski) != null ? "PS " + fmt(f.ski) : "", sgn.code ? "Surf " + sgn.code + (sgn.size ? " " + sgn.size : "") : ""].filter(Boolean);
      if (x.length) parts.push(x.join(", "));
    }
    if (o.pit && (m.tests.length || m.layers.length)) {
      const word = m.ref === "height" ? "up" : "dn";
      const x = m.tests.map(t => { const on = layerAt(m, t.depth); return testLabel(t).replace(/ /g, "") + (num(t.depth) != null ? ` ${word}${fmt(toRef(m, t.depth))}` : "") + (on && on.grain ? " " + on.grain + (sizeLabel(on) ? sizeLabel(on) : "") : ""); });
      const fl = flags(m).max; if (fl && fl.n >= 3) x.push(`${fl.n} flags ${word}${fmt(toRef(m, fl.depth))}`);
      if (!x.length && m.layers.length) x.push(`${m.layers.length} layers to ${fmt(pitBottom(m))}cm`);
      if (x.length) parts.push("Pit: " + x.join("; "));
    }
    if (o.avs && m.avs) parts.push("Avs: " + m.avs);
    if (o.notes && m.notes) parts.push("Note: " + m.notes);
    return plain(parts.map(p => p.replace(/[.\s]+$/, "")).join(". "));
  }
  function smsLink(to, body, ios) {
    const num = String(to || "").replace(/[^\d+,]/g, "");
    return "sms:" + num + (ios ? "&" : "?") + "body=" + encodeURIComponent(body);
  }

  /* ---------- CAAML 6.0.3 (same shape as the photo page writes) ---------- */
  function caaml(m, now) {
    const X = v => String(v == null ? "" : v).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const st = m.site, id = "pit" + (m.id || "").replace(/[^a-z0-9]/gi, ""), n1 = v => +(+v).toFixed(1);
    now = now || new Date();
    const when = st.date && !isNaN(new Date(st.date)) ? new Date(st.date).toISOString() : now.toISOString();
    const layers = m.layers.map(L => {
      const cm = [L.note, L.concern ? "Layer of concern." : "", L.hardBot && L.hardBot !== L.hardTop ? `Hardness ${L.hardTop} at the top to ${L.hardBot} at the bottom.` : ""].filter(Boolean).join(" ");
      let x = `        <caaml:Layer>\n` + (cm ? `          <caaml:metaData>\n            <caaml:comment>${X(cm)}</caaml:comment>\n          </caaml:metaData>\n` : "") +
        `          <caaml:depthTop uom="cm">${n1(L.top)}</caaml:depthTop>\n          <caaml:thickness uom="cm">${n1(L.bottom - L.top)}</caaml:thickness>\n`;
      if (L.grain) x += `          <caaml:grainFormPrimary>${X(L.grain)}</caaml:grainFormPrimary>\n`;
      if (L.grain2) x += `          <caaml:grainFormSecondary>${X(L.grain2)}</caaml:grainFormSecondary>\n`;
      if (num(L.size) != null || num(L.sizeMax) != null) {
        x += `          <caaml:grainSize uom="mm">\n            <caaml:Components>\n`;
        if (num(L.size) != null) x += `              <caaml:avg>${+L.size}</caaml:avg>\n`;
        if (num(L.sizeMax) != null) x += `              <caaml:avgMax>${+L.sizeMax}</caaml:avgMax>\n`;
        x += `            </caaml:Components>\n          </caaml:grainSize>\n`;
      }
      if (L.hardTop) x += `          <caaml:hardness uom="">${X(L.hardTop)}</caaml:hardness>\n`;
      if (L.wet) x += `          <caaml:wetness uom="">${X(L.wet)}</caaml:wetness>\n`;
      return x + `        </caaml:Layer>\n`;
    }).join("");
    const temps = m.temps.map(t => `        <caaml:Obs>\n          <caaml:depth>${n1(t.depth)}</caaml:depth>\n          <caaml:snowTemp>${+t.t}</caaml:snowTemp>\n        </caaml:Obs>\n`).join("");
    const tests = m.tests.map(t => {
      const lay = num(t.depth) != null ? `          <caaml:Layer>\n            <caaml:depthTop uom="cm">${n1(t.depth)}</caaml:depthTop>\n          </caaml:Layer>\n` : "";
      const fc = t.char ? `            <caaml:fractureCharacter>${X(t.char)}</caaml:fractureCharacter>\n` : "";
      const cm = t.note || t.q ? `          <caaml:metaData>\n            <caaml:comment>${X([t.q, t.note].filter(Boolean).join(" "))}</caaml:comment>\n          </caaml:metaData>\n` : "";
      if (t.type === "ECT") {
        if (t.prop === "X") return `        <caaml:ExtColumnTest>\n          <caaml:noFailure/>\n        </caaml:ExtColumnTest>\n`;
        return `        <caaml:ExtColumnTest>\n${cm}${lay}          <caaml:Results>\n            <caaml:testScore>${X(testLabel(t))}</caaml:testScore>\n          </caaml:Results>\n        </caaml:ExtColumnTest>\n`;
      }
      if (t.type === "CT") {
        if (t.none) return `        <caaml:ComprTest>\n          <caaml:noFailure/>\n        </caaml:ComprTest>\n`;
        return `        <caaml:ComprTest>\n${cm}${lay}          <caaml:Results>\n${fc}            <caaml:testScore>${X(t.score != null ? t.score : (t.band || ""))}</caaml:testScore>\n          </caaml:Results>\n        </caaml:ComprTest>\n`;
      }
      if (t.type === "PST") return `        <caaml:PropSawTest>\n${cm}${lay}          <caaml:Results>\n            <caaml:fracturePropagation>${X(t.res || "End")}</caaml:fracturePropagation>\n            <caaml:cutLength uom="cm">${t.cut != null ? t.cut : ""}</caaml:cutLength>\n            <caaml:columnLength uom="cm">${t.col != null ? t.col : ""}</caaml:columnLength>\n          </caaml:Results>\n        </caaml:PropSawTest>\n`;
      if (t.type === "RB") return `        <caaml:RBlockTest>\n${cm}${lay}          <caaml:Results>\n${t.rel ? `            <caaml:releaseType>${X(t.rel)}</caaml:releaseType>\n` : ""}            <caaml:testScore>RB${X(t.score)}</caaml:testScore>\n          </caaml:Results>\n        </caaml:RBlockTest>\n`;
      return ""; // deep tap tests have no CAAML 6 element; they stay in the comment below
    }).join("");
    const extra = m.tests.filter(t => t.type === "DT").map(t => `${testLabel(t)}${num(t.depth) != null ? " at " + n1(t.depth) + " cm depth" : ""}`).join("; ");
    const loc = [`    <caaml:name>${X(st.location || m.name || "Unnamed pit")}</caaml:name>`, `    <caaml:obsPointSubType>Snow pit</caaml:obsPointSubType>`];
    if (num(st.elev) != null) loc.push(`    <caaml:validElevation>\n      <caaml:ElevationPosition uom="m">\n        <caaml:position>${Math.round(st.elevUnit === "ft" ? st.elev * 0.3048 : +st.elev)}</caaml:position>\n      </caaml:ElevationPosition>\n    </caaml:validElevation>`);
    if (st.aspect) loc.push(`    <caaml:validAspect>\n      <caaml:AspectPosition>\n        <caaml:position>${X(st.aspect)}</caaml:position>\n      </caaml:AspectPosition>\n    </caaml:validAspect>`);
    if (num(st.slope) != null) loc.push(`    <caaml:validSlopeAngle>\n      <caaml:SlopeAnglePosition uom="deg">\n        <caaml:position>${+st.slope}</caaml:position>\n      </caaml:SlopeAnglePosition>\n    </caaml:validSlopeAngle>`);
    if (num(st.lat) != null && num(st.lng) != null) loc.push(`    <caaml:pointLocation>\n      <gml:Point gml:id="${id}-pt" srsName="urn:ogc:def:crs:OGC:1.3:CRS84" srsDimension="2">\n        <gml:pos>${+st.lng} ${+st.lat}</gml:pos>\n      </gml:Point>\n    </caaml:pointLocation>`);
    const comment = [m.notes, m.avs ? "Avalanche observations: " + m.avs + "." : "", extra ? "Deep tap tests: " + extra + "." : "", "Recorded by the observer with Snowpack Translator (spoken or typed entry)."].filter(Boolean).join(" ");
    return `<?xml version="1.0" encoding="UTF-8"?>
<caaml:SnowProfile gml:id="${id}" xmlns:caaml="http://caaml.org/Schemas/SnowProfileIACS/v6.0.3" xmlns:gml="http://www.opengis.net/gml" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
  <caaml:metaData>
    <caaml:comment>${X(comment)}</caaml:comment>
  </caaml:metaData>
  <caaml:timeRef>
    <caaml:recordTime>
      <caaml:TimeInstant>
        <caaml:timePosition>${when}</caaml:timePosition>
      </caaml:TimeInstant>
    </caaml:recordTime>
    <caaml:dateTimeReport>${now.toISOString()}</caaml:dateTimeReport>
  </caaml:timeRef>
  <caaml:srcRef>
    <caaml:Person gml:id="${id}-obs">
      <caaml:name>${X(st.observer || "Unknown")}</caaml:name>
    </caaml:Person>
  </caaml:srcRef>
  <caaml:locRef gml:id="${id}-loc">
${loc.join("\n")}
  </caaml:locRef>
  <caaml:snowProfileResultsOf>
    <caaml:SnowProfileMeasurements dir="top down">
      <caaml:profileDepth uom="cm">${n1(pitBottom(m))}</caaml:profileDepth>
      <caaml:hS>
        <caaml:Components>
          <caaml:height uom="cm">${n1(hsOf(m))}</caaml:height>
        </caaml:Components>
      </caaml:hS>
      <caaml:stratProfile>
${layers}      </caaml:stratProfile>
${temps ? `      <caaml:tempProfile uomDepth="cm" uomTemp="degC">\n${temps}      </caaml:tempProfile>\n` : ""}${tests ? `      <caaml:stbTests>\n${tests}      </caaml:stbTests>\n` : ""}    </caaml:SnowProfileMeasurements>
  </caaml:snowProfileResultsOf>
</caaml:SnowProfile>
`;
  }

  return { caaml, GRAINS, HARD, WET, SKY, WIND, PRECIP, BLOW, hIndex, num, fmt, fmtSize, blank, pitBottom, hsOf, toRef, fromRef, setHS, hsConflict, setRef, spokenDepth,
    hardLabel, sizeLabel, grainLabel, layerSay, testLabel, layerAt, surfaceGrain, putLayer, setAttrs, apply, flags, layerHard, layerSize,
    shortDate, satText, textInfo, smsLink, plain };
});
