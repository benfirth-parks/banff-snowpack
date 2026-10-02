"""The periodic update (ADR-037): collect new weather, public reports and dropped-in profiles, then rebuild the
live season for the site tool. Every step is idempotent and safe to repeat; raw data go to the tracked archives
(``archive/``, ``profiles/``) unchanged, derived data to ``data/`` and ``web/data`` (regenerable).

Steps that need a person or a model are not here: retrieving uploads from the site's form store, transcribing
PDFs/photos (docs/transcription/GUIDE.md), deploying, committing. ``docs/operations.md`` is the runbook.

- ``bootstrap``: in a fresh checkout, restore the raw station files from ``archive/fts360`` and rebuild the interim
  conversions the forcing reads (logger exports, dashboard history, ERA5 box heights).
- ``fetch``: FTS360 records since the start of last month; the 00 UTC GFS runs not yet archived (season start to
  today; runs that failed earlier are retried); ERA5 months newly published on the mirror; MIN reports of the last
  14 days; the profile inbox; the Sunshine Village webcams (ADR-041).
- ``build``: the observed-profile set, the live season (all three plots), the public-report files, the site
  index and ``web/data/status.json``.
"""

from __future__ import annotations

import gzip
import json
import shutil
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

FTS_RAW = Path("data/raw/fts360")
FTS_ARCHIVE = Path("archive/fts360")
GFS_ARCHIVE = Path("archive/forecasts/gfs")
GFS_INTERIM = Path("data/interim/forecasts/gfs")
ERA5_DIR = Path("data/interim/era5")
WEB_DATA = Path("web/data")


# ------------------------------------------------------------------------------------------------ bootstrap
def bootstrap() -> dict:
    """Restore what a fresh checkout lacks; never overwrites existing files."""
    out = {"fts360_restored": 0}
    for gz in sorted(FTS_ARCHIVE.glob("*/*.csv.gz")):
        dest = FTS_RAW / gz.parent.name / gz.name[:-3]
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(gzip.decompress(gz.read_bytes()))
            out["fts360_restored"] += 1
    if not (FTS_RAW / "manifest.jsonl").exists() and (FTS_ARCHIVE / "manifest.jsonl").exists():
        shutil.copyfile(FTS_ARCHIVE / "manifest.jsonl", FTS_RAW / "manifest.jsonl")
    if not Path("data/interim/byk_export/summary.json").exists():
        from snowagent.ingest.byk_export import convert as byk_convert

        byk_convert()
        out["byk_converted"] = True
    if not Path("data/interim/fts_dashboard/summary.json").exists():
        from snowagent.ingest.fts_dashboard import convert as dash_convert

        dash_convert()
        out["dashboard_converted"] = True
    if not (ERA5_DIR / "era5_box_z.npz").exists():
        from snowagent.ingest.era5 import fetch_box_height

        ERA5_DIR.mkdir(parents=True, exist_ok=True)
        fetch_box_height(ERA5_DIR)
        out["era5_heights"] = True
    from snowagent.engine import snowpack as sp

    try:
        out["engine"] = sp.find_engine().version_string
    except Exception as exc:  # noqa: BLE001 - reported; the runbook builds the engine (scripts/build_snowpack.sh)
        out["engine"] = f"missing: {exc}"
    return out


# ------------------------------------------------------------------------------------------------ fetch
def sync_fts360_archive(now: pd.Timestamp | None = None) -> int:
    """Raw monthly CSVs -> archive/fts360 as gzip (unchanged bytes); complete months once, the current one refreshed."""
    now = now or pd.Timestamp.now(tz="UTC")
    cur = f"{now:%Y-%m}"
    n = 0
    for f in sorted(FTS_RAW.glob("*/*.csv")):
        dest = FTS_ARCHIVE / f.parent.name / (f.name + ".gz")
        new = gzip.compress(f.read_bytes(), compresslevel=9, mtime=0)
        if not dest.exists() or (f.name.endswith(f"_{cur}.csv") and dest.read_bytes() != new) or \
                f.name.endswith(f"_{(now - pd.offsets.MonthBegin(1)):%Y-%m}.csv") and dest.read_bytes() != new:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(new)
            n += 1
    if (FTS_RAW / "manifest.jsonl").exists():
        shutil.copyfile(FTS_RAW / "manifest.jsonl", FTS_ARCHIVE / "manifest.jsonl")
    return n


def fetch_fts360(now: pd.Timestamp | None = None) -> dict:
    from snowagent.ingest.fts360 import fetch_station

    now = now or pd.Timestamp.now(tz="UTC")
    cfg = yaml.safe_load(Path("config/external_sources.yaml").read_text())["fts360"]
    start = (now.normalize() - pd.offsets.MonthBegin(1)).tz_localize(None) if now.day > 1 else \
        (now.normalize() - pd.offsets.MonthBegin(2)).tz_localize(None)
    out = {}
    for key, hex_id in cfg["stations"].items():
        recs = fetch_station(cfg["agency"], key, hex_id, start.isoformat() + "Z", now.isoformat(), FTS_RAW)
        out[key] = {"files": sum(bool(r.get("path")) for r in recs),
                    "errors": [r.get("error") for r in recs if r.get("error")][:2]}
    out["archived_files"] = sync_fts360_archive(now)
    return out


def _gfs_points() -> dict[str, tuple[float, float]]:
    st = yaml.safe_load(Path("config/stations.yaml").read_text())
    pts = {}
    for s in st["stations"]:
        if s.get("lat") is not None:
            pts[s["station_id"]] = (s["lat"], s["lon"])
        plot = s.get("study_plot") or {}
        if plot.get("lat") is not None:
            pts[s["station_id"] + "_plot"] = (plot["lat"], plot["lon"])
    return pts


def fetch_gfs(season_start: pd.Timestamp, now: pd.Timestamp | None = None, max_lead: int = 72, step: int = 3,
              lookback_days: int = 21) -> dict:
    """00 UTC runs of the live season not yet in the archive (the last ``lookback_days`` are retried)."""
    from snowagent.ingest.gfs_archive import extract_run, write_run

    now = now or pd.Timestamp.now(tz="UTC")
    last_run = now.floor("D") if now.hour >= 5 else now.floor("D") - pd.Timedelta(days=1)  # run complete ~04:30
    first = max(season_start.floor("D") - pd.Timedelta(days=1), last_run - pd.Timedelta(days=lookback_days))
    pts, leads = _gfs_points(), list(range(0, max_lead + 1, step))
    done, failed = [], []
    for run in pd.date_range(first, last_run, freq="D"):
        if (GFS_ARCHIVE / f"gfs_{run:%Y%m%d%H}.csv").exists():
            continue
        try:
            rows, prov = extract_run(run.to_pydatetime(), leads, pts)
            write_run(rows, prov, GFS_INTERIM, run.to_pydatetime())
            done.append(f"{run:%Y-%m-%d}")
        except Exception as exc:  # noqa: BLE001 - a missing or broken run is retried next time
            failed.append(f"{run:%Y-%m-%d}: {type(exc).__name__}: {str(exc)[:120]}")
    if done:
        subprocess.run(["bash", "scripts/sync_forecast_archive.sh"], check=True, capture_output=True)
    return {"runs_added": done, "failed": failed}


def fetch_era5(season_year: int) -> dict:
    """ERA5 months of the season that have appeared on the mirror since the last update (months-late)."""
    from snowagent.ingest.era5 import extract_month

    got, missing = [], []
    for d in pd.date_range(f"{season_year}-09-01", pd.Timestamp.now().normalize(), freq="MS"):
        if (ERA5_DIR / f"era5_box_{d.year}{d.month:02d}.npz").exists():
            continue
        try:
            extract_month(d.year, d.month, ERA5_DIR)
            got.append(f"{d:%Y-%m}")
        except Exception:  # noqa: BLE001 - not yet published (404) or transient; retried next time
            missing.append(f"{d:%Y-%m}")
    return {"added": got, "not_yet_available": missing}


def fetch(now: pd.Timestamp | None = None) -> dict:
    from snowagent.ingest.min import update as min_update
    from snowagent.obs.inbox import process_inbox
    from snowagent.web.build import current_season_year

    now = now or pd.Timestamp.now(tz="UTC")
    y = current_season_year(now)
    res = {"time_utc": now.isoformat(timespec="seconds"), "season": f"{y}-{y + 1}"}
    res["fts360"] = fetch_fts360(now)
    res["gfs"] = fetch_gfs(pd.Timestamp(f"{y}-09-15", tz="UTC"), now)
    res["era5"] = fetch_era5(y)
    today = now.date()
    res["min"] = min_update(today - timedelta(days=14), today)
    res["inbox"] = [{k: r.get(k) for k in ("original_name", "status", "filed_as")} for r in process_inbox()]
    from snowagent.ingest.webcam import capture

    res["webcams"] = [{k: r.get(k) for k in ("cam", "kind", "status", "last_modified", "path")} for r in capture(now)]
    return res


# ------------------------------------------------------------------------------------------------ build
def _station_status() -> dict[str, str | None]:
    from snowagent.ingest.fts360 import load_station

    out = {}
    names = {"sunshine_village_ab_env": "Sunshine Village AB station", "lookout": "Lookout",
             "simpson_lower": "Simpson Lower", "bow_summit": "Bow Summit", "bow_summit_precip_ab_env": "Bow Summit gauge"}
    for key, name in names.items():
        d = load_station(key)
        out[f"{name}: last record"] = None if d.empty else pd.Timestamp(d["time_utc"].max()).isoformat()
    gfs = sorted(GFS_ARCHIVE.glob("gfs_*.csv"))
    out["GFS: latest run archived"] = (pd.to_datetime(gfs[-1].stem[4:], format="%Y%m%d%H", utc=True).isoformat() if gfs else None)
    return out


def build(now: pd.Timestamp | None = None, workers: int = 4, out_dir: Path = WEB_DATA,
          work: Path = Path("artifacts/web_work")) -> dict:
    from snowagent.ingest.min import ARCHIVE as MIN_ARCHIVE
    from snowagent.ingest.min import latest_versions
    from snowagent.obs.inbox import receipts_summary
    from snowagent.obs.observed import build_observed, write_observed
    from snowagent.web.build import SITES, build_season, current_season_year, write_index, write_public

    now = now or pd.Timestamp.now(tz="UTC")
    y = current_season_year(now)
    obs, stats = build_observed(Path("observations/transcriptions"), Path("profiles"))
    write_observed(obs, Path("data/interim/obs/observed_profiles.jsonl"))
    res: dict = {"observed": stats.get("unique_observations"), "seasons": []}
    for plot in SITES:
        res["seasons"].append(build_season(plot, y, out_dir, work, workers=workers))
    res["public"] = write_public(out_dir)
    write_index(out_dir)
    st_min = json.loads((MIN_ARCHIVE / "state.json").read_text()) if (MIN_ARCHIVE / "state.json").exists() else {}
    status = {"generated_utc": datetime.now(UTC).isoformat(timespec="seconds"), "season": f"{y}-{y + 1}",
              "weather": _station_status(),
              "min": {"reports": len(latest_versions(MIN_ARCHIVE)), "last_scan_utc": st_min.get("last_scan_utc")},
              "inbox": {"items": receipts_summary()}}
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "status.json").write_text(json.dumps(status, indent=1))
    res["status"] = str(Path(out_dir) / "status.json")
    return res


def _today() -> date:
    return datetime.now(UTC).date()
