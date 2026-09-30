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


NOT_DUG = re.compile(r"(didn'?t|did not|not)\s+dig", re.I)


def trim_unobserved(layers: list[dict], hs: float | None, pit_depth: float | None, notes: str
                    ) -> tuple[list[dict], list[str]]:
    """Remove the part of a drawn profile below the observed pit bottom (apps often draw bars to 0 cm)."""
    flags: list[str] = []
    if hs is not None and pit_depth is not None and 0 < pit_depth < hs:
        bottom = hs - pit_depth
        kept = []
        for ly in layers:
            if ly["top_cm"] is None or ly["bottom_cm"] is None:
                kept.append(ly)
            elif ly["top_cm"] <= bottom:
                flags.append("layer_below_pit_bottom_removed")
            elif ly["bottom_cm"] < bottom:
                kept.append({**ly, "bottom_cm": bottom})
                flags.append("layer_clipped_at_pit_bottom")
            else:
                kept.append(ly)
        return kept, sorted(set(flags))
    if NOT_DUG.search(notes or ""):
        flags.append("pit_did_not_reach_ground_bottom_unknown")
    return layers, flags


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
    else:
        layers, tflags = trim_unobserved(layers, hs, t.header.profile_depth_cm,
                                         " ".join(ly.comment or "" for ly in t.layers))
        flags += tflags
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

    def primary_of(grp):
        return sorted(grp, key=lambda o: (rank.get(o["provenance"]["confidence"], 9),
                                          any(q.startswith("site_folder_differs") for q in o["flags"]),
                                          o["source_file"]))[0]

    for grp in groups.values():
        if len(grp) > 1:
            primary = primary_of(grp)
            for o in grp:
                if o is not primary:
                    o["duplicate_of"] = primary["profile_id"]
                    o["flags"].append("same_observation_as_another_file")
    # same pit exported by different apps: same date, matching layer-thickness sequence
    by_date: dict[str, list[dict]] = {}
    for o in obs:
        if o["duplicate_of"] is None and o.get("obs_time_utc") and o.get("layers"):
            by_date.setdefault(pd.Timestamp(o["obs_time_utc"]).tz_convert("Etc/GMT+7").date().isoformat(), []).append(o)
    for grp in by_date.values():
        for i, a in enumerate(grp):
            for b in grp[i + 1:]:
                if a["duplicate_of"] or b["duplicate_of"] or not same_pit(a, b):
                    continue
                p, q = (a, b) if primary_of([a, b]) is a else (b, a)
                q["duplicate_of"] = p["profile_id"]
                q["flags"].append("same_pit_other_export_layer_thicknesses_match")
                if p.get("site_key") and q.get("site_key") and p["site_key"] != q["site_key"]:
                    for o in (p, q):
                        o["flags"].append(f"identical_profile_filed_under_sites_{p['site_key']}_and_{q['site_key']}")


def _thicknesses(o: dict) -> list[float]:
    return [abs(ly["top_cm"] - ly["bottom_cm"]) for ly in o["layers"]
            if ly["top_cm"] is not None and ly["bottom_cm"] is not None]


def _full(o: dict) -> list[tuple]:
    return [(ly["top_cm"], ly["bottom_cm"], ly["grain_form"], ly["hardness"]) for ly in o["layers"]]


def same_pit(a: dict, b: dict, tol_cm: float = 1.0, min_frac: float = 0.8) -> bool:
    """Same pit exported twice. Different known snow depths -> no. Different study plots -> only if every
    layer is identical (a copied/misfiled file). Otherwise >= 3 layers, near-equal counts and >= 80% of
    aligned layer thicknesses within 1 cm."""
    if a.get("hs_cm") is not None and b.get("hs_cm") is not None and abs(a["hs_cm"] - b["hs_cm"]) > 2:
        return False
    if a.get("site_key") and b.get("site_key") and a["site_key"] != b["site_key"]:
        return _full(a) == _full(b) and len(a["layers"]) >= 3
    ta, tb = _thicknesses(a), _thicknesses(b)
    if min(len(ta), len(tb)) < 3 or abs(len(ta) - len(tb)) > 1:
        return False
    n = min(len(ta), len(tb))
    best = max(sum(abs(x - y) <= tol_cm for x, y in zip(ta[s:s + n], tb[:n], strict=False))
               for s in range(len(ta) - n + 1))
    best = max(best, max(sum(abs(x - y) <= tol_cm for x, y in zip(ta[:n], tb[s:s + n], strict=False))
                         for s in range(len(tb) - n + 1)))
    return best >= min_frac * n


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
        aliases = cfg.get("site_aliases", {})
        category, site = classify(parts[:-1], aliases, set(cfg.get("study_plots", {})))
        named = None
        if o.get("site_name_as_written"):
            nm = re.sub(r"\s+(study\s*plot|plot)$", "", o["site_name_as_written"].strip().lower())
            hits = [k for k, names in aliases.items() if nm in names or nm == k.replace("_", " ")]
            named = hits[0] if len(hits) == 1 else None
        if site is not None and named is not None and named != site:
            o["flags"].append(f"site_folder_differs_from_name_in_file:{site}->{named}")
            site = named
        if site is None and category.value != "test_profile" and o.get("site_name_as_written"):
            # exact name match only (after dropping "study plot"/"plot"); near-misses stay unassigned
            name = re.sub(r"\s+(study\s*plot|plot)$", "", o["site_name_as_written"].strip().lower())
            hit = [k for k, names in aliases.items() if name in names or name == k.replace("_", " ")]
            if len(hit) == 1:
                from snowagent.obs.models import ProfileCategory

                category, site = ProfileCategory.study_plot, hit[0]
                o["flags"].append("site_from_name_in_file")
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
