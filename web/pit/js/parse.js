/* Snowpack Translator: spoken observation parser.
   Turns the words a phone's dictation wrote down into observation actions with a fixed vocabulary: no model, no LLM,
   nothing guessed. A word it does not know is returned as "not understood" so the observer can see and fix it.
   Works in the browser (window.SnowSpeak) and in Node (tests). */
(function (root, factory) {
  if (typeof module === "object" && module.exports) module.exports = factory();
  else root.SnowSpeak = factory();
})(typeof self !== "undefined" ? self : this, function () {
  "use strict";

  /* ---------- vocabulary ---------- */
  const PHRASES = []; // [words[], kind, value]
  const add = (kind, value, alts) => alts.split("|").forEach(a => PHRASES.push([a.trim().split(/\s+/), kind, value]));

  // grain forms (IACS 2009 codes); common dictation mis-hearings are listed beside the real words
  add("grain", "SH", "surface hoar|surface whore|surface hore|surface hor|surface horror|surface or|hoar frost|hoarfrost|buried surface hoar|sh");
  add("grain", "DH", "depth hoar|depth whore|depth hore|depth hor|death hoar|deaths hoar|sugar snow|dh");
  add("grain", "FCxr", "rounding facets|rounded facets|rounding faceted|mixed forms|fcxr");
  add("grain", "RGxf", "faceted rounds|faceting rounds|rgxf");
  add("grain", "FCsf", "near surface facets|fcsf");
  add("grain", "FC", "facets|facet|faceted crystals|faceted grains|faceted|faucets|fawcetts|fawcett|fc");
  add("grain", "RGwp", "wind packed rounds|wind packed|wind slab grains|rgwp");
  add("grain", "RGsr", "small rounds|rgsr");
  add("grain", "RGlr", "large rounds|rglr");
  add("grain", "RG", "rounded grains|round grains|rounds|rounded|rg");
  add("grain", "DF", "decomposing fragments|decomposing and fragmented|decomposing|decomposed|fragmented|df");
  add("grain", "PPgp", "graupel|grapple|ppgp");
  add("grain", "PPsd", "stellar dendrites|stellars|stellar|ppsd");
  add("grain", "PP", "precipitation particles|precip particles|new snow|fresh snow|pp");
  add("grain", "IFrc", "rain crust|ifrc");
  add("grain", "IFsc", "sun crust|ifsc");
  add("grain", "MFcr", "melt freeze crust|meltfreeze crust|crust|mfcr");
  add("grain", "MF", "melt forms|melt form|melt freeze|wet grains|corn snow|corn|mf");
  add("grain", "IF", "ice formation|ice formations|ice lens|ice lense|ice layer|ice crust");
  add("grain", "MM", "machine made");

  add("hard", "4F", "four finger|four fingers|4 finger|4 fingers|for finger|for fingers|fore finger|forefinger|4f|four f|4 f");
  add("hard", "1F", "one finger|1 finger|won finger|1f|one f|1 f");
  add("hard", "F", "fist|fists");
  add("hard", "P", "pencil|pencils");
  add("hard", "K", "knife|knives");
  add("hard", "I", "ice");

  add("wet", "V", "very wet");
  add("wet", "D", "dry");
  add("wet", "M", "moist");
  add("wet", "W", "wet");
  add("wet", "S", "soaked|slush|slushy");

  add("concern", true, "weak layer|layer of concern|problem layer|persistent weak layer|pwl|of concern|failure layer|critical layer");

  const DIR_WORDS = { north: "N", south: "S", east: "E", west: "W" };
  ["north", "south"].forEach(a => ["east", "west"].forEach(b => {
    const c = DIR_WORDS[a] + DIR_WORDS[b];
    add("dir", c, `${a} ${b}|${a}${b}|${c.toLowerCase()}`);
    add("dir", DIR_WORDS[a] + c, `${a} ${a} ${b}|${a} ${a}${b}|${(DIR_WORDS[a] + c).toLowerCase()}`);
    add("dir", DIR_WORDS[b] + c, `${b} ${a} ${b}|${b} ${a}${b}|${(DIR_WORDS[b] + c).toLowerCase()}`);
  }));
  Object.keys(DIR_WORDS).forEach(w => add("dir", DIR_WORDS[w], w));

  // lead words: each starts a new statement
  add("lead", "layer", "layer|next layer|new layer|then layer|add layer");
  add("lead", "next", "next|then");
  add("lead", "edit", "change layer|edit layer|fix layer|correct layer|correction layer");
  add("lead", "dellayer", "delete layer|remove layer");
  add("lead", "temp", "temperature|temperatures|temp|temps|snow temperature|snow temperatures|snow temp|snow temps");
  add("lead", "tsurf", "surface temperature|surface temp|snow surface temperature");
  add("lead", "air", "air temperature|air temp|air");
  add("lead", "tmax", "max temperature|max temp|maximum temperature|maximum temp|t max|tmax|max air|high temperature|daytime high");
  add("lead", "tmin", "min temperature|min temp|minimum temperature|minimum temp|t min|tmin|min air|low temperature|overnight low");
  add("lead", "ct", "compression test|compression|ct");
  add("lead", "ect", "extended column test|extended column|ect");
  add("lead", "pst", "propagation saw test|propagation saw|saw test|pst");
  add("lead", "rb", "rutschblock test|rutschblock|rutsch block|rootch block|rb");
  add("lead", "dt", "deep tap test|deep tap|dt");
  add("lead", "location", "location|site");
  add("lead", "observer", "observer|observers|observed by");
  add("lead", "org", "organization|organisation|operation|org");
  add("lead", "name", "profile name|pit name|profile title|title");
  add("lead", "aspect", "aspect");
  add("lead", "elev", "elevation|altitude");
  add("lead", "slope", "slope angle|slope|incline");
  add("lead", "hs", "snow depth|total depth|total snow depth|height of snow|snowpack depth|hs");
  add("lead", "sky", "sky cover|sky|cloud cover|clouds|cloud");
  add("lead", "precip", "precipitation|precip|snowfall rate");
  add("lead", "snowing", "snowing");
  add("lead", "raining", "raining");
  add("lead", "wind", "wind speed|winds|wind");
  add("lead", "blow", "blowing snow|snow transport");
  add("lead", "hn24", "hn24|hn 24|24 hour snow|24 hour snowfall|overnight snow|new snow amount|snowfall");
  add("lead", "hst", "hst|storm snow|storm total");
  add("lead", "foot", "foot penetration|foot pen|boot penetration|boot pen|pf");
  add("lead", "ski", "ski penetration|ski pen|ps");
  add("lead", "surf", "surface grain|surface form|surface snow");
  add("lead", "avs", "avalanches|avalanche|avalanche activity|avalanche observations|avs");
  add("lead", "note", "note|notes|comment|comments|remark|remarks");
  add("lead", "lnote", "layer note|layer name|layer comment|label|called");
  add("lead", "undo", "scratch that|undo that|undo|delete that|cancel that");

  const BY_FIRST = {};
  PHRASES.sort((a, b) => b[0].length - a[0].length).forEach(p => { (BY_FIRST[p[0][0]] = BY_FIRST[p[0][0]] || []).push(p); });

  const UNITS = { zero: 0, one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9 };
  const TEENS = { ten: 10, eleven: 11, twelve: 12, thirteen: 13, fourteen: 14, fifteen: 15, sixteen: 16, seventeen: 17, eighteen: 18, nineteen: 19 };
  const TENS = { twenty: 20, thirty: 30, forty: 40, fourty: 40, fifty: 50, sixty: 60, seventy: 70, eighty: 80, ninety: 90 };
  const isNumWord = w => w in UNITS || w in TEENS || w in TENS || w === "hundred" || w === "thousand";
  const DIGITS = /^-?(\d+\.?\d*|\.\d+)$/;
  const LETTER_RUNS = [["e", "c", "t", "p", "v"], ["e", "c", "t", "p"], ["e", "c", "t", "n"], ["e", "c", "t", "x"], ["e", "c", "t"], ["c", "t"], ["p", "s", "t"],
    ["h", "s", "t"], ["h", "s"], ["h", "n"], ["r", "b"]];
  const FILL = new Set(("the a an is are was at of and with on in it its from size sized grain grains hardness about around roughly um uh " +
    "okay ok then to too down by for cm centimetre centimetres centimeter centimeters mm millimetre millimetres millimeter " +
    "millimeters mil mils degrees degree celsius c metres meters m that this there has have had got im i we approximately " +
    "up form forms type snow layer hard hand resistance depth deep high tall long").split(" "));
  const MINUS = new Set(["minus", "negative", "below"]);
  const PLUS = new Set(["plus", "positive", "above"]);

  /* ---------- tokens ---------- */
  function tokenize(text) {
    const out = [];
    String(text || "").replace(/[\u2212\u2013\u2014]/g, "-").replace(/[°º]/g, " degrees ").split(/\n+/).forEach((line, li) => {
      if (li) out.push({ k: "nl", n: "\n", r: "" });
      line.split(/\s+/).forEach(piece => {
        if (!piece) return;
        let n = piece.toLowerCase().replace(/['\u2019]/g, "").replace(/(\d),(?=\d{3}\b)/g, "$1");
        n = n.replace(/^[^a-z0-9+\-.]+/, "").replace(/[^a-z0-9+\-]+$/, "");
        n = n.replace(/^\.(?!\d)/, "").replace(/^[+-]+$/, m => m[0]);
        if (!n) return;
        const raw = piece.replace(/^[("\u201c]+|[)"\u201d]+$/g, "");
        let m;
        if ((m = /^(\d+(?:\.\d+)?)-(\d+(?:\.\d+)?)$/.exec(n))) { // 0-35, 10-3.6
          out.push({ n: m[1], r: m[1] }, { n: "to", r: "-", dash: true }, { n: m[2], r: m[2] });
        } else if ((m = /^(-?\d*\.?\d+)(cm|mm|m|ft|km|kmh|kg|c)$/.exec(n))) {
          out.push({ n: m[1], r: m[1] }, { n: m[2], r: m[2] });
        } else if ((m = /^(ectpv|ectp|ectn|ectx|ect|ctn|cte|ctm|cth|ct|pst|rb|dt)(\d{1,3})?(q[123]|sp|sc|rp|pc|bk|brk|b)?$/.exec(n)) && (m[2] || m[3] || m[1].length > 2)) {
          const base = /^ect/.test(m[1]) ? "ect" : /^ct/.test(m[1]) ? "ct" : m[1];
          out.push({ n: base, r: raw });
          if (m[1].length > base.length) out.push({ n: m[1].slice(base.length), r: "", letter: true });
          if (m[2]) out.push({ n: m[2], r: "" });
          if (m[3]) out.push({ n: m[3], r: "" });
        } else if (/^[a-z]+(-[a-z]+)+$/.test(n)) {
          const rs = raw.split("-"); n.split("-").forEach((w, i) => out.push({ n: w, r: rs[i] || w }));
        } else out.push({ n, r: raw });
      });
    });
    return out;
  }

  function joinLetters(t) { // "e c t p" -> "ectp", "h s" -> "hs"
    const out = [];
    for (let i = 0; i < t.length; i++) {
      const run = LETTER_RUNS.find(L => L.every((c, j) => t[i + j] && !t[i + j].k && t[i + j].n === c));
      if (run) {
        const word = run.join(""), r = t.slice(i, i + run.length).map(x => x.r).join(" ");
        if (/^ect.|^ct./.test(word) && word.length > (word[0] === "e" ? 3 : 2)) {
          out.push({ n: word[0] === "e" ? "ect" : "ct", r }, { n: word.slice(word[0] === "e" ? 3 : 2), r: "", letter: true });
        } else out.push({ n: word, r });
        i += run.length - 1;
      } else out.push(t[i]);
    }
    return out;
  }

  function phrases(t) {
    const out = [];
    for (let i = 0; i < t.length; i++) {
      const tok = t[i];
      if (tok.k) { out.push(tok); continue; }
      let m;
      if ((m = /^(4f|1f|f|p|k|i)([+-])$/.exec(tok.n))) { out.push({ k: "hard", v: m[1].toUpperCase(), mod: m[2], n: "h:" + m[1], r: tok.r }); continue; }
      const cand = BY_FIRST[tok.n]; let hit = null;
      if (cand) for (const p of cand) {
        if (p[0].every((w, j) => t[i + j] && !t[i + j].k && t[i + j].n === w)) { hit = p; break; }
      }
      if (hit) {
        out.push({ k: hit[1], v: hit[2], n: hit[1][0] + ":" + hit[2], r: t.slice(i, i + hit[0].length).map(x => x.r).filter(Boolean).join(" ") });
        i += hit[0].length - 1;
      } else out.push(tok);
    }
    return out;
  }

  function numbers(t) {
    const out = [], plain = x => x && !x.k;
    const fraction = j => { // digits after "point"
      let s = "", used = 0;
      while (plain(t[j + used])) {
        const w = t[j + used].n;
        if (w in UNITS) s += UNITS[w]; else if (/^\d+$/.test(w)) s += w; else break;
        used++;
      }
      return { s, used };
    };
    for (let i = 0; i < t.length; i++) {
      const tok = t[i];
      if (tok.k) { out.push(tok); continue; }
      let v = null, j = i, w = tok.n;
      if (DIGITS.test(w)) { v = parseFloat(w); j = i + 1; }
      else if (isNumWord(w) || (w === "a" && plain(t[i + 1]) && t[i + 1].n === "hundred")) {
        let cur = 0, total = 0, last = null; if (w === "a") j++;
        for (; plain(t[j]); j++) {
          const x = t[j].n;
          if (x in UNITS) { if (last === "unit" || last === "teen") break; cur += UNITS[x]; last = "unit"; }
          else if (x in TEENS) { if (last === "unit" || last === "teen" || last === "ten") break; cur += TEENS[x]; last = "teen"; }
          else if (x in TENS) { if (last === "unit" || last === "teen" || last === "ten") break; cur += TENS[x]; last = "ten"; }
          else if (x === "hundred") { cur = (cur || 1) * 100; last = "hundred"; }
          else if (x === "thousand") { total += (cur || 1) * 1000; cur = 0; last = "thousand"; }
          else if (x === "and" && (last === "hundred" || last === "thousand") && plain(t[j + 1]) && isNumWord(t[j + 1].n)) continue;
          else break;
        }
        v = total + cur;
      } else if (w === "point" && plain(t[i + 1]) && (t[i + 1].n in UNITS || /^\d+$/.test(t[i + 1].n))) { v = 0; j = i; }
      else if (w === "half" && plain(t[i + 1]) && /^(a|an|mm|mil|mils|millimet(re|er)s?|of)$/.test(t[i + 1].n)) { v = 0.5; j = i + 1; }
      if (v === null) { out.push(tok); continue; }
      if (plain(t[j]) && t[j].n === "point") {
        const f = fraction(j + 1);
        if (f.used) { v = parseFloat((v < 0 ? "-" : "") + Math.abs(Math.trunc(v)) + "." + f.s); j += 1 + f.used; }
      }
      if (plain(t[j]) && t[j].n === "and" && plain(t[j + 1]) && t[j + 1].n === "a" && plain(t[j + 2]) && t[j + 2].n === "half") { v += 0.5; j += 3; }
      out.push({ k: "num", v, n: String(v), r: t.slice(i, j).map(x => x.r).filter(Boolean).join(" ") });
      i = j - 1;
    }
    return out;
  }

  const isNum = t => !!t && t.k === "num";
  const isTo = t => !!t && !t.k && (t.n === "to" || t.n === "too" || t.n === "through" || t.n === "until" || t.n === "till");
  const isMinus = t => !!t && !t.k && (MINUS.has(t.n) || t.n === "-");
  const SKY = { clear: "CLR", clr: "CLR", sunny: "CLR", few: "FEW", scattered: "SCT", sct: "SCT", broken: "BKN", bkn: "BKN", overcast: "OVC", ovc: "OVC", cloudy: "OVC", obscured: "X", fog: "X", foggy: "X", whiteout: "X" };
  const WINDSPD = { calm: "C", light: "L", moderate: "M", strong: "S", extreme: "X", variable: "VAR" };
  const NONE = /^(none|nil|no|nothing|zero|0)$/;

  function validLead(tok, i, t, cur) {
    const v = tok.v, nx = t[i + 1], near = n => t.slice(i + 1, i + 1 + n);
    const inLayer = cur.lead && /^(layer|next|edit|ct|ect|pst|rb|dt|pre)$/.test(cur.lead.v);
    if (v === "note" || v === "lnote") return !inLayer;
    if (v === "next") return isNum(nx) || isTo(nx) || (nx && (nx.k === "hard" || nx.k === "grain"));
    if (v === "temp" || v === "tsurf") return near(3).some(x => isNum(x) || isMinus(x));
    if (/^(air|tmax|tmin|elev|slope|hs|hn24|hst|foot|ski)$/.test(v)) return near(2).some(x => isNum(x) || isMinus(x) || (x && !x.k && PLUS.has(x.n)));
    if (v === "aspect") return !!nx && (nx.k === "dir" || isNum(nx) || /^[nsew]$/.test(nx.n));
    if (v === "sky") return !!nx && !nx.k && (nx.n in SKY || nx.n === "partly" || nx.n === "mostly");
    if (v === "wind") return !!nx && (nx.k === "dir" || isNum(nx) || (!nx.k && (nx.n in WINDSPD || NONE.test(nx.n))));
    if (v === "precip") return !!nx && (isNum(nx) || (!nx.k && /^(none|nil|no|nothing|snow|rain|light|moderate|heavy|very|s-?\d+|r[vlmh]|flurries|s)$/.test(nx.n)));
    if (v === "location" || v === "observer" || v === "org" || v === "name") return !!nx && nx.k !== "nl";
    return true;
  }

  function segment(t) {
    const segs = []; let cur = { lead: null, toks: [] };
    const close = () => { if (cur.lead || cur.toks.length) segs.push(cur); };
    t.forEach((tok, i) => {
      if (tok.k === "nl") { close(); cur = { lead: null, toks: [] }; return; }
      if (cur.lead && (cur.lead.v === "note" || cur.lead.v === "avs")) { cur.toks.push(tok); return; }
      if (tok.k === "lead" && validLead(tok, i, t, cur)) { close(); cur = { lead: tok, toks: [] }; return; }
      // a bare depth range starts a layer: "35 to 70 four finger"
      if (!cur.lead && !cur.toks.length && isNum(tok) && (isTo(t[i + 1]) && isNum(t[i + 2]))) { cur = { lead: { k: "lead", v: "layer", r: "" }, toks: [tok] }; return; }
      cur.toks.push(tok);
    });
    close();
    return segs;
  }

  /* ---------- statement parsers ---------- */
  const rawOf = toks => toks.map(x => x.r).filter(Boolean).join(" ").replace(/^[\s,;:.]+|[\s,;:]+$/g, "").replace(/([^.])\.$/, "$1").trim();
  const title = s => (s && s === s.toLowerCase()) ? s.replace(/\b[a-z]/g, c => c.toUpperCase()) : s;
  const cap = s => s ? s[0].toUpperCase() + s.slice(1) : s;
  const textOf = toks => toks.map(x => x.n).join(" ");

  function parseLayer(toks, lead) {
    const a = { type: "layer", mode: "current", attrs: {}, unknown: [] };
    let i = 0;
    const skip = () => { while (i < toks.length && !toks[i].k && FILL.has(toks[i].n) && !isTo(toks[i])) i++; };
    const cmAt = j => { while (toks[j] && !toks[j].k && /^(cm|centimet(re|er)s?)$/.test(toks[j].n)) j++; return j; };
    skip();
    if (lead === "edit" || lead === "dellayer") {
      if (isNum(toks[i])) { a.mode = "index"; a.index = toks[i].v; i++; }
      if (lead === "dellayer") return { type: "dellayer", index: a.index };
    } else if (isTo(toks[i]) && isNum(toks[i + 1])) { a.mode = "to"; a.b = toks[i + 1].v; i += 2; }
    else if (isNum(toks[i]) && toks[i].v >= 0) {
      const A = toks[i].v; let j = cmAt(i + 1);
      if (isTo(toks[j]) && isNum(toks[j + 1])) { a.mode = "range"; a.a = A; a.b = toks[j + 1].v; i = j + 2; }
      else if (isNum(toks[j]) && toks[j].v === 2 && isNum(toks[j + 1]) && toks[j + 1].v !== A && !(toks[j + 2] && /^(mm|mil|millimet)/.test(toks[j + 2].n))) {
        a.mode = "range"; a.a = A; a.b = toks[j + 1].v; i = j + 2; // "0 2 35": dictation wrote "to" as 2
      } else if (toks[j] && !toks[j].k && /^(thick|thickness)$/.test(toks[j].n)) { a.mode = "thick"; a.b = A; i = j + 1; }
      else if (toks[j] && !toks[j].k && /^(mm|mil|mils|millimet(re|er)s?)$/.test(toks[j].n)) { /* a size, not a depth */ }
      else { a.mode = "to"; a.b = A; i = j; }
    }
    const hards = [], grains = [], sizes = [];
    for (; i < toks.length; i++) {
      const t = toks[i], nx = toks[i + 1];
      if (t.k === "hard") {
        let mod = t.mod || "";
        if (!mod && nx && !nx.k && (nx.n === "plus" || nx.n === "+" || nx.n === "minus" || nx.n === "-")) { mod = /plus|\+/.test(nx.n) ? "+" : "-"; i++; }
        hards.push(t.v === "I" ? "I" : t.v + mod);
      } else if (t.k === "grain") grains.push(t.v);
      else if (t.k === "wet") a.attrs.wet = t.v;
      else if (t.k === "concern") a.attrs.concern = true;
      else if (t.k === "lead" && (t.v === "note" || t.v === "lnote")) {
        const rest = toks.slice(i + 1); if (rest.some(x => x.k === "concern")) a.attrs.concern = true;
        a.attrs.note = rawOf(rest.filter(x => x.k !== "concern")); break;
      }
      else if (t.k === "num") {
        const prev = toks[i - 1];
        if (prev && !prev.k && prev.n === "density") a.attrs.density = t.v;
        else if (nx && !nx.k && /^(kg|kilograms?|kilos?)$/.test(nx.n)) { a.attrs.density = t.v; i++; }
        else if (prev && !prev.k && /^(max|maximum)$/.test(prev.n)) sizes.push({ v: t.v, max: true });
        else sizes.push({ v: t.v });
      } else if (!t.k && (FILL.has(t.n) || /^(density|max|maximum|kg|per|cubic|metre|meter)$/.test(t.n))) { /* filler */ }
      else if (t.k === "dir" || t.k === "lead") a.unknown.push(t.r);
      else a.unknown.push(t.r);
    }
    if (!hards.length) { // dictation often writes "fist" as "first"
      const k = a.unknown.findIndex(u => /^first$/i.test(u));
      if (k >= 0) { hards.push("F"); a.unknown.splice(k, 1); a.heardFirst = true; }
    }
    if (hards[0]) a.attrs.hardTop = hards[0];
    if (hards[1]) a.attrs.hardBot = hards[1];
    if (grains[0]) a.attrs.grain = grains[0];
    if (grains[1]) a.attrs.grain2 = grains[1];
    const ok = sizes.filter(s => s.v > 0 && s.v <= 60);
    sizes.filter(s => !(s.v > 0 && s.v <= 60)).forEach(s => a.unknown.push(String(s.v)));
    if (ok.length === 1) a.attrs.size = ok[0].v;
    else if (ok.length >= 2) {
      a.attrs.size = Math.min(ok[0].v, ok[1].v); a.attrs.sizeMax = Math.max(ok[0].v, ok[1].v);
      ok.slice(2).forEach(s => a.unknown.push(String(s.v)));
    }
    return a;
  }

  function signedNums(toks) {
    const out = []; let neg = false, pos = false;
    toks.forEach(t => {
      if (isMinus(t) || (t.dash && !t.k)) neg = true;
      else if (!t.k && PLUS.has(t.n)) pos = true;
      else if (isNum(t)) { out.push({ v: neg ? -Math.abs(t.v) : t.v, signed: neg || pos || t.v < 0 }); neg = pos = false; }
    });
    return out;
  }

  function parseTemps(toks, lead) {
    const ns = signedNums(toks), acts = [];
    if (lead === "tsurf") { if (ns.length) acts.push({ type: "temp", depth: 0, t: -Math.abs(ns[0].v) }); else acts.push({ type: "unknown", text: "surface temperature " + rawOf(toks) }); return acts; }
    if (ns.length < 2) return [{ type: "unknown", text: "temperature " + rawOf(toks), hint: "Say the depth, then the temperature: \u201ctemperature 20 minus 3.5\u201d." }];
    for (let i = 0; i + 1 < ns.length; i += 2) {
      let d = ns[i].v, T = ns[i + 1].v;
      if (d < 0 && T >= 0) { const s = d; d = T; T = s; }
      acts.push({ type: "temp", depth: Math.abs(d), t: -Math.abs(T) });
    }
    if (ns.length % 2) acts.push({ type: "unknown", text: String(ns[ns.length - 1].v), hint: "A temperature reading needs a depth and a temperature." });
    return acts;
  }

  function parseTest(toks, lead) {
    const a = { type: "test", test: lead.toUpperCase(), unknown: [] };
    const ni = toks.findIndex(t => t.k === "lead" && (t.v === "note" || t.v === "lnote"));
    if (ni >= 0) { a.note = rawOf(toks.slice(ni + 1)); toks = toks.slice(0, ni); }
    let s = " " + textOf(toks) + " ";
    const take = re => { const m = re.exec(s); if (m) s = s.replace(m[0], " "); return m; };
    let m;
    if ((m = take(/ (?:down|up|at|depth|below|height|@)(?: (?:to|is|of))? (\d+(?:\.\d+)?)(?= )/)) || (m = take(/ on (\d+(?:\.\d+)?)(?= )/))) a.depth = +m[1];
    const g = toks.find(t => t.k === "grain"); if (g) { a.onGrain = g.v; s = s.replace(/ g:\w+/g, " "); }
    if (lead === "pst") {
      if ((m = take(/ (\d+)\s*(?:\/|of|over|out of)\s*(\d+)(?= )/)) || (m = take(/ (\d+)\/(\d+)(?= )/))) { a.cut = +m[1]; a.col = +m[2]; }
      if (take(/ (to )?end(?= )/)) a.res = "End"; else if (take(/ (self )?arr(est|ested)?(?= )/)) a.res = "Arr"; else if (take(/ (slab fracture|sf)(?= )/)) a.res = "SF";
    }
    if (take(/ sudden planar(?= )| sp(?= )| pops?(?= )/)) a.char = "SP";
    else if (take(/ sudden collapse(?= )| sc(?= )| drops?(?= )/)) a.char = "SC";
    else if (take(/ resistant planar(?= )| rp(?= )/)) a.char = "RP";
    else if (take(/ progressive compression(?= )| pc(?= )/)) a.char = "PC";
    else if (take(/ non planar break(?= )| break(?= )| brk(?= )| bk(?= )/)) a.char = "BRK";
    if ((m = take(/ (?:q|quality|shear quality) ?([123])(?= )/))) a.q = "Q" + m[1];
    if (lead === "rb") {
      if (take(/ whole block(?= )| wb(?= )/)) a.rel = "WB"; else if (take(/ most of (the )?block(?= )| mb(?= )/)) a.rel = "MB"; else if (take(/ edge( of (the )?block| only)?(?= )| eb(?= )/)) a.rel = "EB";
    }
    if (lead === "ect") {
      if (take(/ (no propagation|does ?not propagate|doesnt propagate|did ?not propagate|not propagat\w*|no prop|initiat\w+ only)(?= )/) || take(/^ n(?= )/)) a.prop = "N";
      else if (take(/ (on isolation|during isolation|while isolating)(?= )/) || take(/^ pv(?= )/)) a.prop = "PV";
      else if (take(/ (full propagation|propagat\w+)(?= )/) || take(/^ p(?= )/)) a.prop = "P";
      else if (take(/ (no fracture|no result|no failure|nil|none)(?= )/) || take(/^ x(?= )/)) a.prop = "X";
    } else if (lead === "ct" || lead === "dt") {
      if (take(/ (no fracture|no result|no failure|nil|none|negative|did not fail)(?= )/) || take(/^ n(?= )/)) a.none = true;
      else if (take(/ easy(?= )/) || take(/^ e(?= )/)) a.band = "E";
      else if (take(/ moderate(?= )/) || take(/^ m(?= )/)) a.band = "M";
      else if (take(/ hard(?= )/) || take(/^ h(?= )/)) a.band = "H";
    }
    const nums = (s.match(/ -?\d+(?:\.\d+)?(?= )/g) || []).map(Number);
    s = s.replace(/ -?\d+(?:\.\d+)?(?= )/g, " ");
    if (lead !== "pst" && nums.length) a.score = nums.shift();
    if (a.depth == null && nums.length) a.depth = nums.shift();
    nums.forEach(n => a.unknown.push(String(n)));
    s.trim().split(/\s+/).filter(w => w && !FILL.has(w) && !/^(test|taps?|score|result|fracture|character|release|type|cut|column)$/.test(w))
      .forEach(w => a.unknown.push(w.replace(/^[a-z]:/, "")));
    return a;
  }

  function compass(deg) { return ["N", "NE", "E", "SE", "S", "SW", "W", "NW"][Math.round((((deg % 360) + 360) % 360) / 45) % 8]; }
  function dirOf(toks) {
    const d = toks.find(t => t.k === "dir"); if (d) return d.v;
    const l = toks.find(t => !t.k && /^[nsew]{1,3}$/.test(t.n)); return l ? l.n.toUpperCase() : "";
  }
  const set = (key, value, extra) => Object.assign({ type: "set", key, value }, extra || {});

  function parseField(lead, toks) {
    const ns = signedNums(toks), txt = " " + textOf(toks) + " ", raw = rawOf(toks);
    const need = () => [{ type: "unknown", text: (lead + " " + raw).trim() }];
    switch (lead) {
      case "location": return raw ? [set("site.location", title(raw))] : need();
      case "observer": return raw ? [set("site.observer", title(raw))] : need();
      case "org": return raw ? [set("site.org", title(raw))] : need();
      case "name": return raw ? [set("name", cap(raw))] : need();
      case "aspect": { const d = dirOf(toks) || (ns.length ? compass(ns[0].v) : ""); return d ? [set("site.aspect", d)] : need(); }
      case "elev": {
        if (!ns.length) return need();
        let v = ns[0].v; if (ns.length > 1 && v >= 10 && v < 100 && ns[1].v < 100) v = v * 100 + ns[1].v; // "twenty-two thirty"
        const unit = / (feet|foot|ft) /.test(txt) ? "ft" : / (metres|meters|metre|meter|m) /.test(txt) ? "m" : null;
        const out = [set("site.elev", v)]; if (unit) out.unshift(set("site.elevUnit", unit, { quiet: true })); return out;
      }
      case "slope": return ns.length ? [set("site.slope", ns[0].v)] : need();
      case "hs": return ns.length ? [set("surf.hs", Math.abs(ns[0].v))] : need();
      case "air": return ns.length ? [set("wx.air", ns[0].v)] : need();
      case "tmax": return ns.length ? [set("wx.tmax", ns[0].v)] : need();
      case "tmin": return ns.length ? [set("wx.tmin", ns[0].v)] : need();
      case "hn24": return ns.length ? [set("wx.hn24", Math.abs(ns[0].v))] : need();
      case "hst": return ns.length ? [set("wx.hst", Math.abs(ns[0].v))] : need();
      case "foot": return ns.length ? [set("surf.foot", Math.abs(ns[0].v))] : need();
      case "ski": return ns.length ? [set("surf.ski", Math.abs(ns[0].v))] : need();
      case "sky": {
        const w = toks.find(t => !t.k && t.n in SKY);
        const v = / partly cloudy /.test(txt) ? "SCT" : / mostly cloudy /.test(txt) ? "BKN" : w ? SKY[w.n] : "";
        return v ? [set("wx.sky", v)] : need();
      }
      case "precip": case "snowing": case "raining": {
        let v = "", m;
        const rain = lead === "raining" || / rain/.test(txt), I = / very light /.test(txt) || / flurries /.test(txt) ? 0 : / very heavy /.test(txt) ? 4 : / light/.test(txt) ? 1 : / moderate/.test(txt) ? 2 : / heavy/.test(txt) ? 3 : -1;
        if ((m = / (s-?\d+|r[vlmh]) /.exec(txt))) v = m[1].toUpperCase();
        else if ((m = / s (minus |- ?)?(-?\d+) /.exec(txt))) v = "S" + (m[1] || +m[2] < 0 ? "-" : "") + Math.abs(+m[2]);
        else if (rain) v = ["RV", "RL", "RM", "RH", "RH"][I < 0 ? 1 : I];
        else if (lead === "snowing" || / snow/.test(txt) || I >= 0) v = ["S-1", "S1", "S2", "S5", "S10"][I < 0 ? 1 : I];
        else if (/ (none|nil|no|nothing) /.test(txt)) v = "NIL";
        else if (ns.length) v = "S" + (ns[0].v < 0 ? "-" : "") + Math.abs(ns[0].v);
        return v ? [set("wx.precip", v)] : need();
      }
      case "wind": {
        const w = toks.find(t => !t.k && t.n in WINDSPD); let spd = w ? WINDSPD[w.n] : "";
        const out = [];
        if (!spd && ns.length) { const k = Math.abs(ns[0].v); spd = k < 1 ? "C" : k <= 25 ? "L" : k <= 40 ? "M" : k <= 60 ? "S" : "X"; out.push(set("wx.windKmh", k, { quiet: true })); }
        if (!spd && / (none|nil|no|nothing) /.test(txt)) spd = "C";
        const d = dirOf(toks);
        if (!spd && !d) return need();
        if (spd) out.unshift(set("wx.windSpd", spd)); if (d) out.push(set("wx.windDir", d));
        return out;
      }
      case "blow": {
        const v = / (none|nil|no|nothing) /.test(txt) ? "NIL" : / prev/.test(txt) ? "Prev" : / light /.test(txt) ? "L" : / moderate /.test(txt) ? "M" : / (intense|strong|heavy) /.test(txt) ? "I" : "";
        const d = dirOf(toks); if (!v && !d) return need();
        const out = []; if (v) out.push(set("wx.blow", v)); if (d) out.push(set("wx.blowDir", d)); return out;
      }
      case "surf": {
        const g = toks.find(t => t.k === "grain"); const out = [];
        if (g) out.push(set("surf.grain", g.v)); if (ns.length) out.push(set("surf.size", Math.abs(ns[0].v)));
        return out.length ? out : need();
      }
      case "avs": return raw ? [{ type: "text", key: "avs", text: raw }] : need();
      case "note": return raw ? [{ type: "text", key: "notes", text: raw }] : need();
      case "lnote": return raw ? [{ type: "layer", mode: "current", attrs: { note: raw }, unknown: [] }] : need();
      case "undo": return [{ type: "undo" }];
    }
    return need();
  }

  /* ---------- entry point ---------- */
  function parse(text) {
    const toks = numbers(phrases(joinLetters(tokenize(text))));
    const actions = [];
    segment(toks).forEach(seg => {
      const lead = seg.lead ? seg.lead.v : null;
      if (!lead) {
        const meaningful = seg.toks.some(t => t.k === "hard" || t.k === "grain" || t.k === "wet" || t.k === "concern");
        if (meaningful) actions.push(parseLayer(seg.toks, "pre"));
        else { const r = rawOf(seg.toks.filter(t => t.k || !FILL.has(t.n))); if (r) actions.push({ type: "unknown", text: rawOf(seg.toks) }); }
      } else if (lead === "layer" || lead === "next" || lead === "edit" || lead === "dellayer") actions.push(parseLayer(seg.toks, lead));
      else if (lead === "temp" || lead === "tsurf") actions.push(...parseTemps(seg.toks, lead));
      else if (/^(ct|ect|pst|rb|dt)$/.test(lead)) actions.push(parseTest(seg.toks, lead));
      else actions.push(...parseField(lead, seg.toks));
    });
    return actions;
  }

  return { parse, tokenize, compass, _tokens: text => numbers(phrases(joinLetters(tokenize(text)))) };
});
