"""Review list of study-plot pits whose location, printed date or printed site disagrees with their filing (ADR-051).

One row per study-plot pit (duplicates included, marked) with review reasons (``obs.observed.review_reasons``):
``location_qc`` entries and the printed date/site flags of ADR-049. Whether the pit steers a site run follows
``learn/steer.py``: selected by ``baseline.evaluate.pits_at_plot`` and ``learn.steer.update_pits`` for a
measured-weather season of a site plot (``web.build.SITES``, ``STATION_SEASONS``), with the configured
``exclude_flagged_pits_from_steering_and_scoring``. Two things need the run itself and are not in that rule: a
season cut short by incomplete forcing, and an update skipped because the modelled depth was below 20 cm. For those,
the steer updates recorded in built season files (``web/data/<plot>/<season>.json``) are read when present.

Data QC for the owner. Nothing is changed, reassigned or excluded here.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from snowagent.obs.filenames import parse_filename_date
from snowagent.obs.observed import exclude_flagged_pits, review_reasons

COLUMNS = ["profile_id", "site_key", "flag_types", "printed_date", "filename_date", "printed_site_name",
           "location_qc", "review_flags", "steers_site_run", "steer_run", "steer_update_utc", "built_run_update",
           "duplicate_of", "other_copies", "unusable", "confidence", "reviewed", "method", "folder", "source_file"]
FLAG_TYPES = ("date", "site", "location")
DATE_FLAG = re.compile(r"printed_date_(\d{4}-\d{2}-\d{2})_differs_from_filename_(\d{4}-\d{2}-\d{2})")


def flag_types(o: dict) -> list[str]:
    """Which of the review reasons a pit has: date, site, location."""
    fl = o.get("flags") or []
    return [k for k, hit in zip(FLAG_TYPES, (any(f.startswith("printed_date_") for f in fl),
                                             any(f.startswith("printed_site_name_") for f in fl),
                                             bool(o.get("location_qc"))), strict=True) if hit]


def steering_pits(obs: Iterable[dict], exclude_flagged: bool, now: pd.Timestamp | None = None) -> dict[str, dict]:
    """profile_id -> {run, update_utc} for every pit that updates a measured-weather site run (learn/steer.py)."""
    from snowagent.baseline.evaluate import pits_at_plot
    from snowagent.learn.steer import update_pits
    from snowagent.web.build import SITES, STATION_SEASONS, _cfg

    obs = list(obs)
    cfg = _cfg()
    now = now or pd.Timestamp.now(tz="UTC")
    out: dict[str, dict] = {}
    for plot in SITES:
        for y in STATION_SEASONS[plot]:
            start = pd.Timestamp(f"{y}-{cfg['season_start']}", tz="UTC")
            end = min(pd.Timestamp(f"{y + 1}-{cfg['season_end']}", tz="UTC"), now.floor("h"))
            pits = pits_at_plot(obs, plot, start, end, exclude_flagged)
            for t, o in update_pits(pits, start, end, exclude_flagged).items():
                out[o["profile_id"]] = {"run": f"{plot}/{y}-{y + 1}", "update_utc": t.isoformat()}
    return out


def built_updates(site_data: Path) -> dict[str, str]:
    """profile_id -> what the built season files record for that pit's steer update."""
    out: dict[str, str] = {}
    for f in sorted(Path(site_data).glob("*/*.json")):
        if f.name.endswith(("_forecasts.json", "_public.json")):
            continue
        d = json.loads(f.read_text())
        for u in (d.get("steer") or {}).get("updates", []):
            where = f"{f.parent.name}/{f.stem}"
            out[u["pit"]] = (f"no update in {where}: {u['note']}" if u.get("note") else
                             f"updated {where} ({u.get('method', 'depth')}, factor {u.get('factor')})")
    return out


def review_rows(obs: list[dict], exclude_flagged: bool, site_data: Path | None = None,
                now: pd.Timestamp | None = None) -> list[dict]:
    """One row (``COLUMNS``) per study-plot pit with review reasons, by site and date."""
    steer = steering_pits(obs, exclude_flagged, now)
    built = built_updates(site_data) if site_data is not None and Path(site_data).is_dir() else None
    copies: dict[str, list[str]] = {}
    for o in obs:
        if o.get("duplicate_of"):
            copies.setdefault(o["duplicate_of"], []).append(o["profile_id"])
    rows = []
    for o in obs:
        why = review_reasons(o)
        if o.get("category") != "study_plot" or not why:
            continue
        src = Path(o["source_file"])
        season = next((p for p in src.parts if re.fullmatch(r"\d{4}-\d{4}", p)), None)
        printed, fdate = None, parse_filename_date(src.name, season)[0]
        if o.get("obs_time_utc") and "date_from_filename" not in o.get("flags", []):
            tz = ZoneInfo(o.get("time_zone") or "Etc/GMT+7")
            printed = pd.Timestamp(o["obs_time_utc"]).tz_convert(tz).date().isoformat()
        conflict = next((m for f in o.get("flags", []) if (m := DATE_FLAG.fullmatch(f))), None)
        if conflict:  # the dates the flag was raised on (inventory filename date)
            printed, fdate = conflict.group(1), conflict.group(2)
        prov = o.get("provenance") or {}
        s = steer.get(o["profile_id"])
        rows.append({
            "profile_id": o["profile_id"], "site_key": o.get("site_key"), "flag_types": ";".join(flag_types(o)),
            "printed_date": printed, "filename_date": fdate,
            "printed_site_name": o.get("site_name_as_written"), "location_qc": ";".join(o.get("location_qc") or []),
            "review_flags": ";".join(q for q in why if q not in (o.get("location_qc") or [])),
            "steers_site_run": s is not None, "steer_run": s["run"] if s else None,
            "steer_update_utc": s["update_utc"] if s else None,
            "built_run_update": None if built is None else built.get(o["profile_id"], "not in the built season files"),
            "duplicate_of": o.get("duplicate_of"), "other_copies": ";".join(copies.get(o["profile_id"], [])),
            "unusable": bool(o.get("unusable")), "confidence": prov.get("confidence"),
            "reviewed": prov.get("reviewed"), "method": prov.get("method"), "folder": str(src.parent),
            "source_file": str(src)})
    return sorted(rows, key=lambda r: (r["site_key"] or "", r["printed_date"] or r["filename_date"] or "",
                                       r["profile_id"]))


def summarise(rows: list[dict]) -> dict:
    by_type = {}
    for k in FLAG_TYPES:
        sel = [r for r in rows if k in r["flag_types"].split(";")]
        by_type[k] = {"pits": len(sel), "steering_site_runs": sum(r["steers_site_run"] for r in sel)}
    return {"flagged_study_plot_pits": len(rows), "not_duplicates": sum(not r["duplicate_of"] for r in rows),
            "steering_site_runs": sum(r["steers_site_run"] for r in rows), "by_flag_type": by_type,
            "steering": [f"{r['profile_id']} -> {r['steer_run']} ({r['flag_types']})" for r in rows
                         if r["steers_site_run"]]}


def _md_cell(v) -> str:
    return "" if v is None or v == "" else str(v).replace("|", "\\|").replace(";", "; ")


def write_review(observed: Path, out_dir: Path, site_data: Path | None = None, config: Path | None = None,
                 now: pd.Timestamp | None = None) -> dict:
    """Write ``flagged_pits.csv`` and ``flagged_pits.md`` to ``out_dir``; returns the summary."""
    raw = Path(observed).read_bytes()
    obs = [json.loads(line) for line in raw.decode().splitlines() if line.strip()]
    exclude = exclude_flagged_pits(config)
    rows = review_rows(obs, exclude, site_data, now)
    summary = summarise(rows)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    csv, md = out_dir / "flagged_pits.csv", out_dir / "flagged_pits.md"
    pd.DataFrame(rows, columns=COLUMNS).to_csv(csv, index=False)
    built = site_data is not None and Path(site_data).is_dir()
    lines = [
        "# Study-plot pits flagged for review",
        "",
        "Data QC list for the owner's ruling (not an avalanche forecast). A pit is listed when it has `location_qc` "
        "entries, a printed date that differs from its filename date, or a printed site name that names another "
        "place than its folder's plot (ADR-049, ADR-051). Nothing has been changed or excluded.",
        "",
        f"- Observed set: `{observed}` (sha256 {hashlib.sha256(raw).hexdigest()[:16]}, {len(obs)} records)",
        f"- `exclude_flagged_pits_from_steering_and_scoring`: {str(exclude).lower()} "
        "(\"steers\" below is with this setting, per learn/steer.py)",
        f"- Built season files read for the actual updates: {f'`{site_data}`' if built else 'none'}",
        "",
        f"{summary['flagged_study_plot_pits']} flagged pits ({summary['not_duplicates']} not duplicates); "
        f"{summary['steering_site_runs']} steer a site run.",
        "",
        "| flag | pits | steering a site run |",
        "|---|---|---|",
        *(f"| {k} | {v['pits']} | {v['steering_site_runs']} |" for k, v in summary["by_flag_type"].items()),
        "",
        "| profile_id | site | flags | printed date | filename date | printed site name | location_qc | steers | "
        + ("built run | " if built else "") + "duplicate of | confidence | source file |",
        "|---" * (11 + built) + "|",
    ]
    for r in rows:
        steers = f"yes: {r['steer_run']}" if r["steers_site_run"] else "no"
        cells = [r["profile_id"], r["site_key"], r["flag_types"], r["printed_date"], r["filename_date"],
                 r["printed_site_name"], r["location_qc"], steers, *([r["built_run_update"]] if built else []),
                 r["duplicate_of"], r["confidence"], r["source_file"]]
        lines.append("| " + " | ".join(_md_cell(v) for v in cells) + " |")
    md.write_text("\n".join(lines) + "\n")
    return summary | {"exclude_flagged_pits_from_steering_and_scoring": exclude,
                      "outputs": {"csv": str(csv), "markdown": str(md)}}
