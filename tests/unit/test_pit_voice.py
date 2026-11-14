"""The Snowpack Translator's voice page (web/pit/js): the spoken-observation parser, the observation it builds,
the yellow-flag count, the satellite text and the PDF writer, run in node on dictated sentences."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

JS = Path(__file__).resolve().parents[2] / "web" / "pit" / "js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")

PRELUDE = f"""
const P = require({json.dumps(str(JS / "parse.js"))}), M = require({json.dumps(str(JS / "model.js"))}),
      D = require({json.dumps(str(JS / "draw.js"))});
function build(ref, lines) {{
  const m = M.blank(); m.ref = ref; m.site.date = "2026-11-12T14:40"; const said = [];
  lines.forEach(t => M.apply(m, P.parse(t)).forEach(r => said.push([r.ok, !!r.warn, r.say])));
  return {{ m, said }};
}}
"""

# The pit in the owner's Snow Scope screenshot (2026-11-12, Nakiska), spoken the way an observer would.
NAKISKA = [
    "Layer 0 to 35 fist plus rounds 0.3. Layer 35 to 70 four finger plus rounds point five. "
    "Layer 70 to 100 fist facets with depth hoar 1.5 max 2 weak layer",
    "location Nakiska observer Nicholas Sexton aspect north east elevation 7328 feet slope 34 degrees snow depth 100",
    "air temperature minus 7.8 sky broken precip none wind light south west",
    "temperatures 10 minus 3.6, 20 minus 3.5, 30 -3.2, 40 minus 2.9",
    "compression test twenty one Q2 down 88",
]


def _run(body: str):
    out = subprocess.run([NODE, "-e", PRELUDE + body], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _parse(text: str):
    return _run(f"console.log(JSON.stringify(P.parse({json.dumps(text)})));")


def test_layers_with_hardness_grain_and_size():
    a, b, c = _parse(NAKISKA[0])
    assert (a["a"], a["b"], a["attrs"]) == (0, 35, {"hardTop": "F+", "grain": "RG", "size": 0.3})
    assert (b["a"], b["b"], b["attrs"]) == (35, 70, {"hardTop": "4F+", "grain": "RG", "size": 0.5})
    assert c["attrs"] == {"concern": True, "hardTop": "F", "grain": "FC", "grain2": "DH", "size": 1.5, "sizeMax": 2}


@pytest.mark.parametrize("said, attrs", [
    ("layer 0 2 35 first rounds", {"hardTop": "F", "grain": "RG"}),                 # "to" heard as 2, "fist" as "first"
    ("35-70 4F+ rounds 0.5mm moist", {"wet": "M", "hardTop": "4F+", "grain": "RG", "size": 0.5}),
    ("layer one hundred ten to one hundred thirteen fist surface whore 4 to 8 millimetres label Jan 3rd weak layer",
     {"concern": True, "note": "Jan 3rd", "hardTop": "F", "grain": "SH", "size": 4, "sizeMax": 8}),
    ("layer 40 to 50 pencil minus to knife melt freeze crust", {"hardTop": "P-", "hardBot": "K", "grain": "MFcr"}),
    ("layer 10 to 30 for fingers faucets one and a half", {"hardTop": "4F", "grain": "FC", "size": 1.5}),
])
def test_dictation_quirks(said, attrs):
    (a,) = _parse(said)
    assert a["type"] == "layer" and a["mode"] == "range" and a["attrs"] == attrs and a["unknown"] == []


def test_attributes_without_depths_go_to_the_current_layer():
    r = _run("""const { m, said } = build("depth", ["layer 0 to 35", "fist rounds 0.3", "layer to 70 one finger", "next 20 thick pencil",
      "change layer 1 four finger", "delete layer 2", "the dog ran away"]);
      console.log(JSON.stringify({ L: m.layers.map(l => [l.top, l.bottom, l.hardTop, l.grain]), said }));""")
    assert r["L"] == [[0, 35, "4F", "RG"], [70, 90, "P", ""]]
    assert r["said"][-1] == [False, False, "Not understood: \u201cthe dog ran away\u201d"]


def test_site_weather_and_free_text():
    acts = _parse("location Wawa Ridge West Aspect, elevation twenty two thirty, aspect 315 degrees. "
                  "Wind 30 kilometres south. Snowing S2. Sky overcast. Max temp minus 5 min temp minus 12. "
                  "Foot pen 30 ski pen 15 HN24 5 storm snow 20 blowing snow moderate west. "
                  "Avalanches size 2 natural slab on north aspect at 2400 metres.")
    got = {a["key"]: a.get("value", a.get("text")) for a in acts}
    assert got == {"site.location": "Wawa Ridge West Aspect", "site.elev": 2230, "site.aspect": "NW", "wx.windSpd": "M",
                   "wx.windKmh": 30, "wx.windDir": "S", "wx.precip": "S2", "wx.sky": "OVC", "wx.tmax": -5, "wx.tmin": -12,
                   "surf.foot": 30, "surf.ski": 15, "wx.hn24": 5, "wx.hst": 20, "wx.blow": "M", "wx.blowDir": "W",
                   "avs": "size 2 natural slab on north aspect at 2400 metres"}


def test_stability_tests():
    r = _run("""const { m } = build("depth", ["layer 0 to 60 one finger rounds 1", "layer 60 to 62 fist surface hoar 6 label Jan 3rd", "layer 62 to 100 pencil rounds",
      "compression test twenty one Q2 down 88", "CT12 SP down 60 note clean", "ECTP 22 down 60. E C T N 15 at 45. ECTX",
      "PST 45 of 100 end down 70", "rutschblock 4 whole block down 50", "compression test no result", "deep tap 18 sudden collapse at 62"]);
      console.log(JSON.stringify(m.tests.map(t => [M.testLabel(t), t.depth, t.note])));""")
    assert r == [["CT21Q2", 88, ""], ["CT12SP", 60, "clean"], ["ECTP22", 60, ""], ["ECTN15", 45, ""], ["ECTX", None, ""],
                 ["PST45/100(End)", 70, ""], ["RB4(WB)", 50, ""], ["CTN", None, ""], ["DT18SC", 62, ""]]


def test_ect_on_isolation_and_heights_spoken_as_up():
    r = _run("""const { m } = build("height", ["snow depth 120", "ECTPV up 40", "extended column test on isolation at 35", "CT 12 SP up 60"]);
      console.log(JSON.stringify(m.tests.map(t => [M.testLabel(t), t.depth])));""")
    assert r == [["ECTPV", 80], ["ECTPV", 85], ["CT12SP", 60]]


def test_snow_depth_cannot_drop_below_what_is_recorded():
    """Heights are measured from the ground, so a lower HS would lift recorded layers above the surface."""
    r = _run("""const { m, said } = build("height", ["layer 150 to 141 fist new snow", "snow depth 120", "snow depth 160"]);
      console.log(JSON.stringify({ said: said.map(s => s[0]), hs: m.surf.hs, top: M.toRef(m, m.layers[0].top), conflict: [M.hsConflict(m, 140), M.hsConflict(m, 150)] }));""")
    assert r == {"said": [True, False, True], "hs": 160, "top": 150, "conflict": [150, None]}


def test_snow_scope_pit_flags_and_satellite_text():
    r = _run(f"""const {{ m, said }} = build("depth", {json.dumps(NAKISKA)});
      const text = M.satText(m);
      console.log(JSON.stringify({{ bad: said.filter(s => !s[0] || s[1]), flags: M.flags(m).max, temps: m.temps.map(t => [t.depth, t.t]),
        text, info: M.textInfo(text), sms: M.smsLink("+1 403 555 0123", "Sky BKN, Ta -7.8", true), unit: m.site.elevUnit }}));""")
    assert r["bad"] == [] and r["unit"] == "ft"
    assert r["flags"]["n"] == 6 and r["flags"]["depth"] == 70      # as Snow Scope shows for this pit: 6 at 70 cm
    assert r["temps"] == [[10, -3.6], [20, -3.5], [30, -3.2], [40, -2.9]]
    assert r["text"] == ("12Nov 1440 Nakiska 7328ft NE 34deg. Sky BKN, Pcp NIL, Wind L SW, Ta -7.8. HS 100, Surf RG 0.3. "
                         "Pit: CT21Q2 dn88 FC1.5-2.0; 6 flags dn70")
    assert r["info"] == {"chars": len(r["text"]), "gsm": True, "parts": 1}
    assert r["sms"] == "sms:+14035550123&body=Sky%20BKN%2C%20Ta%20-7.8"


def test_yellow_flag_thresholds():
    """Jamieson and Schweizer (2005): size > 1 mm, softer than 1F, persistent grains, size difference > 0.5 mm,
    hardness difference > 1 step, interface 20 to 85 cm deep."""
    r = _run("""const f = lines => M.flags(build("depth", lines).m).list.map(o => [o.depth, o.n, Object.keys(o.f).filter(k => o.f[k]).sort()]);
      console.log(JSON.stringify([
        f(["layer 0 to 30 pencil rounds 0.5", "layer 30 to 60 pencil rounds 1"]),
        f(["layer 0 to 30 one finger rounds 1", "layer 30 to 60 four finger rounds 1"]),
        f(["layer 0 to 10 pencil rounds 0.5", "layer 10 to 60 four finger minus depth hoar 2 to 4"]),
      ]));""")
    assert r[0] == [[30, 1, ["depth"]]]
    assert r[1] == [[30, 2, ["depth", "hard"]]]          # exactly one step apart is not "more than one"
    assert r[2] == [[10, 5, ["dHard", "dSize", "hard", "size", "type"]]]


def test_heights_from_the_ground():
    r = _run("""const { m } = build("height", ["layer 150 to 141 fist new snow 2", "layer 141 to 115 four finger decomposing",
      "to 100 one finger rounds", "snow depth 155", "CT12 down 141", "temperature 150 minus 6"]);
      console.log(JSON.stringify({ L: m.layers.map(l => [l.top, l.bottom, M.toRef(m, l.top)]), hs: m.surf.hs, test: m.tests[0].depth, temp: m.temps[0].depth,
        text: M.satText(m) }));""")
    assert r["L"] == [[5, 14, 150], [14, 40, 141], [40, 55, 115]] and r["hs"] == 155  # heights kept when HS is corrected
    assert r["test"] == 14 and r["temp"] == 5 and "CT12 up141 DF" in r["text"]


def test_pdf_and_svg_are_complete():
    r = _run(f"""const {{ m }} = build("depth", {json.dumps(NAKISKA)});
      const pdf = Buffer.from(D.profilePdf(m, new Date(2026, 10, 12, 14, 40))).toString("latin1");
      const xref = +/startxref\\n(\\d+)/.exec(pdf)[1], offs = pdf.slice(xref).split("\\n").slice(3).filter(l => / n $/.test(l)).map(l => +l.slice(0, 10));
      const svg = D.svg(D.screen(m, 358, 470, "dark"));
      console.log(JSON.stringify({{ head: pdf.slice(0, 8), eof: pdf.trimEnd().slice(-5), pages: (pdf.match(/\\/Type \\/Page /g) || []).length,
        objsOk: offs.every((o, i) => pdf.slice(o, o + String(i + 1).length + 6) === (i + 1) + " 0 obj"), n: offs.length,
        text: ["(CT21Q2)", "(Nicholas Sexton)", "(Hand Hardness)", "(7328 ft)"].map(t => pdf.includes(t)),
        svg: [svg.startsWith("<svg"), svg.endsWith("</svg>"), (svg.match(/class="lay"/g) || []).length, svg.includes(">CT21Q2<")],
        caaml: M.caaml(m).includes("<caaml:hardness uom=\\"\\">4F+</caaml:hardness>"), empty: D.profilePdf(M.blank()).length > 1000 }}));""")
    assert r["head"] == "%PDF-1.4" and r["eof"] == "%%EOF" and r["pages"] == 2
    assert r["objsOk"] and r["n"] >= 9 and all(r["text"])
    assert r["svg"] == [True, True, 3, True] and r["caaml"] and r["empty"]


def test_bars_from_the_right_and_an_empty_height_grid():
    """Mirror mode puts the table on the right, as the phone recorder does; with no HS a height axis has no numbers yet."""
    r = _run(f"""const {{ m }} = build("depth", {json.dumps(NAKISKA)});
      const head = o => +/<text x="([\\d.]+)"[^>]*>Grain</.exec(D.svg(D.screen(m, 358, 470, "dark", o)))[1];
      const blank = M.blank(); blank.ref = "height"; const empty = D.svg(D.screen(blank, 358, 470, "dark"));
      const pdf = Buffer.from(D.profilePdf(m, new Date(2026, 10, 12), {{ mirror: true }})).toString("latin1");
      console.log(JSON.stringify({{ left: head({{}}), right: head({{ mirror: true }}), empty: [empty.includes(">-10<"), empty.includes(">HS<"), empty.includes(">Hand Hardness<")],
        pdf: pdf.includes("(CT21Q2)") }}));""")
    assert r["left"] < 179 < r["right"] and r["empty"] == [False, False, True] and r["pdf"]


def test_voice_page_has_every_element_its_script_drives():
    pit = JS.parent
    html, js = (pit / "voice.html").read_text(), (JS / "voice.js").read_text()
    ids = set(re.findall(r'\bid="([^"]+)"', html))
    used = set(re.findall(r'\$\("([A-Za-z0-9_-]+)"\)', js)) | set(re.findall(r'(?:open|close)Sheet\("([A-Za-z0-9_-]+)"\)', js))
    assert len(used) > 60 and used <= ids, sorted(used - ids)
    assert set(re.findall(r'data-k="([^"]+)"', html)) >= {"name", "site.date", "site.location", "surf.hs", "wx.sky", "avs", "notes"}


def test_service_worker_caches_every_file_the_pages_load():
    pit = JS.parent
    shell = set(re.findall(r'"\./([^"]*)"', re.search(r"const SHELL = \[(.*?)\];", (pit / "sw.js").read_text(), re.S).group(1)))
    for page in ("index.html", "voice.html"):
        local = {s for s in re.findall(r'<script[^>]* src="([^"]+)"', (pit / page).read_text()) if "//" not in s}
        assert page in shell and local <= shell, (page, local - shell)
    assert all((pit / f).is_file() for f in shell if f)


def test_photo_page_links_to_voice_and_satellite_text_and_offline_copies_are_plain():
    """The Translator's first screen leads to both new features. The service worker stores plain copies, because a
    host that redirects voice.html to /voice hands back a redirected response, which cannot answer a page load."""
    pit = JS.parent
    nav = re.search(r'<nav class="tabs".*?</nav>', (pit / "index.html").read_text(), re.S).group(0)
    assert re.findall(r'href="([^"]+)"', nav) == ["./", "voice.html", "voice.html#sat"]
    sw = (pit / "sw.js").read_text()
    assert "addAll" not in sw and "res.redirected" in sw and ".then(plain)" in sw and "plain(res.clone())" in sw
