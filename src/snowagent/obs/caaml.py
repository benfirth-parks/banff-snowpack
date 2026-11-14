"""Parsers for CAAML v5 snow profiles (niViz "SnowProfileIACS" exports) and CAAML v6 snow profiles (Snow Scope,
the Snowpack Translator); structured, exact data.

Conventions (CAAML v5.0 Profiles/SnowProfileIACS):
- ``SnowProfileMeasurements dir="top down"``: ``depthTop`` is measured down from the surface and each layer
  has a ``thickness``; heights above ground are HS - depth.
- Temperatures and test failure layers are depths from the surface too.
- Hardness/lwc such as ``P-K`` or ``D-M`` are CAAML intermediate classes (between the two), not ranges
  over the layer; hardness index is the midpoint.
- ``gml:pos`` in CRS84 is "lon lat".

CAAML v6 (``parse_caaml_v6``, ADR-110) has the same top-down depths. It differs in names: ``timeRef/recordTime``
for the time, ``hS/Components/height``, ``wetness`` for ``lwc``, comments under ``metaData``. A test's failure
layer and results are under ``failedOn``; the Snowpack Translator writes them directly under the test, and the
Translator's ``locRef`` has no ``ObsPoint`` wrapper: both are read.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from snowagent.obs.observed import hardness_index
from snowagent.obs.snowpro import _num, _record

CAAML_V5_NS = "http://caaml.org/Schemas/V5.0/Profiles/SnowProfileIACS"
CAAML_V6_NS = re.compile(r"http://caaml\.org/Schemas/SnowProfileIACS/v6(?:\.\d+)*")
NS = {"caaml": CAAML_V5_NS, "gml": "http://www.opengis.net/gml"}
XML_EXT = {".xml", ".caaml"}  # profile files whose format comes from their content (xml_kind), not their name
XML_NOT_READ = {  # why a profile file of each other XML kind is kept but not read (ADR-048)
    "caaml_other": "CAAML other than v5 or v6: no parser; kept, not read",
    "xml_unknown": "XML without a CAAML namespace: not a known profile format; kept, not read",
}
TEST_TYPES = {"ComprTest": "CT", "ExtColumnTest": "ECT", "RBlockTest": "RB", "PropSawTest": "PST",
              "ShearFrameTest": "SF"}
MONTH_TAG = re.compile(r"^((Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2})\b\??", re.I)


def _c(tag: str) -> str:
    return "/".join(f"caaml:{p}" for p in tag.split("/"))


def _txt(e: ET.Element | None, tag: str) -> str | None:
    if e is None:
        return None
    x = e.find(_c(tag), NS)
    return x.text.strip() if x is not None and x.text and x.text.strip() else None


def xml_kind(raw: bytes) -> str:
    """Format of an XML profile file from its first 4000 bytes, whatever its name (CAAML v5 is often saved
    as .xml): ``caaml_v5`` (read by ``parse_caaml_v5``), ``caaml_v6`` (read by ``parse_caaml_v6``),
    ``caaml_other`` (another CAAML version) or ``xml_unknown``. Shared by the inbox (receipt status) and the observed-set reader, so they agree."""
    head = raw[:4000].decode("utf-8", "ignore")
    if CAAML_V5_NS in head:
        return "caaml_v5"
    if CAAML_V6_NS.search(head):
        return "caaml_v6"
    if "caaml" in head.lower():
        return "caaml_other"
    return "xml_unknown"


def is_caaml_v5(raw: bytes) -> bool:
    return xml_kind(raw) == "caaml_v5"


def intermediate_hardness_index(h: str | None) -> float | None:
    """Index for a single class (F..I with +/-) or a CAAML intermediate class 'A-B' (midpoint)."""
    if h is None:
        return None
    m = re.fullmatch(r"(F|4F|1F|P|K|I)-(F|4F|1F|P|K|I)", h)
    if m:
        a, b = hardness_index(m.group(1)), hardness_index(m.group(2))
        return (a + b) / 2
    return hardness_index(h)


def parse_caaml_v5(path: Path, tz: str) -> dict:
    root = ET.fromstring(Path(path).read_bytes())
    flags: list[str] = []
    meas = root.find(_c("snowProfileResultsOf/SnowProfileMeasurements"), NS)
    if meas is None:
        raise ValueError(f"no SnowProfileMeasurements in {path}")
    if meas.get("dir", "top down") != "top down":
        raise ValueError(f"unsupported CAAML direction {meas.get('dir')!r} in {path}")

    date = time = None
    stamp = _txt(root, "validTime/TimeInstant/timePosition")
    if stamp:
        ts = pd.Timestamp(stamp)
        if ts.tzinfo is None:
            flags.append("file_time_without_offset_assumed_local")
            ts = ts.tz_localize(ZoneInfo(tz))
        elif ts.utcoffset() != ts.tz_convert(ZoneInfo(tz)).utcoffset():
            flags.append(f"file_utc_offset:{ts.strftime('%z')}")
        local = ts.tz_convert(ZoneInfo(tz))
        date, time = local.date().isoformat(), local.strftime("%H:%M")

    hs = _num(_txt(meas, "hS/Components/snowHeight"))
    depth = _num(_txt(meas, "profileDepth"))
    if hs is None:
        flags.append("no_hs_in_file_heights_from_profile_depth")
        hs = depth

    layers = []
    for ly in meas.findall(_c("stratProfile/Layer"), NS):
        d, th = _num(_txt(ly, "depthTop")), _num(_txt(ly, "thickness"))
        if d is None or th is None or hs is None:
            flags.append(f"layer_without_depth_or_thickness_at_{d}")
            continue
        size = [v for v in (_num(_txt(ly, "grainSize/Components/avg")),
                            _num(_txt(ly, "grainSize/Components/avgMax"))) if v is not None]
        if len(size) == 2 and size[0] == size[1]:
            size = size[:1]
        hard = _txt(ly, "hardness")
        lwc = _txt(ly, "lwc")
        comment = _txt(ly, "comment")
        notes = [comment] if comment else []
        moisture = lwc if lwc in {"D", "M", "W", "V", "S"} else None
        if lwc and moisture is None:
            notes.append(f"lwc {lwc!r} (CAAML intermediate class)")
        tag = MONTH_TAG.match(comment or "")
        layers.append({
            "top_cm": hs - d, "bottom_cm": hs - d - th, "grain_form": _txt(ly, "grainFormPrimary"),
            "grain_form_2": _txt(ly, "grainFormSecondary"),
            "grain_class": (_txt(ly, "grainFormPrimary") or "")[:2] or None, "grain_size_mm": size or None,
            "hardness": hard, "hardness_bottom": None, "hardness_index": intermediate_hardness_index(hard),
            "moisture": moisture, "density_kg_m3": _num(_txt(ly, "density")),
            "date_tag": tag.group(1) if tag else None, "uncertain_fields": [],
            "comment": "; ".join(notes) or None,
        })
    layers.sort(key=lambda r: -r["top_cm"])
    for a, b in zip(layers, layers[1:], strict=False):
        if abs(a["bottom_cm"] - b["top_cm"]) > 0.5:
            flags.append(f"gap_or_overlap_{a['bottom_cm']}_{b['top_cm']}")

    temps = []
    for ob in meas.findall(_c("tempProfile/Obs"), NS):
        d, v = _num(_txt(ob, "depth")), _num(_txt(ob, "snowTemp"))
        if d is not None and v is not None and hs is not None:
            temps.append({"height_cm": hs - d, "t_c": v})

    tests = []
    for test in list(meas.find(_c("stbTests"), NS) or []):
        name = test.tag.split("}")[-1]
        ttype = TEST_TYPES.get(name, name)
        comment = _txt(test, "comment")
        if test.find(_c("noFailure"), NS) is not None:
            tests.append({"raw": f"{name} noFailure", "type": ttype, "result": f"{ttype}N" if ttype == "CT" else
                          f"{ttype}X" if ttype == "ECT" else f"{ttype} no failure", "score": None,
                          "fracture_character": None, "shear_quality": None, "height_cm": None,
                          "layer_date_tag": None, "comment": comment})
            continue
        for fo in test.findall(_c("failedOn"), NS):
            d = _num(_txt(fo, "Layer/depthTop"))
            score_txt = _txt(fo, "Results/testScore")
            score = _num(re.sub(r"^[A-Za-z]+", "", score_txt)) if score_txt else None
            result = score_txt if score_txt and not score_txt[0].isdigit() else (
                f"{ttype}{score_txt}" if score_txt else None)
            tests.append({"raw": f"{name} {score_txt or ''} {_txt(fo, 'Results/fractureCharacter') or ''}".strip(),
                          "type": ttype, "result": result, "score": score,
                          "fracture_character": _txt(fo, "Results/fractureCharacter"),
                          "shear_quality": _txt(fo, "Results/shearQuality"),
                          "height_cm": hs - d if d is not None and hs is not None else None,
                          "layer_date_tag": None, "comment": comment})

    point = root.find(_c("locRef/ObsPoint"), NS)
    lat = lon = None
    pos = point.find(".//gml:pos", NS) if point is not None else None
    if pos is not None and pos.text:
        srs = (point.find(".//gml:Point", NS).get("srsName") or "") if point.find(".//gml:Point", NS) is not None \
            else ""
        a, b = (float(x) for x in pos.text.split()[:2])
        lon, lat = (a, b) if "CRS84" in srs or abs(a) > 90 else (b, a)
    aspect = _txt(point, "validAspect/AspectPosition/position")
    meta = {"hs": hs, "elevation": _num(_txt(point, "validElevation/ElevationPosition/position")),
            "site": _txt(point, "name"), "slope": _num(_txt(point, "validSlopeAngle/SlopeAnglePosition/position")),
            "aspect": aspect, "lat": lat, "lon": lon,
            "comments": "; ".join(x for x in (_txt(point, "description"), _txt(meas, "comment")) if x) or None}
    rec = _record(Path(path), "caaml_v5", date, time, tz, meta, layers, temps, tests, flags)
    if depth is not None and hs is not None and depth < hs - 0.5:
        rec["profile_depth_cm"] = depth
    return rec


def _local_time(stamp: str | None, tz: str, flags: list[str]) -> tuple[str | None, str | None]:
    if not stamp:
        return None, None
    ts = pd.Timestamp(stamp)
    if ts.tzinfo is None:
        flags.append("file_time_without_offset_assumed_local")
        ts = ts.tz_localize(ZoneInfo(tz))
    elif ts.utcoffset() != ts.tz_convert(ZoneInfo(tz)).utcoffset():
        flags.append(f"file_utc_offset:{ts.strftime('%z')}")
    local = ts.tz_convert(ZoneInfo(tz))
    return local.date().isoformat(), local.strftime("%H:%M")


def parse_caaml_v6(path: Path, tz: str) -> dict:
    """A CAAML v6 SnowProfileIACS file as the same record ``parse_caaml_v5`` returns (format ``caaml_v6``)."""
    root = ET.fromstring(Path(path).read_bytes())
    uri = root.tag[1:].split("}")[0] if root.tag.startswith("{") else ""
    if not CAAML_V6_NS.fullmatch(uri):
        raise ValueError(f"not a CAAML v6 SnowProfile in {path}")
    ns = {"caaml": uri, "gml": "http://www.opengis.net/gml"}

    def find(e: ET.Element | None, tag: str) -> ET.Element | None:
        return None if e is None else e.find(_c(tag), ns)

    def txt(e: ET.Element | None, tag: str) -> str | None:
        x = find(e, tag)
        return x.text.strip() if x is not None and x.text and x.text.strip() else None

    def comment(e: ET.Element | None) -> str | None:
        return txt(e, "metaData/comment") or txt(e, "comment")

    flags: list[str] = []
    meas = find(root, "snowProfileResultsOf/SnowProfileMeasurements")
    if meas is None:
        raise ValueError(f"no SnowProfileMeasurements in {path}")
    if meas.get("dir", "top down") != "top down":
        raise ValueError(f"unsupported CAAML direction {meas.get('dir')!r} in {path}")
    date, time = _local_time(txt(root, "timeRef/recordTime/TimeInstant/timePosition"), tz, flags)

    hs = _num(txt(meas, "hS/Components/height"))
    depth = _num(txt(meas, "profileDepth"))
    if hs is None:
        flags.append("no_hs_in_file_heights_from_profile_depth")
        hs = depth

    layers = []
    for ly in meas.findall(_c("stratProfile/Layer"), ns):
        d, th = _num(txt(ly, "depthTop")), _num(txt(ly, "thickness"))
        if d is None or th is None or hs is None:
            flags.append(f"layer_without_depth_or_thickness_at_{d}")
            continue
        size = [v for v in (_num(txt(ly, "grainSize/Components/avg")),
                            _num(txt(ly, "grainSize/Components/avgMax"))) if v is not None]
        if len(size) == 2 and size[0] == size[1]:
            size = size[:1]
        hard, wet, note = txt(ly, "hardness"), txt(ly, "wetness"), comment(ly)
        notes = [note] if note else []
        moisture = wet if wet in {"D", "M", "W", "V", "S"} else None
        if wet and moisture is None:
            notes.append(f"wetness {wet!r} (CAAML intermediate class)")
        tag = MONTH_TAG.match(note or "")
        layers.append({
            "top_cm": hs - d, "bottom_cm": hs - d - th, "grain_form": txt(ly, "grainFormPrimary"),
            "grain_form_2": txt(ly, "grainFormSecondary"),
            "grain_class": (txt(ly, "grainFormPrimary") or "")[:2] or None, "grain_size_mm": size or None,
            "hardness": hard, "hardness_bottom": None, "hardness_index": intermediate_hardness_index(hard),
            "moisture": moisture, "density_kg_m3": _num(txt(ly, "density")),
            "date_tag": tag.group(1) if tag else None, "uncertain_fields": [],
            "comment": "; ".join(notes) or None,
        })
    layers.sort(key=lambda r: -r["top_cm"])
    for a, b in zip(layers, layers[1:], strict=False):
        if abs(a["bottom_cm"] - b["top_cm"]) > 0.5:
            flags.append(f"gap_or_overlap_{a['bottom_cm']}_{b['top_cm']}")

    temps = []
    for ob in meas.findall(_c("tempProfile/Obs"), ns):
        d, v = _num(txt(ob, "depth")), _num(txt(ob, "snowTemp"))
        if d is not None and v is not None and hs is not None:
            temps.append({"height_cm": hs - d, "t_c": v})

    tests = []
    stb = find(meas, "stbTests")
    for test in list(stb) if stb is not None else []:
        name = test.tag.split("}")[-1]
        ttype = TEST_TYPES.get(name, name)
        note = comment(test)
        if find(test, "noFailure") is not None:
            tests.append({"raw": f"{name} noFailure", "type": ttype, "result": f"{ttype}N" if ttype == "CT" else
                          f"{ttype}X" if ttype == "ECT" else f"{ttype} no failure", "score": None,
                          "fracture_character": None, "shear_quality": None, "height_cm": None,
                          "layer_date_tag": None, "comment": note})
            continue
        for fo in test.findall(_c("failedOn"), ns) or [test]:
            d = _num(txt(fo, "Layer/depthTop"))
            score_txt = txt(fo, "Results/testScore")
            if ttype == "PST" and score_txt is None:
                prop, cut, col = (txt(fo, f"Results/{k}") for k in ("fracturePropagation", "cutLength", "columnLength"))
                score_txt = f"PST{cut or '?'}/{col or '?'} {prop}" if prop else None
                result, score = score_txt, None
            else:
                score = _num(re.sub(r"^[A-Za-z]+", "", score_txt).rstrip("PNX") or None) if score_txt else None
                result = score_txt if score_txt and not score_txt[0].isdigit() else (
                    f"{ttype}{score_txt}" if score_txt else None)
            frac = txt(fo, "Results/fractureCharacter")
            tests.append({"raw": f"{name} {score_txt or ''} {frac or ''}".strip(), "type": ttype, "result": result,
                          "score": score, "fracture_character": frac, "shear_quality": txt(fo, "Results/shearQuality"),
                          "height_cm": hs - d if d is not None and hs is not None else None,
                          "layer_date_tag": None, "comment": note})

    loc = find(root, "locRef")
    point = find(loc, "ObsPoint") if find(loc, "ObsPoint") is not None else loc
    lat = lon = None
    gpt = point.find(".//gml:Point", ns) if point is not None else None
    pos = gpt.find("gml:pos", ns) if gpt is not None else None
    if pos is not None and pos.text:
        a, b = (float(x) for x in pos.text.split()[:2])
        lon, lat = (a, b) if "CRS84" in (gpt.get("srsName") or "") or abs(a) > 90 else (b, a)
    meta = {"hs": hs, "elevation": _num(txt(point, "validElevation/ElevationPosition/position")),
            "site": txt(point, "name"), "slope": _num(txt(point, "validSlopeAngle/SlopeAnglePosition/position")),
            "aspect": txt(point, "validAspect/AspectPosition/position"), "lat": lat, "lon": lon,
            "comments": "; ".join(x for x in (txt(point, "description"), comment(root), comment(meas)) if x) or None}
    rec = _record(Path(path), "caaml_v6", date, time, tz, meta, layers, temps, tests, flags)
    if depth is not None and hs is not None and depth < hs - 0.5:
        rec["profile_depth_cm"] = depth
    return rec
