/* Snowpack Translator, voice page: speech (or typing) in, snow profile + PDF + satellite text out.
   Laid out like a field recorder: Info / Profile / Sat text tabs, a speak bar, and bottom sheets for a layer,
   a test result and a temperature. Everything stays on the phone: observations are kept in this browser's
   storage and nothing is sent anywhere. */
(function () {
  "use strict";
  const P = window.SnowSpeak, M = window.SnowModel, D = window.SnowDraw;
  const $ = id => document.getElementById(id);
  const $$ = (q, root) => Array.from((root || document).querySelectorAll(q));
  const esc = s => String(s == null ? "" : s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const KEY = "snowpack-voice-v1", SET = "snowpack-voice-settings-v1";
  const IOS = /iphone|ipad|ipod/i.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const DIRS = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"];
  let store = { cur: null, list: [] };
  let cfg = { to: "", sat: {}, auto: true, tab: "profile", observer: "", org: "", elevUnit: "m", ref: "height", mirror: true, theme: "dark" };
  let m = null, log = [], undo = [], redo = [], sel = -1, satManual = null, tab = "profile", editTag = null;

  function toast(msg) { // shown inside the open sheet, if any, so a sheet never covers it
    const t = $("toast"), host = $$("dialog[open]").pop() || document.body;
    if (t.parentNode !== host) host.appendChild(t);
    t.textContent = msg; t.hidden = false; clearTimeout(toast.t); toast.t = setTimeout(() => { t.hidden = true; }, 3400);
  }
  function nowLocal() { const d = new Date(); d.setMinutes(d.getMinutes() - d.getTimezoneOffset()); return d.toISOString().slice(0, 16); }
  const press = (q, fn) => $$(q).forEach(b => b.setAttribute("aria-pressed", String(!!fn(b))));
  const setVal = (id, v) => { const el = $(id); if (el !== document.activeElement) el.value = v == null ? "" : v; };
  const numVal = el => (M.num(el.value) == null ? "" : +el.value);
  const isHeight = () => m.ref === "height";
  const hasData = () => m.layers.length > 0 || m.temps.length > 0 || m.tests.length > 0;

  /* ---------- storage ---------- */
  function entry() { return store.list.find(e => e.m.id === store.cur); }
  let saveT = null;
  function flush() {
    clearTimeout(saveT); saveT = null;
    const e = entry(); if (e) { e.m = m; e.log = log; }
    try { localStorage.setItem(KEY, JSON.stringify(store)); localStorage.setItem(SET, JSON.stringify(cfg)); }
    catch (err) { toast("This phone's storage is full. Export the PDF, then delete old observations."); }
  }
  function persist() { clearTimeout(saveT); saveT = setTimeout(flush, 300); }
  window.addEventListener("pagehide", () => { if (saveT) flush(); });
  document.addEventListener("visibilitychange", () => { if (document.hidden && saveT) flush(); });
  function newObs() {
    m = M.blank(); m.site.date = nowLocal(); m.site.observer = cfg.observer || ""; m.site.org = cfg.org || ""; m.site.elevUnit = cfg.elevUnit || "m"; m.ref = cfg.ref === "depth" ? "depth" : "height";
    log = []; undo = []; redo = []; sel = -1; satManual = null; editTag = null;
    store.list.unshift({ m, log }); store.cur = m.id;
  }
  function open(id) { const e = store.list.find(x => x.m.id === id); if (!e) return; store.cur = id; m = e.m; log = e.log || (e.log = []); undo = []; redo = []; sel = -1; satManual = null; editTag = null; }
  function load() {
    try { cfg = Object.assign(cfg, JSON.parse(localStorage.getItem(SET) || "{}")); } catch (e) { /* first run */ }
    try { const s = JSON.parse(localStorage.getItem(KEY) || "null"); if (s && Array.isArray(s.list)) store = s; } catch (e) { /* first run */ }
    store.list = store.list.filter(e => e && e.m && e.m.layers);
    if (store.cur && entry()) open(store.cur); else if (store.list.length) open(store.list[0].m.id); else newObs();
  }
  const isEmpty = () => !hasData() && !log.length && !m.site.location && !m.name;

  /* ---------- undo and redo ---------- */
  const state = () => JSON.stringify({ m, log });
  function restore(s) { const o = JSON.parse(s); m = o.m; log = o.log; const e = entry(); if (e) { e.m = m; e.log = log; } sel = -1; satManual = null; editTag = null; }
  function snap(tag) { // a run of typing in one field, or one visit to a sheet, is a single undo step
    if (tag && tag === editTag) return;
    undo.push(state()); if (undo.length > 60) undo.shift(); redo = []; editTag = tag || null;
  }
  function doUndo() { if (!undo.length) { toast("Nothing to undo."); return; } redo.push(state()); restore(undo.pop()); renderAll(); persist(); toast("Undone."); }
  function doRedo() { if (!redo.length) return; undo.push(state()); restore(redo.pop()); renderAll(); persist(); toast("Redone."); }
  $("undoBtn").onclick = doUndo; $("redoBtn").onclick = doRedo;

  /* ---------- speaking ---------- */
  function resLines(out) { return out.map(r => ({ k: r.ok ? (r.warn ? "warn" : "ok") : "bad", say: r.say, hint: r.hint || "" })); }
  function linesHtml(lines) {
    return lines.map(r => `<div class="h ${r.k}"><i aria-hidden="true">${r.k === "ok" ? "✓" : r.k === "warn" ? "!" : "?"}</i><span>${esc(r.say)}${r.hint ? `<small>${esc(r.hint)}</small>` : ""}</span></div>`).join("");
  }
  function grow() { const t = $("say"); t.style.height = "auto"; t.style.height = (t.scrollHeight + 3) + "px"; }
  function preview() {
    const text = $("say").value.trim(), box = $("preview"); grow();
    if (!text) { box.innerHTML = ""; return; }
    const acts = P.parse(text); if (!acts.length) { box.innerHTML = ""; return; }
    const out = M.apply(JSON.parse(JSON.stringify(m)), acts.filter(a => a.type !== "undo"));
    const lines = resLines(out); if (acts.some(a => a.type === "undo")) lines.unshift({ k: "ok", say: "Undo the last thing added" });
    box.innerHTML = `<div class="heard-title">Heard, not added yet</div>` + linesHtml(lines); $("last").hidden = true;
  }
  function setSay(v) { $("say").value = v; preview(); }
  function showLast(e) {
    const bad = e.res.filter(r => r.k === "bad"), k = bad.length ? "bad" : e.res.some(r => r.k === "warn") ? "warn" : "ok", lead = bad[0] || e.res[0], more = e.res.length - 1;
    $("last").innerHTML = linesHtml([{ k, say: lead.say + (more > 0 ? `  (+${more} more)` : ""), hint: bad.length ? lead.hint : "" }]) + `<button type="button" id="lastAll">All heard</button>`;
    $("last").hidden = false;
  }
  function add(text) {
    text = String(text || "").trim(); if (!text) { $("say").focus(); return false; }
    const acts = P.parse(text);
    if (acts.length && acts.every(a => a.type === "undo")) { setSay(""); doUndo(); return true; }
    const real = acts.filter(a => a.type !== "undo");
    if (!M.apply(JSON.parse(JSON.stringify(m)), real).some(r => r.ok)) { preview(); toast("Not understood, so nothing was added. Fix the words, or open the gear for “What can I say?”"); return false; }
    snap();
    const out = M.apply(m, real), e = { text, res: resLines(out) };
    log.unshift(e); if (log.length > 200) log.length = 200;
    if (m.site.observer) cfg.observer = m.site.observer; if (m.site.org) cfg.org = m.site.org; cfg.elevUnit = m.site.elevUnit;
    satManual = null; if (real.some(a => a.type === "layer")) sel = m.layers.findIndex(l => l.id === m.cur);
    setSay(""); renderAll(); showLast(e); persist();
    return true;
  }
  $("addBtn").onclick = () => add($("say").value);
  $("say").addEventListener("input", preview);
  $("say").setAttribute("enterkeyhint", "done");
  $("say").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); add($("say").value); } });
  $("last").addEventListener("click", e => { if (e.target.id === "lastAll") { renderLog(); openSheet("logSheet"); } });

  // Live listening where the browser has it. An iPhone home-screen app has none: there the keyboard microphone is the way,
  // and it is the one that works with no signal.
  const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
  const KB = "Tap the microphone on the keyboard and talk. It works with no signal.";
  let rec = null, listening = false, heardBase = "";
  function micState() { const b = $("micBtn"); b.setAttribute("aria-pressed", String(listening)); b.setAttribute("aria-label", !SR ? "Dictate with the keyboard microphone" : listening ? "Stop listening" : "Listen"); }
  function stopListening(msg) { listening = false; if (rec) { try { rec.stop(); } catch (e) { /* already stopped */ } } micState(); if (msg) toast(msg); }
  function startListening() {
    try {
      rec = new SR(); rec.lang = /^en/i.test(navigator.language || "") ? navigator.language : "en-CA"; rec.continuous = true; rec.interimResults = true;
      if ("processLocally" in rec && !navigator.onLine) rec.processLocally = true;
      heardBase = $("say").value ? $("say").value.replace(/\s*$/, " ") : "";
      rec.onresult = e => {
        let interim = "";
        for (let i = e.resultIndex; i < e.results.length; i++) {
          const r = e.results[i], t = r[0].transcript.trim();
          if (r.isFinal) { heardBase += t + " "; if (cfg.auto !== false && add(heardBase)) heardBase = ""; }
          else interim += t + " ";
        }
        setSay((heardBase + interim).trim());
      };
      rec.onerror = e => {
        if (e.error === "no-speech" || e.error === "aborted") return;
        stopListening(e.error === "network" ? "Live listening needs a connection on this device. " + KB
          : e.error === "not-allowed" || e.error === "service-not-allowed" ? "The microphone is blocked for this page. " + KB : "Listening stopped. " + KB);
      };
      rec.onend = () => { if (listening) { try { rec.start(); } catch (e) { stopListening(); } } };
      rec.start(); listening = true; micState();
    } catch (e) { stopListening("Live listening isn't available here. " + KB); }
  }
  $("micBtn").onclick = () => { if (!SR) { $("say").focus(); toast(KB); } else if (listening) stopListening(); else startListening(); };

  /* ---------- sheets ---------- */
  function openSheet(id) { const d = $(id); if (d.open) return; if (d.showModal) d.showModal(); else d.setAttribute("open", ""); }
  function closeSheet(id) { const d = typeof id === "string" ? $(id) : id; if (!d.open) return; if (d.close) d.close(); else d.removeAttribute("open"); }
  $$("dialog.sheet").forEach(d => {
    d.addEventListener("click", e => { if (e.target === d || e.target.closest("[data-close]")) closeSheet(d); });
    d.addEventListener("close", () => { const t = $("toast"); if (d.contains(t)) document.body.appendChild(t); editTag = null; });
  });

  /* ---------- profile tab: graph, selected layer, flags ---------- */
  function renderGraph() {
    if (tab !== "profile") return;
    const box = $("graph"), W = Math.max(300, Math.round(box.clientWidth || 340)), H = Math.round(Math.max(440, Math.min(700, W * 1.12))), light = cfg.theme === "light";
    box.className = light ? "light" : "";
    box.innerHTML = D.svg(D.screen(m, W, H, light ? "light" : "dark", { sel, mirror: !!cfg.mirror }), "Snow profile graph: hand hardness by " + (isHeight() ? "height" : "depth"));
  }
  function renderSel() {
    const L = m.layers[sel], box = $("selbar");
    if (L) { box.innerHTML = `<span>${isHeight() ? "Height" : "Depth"}: <b>${M.fmt(M.toRef(m, L.top))} – ${M.fmt(M.toRef(m, L.bottom))} cm</b></span><button type="button" class="primary" id="editLayerBtn" style="min-height:38px">Edit Layer</button>`; return; }
    const hint = m.layers.length ? "Tap a layer to edit it."
      : isHeight() && M.num(m.surf.hs) == null ? "Record the snowpack height to begin."
      : isHeight() ? `Say “layer ${M.fmt(m.surf.hs)} to ${M.fmt(Math.max(0, m.surf.hs - 15))} fist rounds”, or tap + Layer.` : "Say “layer 0 to 35 fist rounds”, or tap + Layer.";
    box.innerHTML = `<span class="hint">${hint}</span>`;
  }
  const FLAG = `<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M5 2h2v20H5zM8 3h11l-2.500 4.500L19 12H8z"/></svg>`;
  const FLAG_NAMES = { size: "grain size > 1 mm", hard: "softer than 1F", type: "persistent grain type", dSize: "grain size difference > 0.5 mm", dHard: "hardness difference > 1 step", depth: "20 to 85 cm deep" };
  function renderFlags() {
    const fl = M.flags(m).max;
    $("flagBox").innerHTML = fl
      ? `<div class="flags">${FLAG}<span>${fl.n} <span style="font-weight:500">at ${M.fmt(M.toRef(m, fl.depth))} cm ${isHeight() ? "height" : "depth"}</span></span></div><p class="note">${esc(Object.keys(fl.f).filter(k => fl.f[k]).map(k => FLAG_NAMES[k]).join(" · "))}</p>`
      : `<p class="note" style="margin-top:2px;font-size:14.5px">${m.layers.length > 1 ? "No flags at any layer boundary." : "Needs two layers with hardness, grain form and size."}</p>`;
  }
  function select(i) { sel = i; renderGraph(); renderSel(); $$("#layerTbl tr[data-i]").forEach(r => r.classList.toggle("sel", +r.dataset.i === sel)); }
  $("graph").addEventListener("click", e => { const r = e.target.closest(".lay"); if (!r) { if (sel >= 0) select(-1); return; } const i = +r.dataset.i; if (sel === i) openLayer(i); else select(i); });
  $("selbar").addEventListener("click", e => { if (e.target.id === "editLayerBtn" && m.layers[sel]) openLayer(sel); });
  $("refSeg").addEventListener("click", e => {
    const b = e.target.closest("button"); if (!b || b.dataset.ref === m.ref) return;
    snap(); M.setRef(m, b.dataset.ref); cfg.ref = m.ref; renderAll(); persist();
    toast(isHeight() ? "Heights above the ground: say “layer 150 to 141”." : "Depths below the surface: say “layer 0 to 35”.");
  });

  /* ---------- tables ---------- */
  const opt = (list, val, none) => `<option value=""${val ? "" : " selected"}>${none || "–"}</option>` + list.map(o => { const k = Array.isArray(o) ? o[0] : o, t = Array.isArray(o) ? o[1] : o; return `<option value="${esc(k)}"${k === val ? " selected" : ""}>${esc(t)}</option>`; }).join("");
  const GRAIN_OPTS = Object.keys(M.GRAINS).map(k => [k, `${k} · ${M.GRAINS[k]}`]);
  function renderTables() {
    const word = isHeight() ? "Height" : "Depth", dash = `<span style="color:var(--muted)">--</span>`, none = (n, t) => `<tr><td colspan="${n}" style="color:var(--muted);white-space:normal">${t}</td></tr>`;
    let h = `<thead><tr><th>#</th><th>${word} (cm)</th><th>Hardness</th><th>Form</th><th>Size (mm)</th><th>Note</th><th></th></tr></thead><tbody>`;
    if (!m.layers.length) h += none(7, "No layers yet.");
    m.layers.forEach((l, i) => {
      h += `<tr data-i="${i}"${i === sel ? ' class="sel"' : ""} style="cursor:pointer"><td class="idx">${i + 1}</td><td>${M.fmt(M.toRef(m, l.top))} – ${M.fmt(M.toRef(m, l.bottom))}</td><td>${esc(M.hardLabel(l)) || dash}</td><td>${esc(M.grainLabel(l)) || dash}</td><td>${esc(M.sizeLabel(l)) || dash}</td>`
        + `<td style="white-space:normal;min-width:96px">${l.concern ? `<b style="color:var(--bad)">Concern</b> ` : ""}${esc(l.note)}</td><td><button class="quiet" type="button" aria-label="Edit layer ${i + 1}">Edit</button></td></tr>`;
    });
    $("layerTbl").innerHTML = h + "</tbody>";
    h = `<thead><tr><th>${word} (cm)</th><th>Snow temperature (°C)</th><th></th></tr></thead><tbody>`;
    if (!m.temps.length) h += none(3, "No readings yet. Say “temperature 20 minus 3.5”, or tap + Temp.");
    m.temps.forEach((t, i) => { h += `<tr data-i="${i}"><td><input type="number" inputmode="decimal" step="any" data-f="depth" value="${M.toRef(m, t.depth)}" aria-label="${word}"></td><td><input type="number" step="any" data-f="t" value="${esc(t.t)}" aria-label="Temperature"></td><td><button class="quiet" data-act="del" type="button" aria-label="Delete this reading">Delete</button></td></tr>`; });
    $("tempTbl").innerHTML = h + "</tbody>";
    h = `<thead><tr><th>Test</th><th>${word} (cm)</th><th>Note</th><th></th></tr></thead><tbody>`;
    if (!m.tests.length) h += none(4, "No tests yet. Say “compression test 21 sudden planar at 60”, or tap + Test Result.");
    m.tests.forEach((t, i) => { h += `<tr data-i="${i}"><td><b style="font-family:var(--mono)">${esc(M.testLabel(t))}</b></td><td><input type="number" inputmode="decimal" step="any" data-f="depth" value="${M.num(t.depth) != null ? M.toRef(m, t.depth) : ""}" aria-label="${word}"></td><td><input type="text" data-f="note" value="${esc(t.note)}" aria-label="Note"></td><td><button class="quiet" data-act="del" type="button" aria-label="Delete ${esc(M.testLabel(t))}">Delete</button></td></tr>`; });
    $("testTbl").innerHTML = h + "</tbody>";
    $("sumLayers").textContent = m.layers.length ? `${m.layers.length} to ${M.fmt(M.pitBottom(m))} cm deep` : "none yet";
    $("sumTemps").textContent = m.temps.length ? `${m.temps.length} reading${m.temps.length > 1 ? "s" : ""}` : "none yet";
    $("sumTests").textContent = m.tests.length ? m.tests.map(M.testLabel).join(", ") : "none yet";
  }
  function buttons() { $("undoBtn").disabled = !undo.length; $("redoBtn").disabled = !redo.length; $("delLayerBtn").disabled = !m.layers.length; }
  function afterEdit(keepTables) { satManual = null; if (!keepTables) renderTables(); renderForm(); renderGraph(); renderSel(); renderFlags(); renderSat(); renderObs(); buttons(); persist(); }
  $("layerTbl").addEventListener("click", e => { const tr = e.target.closest("tr[data-i]"); if (tr) openLayer(+tr.dataset.i); });
  // a spoken or typed position, as the observer measures it, to a stored depth; null (with a message) when it can't be
  function refDepth(v) {
    if (v == null || v < 0) { toast(isHeight() ? "That is below the ground." : "A depth can't be negative."); return null; }
    if (isHeight() && v > M.hsOf(m) + 0.01) { toast(`That is above the snow surface (HS ${M.fmt(M.hsOf(m))} cm).`); return null; }
    return Math.max(0, +M.fromRef(m, v).toFixed(1));
  }
  $("tempTbl").addEventListener("change", e => {
    const tr = e.target.closest("tr[data-i]"); if (!tr) return; const T = m.temps[+tr.dataset.i], f = e.target.dataset.f, val = M.num(e.target.value); if (!T) return;
    if (val == null) { renderTables(); return; }
    if (f === "depth") { const d = refDepth(val); if (d == null) { renderTables(); return; } snap(); T.depth = d; m.temps.sort((a, b) => a.depth - b.depth); afterEdit(); }
    else { snap(); T.t = val; afterEdit(true); }
  });
  $("tempTbl").addEventListener("click", e => { if (e.target.dataset.act === "del") { snap(); m.temps.splice(+e.target.closest("tr").dataset.i, 1); afterEdit(); } });
  $("testTbl").addEventListener("change", e => {
    const tr = e.target.closest("tr[data-i]"); if (!tr) return; const T = m.tests[+tr.dataset.i], f = e.target.dataset.f; if (!T) return;
    if (f === "note") { snap(); T.note = e.target.value; afterEdit(true); return; }
    const val = M.num(e.target.value), d = val == null ? null : refDepth(val); if (val != null && d == null) { renderTables(); return; }
    snap(); T.depth = d; afterEdit(true);
  });
  $("testTbl").addEventListener("click", e => { if (e.target.dataset.act === "del") { snap(); m.tests.splice(+e.target.closest("tr").dataset.i, 1); afterEdit(); } });

  /* ---------- layer sheet ---------- */
  const HB = /^(4F|1F|F|P|K|I)([+-]?)$/, HBASES = [["F", "Fist"], ["4F", "4 finger"], ["1F", "1 finger"], ["P", "Pencil"], ["K", "Knife"], ["I", "Ice"]];
  const CHIP_GRAINS = ["PP", "DF", "RG", "FC", "DH", "SH", "MF", "MFcr", "IF"];
  function glyphSvg(code) { const L = []; return D.glyph(L, code, 9, 9, 6, "currentColor") ? D.svg({ w: 18, h: 18, list: L }, "").replace(/ role="img" aria-label="[^"]*"/, ' aria-hidden="true"') : ""; }
  $("lsHard").innerHTML = HBASES.map(([k, n]) => `<button type="button" class="chip" data-h="${k}" aria-pressed="false">${k} <small>${n}</small></button>`).join("");
  $("lsHardBot").innerHTML = opt(M.HARD, "", "Same as the top");
  $("lsGrain").innerHTML = CHIP_GRAINS.map(k => `<button type="button" class="chip" data-g="${k}" aria-pressed="false" aria-label="${esc(M.GRAINS[k])}" title="${esc(M.GRAINS[k])}">${glyphSvg(k)}${k}</button>`).join("");
  $("lsGrainSel").innerHTML = opt(GRAIN_OPTS, ""); $("lsGrain2").innerHTML = opt(GRAIN_OPTS, "", "None");
  $("lsWet").innerHTML = Object.keys(M.WET).map(k => `<button type="button" class="chip" data-w="${k}" aria-pressed="false">${k} <small>${M.WET[k]}</small></button>`).join("");
  let LS = null; // id of the layer open in the sheet
  const curLayer = () => m.layers.find(l => l.id === LS);
  function selValue(id, v) { const el = $(id), s = v || ""; if (s && !Array.from(el.options).some(o => o.value === s)) el.add(new Option(s, s)); el.value = s; }
  function fillLayer() {
    const L = curLayer(); if (!L) return; const i = m.layers.indexOf(L), hb = HB.exec(L.hardTop || "") || [];
    $("ls-h").textContent = `Layer ${i + 1} of ${m.layers.length}`;
    $("lsDepthLbl").textContent = isHeight() ? "Height from the ground:" : "Depth from the surface:";
    setVal("lsTop", M.toRef(m, L.top)); setVal("lsBottom", M.toRef(m, L.bottom));
    press("#lsHard .chip", b => b.dataset.h === hb[1]); press("#lsMod button", b => !!hb[1] && b.dataset.mod === (hb[2] || ""));
    selValue("lsHardBot", L.hardBot && L.hardBot !== L.hardTop ? L.hardBot : "");
    press("#lsGrain .chip", b => b.dataset.g === L.grain); selValue("lsGrainSel", L.grain); selValue("lsGrain2", L.grain2);
    setVal("lsSize", L.size); setVal("lsSizeMax", L.sizeMax); setVal("lsDensity", L.density);
    press("#lsWet .chip", b => b.dataset.w === L.wet); setVal("lsNote", L.note); $("lsConcern").checked = !!L.concern;
  }
  function openLayer(i) { const L = m.layers[i]; if (!L) return; LS = L.id; editTag = null; select(i); fillLayer(); openSheet("layerSheet"); }
  function lsEdit(fn) { const L = curLayer(); if (!L) return; snap("layer:" + LS); fn(L); m.cur = L.id; sel = m.layers.indexOf(L); afterEdit(); fillLayer(); }
  $("lsHard").addEventListener("click", e => {
    const b = e.target.closest("[data-h]"); if (!b) return;
    lsEdit(L => { const hb = HB.exec(L.hardTop || "") || [], h = b.dataset.h; if (hb[1] === h) { L.hardTop = ""; L.hardBot = ""; } else L.hardTop = h + (h === "I" ? "" : hb[2] || ""); });
  });
  $("lsMod").addEventListener("click", e => {
    const b = e.target.closest("[data-mod]"), L = curLayer(), hb = L && HB.exec(L.hardTop || ""); if (!b || !L) return;
    if (!hb) { toast("Pick the hardness first."); return; }
    if (hb[1] === "I") { toast("Ice has no plus or minus."); return; }
    lsEdit(x => { x.hardTop = hb[1] + b.dataset.mod; });
  });
  $("lsHardBot").addEventListener("change", e => { const L = curLayer(); if (L && !L.hardTop && e.target.value) { toast("Pick the hardness at the top first."); e.target.value = ""; return; } lsEdit(x => { x.hardBot = e.target.value === x.hardTop ? "" : e.target.value; }); });
  $("lsGrain").addEventListener("click", e => { const b = e.target.closest("[data-g]"); if (b) lsEdit(L => { if (L.grain === b.dataset.g) { L.grain = ""; L.grain2 = ""; } else L.grain = b.dataset.g; }); });
  $("lsGrainSel").addEventListener("change", e => lsEdit(L => { L.grain = e.target.value; if (!L.grain) L.grain2 = ""; }));
  $("lsGrain2").addEventListener("change", e => { const L = curLayer(); if (L && !L.grain && e.target.value) { toast("Pick the main grain form first."); e.target.value = ""; return; } lsEdit(x => { x.grain2 = e.target.value === x.grain ? "" : e.target.value; }); });
  [["lsSize", "size"], ["lsSizeMax", "sizeMax"], ["lsDensity", "density"]].forEach(([id, f]) => $(id).addEventListener("input", e => lsEdit(L => { L[f] = numVal(e.target) === "" ? "" : Math.max(0, numVal(e.target)); })));
  $("lsWet").addEventListener("click", e => { const b = e.target.closest("[data-w]"); if (b) lsEdit(L => { L.wet = L.wet === b.dataset.w ? "" : b.dataset.w; }); });
  $("lsNote").addEventListener("input", e => lsEdit(L => { L.note = e.target.value; }));
  $("lsConcern").addEventListener("change", e => lsEdit(L => { L.concern = e.target.checked; }));
  // A boundary shared with the next layer moves both, so the profile stays without gaps or overlaps.
  function moveBoundary(which, el) {
    const L = curLayer(), v = M.num(el.value); if (!L) return;
    const d = v == null ? null : refDepth(v), i = m.layers.indexOf(L), up = m.layers[i - 1], lo = m.layers[i + 1];
    let err = d == null ? " " : "";
    if (!err && which === "top") {
      const tied = up && Math.abs(up.bottom - L.top) < 0.26;
      if (d > L.bottom - 0.05) err = "The top and bottom of a layer can't be the same.";
      else if (up && d < (tied ? up.top + 0.05 : up.bottom - 0.01)) err = `That runs into layer ${i}. Change that layer first.`;
      else lsEdit(x => { if (tied) up.bottom = d; x.top = d; });
    } else if (!err) {
      const tied = lo && Math.abs(lo.top - L.bottom) < 0.26;
      if (d < L.top + 0.05) err = "The top and bottom of a layer can't be the same.";
      else if (lo && d > (tied ? lo.bottom - 0.05 : lo.top + 0.01)) err = `That runs into layer ${i + 2}. Change that layer first.`;
      else lsEdit(x => { if (tied) lo.top = d; x.bottom = d; });
    }
    if (err) { if (err.trim()) toast(err); el.value = M.toRef(m, which === "top" ? L.top : L.bottom); }
  }
  $("lsTop").addEventListener("change", e => moveBoundary("top", e.target));
  $("lsBottom").addEventListener("change", e => moveBoundary("bottom", e.target));
  function removeLayer(i) {
    const L = m.layers[i]; if (!L) return;
    snap(); m.layers.splice(i, 1); if (m.cur === L.id) m.cur = null; sel = -1; afterEdit();
    toast(`Layer ${M.fmt(M.toRef(m, L.top))}–${M.fmt(M.toRef(m, L.bottom))} cm deleted. Undo brings it back.`);
  }
  $("lsDelete").onclick = () => { const i = m.layers.indexOf(curLayer()); closeSheet("layerSheet"); removeLayer(i); };
  $("delLayerBtn").onclick = () => removeLayer(sel >= 0 ? sel : m.layers.length - 1);
  $("addLayerBtn").onclick = () => {
    const hs = M.num(m.surf.hs);
    if (isHeight() && hs == null) { $("f_hs").focus(); toast("Record the snowpack height first."); return; }
    const top = m.layers.length ? m.layers[m.layers.length - 1].bottom : 0, room = hs != null ? hs - top : Infinity;
    if (room < 0.5) { toast("The layers already reach the ground."); return; }
    snap(); const r = M.putLayer(m, top, top + Math.min(10, room), {}); if (r.err) { undo.pop(); toast(r.err); return; }
    afterEdit(); openLayer(m.layers.indexOf(r.layer)); editTag = "layer:" + LS; // adding and filling it in is one undo step
  };

  /* ---------- temperature sheet ---------- */
  function nextTempDepth() { // readings run down from the surface, every 10 cm
    const H = isHeight(), hs = M.hsOf(m), last = m.temps.length ? m.temps[m.temps.length - 1].depth : null;
    if (!H) return last == null ? 10 : last + 10;
    if (last == null) return hs > 0 ? Math.max(0, Math.ceil(hs / 10) * 10 - 10) : "";
    return Math.max(0, +(hs - last - 10).toFixed(1));
  }
  $("addTempBtn").onclick = () => {
    if (isHeight() && M.num(m.surf.hs) == null) { $("f_hs").focus(); toast("Record the snowpack height first."); return; }
    $("tpDepthLbl").textContent = isHeight() ? "Height from the ground" : "Depth from the surface"; $("tpDepth").value = nextTempDepth(); $("tpT").value = "";
    openSheet("tempSheet"); $("tpT").focus();
  };
  $("tpAdd").onclick = () => {
    const v = M.num($("tpDepth").value), t = M.num($("tpT").value);
    if (v == null) { toast("Enter where the reading was taken."); $("tpDepth").focus(); return; }
    if (t == null) { toast("Enter the temperature."); $("tpT").focus(); return; }
    if (refDepth(v) == null) return;
    snap(); const out = M.apply(m, [{ type: "temp", depth: v, t: -Math.abs(t) }])[0];
    if (!out.ok) { undo.pop(); toast(out.say); return; }
    afterEdit(); toast(out.say);
    $("tpDepth").value = isHeight() ? Math.max(0, v - 10) : v + 10; $("tpT").value = ""; $("tpT").focus();
  };
  $("tpT").addEventListener("keydown", e => { if (e.key === "Enter") { e.preventDefault(); $("tpAdd").click(); } });

  /* ---------- test result sheet ---------- */
  const TESTS = { ECT: "Extended column test", CT: "Compression test", PST: "Propagation saw test", RB: "Rutschblock", DT: "Deep tap test" };
  const RESULTS = {
    ECT: [["PV", "ECTPV", "Fracture propagates across the entire column during isolation."], ["P", "ECTP", "Fracture propagates across the entire column on this tap or the next."],
      ["N", "ECTN", "Fracture initiates on this tap but does not cross the entire column."], ["X", "ECTX", "No fracture occurs during the test."]],
    CT: [["f", "Fracture", "Taps: 1 to 10 easy (from the wrist), 11 to 20 moderate (from the elbow), 21 to 30 hard (from the shoulder)."], ["n", "CTN", "No fracture after 30 taps."]],
    DT: [["f", "Fracture", "Taps on a column cut down to 15 cm above the weak layer: 1 to 10 easy, 11 to 20 moderate, 21 to 30 hard."], ["n", "DTN", "No fracture after 30 taps."]],
    PST: [["End", "End", "The fracture ran to the end of the column."], ["Arr", "Arr", "The fracture stopped itself before the end of the column."], ["SF", "SF", "The slab fractured above the saw cut."]],
    RB: [1, 2, 3, 4, 5, 6, 7].map(n => [String(n), "RB" + n, ["Released while isolating the block.", "Released as the skier stepped onto the block.", "Released on a knee drop, without jumping.", "Released on the first jump.", "Released on the second or third jump.", "Released on a jump without skis.", "No release."][n - 1]])
  };
  const CHARS = [["SP", "Sudden planar"], ["SC", "Sudden collapse"], ["RP", "Resistant planar"], ["PC", "Progressive compression"], ["BRK", "Non-planar break"]];
  let TS = null;
  const tsFractured = () => TS.test === "ECT" ? TS.res === "P" || TS.res === "N" || TS.res === "PV" : TS.test === "RB" ? !!TS.res && TS.res !== "7" : TS.test === "PST" ? true : TS.res === "f";
  function renderTest() {
    const t = TS.test, res = RESULTS[t], cur = res.find(r => r[0] === TS.res), taps = (t === "ECT" && (TS.res === "P" || TS.res === "N")) || ((t === "CT" || t === "DT") && TS.res === "f");
    $("tsType").innerHTML = Object.keys(TESTS).map(k => `<button type="button" class="chip" data-t="${k}" aria-pressed="${k === t}" title="${TESTS[k]}">${k}</button>`).join("");
    $("tsResLbl").textContent = `${TESTS[t]} result:`;
    $("tsRes").innerHTML = res.map(r => `<button type="button" class="chip" data-r="${r[0]}" aria-pressed="${r[0] === TS.res}">${r[1]}</button>`).join("");
    $("tsExplain").textContent = cur ? cur[2] : t === "PST" ? "Cut length over column length, then where the fracture stopped." : "Pick the result.";
    const numIn = (id, label, unit, val, w) => `<label><span>${label}</span><span class="with-unit"><input id="${id}" type="number" inputmode="numeric" step="1" min="0" value="${esc(val)}" style="width:${w || 84}px;font-size:19px;text-align:center">${unit ? `<span class="unit">${unit}</span>` : ""}</span></label>`;
    $("tsNums").innerHTML = taps ? numIn("tsTaps", "# of taps", "", TS.taps) : t === "PST" ? numIn("tsCut", "Cut length", "cm", TS.cut) + numIn("tsCol", "Column length", "cm", TS.col) : "";
    const showChar = (t === "CT" || t === "DT") && TS.res === "f", rb = t === "RB" && tsFractured();
    $("tsCharSec").hidden = !showChar && !rb; $("tsCharSec").querySelector(".lbl").textContent = rb ? "Release Type:" : "Fracture Character:";
    $("tsChar").innerHTML = rb ? [["WB", "Whole block"], ["MB", "Most of block"], ["EB", "Edge of block"]].map(c => `<button type="button" class="chip" data-c="${c[0]}" aria-pressed="${TS.rel === c[0]}">${c[0]} <small>${c[1]}</small></button>`).join("")
      : CHARS.map(c => `<button type="button" class="chip" data-c="${c[0]}" aria-pressed="${TS.char === c[0]}">${c[0]} <small>${c[1]}</small></button>`).join("");
    $("tsQ").hidden = rb; $("tsQ").innerHTML = ["Q1", "Q2", "Q3"].map(q => `<button type="button" class="chip" data-q="${q}" aria-pressed="${TS.q === q}">${q}</button>`).join("");
    $("tsDepthLbl").textContent = isHeight() ? "Height From Ground:" : "Depth From Surface:";
  }
  function keepNums() { ["taps", "cut", "col"].forEach(k => { const el = $("ts" + k[0].toUpperCase() + k.slice(1)); if (el) TS[k] = el.value; }); }
  $("addTestBtn").onclick = () => {
    if (isHeight() && M.num(m.surf.hs) == null) { $("f_hs").focus(); toast("Record the snowpack height first."); return; }
    const L = m.layers[sel];
    TS = { test: TS ? TS.test : "ECT", res: "", taps: "", cut: "", col: "100", char: "", q: "", rel: "" };
    $("tsDepth").value = L && L.top > 0.05 ? M.toRef(m, L.top) : ""; $("tsNote").value = L && L.top > 0.05 && L.note ? L.note : "";
    renderTest(); openSheet("testSheet");
  };
  $("testSheet").addEventListener("click", e => {
    const b = e.target.closest(".chip"); if (!b || !TS) return; keepNums();
    if (b.dataset.t) { TS.test = b.dataset.t; TS.res = ""; TS.char = ""; TS.q = ""; TS.rel = ""; }
    else if (b.dataset.r) TS.res = b.dataset.r;
    else if (b.dataset.c) { if (TS.test === "RB") TS.rel = TS.rel === b.dataset.c ? "" : b.dataset.c; else TS.char = TS.char === b.dataset.c ? "" : b.dataset.c; }
    else if (b.dataset.q) TS.q = TS.q === b.dataset.q ? "" : b.dataset.q;
    renderTest();
  });
  $("tsAdd").onclick = () => {
    keepNums();
    const t = TS.test, taps = M.num(TS.taps), a = { type: "test", test: t, note: $("tsNote").value.trim() }, need = (msg, id) => { toast(msg); if (id && $(id)) $(id).focus(); };
    if (t !== "PST" && !TS.res) return need("Pick the result.");
    if (t === "ECT") { a.prop = TS.res; if (TS.res === "P" || TS.res === "N") { if (taps == null || taps < 1 || taps > 30) return need("Enter the number of taps, 1 to 30.", "tsTaps"); a.score = taps; } }
    else if (t === "CT" || t === "DT") { if (TS.res === "n") a.none = true; else { if (taps == null || taps < 1 || taps > 30) return need("Enter the number of taps, 1 to 30.", "tsTaps"); a.score = taps; if (TS.char) a.char = TS.char; if (TS.q) a.q = TS.q; } }
    else if (t === "RB") { a.score = +TS.res; if (TS.rel && tsFractured()) a.rel = TS.rel; }
    else {
      const cut = M.num(TS.cut), col = M.num(TS.col);
      if (cut == null || cut <= 0) return need("Enter the cut length.", "tsCut");
      if (col == null || col < cut) return need("The column can't be shorter than the cut.", "tsCol");
      if (!TS.res) return need("Pick where the fracture stopped: End, Arr or SF.");
      a.cut = cut; a.col = col; a.res = TS.res;
    }
    const v = M.num($("tsDepth").value);
    if (v != null) { if (refDepth(v) == null) { $("tsDepth").focus(); return; } a.depth = v; }
    else if (tsFractured()) return need(isHeight() ? "Enter the height of the failure, in cm from the ground." : "Enter the depth of the failure, in cm from the surface.", "tsDepth");
    snap(); const out = M.apply(m, [a])[0];
    if (!out.ok) { undo.pop(); toast(out.say); return; }
    closeSheet("testSheet"); afterEdit(); $("gTests").open = true; toast(out.say);
  };

  /* ---------- info tab ---------- */
  const fields = $$("[data-k]");
  const getK = k => { const p = k.split("."); return p.length > 1 ? m[p[0]][p[1]] : m[k]; };
  function setHsField(el) {
    const v = M.num(el.value), back = msg => { el.value = m.surf.hs; if (msg) toast(msg); };
    if (v == null) { if (isHeight() && hasData()) return back("Heights are measured from the ground, so the snowpack height can't be empty."); if (m.surf.hs === "") return; snap(); m.surf.hs = ""; return afterEdit(); }
    if (v <= 0) return back("The snowpack height must be more than 0.");
    const over = M.hsConflict(m, v); if (over != null) return back(`Something is already recorded at ${M.fmt(over)} cm. Fix that first.`);
    snap(); M.setHS(m, v); afterEdit();
  }
  fields.forEach(el => {
    const o = el.dataset.opts, k = el.dataset.k;
    if (o) el.innerHTML = opt(o === "DIR" ? DIRS : o === "GRAINS" ? GRAIN_OPTS : Object.keys(M[o]).map(x => [x, `${x} · ${M[o][x]}`]), "");
    if (k === "surf.hs") { el.addEventListener("change", () => setHsField(el)); el.addEventListener("keydown", e => { if (e.key === "Enter") el.blur(); }); return; }
    el.addEventListener(el.tagName === "SELECT" ? "change" : "input", () => {
      const p = k.split("."), val = el.type === "number" ? numVal(el) : el.value;
      snap("field:" + k);
      if (p.length > 1) m[p[0]][p[1]] = val; else m[k] = val;
      if (k === "wx.windSpd" && val === "C") m.wx.windDir = "";
      if (k === "site.observer") cfg.observer = val; if (k === "site.org") cfg.org = val; if (k === "site.elevUnit") cfg.elevUnit = val;
      afterEdit(true);
    });
    el.addEventListener("blur", () => { if (editTag === "field:" + k) editTag = null; });
  });
  function renderForm() {
    fields.forEach(el => {
      if (el === document.activeElement) return;
      const val = getK(el.dataset.k), s = val == null ? "" : String(val);
      if (el.tagName === "SELECT" && s && !Array.from(el.options).some(o => o.value === s)) el.add(new Option(s, s));
      el.value = s;
    });
    const has = M.num(m.site.lat) != null && M.num(m.site.lng) != null, c = $("coords");
    c.textContent = has ? `${(+m.site.lat).toFixed(5)}, ${(+m.site.lng).toFixed(5)}` : "No position yet"; c.className = "coords" + (has ? "" : " none");
    if (M.num(m.wx.tmax) != null || M.num(m.wx.tmin) != null || M.num(m.wx.hn24) != null || M.num(m.wx.hst) != null || m.wx.blow) $("moreWx").open = true;
  }
  function gps() {
    if (!navigator.geolocation) { toast("This phone doesn't share its position with web apps."); return; }
    toast("Finding your position… keep the sky in view.");
    navigator.geolocation.getCurrentPosition(p => {
      snap(); m.site.lat = +p.coords.latitude.toFixed(5); m.site.lng = +p.coords.longitude.toFixed(5);
      let extra = "";
      if (p.coords.altitude != null && m.site.elev === "") { m.site.elev = Math.round(m.site.elevUnit === "ft" ? p.coords.altitude / 0.3048 : p.coords.altitude); extra = `, elevation ${m.site.elev} ${m.site.elevUnit} from GPS`; }
      afterEdit(true); toast(`Position set, good to about ${Math.round(p.coords.accuracy)} m${extra}.`);
    }, err => toast(err.code === 1 ? "Location is blocked for this app. Allow it in the phone's settings, or type the coordinates." : "No position fix yet. Try again with open sky."),
    { enableHighAccuracy: true, timeout: 30000, maximumAge: 60000 });
  }
  $("gpsBtn").onclick = gps; $("gpsBtn2").onclick = gps;

  /* ---------- satellite text ---------- */
  const satOpts = () => Object.assign({ pos: true, wx: true, snow: true, pit: true, avs: true, notes: true }, cfg.sat || {});
  function satMeta(text) {
    const i = M.textInfo(text), c = $("satCount");
    c.textContent = text ? `${i.chars} characters · ${i.parts} text${i.parts > 1 ? "s" : ""}${i.gsm ? "" : " · special characters"}` : "";
    c.className = "count" + (i.parts > 1 ? " over" : "");
    $("smsBtn").href = M.smsLink(cfg.to, text, IOS); $("smsBtn").setAttribute("aria-disabled", text ? "false" : "true");
    $("satEdited").hidden = satManual == null;
  }
  function renderSat() {
    const ta = $("satOut"), text = satManual != null ? satManual : M.satText(m, satOpts());
    if (document.activeElement !== ta) ta.value = text;
    ta.placeholder = "Nothing to send yet. Say the weather: “sky broken, wind light south west, air temperature minus 7”.";
    satMeta(ta.value);
    $$("#satChecks input").forEach(c => { c.checked = satOpts()[c.dataset.o] !== false; });
    if (document.activeElement !== $("satTo")) $("satTo").value = cfg.to || "";
  }
  $("satOut").addEventListener("input", e => { satManual = e.target.value; satMeta(satManual); });
  $("satReset").onclick = () => { satManual = null; renderSat(); };
  $("satChecks").addEventListener("change", e => { const o = e.target.dataset.o; if (!o) return; cfg.sat = Object.assign({}, cfg.sat, { [o]: e.target.checked }); satManual = null; renderSat(); persist(); });
  $("satTo").addEventListener("input", e => { cfg.to = e.target.value; satMeta($("satOut").value); persist(); });
  $("smsBtn").addEventListener("click", e => { if (!$("satOut").value.trim()) { e.preventDefault(); toast("Nothing to send yet."); } });
  $("copyBtn").onclick = () => {
    const ta = $("satOut"); if (!ta.value.trim()) { toast("Nothing to copy yet."); return; }
    const fallback = () => { ta.focus(); ta.select(); try { document.execCommand("copy"); toast("Copied."); } catch (e) { toast("Select the text and copy it."); } };
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(ta.value).then(() => toast("Copied. Paste it into Messages."), fallback); else fallback();
  };
  if (!navigator.share) $("shareBtn").hidden = true;
  $("shareBtn").onclick = () => { const t = $("satOut").value.trim(); if (!t) { toast("Nothing to share yet."); return; } navigator.share({ text: t }).catch(() => {}); };

  /* ---------- exports ---------- */
  function baseName() {
    const d = (m.site.date || nowLocal()).slice(0, 10), loc = (m.name || m.site.location || "snow-profile").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "snow-profile";
    return `${d}-${loc}`;
  }
  async function saveFile(name, blob) {
    try {
      const file = new File([blob], name, { type: blob.type });
      if (IOS && navigator.canShare && navigator.canShare({ files: [file] })) { await navigator.share({ files: [file], title: name }); return; }
    } catch (e) { if (e && e.name === "AbortError") return; }
    const url = URL.createObjectURL(blob), a = document.createElement("a"); a.href = url; a.download = name; document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 5000); toast("Saved " + name);
  }
  const nothingYet = () => { if (hasData()) return false; toast("Nothing to export yet. Add a layer, a temperature or a test first."); return true; };
  $("exportBtn").onclick = () => openSheet("exportSheet");
  $("pdfBtn").onclick = () => {
    if (nothingYet()) return; closeSheet("exportSheet");
    try { saveFile(baseName() + ".pdf", new Blob([D.profilePdf(m, new Date(), { mirror: !!cfg.mirror })], { type: "application/pdf" })); } catch (e) { toast("The PDF couldn't be built: " + (e.message || e)); }
  };
  $("xmlBtn").onclick = () => { if (nothingYet()) return; closeSheet("exportSheet"); saveFile(baseName() + ".caaml.xml", new Blob([M.caaml(m)], { type: "application/xml" })); };
  $("pngBtn").onclick = () => {
    if (nothingYet()) return; closeSheet("exportSheet");
    const page = D.screen(m, 900, 880, "light", { live: false, notes: true, sel: -1, mirror: !!cfg.mirror }), im = new Image();
    im.onload = () => { const c = document.createElement("canvas"); c.width = page.w * 2; c.height = page.h * 2; c.getContext("2d").drawImage(im, 0, 0, c.width, c.height); c.toBlob(b => b ? saveFile(baseName() + ".png", b) : toast("The image couldn't be made."), "image/png"); };
    im.onerror = () => toast("The image couldn't be made."); im.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(D.svg(page));
  };
  $("toSatBtn").onclick = () => { closeSheet("exportSheet"); setTab("sat"); persist(); };

  /* ---------- observations and settings ---------- */
  const obsName = o => o.name || o.site.location || "Untitled";
  function renderObs() {
    $("obsBtn").textContent = m.name || m.site.location || "Untitled" + (m.site.date ? " · " + M.shortDate(m.site.date) : "");
    $("obsList").innerHTML = store.list.map(e => {
      const o = e.m.id === store.cur ? m : e.m, bits = [M.shortDate(o.site.date), o.layers.length ? o.layers.length + " layers" : ""].filter(Boolean).join(" · ");
      return `<button type="button" data-id="${esc(o.id)}"${o.id === store.cur ? ' aria-current="true"' : ""}><span>${esc(obsName(o))}</span><small>${esc(bits)}</small></button>`;
    }).join("");
  }
  const openMenu = () => { renderObs(); openSheet("menuSheet"); };
  $("menuBtn").onclick = openMenu; $("obsBtn").onclick = openMenu;
  $("obsList").addEventListener("click", e => {
    const b = e.target.closest("button[data-id]"); if (!b) return; closeSheet("menuSheet"); if (b.dataset.id === store.cur) return;
    flush(); open(b.dataset.id); $("last").hidden = true; renderAll(); persist();
  });
  $("newBtn").onclick = () => {
    if (isEmpty()) { closeSheet("menuSheet"); toast("This observation is still empty."); return; }
    flush(); newObs(); closeSheet("menuSheet"); $("last").hidden = true; renderAll(); persist(); toast("New observation. The last one is kept in the list.");
  };
  let delArm = null;
  $("delBtn").onclick = e => {
    const b = e.currentTarget, reset = () => { delArm = null; b.textContent = "Delete this observation"; };
    if (!delArm) { b.textContent = "Tap again to delete it for good"; delArm = setTimeout(reset, 3500); return; }
    clearTimeout(delArm); reset();
    store.list = store.list.filter(x => x.m.id !== store.cur); if (store.list.length) open(store.list[0].m.id); else newObs();
    closeSheet("menuSheet"); $("last").hidden = true; renderAll(); persist(); toast("Deleted.");
  };
  function applyTheme() {
    const light = cfg.theme === "light"; document.documentElement.dataset.theme = light ? "light" : "dark";
    const meta = document.querySelector('meta[name="theme-color"]'); if (meta) meta.content = light ? "#ffffff" : "#1d1c22";
  }
  $("mirSeg").addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return; cfg.mirror = b.dataset.mir === "1"; renderAll(); persist(); });
  $("themeSeg").addEventListener("click", e => { const b = e.target.closest("button"); if (!b) return; cfg.theme = b.dataset.theme; applyTheme(); renderAll(); persist(); });
  $("autoAdd").onchange = e => { cfg.auto = e.target.checked; persist(); };
  $("srNote").textContent = SR
    ? "The microphone button listens live in this browser, and may need a connection. With no signal, or in the app on your home screen, use the microphone on the keyboard instead."
    : "This app has no live listening here, so the microphone button opens the keyboard: tap the keyboard's microphone and talk. That works with no signal.";
  $("autoAdd").disabled = !SR;
  function renderLog() {
    $("log").innerHTML = log.length ? log.slice(0, 40).map(e => `<div style="padding:10px 0;border-bottom:1px solid var(--line);display:grid;gap:4px"><div class="lbl">“${esc(e.text.length > 200 ? e.text.slice(0, 197) + "…" : e.text)}”</div>${linesHtml(e.res)}</div>`).join("")
      : `<p class="note" style="font-size:14.5px;padding:10px 0">Nothing yet. What you say or type, and how it was read, is listed here.</p>`;
  }
  $("logBtn").onclick = () => { closeSheet("menuSheet"); renderLog(); openSheet("logSheet"); };
  const EXAMPLE = {
    height: "Location Bow Summit study plot, aspect north east, elevation 2080 metres, slope 28, snow depth 100.\nLayer 100 to 65 fist plus rounds point three. Layer 65 to 30 four finger plus rounds point five. Layer 30 to 0 fist facets with depth hoar 1.5 max 2 weak layer.\nTemperature 90 minus 3.6, 70 minus 3.2, 50 minus 2.7, 30 minus 1.8, 10 minus 0.9.\nCompression test 21 sudden planar at 30.\nSky broken, precip none, wind light south west, air temperature minus 7.8.",
    depth: "Location Bow Summit study plot, aspect north east, elevation 2080 metres, slope 28, snow depth 100.\nLayer 0 to 35 fist plus rounds point three. Layer 35 to 70 four finger plus rounds point five. Layer 70 to 100 fist facets with depth hoar 1.5 max 2 weak layer.\nTemperature 10 minus 3.6, 30 minus 3.2, 50 minus 2.7, 70 minus 1.8, 90 minus 0.9.\nCompression test 21 sudden planar down 70.\nSky broken, precip none, wind light south west, air temperature minus 7.8."
  };
  function renderHelp() {
    const H = isHeight();
    $("hpStart").innerHTML = H ? "snow depth 155<br>then each layer by its heights above the ground" : "layers by depth below the surface<br>snow depth 155 (if the pit doesn't reach the ground)";
    $("hpLayers").innerHTML = H ? "layer 155 to 141 fist new snow 2<br>to 115 four finger decomposing<br>next 15 thick one finger rounds 1<br>layer 62 to 60 fist surface hoar 6 label Jan 3rd weak layer"
      : "layer 0 to 35 fist plus rounds 0.3<br>to 70 four finger rounds point five<br>next 30 thick fist facets 1.5 max 2 weak layer<br>layer 93 to 95 fist surface hoar 6 label Jan 3rd";
    $("hpTemps").innerHTML = H ? "temperature 150 minus 6, 140 minus 5.2<br>surface temperature minus 9" : "temperature 10 minus 3.6, 20 minus 3.5<br>surface temperature minus 9";
    const at = H ? "at 60" : "down 95";
    $("hpTests").innerHTML = `compression test 21 sudden planar ${at}<br>CT 12 Q2 ${at} · compression test no result<br>ECTP 22 ${at} · ECTN 15 ${at} · ECTX<br>PST 45 of 100 end ${at}<br>rutschblock 4 whole block ${at}<br>deep tap 18 sudden collapse ${at}`;
    $("say").placeholder = tab === "info" ? "Say “location Bow Summit”" : tab === "sat" ? "Say “sky broken, wind calm”" : H ? "Say “layer 100 to 83 fist”" : "Say “layer 0 to 35 fist”";
  }
  $("helpBtn").onclick = () => { closeSheet("menuSheet"); openSheet("helpSheet"); };
  $("exampleBtn").onclick = () => { closeSheet("helpSheet"); setSay(EXAMPLE[isHeight() ? "height" : "depth"]); $("say").focus(); };

  /* ---------- tabs ---------- */
  function setTab(t) {
    tab = t === "info" || t === "sat" ? t : "profile"; cfg.tab = tab;
    $("tInfo").hidden = tab !== "info"; $("tProfile").hidden = tab !== "profile"; $("tSat").hidden = tab !== "sat";
    $$("#tabs button").forEach(b => b.setAttribute("aria-selected", String(b.dataset.tab === tab)));
    if (m) { renderHelp(); if (tab === "profile") renderGraph(); else if (tab === "sat") renderSat(); else renderForm(); }
    window.scrollTo(0, 0);
  }
  $("tabs").addEventListener("click", e => { const b = e.target.closest("button[data-tab]"); if (b) { setTab(b.dataset.tab); persist(); } });
  const hashTab = () => (location.hash === "#sat" ? "sat" : location.hash === "#info" ? "info" : location.hash === "#profile" ? "profile" : "");
  window.addEventListener("hashchange", () => { if (hashTab()) setTab(hashTab()); }); // the home-screen shortcut to the satellite text
  function renderAll() {
    press("#refSeg button", b => b.dataset.ref === m.ref); press("#mirSeg button", b => (b.dataset.mir === "1") === !!cfg.mirror); press("#themeSeg button", b => b.dataset.theme === (cfg.theme === "light" ? "light" : "dark"));
    $("autoAdd").checked = cfg.auto !== false;
    renderForm(); renderTables(); renderGraph(); renderSel(); renderFlags(); renderSat(); renderObs(); renderHelp(); buttons(); micState();
  }

  /* ---------- install + offline ---------- */
  let installEvt = null;
  window.addEventListener("beforeinstallprompt", e => { e.preventDefault(); installEvt = e; $("installBtn").hidden = false; });
  $("installBtn").onclick = async () => {
    if (installEvt) { installEvt.prompt(); await installEvt.userChoice.catch(() => null); installEvt = null; $("installBtn").hidden = true; }
    else toast("On iPhone: tap Share, then Add to Home Screen.");
  };
  window.addEventListener("appinstalled", () => { $("installBtn").hidden = true; toast("Installed. It works without signal."); });
  const standalone = matchMedia("(display-mode: standalone)").matches || navigator.standalone;
  if (!standalone && /iphone|ipad|ipod/i.test(navigator.userAgent)) $("installBtn").hidden = false;
  if ("serviceWorker" in navigator && location.protocol !== "file:") navigator.serviceWorker.register("sw.js").catch(() => {});
  let rsT = null; window.addEventListener("resize", () => { clearTimeout(rsT); rsT = setTimeout(renderGraph, 120); });

  /* ---------- boot ---------- */
  load(); applyTheme();
  setTab(hashTab() || cfg.tab);
  renderAll();
  window.__voice = { add, model: () => m, flush }; // for tests
})();
