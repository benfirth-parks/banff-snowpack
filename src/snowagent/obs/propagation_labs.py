"""Parse the text header of Propagation Labs "Manual Snow Profile" PDF exports.

The export's text layer holds the header only (name, date, observer, location,
weather, snow conditions, notes). Layers, grain forms, hardness, temperatures and
test results are drawn as an image and are NOT extracted here. Some exports (FPDF
producer) have no text layer at all.

Text extraction is inconsistent about spaces ("Date:2026-01-1214:28:12.000" vs
"Date: 2026-01-12 14:28:12.000"), so patterns tolerate missing whitespace.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

FT_TO_M = 0.3048
ASPECTS = {"N": 0, "NNE": 22.5, "NE": 45, "ENE": 67.5, "E": 90, "ESE": 112.5, "SE": 135, "SSE": 157.5,
           "S": 180, "SSW": 202.5, "SW": 225, "WSW": 247.5, "W": 270, "WNW": 292.5, "NW": 315, "NNW": 337.5}
FIELD_ORDER = ["ProfileName", "Date", "Observer", "Org", "Elevation", "Aspect", "Slope", "Lat/Lng",
               "AirTemperature", "SkyCover", "Precipitation", "Wind", "BlowingSnow", r"TotalSnowDepth\(HS\)",
               "SurfaceGrain", "FootPen", "SkiPen", "Notes"]
SECTION_WORDS = ("General", "Location", "Weather", "SnowConditions", "Snow Conditions")


@dataclass
class ParsedHeader:
    raw: dict[str, str | None]
    values: dict[str, object] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)


def _clean(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    for w in SECTION_WORDS:
        if v.endswith(w):
            v = v[: -len(w)]
    v = v.strip()
    return None if v in ("", "--") else v


PRIVATE_USE = re.compile("[\ue000-\uf8ff]")  # icon-font glyphs embedded before each label


def raw_fields(text: str) -> dict[str, str | None]:
    text = PRIVATE_USE.sub("", text)
    out: dict[str, str | None] = {}
    for i, key in enumerate(FIELD_ORDER):
        nxt = "|".join(FIELD_ORDER[i + 1:]) or r"\Z"
        m = re.search(rf"{key}\s*:(.*?)(?=(?:{nxt})\s*:|\Z)", text, flags=re.S)
        name = key.replace("\\", "").replace("(HS)", "_HS").replace("TotalSnowDepth_HS", "HS")
        out[name] = _clean(re.sub(r"\s*\n\s*", " ", m.group(1))) if m else None
    return out


def _length_m(v: str | None, what: str, flags: list[str]) -> tuple[float | None, str | None]:
    if v is None:
        return None, None
    m = re.fullmatch(r"(-?[\d.]+)\s*(cm|m|ft|in)", v.replace(" ", ""))
    if not m:
        flags.append(f"{what}_unparsed:{v}")
        return None, None
    x, u = float(m.group(1)), m.group(2)
    return {"cm": x / 100, "m": x, "ft": x * FT_TO_M, "in": x * 0.0254}[u], u


def parse_header_text(text: str) -> ParsedHeader:
    raw = raw_fields(text)
    p = ParsedHeader(raw=raw)
    v, f = p.values, p.flags
    if raw.get("Date"):
        m = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\s*(\d{2}:\d{2}:\d{2})(?:\.\d+)?", raw["Date"].replace(" ", ""))
        if m:
            v["obs_time_local"] = f"{m.group(1)}T{m.group(2)}"
        else:
            f.append(f"date_unparsed:{raw['Date']}")
    elev, unit = _length_m(raw.get("Elevation"), "elevation", f)
    v["elevation_m"], v["elevation_source_unit"] = elev, unit
    if unit == "ft":
        f.append("elevation_converted_from_ft")
    if raw.get("Lat/Lng"):
        m = re.fullmatch(r"(-?\d+\.\d+),\s*(-?\d+\.\d+)", raw["Lat/Lng"].replace(" ", ""))
        if m:
            v["lat"], v["lon"] = float(m.group(1)), float(m.group(2))
        else:
            f.append(f"latlng_unparsed:{raw['Lat/Lng']}")
    a = raw.get("Aspect")
    if a:
        v["aspect_text"] = a
        v["aspect_deg"] = ASPECTS.get(a.upper())
    if raw.get("Slope"):
        m = re.fullmatch(r"(\d+(?:\.\d+)?)°?", raw["Slope"].replace(" ", ""))
        v["slope_deg"] = float(m.group(1)) if m else None
    v["hs_m"], _ = _length_m(raw.get("HS"), "hs", f)
    v["foot_pen_m"], _ = _length_m(raw.get("FootPen"), "foot_pen", f)
    v["ski_pen_m"], _ = _length_m(raw.get("SkiPen"), "ski_pen", f)
    if raw.get("AirTemperature"):
        m = re.fullmatch(r"(-?[\d.]+)°?C", raw["AirTemperature"].replace(" ", ""))
        v["air_temp_c"] = float(m.group(1)) if m else None
    for src, dst in (("ProfileName", "profile_name"), ("Observer", "observer"), ("SkyCover", "sky_cover"),
                     ("Precipitation", "precipitation"), ("Wind", "wind"), ("BlowingSnow", "blowing_snow"),
                     ("SurfaceGrain", "surface_grain"), ("Notes", "notes")):
        v[dst] = raw.get(src)
    return p
