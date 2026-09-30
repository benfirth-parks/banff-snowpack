"""Parsers for Gasman SnowPro profile files (structured, exact data; no transcription).

Formats found in the archive (verified against a matching printout, BS 05 12 27):
- SnowPro 2.1   ``[SNOWPROFILE: version]`` header/value blocks, 1990 numeric grain codes (1997-2002)
- SnowPro 3.x   INI ``[CrystalLayer...]`` sections, 1990 grain codes (.PRO, some .prx)
- SnowPro Plus  XML ``<caamlObservations>`` with IACS codes (.prx, 2009-2014)

Layer convention (all three): layers are listed bottom-up and each carries its TOP height (cm above
ground); a layer's bottom is the previous layer's top, the first real layer starts at the pit bottom.
SnowPro 3.x stores the surface grains as entry 1 with height -1; SnowPro Plus as a zero-thickness
entry at HS. Those are surface observations, not layers.
"""

from __future__ import annotations

import configparser
import csv
import hashlib
import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from snowagent.obs.observed import hardness_index

# 1990 International Classification (Colbeck et al.) -> IACS 2009 (Fierz et al.) as used by OGRS.
CLASS_1990 = {"1": "PP", "2": "DF", "3": "RG", "4": "FC", "5": "DH", "6": "MF", "7": "SH", "8": "IF"}
SUB_1990 = {
    "1a": "PPco", "1b": "PPnd", "1c": "PPpl", "1d": "PPsd", "1e": "PPir", "1f": "PPgp", "1g": "PPhl", "1h": "PPip",
    "2a": "DFdc", "2b": "DFbk", "3a": "RGsr", "3b": "RGlr", "3c": "RGxf", "4a": "FCso", "4b": "FC", "4c": "FCxr",
    "5a": "DHcp", "5b": "DHpr", "5c": "DHch", "6a": "MFcl", "6b": "MFpc", "6c": "MFsl", "7a": "SHsu", "7b": "SHcv",
    "8a": "IFil", "8b": "IFic", "8c": "IFbi", "9a": "PPrm", "9b": "IFrc", "9c": "IFsc", "9d": "RGwp", "9e": "MFcr",
}
HARDNESS_NUM = {1: "F", 2: "4F", 3: "1F", 4: "P", 5: "K", 6: "I"}
MOISTURE = {"dry": "D", "d": "D", "moist": "M", "m": "M", "wet": "W", "w": "W", "very wet": "V", "v": "V",
            "soaked": "S", "slush": "S", "s": "S"}


def grain_1990(code: str | None) -> tuple[str | None, str | None]:
    """Return (IACS code, note). Unknown codes -> (None, note)."""
    if code is None:
        return None, None
    c = code.strip().lower()
    if c in ("", ".", "n/a"):
        return None, None
    if c in SUB_1990:
        return SUB_1990[c], None
    if c in CLASS_1990:
        return CLASS_1990[c], None
    if re.fullmatch(r"[a-z]{2}[a-z]{0,2}", c):  # already an IACS code, e.g. "fcxr"
        iacs = c[:2].upper() + c[2:]
        return iacs, None
    if c == "9":
        return None, "1990 class 9 (crust or surface deposit, subclass not given): crust_class_unspecified"
    return None, f"unmapped 1990 grain code {code!r}"


def _size(s: str | None) -> list[float] | None:
    if s is None:
        return None
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", s)]
    return nums[:2] or None


def _hardness(s: str | None) -> str | None:
    if s is None:
        return None
    s = s.strip().upper().replace(" ", "")
    m = re.fullmatch(r"([1-6])([+-]?)", s)
    if m:
        return HARDNESS_NUM[int(m.group(1))] + m.group(2)
    return s if re.fullmatch(r"(F|4F|1F|P|K|I)[+-]?", s) else None


def _moist(s: str | None) -> str | None:
    return MOISTURE.get((s or "").strip().lower()) if s and s.strip() else None


def _num(s) -> float | None:
    try:
        v = float(str(s).strip())
    except (TypeError, ValueError):
        return None
    return v


def _layers_bottom_up(rows: list[dict], pit_bottom: float, hs: float | None) -> tuple[list[dict], list[str]]:
    """rows: bottom-up entries with 'top'. Drops surface entries; builds contiguous layers top-first."""
    flags: list[str] = []
    real = [r for r in rows if r["top"] is not None and r["top"] > pit_bottom
            and not (hs is not None and r is rows[0] and r["top"] >= hs and len(rows) > 1)]
    out = []
    prev = pit_bottom
    for r in real:
        if r["top"] <= prev:
            flags.append(f"non_increasing_layer_height_{r['top']}")
            continue
        out.append({**r, "top_cm": r["top"], "bottom_cm": prev})
        prev = r["top"]
    if hs is not None and out and abs(out[-1]["top_cm"] - hs) > 1:
        flags.append(f"top_layer_{out[-1]['top_cm']}_differs_from_hs_{hs}")
    return out[::-1], flags


def _layer_record(r: dict) -> dict:
    g1, n1 = grain_1990(r.get("g1"))
    g2, n2 = grain_1990(r.get("g2"))
    hard = _hardness(r.get("hard"))
    notes = [n for n in (n1, n2) if n]
    if r.get("rimed"):
        notes.append("rimed")
    if r.get("comment"):
        notes.append(str(r["comment"]).strip())
    dens = _num(r.get("density"))
    return {
        "top_cm": r["top_cm"], "bottom_cm": r["bottom_cm"], "grain_form": g1, "grain_form_2": g2,
        "grain_class": g1[:2] if g1 else None, "grain_size_mm": _size(r.get("size")),
        "hardness": hard, "hardness_bottom": None, "hardness_index": hardness_index(hard),
        "moisture": _moist(r.get("water")), "density_kg_m3": dens if dens and dens > 0 else None,
        "date_tag": None, "uncertain_fields": [], "comment": "; ".join(notes) or None,
    }


def _record(path: Path, fmt: str, date: str | None, time: str | None, tz: str, meta: dict, layers: list[dict],
            temps: list[dict], tests: list[dict], flags: list[str]) -> dict:
    obs_utc = None
    if date:
        stamp = f"{date}T{time}" if time else date
        obs_utc = pd.Timestamp(stamp).tz_localize(ZoneInfo(tz)).tz_convert("UTC").isoformat()
        if not time:
            flags.append("time_unknown_date_only")
    else:
        flags.append("no_observation_date")
    return {
        "profile_id": None, "source_file": str(path), "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "obs_time_utc": obs_utc, "time_zone": tz, "hs_cm": meta.get("hs"), "elevation_m": meta.get("elevation"),
        "aspect": meta.get("aspect"), "slope_deg": meta.get("slope"), "lat": meta.get("lat"), "lon": meta.get("lon"),
        "profile_depth_cm": None, "height_reference": "height_above_ground", "layers": layers,
        "temperatures": temps, "tests": tests, "site_name_as_written": meta.get("site"),
        "provenance": {"method": f"structured:{fmt}", "agent": None, "reviewed": None, "confidence": "exact",
                       "source_format": fmt},
        "duplicate_of": None, "flags": flags, "comments": meta.get("comments"),
    }


def _date_yy(s: str) -> str | None:
    m = re.fullmatch(r"(\d{2,4})-(\d{1,2})-(\d{1,2})", s.strip())
    if not m:
        return None
    y = int(m.group(1))
    y = y + (1900 if y > 50 else 2000) if y < 100 else y
    try:
        return pd.Timestamp(year=y, month=int(m.group(2)), day=int(m.group(3))).date().isoformat()
    except ValueError:
        return None


def parse_v21(path: Path, text: str, tz: str) -> dict:
    """SnowPro 2.1: '[header fields]' followed by one value per line / CSV rows."""
    blocks: list[tuple[str, list[str]]] = []
    for line in text.replace("\r", "").split("\n"):
        if line.startswith("["):
            blocks.append((line.strip("[] "), []))
        elif blocks and line.strip() != "":
            blocks[-1][1].append(line)
    flags: list[str] = []
    head = next((v for k, v in blocks if k.startswith("prf_desc")), [])
    date = next((d for d in (_date_yy(x) for x in head) if d), None)
    time = next((x.strip() for x in head if re.fullmatch(r"\d{1,2}:\d{2}", x.strip())), None)
    hs = _num(head[-2]) if len(head) >= 2 else None
    air = _num(head[-3]) if len(head) >= 3 else None
    elev = _num(head[5]) if len(head) > 6 else None
    if len(head) > 6 and head[6].strip().upper().startswith("F") and elev:
        elev *= 0.3048
        flags.append("elevation_converted_from_ft")
    site = head[0].strip().strip('"') if head else None
    temps = []
    tb = next((v for k, v in blocks if k.startswith("temp_hgt")), [])
    for row in tb:
        h, t = (row.split(",") + [None])[:2]
        if _num(h) is not None and _num(t) is not None:
            temps.append({"height_cm": _num(h), "t_c": _num(t)})
    surf = next((v for k, v in blocks if k.startswith("SURFACE")), [])
    pit_bottom = _num(surf[-1]) if surf and _num(surf[-1]) is not None else 0.0
    rows = []
    lb = next((v for k, v in blocks if k.startswith("layer_hgt")), [])
    for rec in csv.reader(io.StringIO("\n".join(lb))):
        if len(rec) < 9:
            continue
        rows.append({"top": _num(rec[0]), "water": rec[1], "g1": rec[2], "rimed": rec[3].strip() == "r",
                     "g2": rec[4], "size": rec[6], "hard": rec[7], "density": rec[8],
                     "comment": rec[10].strip().strip('"') if len(rec) > 10 else None})
    layers, lflags = _layers_bottom_up(rows, pit_bottom, hs)
    tests = []
    sb = next((v for k, v in blocks if k.startswith("shr_hgt")), [])
    for rec in csv.reader(io.StringIO("\n".join(sb))):
        if rec:
            tests.append({"raw": ",".join(rec), "type": None, "result": None, "score": None,
                          "fracture_character": None, "shear_quality": None, "height_cm": _num(rec[0]),
                          "layer_date_tag": None, "comment": rec[-1].strip().strip('"') if len(rec) > 3 else None})
    com = next((v for k, v in blocks if k == "COMMENTS"), [])
    meta = {"hs": hs, "elevation": elev, "site": site, "air": air, "comments": " ".join(com).strip('" ') or None}
    return _record(path, "snowpro_2.1", date, time, tz, meta, [_layer_record(r) for r in layers], temps, tests,
                   flags + lflags)


def parse_v3(path: Path, text: str, tz: str) -> dict:
    cp = configparser.ConfigParser(strict=False, interpolation=None)
    cp.optionxform = str
    cp.read_string(text)

    def sec(name: str) -> dict:
        return dict(cp[name]) if cp.has_section(name) else {}

    idd, surf, flags = sec("Id"), sec("Surface"), []
    date = None
    raw_date = (idd.get("Date") or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%Y/%m/%d") if raw_date else ():
        try:
            date = pd.to_datetime(raw_date, format=fmt).date().isoformat()
            if fmt == "%m/%d/%Y":
                flags.append("date_parsed_month_first")
            break
        except (ValueError, TypeError):
            continue
    if raw_date and date is None:
        flags.append(f"date_unparsed:{raw_date}")
    time = (idd.get("Time") or "").strip() or None
    hs = _num(surf.get("PackHeight"))
    elev = _num(idd.get("Altitude"))
    if elev and (idd.get("AltUnits") or "").lower().startswith("f"):
        elev *= 0.3048
        flags.append("elevation_converted_from_ft")
    n = int(_num(sec("CrystalLLayers").get("Count")) or 0)
    pit_bottom = _num(sec("CrystalLLayers").get("BottomOfPit")) or 0.0
    H, W = sec("CrystalLayerHeight"), sec("CrystalLayerWaterContent")
    G1, R1, D1 = sec("CrystalLayerGrainForm1"), sec("CrystalLayerGrainRimed1"), sec("CrystalLayerGrainDiameter1")
    G2 = sec("CrystalLayerGrainForm2")
    HH, DE, CO = sec("CrystalLayerHandHardness"), sec("CrystalLayerDensity"), sec("CrystalLayerComments")
    rows = [{"top": _num(H.get(str(i))), "water": W.get(str(i)), "g1": G1.get(str(i)),
             "rimed": (R1.get(str(i)) or "").strip() == "r", "g2": G2.get(str(i)), "size": D1.get(str(i)),
             "hard": HH.get(str(i)), "density": DE.get(str(i)), "comment": CO.get(str(i))} for i in range(1, n + 1)]
    layers, lflags = _layers_bottom_up(rows, pit_bottom, hs)
    sh, st = sec("SnowHeight"), sec("SnowTemp")
    temps = [{"height_cm": _num(sh[k]), "t_c": _num(st.get(k))} for k in sh
             if k.isdigit() and _num(sh[k]) is not None and _num(st.get(k)) is not None]
    tests = []
    th, tt, tv, hits, tc = (sec(x) for x in ("ShearLayerHeight", "ShearLayerType", "ShearLayerValue", "ShearHits",
                                              "ShearLayerComments"))
    for k in th:
        if k.isdigit():
            tests.append({"raw": " ".join(filter(None, [tt.get(k), tv.get(k), hits.get(k), tc.get(k)])),
                          "type": "CT" if (tt.get(k) or "").lower().startswith("comp") else tt.get(k),
                          "result": None, "score": _num(hits.get(k)), "fracture_character": None,
                          "shear_quality": None, "height_cm": _num(th[k]), "layer_date_tag": None,
                          "comment": tc.get(k)})
    comments = " ".join(v for k, v in sec("Comments").items() if k.isdigit())
    meta = {"hs": hs, "elevation": elev, "site": idd.get("Location"), "slope": _num(idd.get("Slope")),
            "comments": comments or None}
    return _record(path, f"snowpro_{cp.get('File', 'Release', fallback='3')}", date, time, tz, meta,
                   [_layer_record(r) for r in layers], temps, tests, flags + lflags)


def parse_plus_xml(path: Path, root: ET.Element, tz: str) -> dict:
    o = root.find("SnowprofileObservationType")
    flags: list[str] = []

    def t(tag):
        e = o.find(tag)
        return e.text.strip() if e is not None and e.text else None

    dto = o.find("dateTimeObs")
    date = time = None
    if dto is not None and dto.text:
        ts = pd.Timestamp(dto.text.strip())
        date, time = ts.date().isoformat(), ts.strftime("%H:%M")
        zone = dto.get("timeZone", "")
        if zone and "mountain standard" not in zone.lower():
            flags.append(f"file_time_zone:{zone}")
    hs = _num(t("profileDepth"))
    pit_bottom = _num(t("bottomOfPitHeight")) or 0.0
    rows = []
    lp = o.find("layerProfile")
    for ly in (lp.findall("layer") if lp is not None else []):
        g = ly.find("grains")
        def gt(i, g=g):
            e = g.find(f"grainType{i}") if g is not None else None
            return (e.text.strip() if e is not None and e.text else None), (e is not None and e.get("rimed") == "true")
        (g1, r1), (g2, _r2) = gt(1), gt(2)
        sz = g.find("grainSize1") if g is not None else None
        size = None
        if sz is not None:
            mn, mx = sz.findtext("min"), sz.findtext("max")
            size = f"{mn}-{mx}" if mn and mx and mn != mx else (mn or mx)
        rows.append({"top": _num(ly.get("depthTop")), "water": ly.findtext("lwc"), "g1": g1, "rimed": r1, "g2": g2,
                     "size": size, "hard": ly.findtext("hardness"), "density": ly.findtext("density"),
                     "comment": ly.findtext("comment")})
    layers, lflags = _layers_bottom_up(rows, pit_bottom, hs)
    temps = []
    tp = o.find("tempProfile")
    for st in (tp.findall("snowTemp") if tp is not None else []):
        h = _num(st.get("depth") or st.get("height"))
        v = _num(st.text)
        if h is not None and v is not None:
            temps.append({"height_cm": h, "t_c": v})
    tests = []
    tsec = o.find("tests")
    for test in (list(tsec) if tsec is not None else []):
        fp = test.find("failurePlain")
        tests.append({"raw": ET.tostring(test, encoding="unicode")[:300], "type": test.tag,
                      "result": fp.findtext("testScore") if fp is not None else None,
                      "score": _num(fp.findtext("numTaps")) if fp is not None else None,
                      "fracture_character": fp.findtext("fracCharMinor") if fp is not None else None,
                      "shear_quality": None, "height_cm": _num(fp.get("depth")) if fp is not None else None,
                      "layer_date_tag": None, "comment": fp.findtext("fracCharMajor") if fp is not None else None})
    meta = {"hs": hs, "elevation": _num(t("elevation")), "site": t("site"), "slope": _num(t("incline")),
            "aspect": t("aspect"), "comments": t("comment")}
    return _record(path, "snowpro_plus_xml", date, time, tz, meta, [_layer_record(r) for r in layers], temps,
                   tests, flags + lflags)


def parse_snowpro(path: Path, tz: str) -> dict:
    raw = Path(path).read_bytes().decode("latin-1")
    head = raw.lstrip()[:200]
    if head.startswith("<?xml") or head.startswith("<caaml"):
        return parse_plus_xml(path, ET.fromstring(raw.encode("latin-1")), tz)
    if head.startswith("[SNOWPROFILE"):
        return parse_v21(path, raw, tz)
    if "[CrystalLayerHeight]" in raw or "[Id]" in raw:
        return parse_v3(path, raw, tz)
    raise ValueError(f"not a recognised SnowPro file: {path}")


SNOWPRO_EXT = {".pro", ".prx"}
