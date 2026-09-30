"""Build analysis-ready observed profiles from validated transcriptions.

Output layers are height above ground (cm, as observed) with a numeric hand-hardness
index. Nothing is filled in: a depth chart without HS stays depth-only (flagged), a
profile without a time keeps a date-only timestamp (flagged). Repeated exports of the
same pit (not byte-identical, e.g. PDF + JPG) are detected by identical layer
sequences on the same site and date and marked as duplicates.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import yaml

from snowagent.obs.inventory import DEFAULT_CONFIG, build_inventory
from snowagent.obs.transcription import Transcription, load_all, validate_transcription

HARDNESS_BASE = {"F": 1, "4F": 2, "1F": 3, "P": 4, "K": 5, "I": 6}


def hardness_index(h: str | None) -> float | None:
    """F=1 .. I=6; '+' adds 1/3, '-' subtracts 1/3 (OGRS-style intermediate steps)."""
    if h is None:
        return None
    m = re.fullmatch(r"(F|4F|1F|P|K|I)([+-]?)", h)
    if not m:
        return None
    return HARDNESS_BASE[m.group(1)] + {"": 0.0, "+": 1 / 3, "-": -1 / 3}[m.group(2)]


def _obs_time(t: Transcription, inv_row: dict | None, tz: str) -> tuple[str | None, list[str]]:
    flags: list[str] = []
    date = t.header.date_local or (inv_row or {}).get("filename_date")
    if t.header.date_local is None and date:
        flags.append("date_from_filename")
    if not date:
        return None, ["no_observation_date"]
    time = t.header.time_local
    if time is None and inv_row and inv_row.get("obs_time_local") and str(inv_row["obs_time_local"])[:10] == date:
        time = str(inv_row["obs_time_local"])[11:16]
    if time is None:
        flags.append("time_unknown_date_only")
        return pd.Timestamp(date).tz_localize(ZoneInfo(tz)).tz_convert("UTC").isoformat(), flags
    ts = pd.Timestamp(f"{date}T{time}").tz_localize(ZoneInfo(tz)).tz_convert("UTC")
    return ts.isoformat(), flags


def to_observed(t: Transcription, inv_row: dict | None, tz: str) -> dict:
    flags: list[str] = []
    hs = t.header.hs_cm
    layers = []
    depth_only = False
    for ly in t.layers:
        top, bot = ly.top_cm, ly.bottom_cm
        if t.height_reference == "depth_from_surface":
            if hs is None:
                depth_only = True
            else:
                top, bot = (None if top is None else hs - top), (None if bot is None else hs - bot)
        layers.append({
            "top_cm": top, "bottom_cm": bot, "grain_form": ly.grain_form, "grain_form_2": ly.grain_form_2,
            "grain_class": ly.grain_form[:2] if ly.grain_form else None,
            "grain_size_mm": ly.grain_size_mm, "hardness": ly.hardness, "hardness_bottom": ly.hardness_bottom,
            "hardness_index": hardness_index(ly.hardness), "moisture": ly.moisture,
            "density_kg_m3": ly.density_kg_m3, "date_tag": ly.date_tag, "uncertain_fields": ly.uncertain_fields,
        })
    if depth_only:
        flags.append("depth_chart_without_hs_heights_are_depths")
    obs_utc, tflags = _obs_time(t, inv_row, tz)
    flags += tflags
    inv = inv_row or {}
    lat = t.header.lat if t.header.lat is not None else (float(inv["lat"]) if inv.get("lat") else None)
    lon = t.header.lon if t.header.lon is not None else (float(inv["lon"]) if inv.get("lon") else None)
    return {
        "profile_id": t.record_id, "source_file": t.source_file, "source_sha256": t.source_sha256,
        "site_key": inv.get("site_key") or None, "station_id": inv.get("station_id") or None,
        "category": inv.get("category"), "obs_time_utc": obs_utc, "time_zone": tz,
        "lat": lat, "lon": lon, "location_qc": [q for q in str(inv.get("qc_flags", "")).split(";") if "gps" in q],
        "elevation_m": t.header.elevation_m, "aspect": t.header.aspect, "slope_deg": t.header.slope_deg,
        "hs_cm": hs, "profile_depth_cm": t.header.profile_depth_cm,
        "height_reference": "depth_from_surface" if depth_only else "height_above_ground",
        "layers": layers, "temperatures": [x.model_dump() for x in t.temperatures],
        "tests": [x.model_dump() for x in t.tests],
        "provenance": {"method": "transcription:" + t.transcriber.method, "agent": t.transcriber.agent,
                       "reviewed": t.transcriber.reviewed, "confidence": t.confidence,
                       "source_format": t.source_format},
        "duplicate_of": None, "flags": flags,
    }


def _signature(o: dict) -> tuple:
    return tuple((ly["top_cm"], ly["bottom_cm"], ly["grain_form"], ly["hardness"]) for ly in o["layers"])


def mark_observation_duplicates(obs: list[dict]) -> None:
    groups: dict[tuple, list[dict]] = {}
    for o in obs:
        key = (o["site_key"] or o["profile_id"][11:30], (o["obs_time_utc"] or "")[:10], _signature(o))
        groups.setdefault(key, []).append(o)
    rank = {"exact": 0, "high": 1, "medium": 2, "low": 3}
    for grp in groups.values():
        if len(grp) > 1:
            primary = sorted(grp, key=lambda o: (rank.get(o["provenance"]["confidence"], 9), o["source_file"]))[0]
            for o in grp:
                if o is not primary:
                    o["duplicate_of"] = primary["profile_id"]
                    o["flags"].append("same_observation_as_another_file")


def build_observed(transcriptions: Path, profiles_root: Path, config: Path | None = None) -> tuple[list[dict], dict]:
    cfg = yaml.safe_load(Path(config or DEFAULT_CONFIG).read_text())
    tz = cfg["time_zone"]
    headers, _ = build_inventory(profiles_root, config)
    inv = {h.record_id: h.model_dump(mode="json") | {"qc_flags": ";".join(h.qc_flags)} for h in headers}
    out: list[dict] = []
    stats = {"transcriptions": 0, "invalid": 0, "not_profiles": 0, "observed": 0}
    for _p, d in load_all(transcriptions):
        stats["transcriptions"] += 1
        t, errors, _flags = validate_transcription(d)
        if errors or t is None:
            stats["invalid"] += 1
            continue
        if not (t.readable and t.is_snow_profile):
            stats["not_profiles"] += 1
            continue
        out.append(to_observed(t, inv.get(t.record_id), tz))
    stats.update(add_structured(out, profiles_root, cfg, tz))
    mark_observation_duplicates(out)
    stats["observed"] = len(out)
    stats["duplicates"] = sum(o["duplicate_of"] is not None for o in out)
    stats["unique_observations"] = stats["observed"] - stats["duplicates"]
    return out, stats


def write_observed(obs: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as fh:
        for o in obs:
            fh.write(json.dumps(o, default=str) + "\n")


def add_structured(out: list[dict], profiles_root: Path, cfg: dict, tz: str) -> dict:
    """Parse SnowPro files (exact data). Backups (*.~PR, *.~rx) are ignored."""
    import hashlib

    from snowagent.obs.filenames import parse_filename_date
    from snowagent.obs.inventory import classify
    from snowagent.obs.snowpro import SNOWPRO_EXT, parse_snowpro

    stats = {"structured_files": 0, "structured_parsed": 0, "structured_errors": 0, "structured_identical_files": 0}
    seen: set[str] = set()
    root = Path(profiles_root)
    for f in sorted(root.rglob("*")):
        if not f.is_file() or f.suffix.lower() not in SNOWPRO_EXT:
            continue
        stats["structured_files"] += 1
        sha = hashlib.sha256(f.read_bytes()).hexdigest()
        if sha in seen:
            stats["structured_identical_files"] += 1
            continue
        seen.add(sha)
        try:
            o = parse_snowpro(f, tz)
        except Exception as exc:  # noqa: BLE001 - recorded, never silently dropped
            stats["structured_errors"] += 1
            out.append({"profile_id": f"unparsed_{sha[:6]}", "source_file": str(f), "source_sha256": sha,
                        "obs_time_utc": None, "layers": [], "flags": [f"parse_error:{type(exc).__name__}"],
                        "provenance": {"method": "structured:unknown", "confidence": "exact"}, "duplicate_of": None,
                        "site_key": None, "unusable": True})
            continue
        parts = f.relative_to(root).parts
        category, site = classify(parts[:-1], cfg.get("site_aliases", {}), set(cfg.get("study_plots", {})))
        season = next((p for p in parts if re.fullmatch(r"\d{4}-\d{4}", p)), None)
        fdate, fflags = parse_filename_date(f.name, season)
        if o["obs_time_utc"] is None and fdate:
            o["obs_time_utc"] = pd.Timestamp(fdate).tz_localize(ZoneInfo(tz)).tz_convert("UTC").isoformat()
            o["flags"] = [q for q in o["flags"] if q != "no_observation_date"] + ["date_from_filename"] + fflags
        elif o["obs_time_utc"] and fdate:
            local = pd.Timestamp(o["obs_time_utc"]).tz_convert(ZoneInfo(tz)).date().isoformat()
            if local != fdate:
                o["flags"].append(f"file_date_{local}_differs_from_filename_{fdate}")
        date = (o["obs_time_utc"] or "nodate")[:10]
        slug = re.sub(r"[^a-z0-9]+", "_", f.stem.lower()).strip("_")[:30]
        plots = cfg.get("study_plots", {})
        o.update({"profile_id": f"{date}_{site or slug}_{sha[:6]}", "site_key": site,
                  "station_id": (plots.get(site) or {}).get("station_id") if site else None,
                  "category": category.value, "source_file": str(f), "location_qc": []})
        o["unusable"] = not o["layers"] or o["obs_time_utc"] is None
        out.append(o)
        stats["structured_parsed"] += 1
    return stats
