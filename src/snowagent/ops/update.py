"""The periodic update (ADR-037): collect new weather, public reports and dropped-in profiles, then rebuild the
live season for the site tool. Every step is idempotent and safe to repeat; raw data go to the tracked archives
(``archive/``, ``profiles/``) unchanged, derived data to ``data/`` and ``web/data`` (regenerable).

Steps that need a person or a model are not here: retrieving uploads from the site's form store, transcribing
PDFs/photos (docs/transcription/GUIDE.md), deploying, committing. ``docs/operations.md`` is the runbook.

- ``bootstrap``: in a fresh checkout, restore the raw station files from ``archive/fts360`` and rebuild the interim
  conversions the forcing reads (logger exports, dashboard history, ERA5 box heights).
- ``fetch``: FTS360 records since the start of last month; the 00 UTC GFS runs not yet archived or incomplete
  there (season start to today; those of the last 21 days are retried, older gaps are reported); ERA5 months newly
  published on the mirror; MIN reports of the last 14 days; the profile inbox; the Sunshine Village webcams
  (ADR-041).
- ``build``: the observed-profile set, the live season (all three plots), the public-report files, the site
  index and ``web/data/status.json``.

Problems are flagged, never filled silently: both steps return a ``warnings`` list (``warning()`` layout: level
info/warning/error, source, message, last_record_utc, age_h); the build's list is also written to status.json
and shown on the site (ADR-043).

Each source of ``fetch`` and each part of ``build`` runs in its own error boundary (``Steps``): a failure is listed
in ``failed_steps`` (step, error) and as an ``error`` warning, and the run goes on with the next step, so one failed
source never stops the others and status.json is always written (ADR-044).
"""

from __future__ import annotations

import gzip
import json
import shutil
import subprocess
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

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


def warning(level: str, source: str, message: str, last_record_utc: str | None = None, age_h: float | None = None,
            **extra) -> dict:
    """One entry of the ``warnings`` lists in the fetch output and status.json (level: info, warning or error)."""
    return {"level": level, "source": source, "message": message, "last_record_utc": last_record_utc,
            "age_h": age_h, **extra}


def failure(step: str, exc: BaseException) -> dict:
    """One entry of ``failed_steps``: the step and the exception's type and text."""
    msg = str(exc)
    if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
        err = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else str(exc.stderr)
        msg += f" stderr: {err.strip()[-200:]}"
    return {"step": step, "error": f"{type(exc).__name__}: {msg[:300]}"}


class Steps:
    """Error boundary per step of an update run: a step that raises is recorded in ``failed`` and returns
    ``default``, and the run goes on with the next step. Failures a step contained itself (a ``failed_steps`` list
    in its dict result) are moved into ``failed`` as well."""

    def __init__(self) -> None:
        self.failed: list[dict] = []

    def __call__(self, name: str, fn: Callable[..., Any], *args: Any, default: Any = None, **kwargs: Any) -> Any:
        try:
            out = fn(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 - recorded; the run reports it in failed_steps and its exit code
            self.failed.append(failure(name, exc))
            return default
        if isinstance(out, dict):
            self.failed += out.pop("failed_steps", [])
        return out


# What a failed step leaves undone (the message of its error warning); looked up by step, then by its prefix.
STEP_EFFECT = {
    "fts360": "no new station records collected this run",
    "fts360:archive": "new station records not copied to archive/fts360; copied at the next fetch",
    "gfs": "no new GFS runs archived this run; missing runs are retried at the next fetch",
    "gfs:archive_sync": "new GFS runs extracted but not copied to archive/; retried at the next fetch",
    "era5": "no new ERA5 months this run; retried at the next fetch",
    "min": "no new MIN reports collected this run",
    "inbox": "dropped-in profiles not filed this run",
    "webcams": "no webcam images stored this run",
    "observed": "observed-profile set not rebuilt; the previous one is used",
    "season": "live season not rebuilt; the site keeps the previous build of this plot, if any",
    "public": "public-report files not rewritten",
    "index": "sites.json not rewritten; the site lists the previous builds",
    "station_status": "station and GFS staleness not checked",
    "gfs_check": "GFS archive gaps not checked",
    "min_status": "MIN report status not updated",
    "inbox_status": "inbox status not updated",
}


def step_failed_warning(f: dict) -> dict:
    """The ``error`` warning for one entry of ``failed_steps``."""
    step = f["step"]
    effect = STEP_EFFECT.get(step) or STEP_EFFECT.get(step.split(":")[0], "its outputs are from an earlier run")
    return warning("error", f"update:{step}", f"Daily update step {step} failed ({f['error']}): {effect}.",
                   step=step)


# ------------------------------------------------------------------------------------------------ fetch
def sync_fts360_archive(now: pd.Timestamp | None = None) -> dict:
    """Raw monthly CSVs -> archive/fts360 as gzip (unchanged bytes); complete months once, the current and previous
    one refreshed. An archived month is never replaced by a raw file with fewer data rows (kept, and listed)."""
    from snowagent.ingest.fts360 import csv_data_rows

    now = now or pd.Timestamp.now(tz="UTC")
    refresh = (f"_{now:%Y-%m}.csv", f"_{(now - pd.offsets.MonthBegin(1)):%Y-%m}.csv")
    n, kept = 0, []
    for f in sorted(FTS_RAW.glob("*/*.csv")):
        dest = FTS_ARCHIVE / f.parent.name / (f.name + ".gz")
        if dest.exists() and not f.name.endswith(refresh):
            continue
        raw = f.read_bytes()
        new = gzip.compress(raw, compresslevel=9, mtime=0)
        if dest.exists():
            old = dest.read_bytes()
            if old == new:
                continue
            rows, old_rows = csv_data_rows(raw), csv_data_rows(gzip.decompress(old))
            if rows < old_rows:
                kept.append(f"{f.name}: {rows} data rows, fewer than the {old_rows} archived; archived copy kept")
                continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(new)
        n += 1
    if (FTS_RAW / "manifest.jsonl").exists():
        shutil.copyfile(FTS_RAW / "manifest.jsonl", FTS_ARCHIVE / "manifest.jsonl")
    return {"archived": n, "kept": kept}


def fetch_fts360(now: pd.Timestamp | None = None) -> dict:
    """Records since the start of last month for every configured station, then the archive sync. A station that
    raises is listed in ``failed_steps`` and the others go on; a refused credential (401/403, ``PermissionError``)
    concerns every station, so the rest are skipped (``skipped``). The archive sync runs either way."""
    from snowagent.ingest.fts360 import fetch_station

    now = now or pd.Timestamp.now(tz="UTC")
    cfg = yaml.safe_load(Path("config/external_sources.yaml").read_text())["fts360"]
    start = (now.normalize() - pd.offsets.MonthBegin(1)).tz_localize(None) if now.day > 1 else \
        (now.normalize() - pd.offsets.MonthBegin(2)).tz_localize(None)
    out: dict = {}
    warnings, failed = [], []
    keys = list(cfg["stations"])
    for i, key in enumerate(keys):
        try:
            recs = fetch_station(cfg["agency"], key, cfg["stations"][key], start.isoformat() + "Z", now.isoformat(),
                                 FTS_RAW)
        except PermissionError as exc:  # credential missing or refused: the same for every station
            failed.append(failure("fts360", exc))
            out[key] = {"files": 0, "errors": [failed[-1]["error"]]}
            out["skipped"] = keys[i + 1:]
            break
        except Exception as exc:  # noqa: BLE001 - this station is reported, the others go on
            failed.append(failure(f"fts360:{key}", exc))
            out[key] = {"files": 0, "errors": [failed[-1]["error"]]}
            continue
        out[key] = {"files": sum(bool(r.get("path")) for r in recs),
                    "errors": [r.get("error") for r in recs if r.get("error")][:2]}
        warnings += [warning("warning", f"fts360:{key}", r["warning"]) for r in recs if r.get("warning")]
    try:
        sync = sync_fts360_archive(now)
        out["archived_files"] = sync["archived"]
        warnings += [warning("warning", "fts360:archive", m) for m in sync["kept"]]
    except Exception as exc:  # noqa: BLE001 - reported; the raw files are synced at the next fetch
        failed.append(failure("fts360:archive", exc))
        out["archived_files"] = None
    out["warnings"] = warnings
    out["failed_steps"] = failed
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


def _gfs_window(season_start: pd.Timestamp, now: pd.Timestamp, lookback_days: int
                ) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    """First run of the live season, first run still retried, latest run expected (complete ~04:30 UTC)."""
    last_run = now.floor("D") if now.hour >= 5 else now.floor("D") - pd.Timedelta(days=1)
    season_first = season_start.floor("D") - pd.Timedelta(days=1)
    return season_first, max(season_first, last_run - pd.Timedelta(days=lookback_days)), last_run


def gfs_archive_check(season_start: pd.Timestamp, now: pd.Timestamp | None = None, lookback_days: int = 21,
                      max_lead: int = 72, points: list[str] | None = None, archive: Path | None = None) -> dict:
    """00 UTC runs of the live season missing from the archive or incomplete there (``gfs_archive.run_complete``:
    fewer points or leads), split into those the daily fetch still retries (the last ``lookback_days``) and those
    past the retry window (``*_permanent``: no longer fetched)."""
    from snowagent.ingest.gfs_archive import run_complete

    now = now or pd.Timestamp.now(tz="UTC")
    archive = archive or GFS_ARCHIVE
    pts = list(_gfs_points()) if points is None else points
    season_first, first_retry, last_run = _gfs_window(season_start, now, lookback_days)
    out: dict[str, list[pd.Timestamp]] = {"retry_missing": [], "retry_incomplete": [], "missing_permanent": [],
                                          "incomplete_permanent": []}
    for run in pd.date_range(season_first, last_run, freq="D"):
        f = archive / f"gfs_{run:%Y%m%d%H}.csv"
        state = "missing" if not f.exists() else None if run_complete(f, pts, max_lead) else "incomplete"
        if state:
            out[f"retry_{state}" if run >= first_retry else f"{state}_permanent"].append(run)
    return out


def _days(runs: list[pd.Timestamp]) -> list[str]:
    return [f"{r:%Y-%m-%d}" for r in runs]


def gfs_gap_warnings(chk: dict, lookback_days: int = 21, retrying: bool = True) -> list[dict]:
    """Warnings for the runs ``gfs_archive_check`` found: past the retry window (warning) and, with ``retrying``,
    still retried (info)."""
    out = []
    for key, what in (("missing_permanent", "missing"), ("incomplete_permanent", "incomplete (fewer points or leads)")):
        if chk[key]:
            out.append(warning("warning", "gfs", f"GFS: {len(chk[key])} 00 UTC run(s) of the season {what} and past "
                               f"the {lookback_days}-day retry window, no longer fetched: {', '.join(_days(chk[key]))}",
                               runs=_days(chk[key])))
    retry = sorted(chk["retry_missing"] + chk["retry_incomplete"])
    if retrying and retry:
        out.append(warning("info", "gfs", f"GFS: {len(retry)} recent 00 UTC run(s) not (fully) archived yet, retried "
                           f"at the next fetch: {', '.join(_days(retry))}", runs=_days(retry)))
    return out


def fetch_gfs(season_start: pd.Timestamp, now: pd.Timestamp | None = None, max_lead: int = 72, step: int = 3,
              lookback_days: int = 21) -> dict:
    """00 UTC runs of the live season missing from the archive or incomplete there: those of the last
    ``lookback_days`` are (re-)extracted, older ones are reported as permanently missing/incomplete."""
    from snowagent.ingest.gfs_archive import extract_run, write_run

    now = now or pd.Timestamp.now(tz="UTC")
    pts, leads = _gfs_points(), list(range(0, max_lead + 1, step))
    chk = gfs_archive_check(season_start, now, lookback_days, max_lead, list(pts))
    done, failed = [], []
    for run in sorted(chk["retry_missing"] + chk["retry_incomplete"]):
        try:
            rows, prov = extract_run(run.to_pydatetime(), leads, pts)
            write_run(rows, prov, GFS_INTERIM, run.to_pydatetime())
            done.append(f"{run:%Y-%m-%d}")
        except Exception as exc:  # noqa: BLE001 - a missing or broken run is retried next time
            failed.append(f"{run:%Y-%m-%d}: {type(exc).__name__}: {str(exc)[:120]}")
    synced, steps_failed = None, []
    if done:  # the sync replaces an archived run only by a larger extract
        try:
            subprocess.run(["bash", "scripts/sync_forecast_archive.sh"], check=True, capture_output=True)
            synced = True
        except (subprocess.CalledProcessError, OSError) as exc:  # not archived: the runs are redone next time
            steps_failed.append(failure("gfs:archive_sync", exc))
            synced = False
    return {"runs_added": done, "archive_synced": synced, "failed": failed,
            "incomplete_retried": _days(chk["retry_incomplete"]),
            "permanently_missing": _days(chk["missing_permanent"]),
            "permanently_incomplete": _days(chk["incomplete_permanent"]),
            "warnings": gfs_gap_warnings(chk, lookback_days, retrying=False)
            + [warning("info", "gfs", f"GFS run {f}; retried at the next fetch") for f in failed],
            "failed_steps": steps_failed}


ERA5_OVERDUE_DAYS = 122  # unpublished this long after the month's end -> reported (weather.sources mirror latency + 30 d)


def fetch_era5(season_year: int, now: pd.Timestamp | None = None) -> dict:
    """ERA5 months of the season that have appeared on the mirror since the last update (months-late).

    A month the mirror does not have yet is ``not_yet_available``; every other failure is listed in ``errors`` with
    its exception text and is a warning, as is a month still unpublished ``ERA5_OVERDUE_DAYS`` after its end and an
    extracted month with hours lacking flux values (detected only, never re-extracted here). Missing months and
    errors are retried next time.
    """
    from snowagent.ingest import era5

    now = now or pd.Timestamp.now(tz="UTC")
    got, missing, errors, warnings = [], [], [], []
    for d in pd.date_range(f"{season_year}-09-01", now.tz_localize(None).normalize(), freq="MS"):
        month, f = f"{d:%Y-%m}", ERA5_DIR / f"era5_box_{d.year}{d.month:02d}.npz"
        if not f.exists():
            try:
                era5.extract_month(d.year, d.month, ERA5_DIR)
                got.append(month)
            except Exception as exc:  # noqa: BLE001 - classified and reported; retried next time
                if era5.is_unpublished(exc):
                    missing.append(month)
                    late = (now.tz_localize(None) - (d + pd.offsets.MonthBegin(1))).days
                    if late > ERA5_OVERDUE_DAYS:
                        warnings.append(warning("warning", "era5", f"ERA5 {month}: still not on the mirror {late} "
                                                "days after the month ended (expected ~3 months)"))
                else:
                    cause = f" (from {type(exc.__cause__).__name__}: {exc.__cause__})" if exc.__cause__ else ""
                    errors.append(f"{month}: {type(exc).__name__}: {exc}{cause}"[:300])
                    warnings.append(warning("warning", "era5", f"ERA5 {month}: extraction failed: {errors[-1]}"))
                continue
        try:
            gaps = era5.flux_gap_hours(f)
        except Exception as exc:  # noqa: BLE001 - reported, the fetch goes on
            warnings.append(warning("warning", "era5", f"ERA5 {month}: {f.name} unreadable: {type(exc).__name__}: "
                                    f"{str(exc)[:160]}"))
            continue
        if gaps:
            warnings.append(warning("warning", "era5", f"ERA5 {month}: {gaps} h without flux values "
                                    f"(precipitation/radiation) in {f.name}; not re-extracted automatically",
                                    hours=gaps))
    return {"added": got, "not_yet_available": missing, "errors": errors, "warnings": warnings}


def fetch_min(now: pd.Timestamp) -> dict:
    from snowagent.ingest.min import update as min_update

    today = now.date()
    return min_update(today - timedelta(days=14), today)


def fetch_inbox() -> list[dict]:
    from snowagent.obs.inbox import process_inbox

    return [{k: r.get(k) for k in ("original_name", "status", "filed_as")} for r in process_inbox()]


def fetch_webcams(now: pd.Timestamp) -> list[dict]:
    from snowagent.ingest.webcam import capture

    return [{k: r.get(k) for k in ("cam", "kind", "status", "last_modified", "path")} for r in capture(now)]


def fetch(now: pd.Timestamp | None = None) -> dict:
    """Every source in its own error boundary (``Steps``): a failed source is ``None`` in the result, listed in
    ``failed_steps`` and as an ``error`` warning; ``ok`` is false when any step failed."""
    from snowagent.web.build import current_season_year

    now = now or pd.Timestamp.now(tz="UTC")
    y = current_season_year(now)
    step = Steps()
    res: dict = {"time_utc": now.isoformat(timespec="seconds"), "season": f"{y}-{y + 1}"}
    res["fts360"] = step("fts360", fetch_fts360, now)
    res["gfs"] = step("gfs", fetch_gfs, pd.Timestamp(f"{y}-09-15", tz="UTC"), now)
    res["era5"] = step("era5", fetch_era5, y, now)
    res["min"] = step("min", fetch_min, now)
    res["inbox"] = step("inbox", fetch_inbox)
    res["webcams"] = step("webcams", fetch_webcams, now)
    res["warnings"] = [w for k in ("fts360", "gfs", "era5") for w in (res[k] or {}).get("warnings", [])]
    res["warnings"] = [step_failed_warning(f) for f in step.failed] + res["warnings"]
    res["failed_steps"] = step.failed
    res["ok"] = not step.failed
    return res


# ------------------------------------------------------------------------------------------------ build
STATION_STALE_H = 24.0  # a plot station more than 24 h behind is stale (docs/operations.md)
GFS_STALE_H = 48.0  # no GFS run for 2 days
LEVELS = ("error", "warning", "info")
STATION_NAMES = {"sunshine_village_ab_env": "Sunshine Village AB station", "lookout": "Lookout",
                 "simpson_lower": "Simpson Lower", "simpson_upper": "Simpson Upper", "bow_summit": "Bow Summit",
                 "bow_summit_precip_ab_env": "Bow Summit gauge"}
ROLES = {"ta": "temperature", "rh": "humidity", "psum": "precipitation", "hs_check": "snow-depth check"}
FILL = "GFS day-1 fill"  # the live season's fill for recent hours (ERA5 is ~3 months late; ADR-037)


def plot_stations(plots: dict) -> list[str]:
    """Stations that feed a plot (forcing variables or the snow-depth check), in config order."""
    out: list[str] = []
    for p in plots.values():
        for var in ROLES:
            out += [s for s in p.get(var) or [] if s not in out]
    return out


def _roles(station: str, plots: dict, stale: set[str]) -> list[str]:
    """What a stale station does for each plot and what is used in its place now, one sentence per consequence
    (those with an effect first), e.g. "Lookout supplies Goat's Eye humidity: GFS day-1 fill used instead."."""
    from snowagent.web.build import SITES

    name = STATION_NAMES.get(station, station)
    groups: dict[tuple[bool, str, str], list[str]] = {}
    for plot, p in plots.items():
        site = SITES.get(plot, plot).split(" - ")[-1]
        for var, label in ROLES.items():
            srcs = p.get(var) or []
            if station not in srcs:
                continue
            use = next((s for s in srcs if s not in stale), None)
            verb = "backs up" if srcs.index(station) > 0 else "supplies"
            if use is not None and srcs.index(use) < srcs.index(station):
                key = (True, verb, f"{STATION_NAMES.get(use, use)} in use")
            elif use is not None:
                key = (False, verb, f"{STATION_NAMES.get(use, use)} used instead")
            else:
                key = (False, verb, "no measured snow depth to compare" if var == "hs_check" else f"{FILL} used instead")
            groups.setdefault(key, []).append(f"{site} {label}")
    out = []
    for (_no_effect, verb, then), items in sorted(groups.items(), key=lambda g: g[0][0]):
        listed = items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]
        out.append(f"{name} {verb} {listed}: {then}.")
    return out


def staleness_warnings(now: pd.Timestamp, last: dict[str, pd.Timestamp | None], gfs_latest: pd.Timestamp | None,
                       cfg: dict) -> list[dict]:
    """Stale inputs at ``now``: a plot station whose last record is more than ``STATION_STALE_H`` old (or absent),
    and a latest archived GFS run more than ``GFS_STALE_H`` old. ``cfg`` is config/plot_forcing.yaml; a station in
    its ``seasonal_stations`` that is stale in one of its ``off_months`` is reported as expected (level info)."""
    plots, seasonal = cfg["plots"], cfg.get("seasonal_stations") or {}
    if isinstance(seasonal, list):  # a bare list of station ids: off months not given, always expected
        seasonal = {k: {} for k in seasonal}
    age = {k: None if t is None else round((now - t).total_seconds() / 3600, 1) for k, t in last.items()}
    stale = {k for k, a in age.items() if a is None or a > STATION_STALE_H}
    out = []
    for k in last:
        if k not in stale:
            continue
        name, t = STATION_NAMES.get(k, k), last[k]
        seen = "no records found" if t is None else f"last record {t:%Y-%m-%d %H:%M} UTC, {age[k]:.0f} h ago"
        level = "warning"
        if k in seasonal and now.month in ((seasonal[k] or {}).get("off_months") or range(1, 13)):
            level, state = "info", f"seasonal station, off for the summer (expected); {seen}"
        elif k in seasonal:
            state = f"seasonal station, but no record in a month it usually runs; {seen}"
        else:
            state = f"no record for more than {STATION_STALE_H:.0f} h; {seen}"
        out.append(warning(level, f"station:{k}", " ".join([f"{name}: {state}."] + _roles(k, plots, stale)),
                           None if t is None else t.isoformat(), age[k], station_id=k, seasonal=k in seasonal))
    if gfs_latest is None:
        out.append(warning("warning", "gfs", "GFS: no run archived; no forecasts and no GFS day-1 fill."))
    else:
        h = round((now - gfs_latest).total_seconds() / 3600, 1)
        if h > GFS_STALE_H:
            out.append(warning("warning", "gfs", f"GFS: latest archived 00 UTC run {gfs_latest:%Y-%m-%d} is {h:.0f} h "
                               f"old (stale after {GFS_STALE_H:.0f} h): no forecasts since, and the live weather fill "
                               "relies on older runs or stops.", gfs_latest.isoformat(), h))
    return out


def _station_status(now: pd.Timestamp | None = None) -> tuple[dict[str, str | None], list[dict]]:
    """Last record per plot station and the latest GFS run (status.json ``weather``), and their staleness warnings."""
    from snowagent.ingest.fts360 import load_station
    from snowagent.web.build import _cfg

    now = now or pd.Timestamp.now(tz="UTC")
    cfg = _cfg()
    used = plot_stations(cfg["plots"])
    last: dict[str, pd.Timestamp | None] = {}
    out: dict[str, str | None] = {}
    for key in [k for k in STATION_NAMES if k in used] + [k for k in used if k not in STATION_NAMES]:
        d = load_station(key)
        last[key] = None if d.empty else pd.Timestamp(d["time_utc"].max())
        out[f"{STATION_NAMES.get(key, key)}: last record"] = None if last[key] is None else last[key].isoformat()
    gfs = sorted(GFS_ARCHIVE.glob("gfs_*.csv"))
    gfs_latest = pd.to_datetime(gfs[-1].stem[4:], format="%Y%m%d%H", utc=True) if gfs else None
    out["GFS: latest run archived"] = None if gfs_latest is None else gfs_latest.isoformat()
    return out, staleness_warnings(now, last, gfs_latest, cfg)


def _observed() -> int | None:
    from snowagent.obs.observed import build_observed, write_observed

    obs, stats = build_observed(Path("observations/transcriptions"), Path("profiles"))
    write_observed(obs, Path("data/interim/obs/observed_profiles.jsonl"))
    return stats.get("unique_observations")


def _min_status() -> dict:
    from snowagent.ingest.min import ARCHIVE as MIN_ARCHIVE
    from snowagent.ingest.min import latest_versions

    st_min = json.loads((MIN_ARCHIVE / "state.json").read_text()) if (MIN_ARCHIVE / "state.json").exists() else {}
    return {"reports": len(latest_versions(MIN_ARCHIVE)), "last_scan_utc": st_min.get("last_scan_utc")}


def _inbox_status() -> dict:
    from snowagent.obs.inbox import receipts_summary

    return {"items": receipts_summary()}


def build(now: pd.Timestamp | None = None, workers: int = 4, out_dir: Path = WEB_DATA,
          work: Path = Path("artifacts/web_work")) -> dict:
    """Each part in its own error boundary (``Steps``): a plot that fails does not stop the others, and the index
    and status.json are always written, with every failed step as an ``error`` warning; ``ok`` is false when any
    step failed."""
    from snowagent.web.build import SITES, build_season, current_season_year, write_index, write_public

    now = now or pd.Timestamp.now(tz="UTC")
    y = current_season_year(now)
    step = Steps()
    res: dict = {"observed": step("observed", _observed), "seasons": []}
    for plot in SITES:
        s = step(f"season:{plot}", build_season, plot, y, out_dir, work, workers=workers)
        res["seasons"].append(s if s is not None else
                              {"site": plot, "season": f"{y}-{y + 1}", "error": step.failed[-1]["error"]})
    res["public"] = step("public", write_public, out_dir)
    step("index", write_index, out_dir)
    weather, warnings = step("station_status", _station_status, now, default=({}, []))
    warnings += [w for s in res["seasons"] for w in s.get("warnings", [])]  # forcing cuts (web.build.season_forcing)
    warnings += step("gfs_check", lambda: gfs_gap_warnings(gfs_archive_check(pd.Timestamp(f"{y}-09-15", tz="UTC"),
                                                                             now)), default=[])
    min_status, inbox_status = step("min_status", _min_status), step("inbox_status", _inbox_status)
    warnings += [step_failed_warning(f) for f in step.failed]
    warnings.sort(key=lambda w: LEVELS.index(w["level"]))  # most severe first (stable)
    status = {"generated_utc": datetime.now(UTC).isoformat(timespec="seconds"), "season": f"{y}-{y + 1}",
              "weather": weather, "warnings": warnings,
              "stale_after_h": {"station": STATION_STALE_H, "gfs": GFS_STALE_H},
              "min": min_status, "inbox": inbox_status}
    res["warnings"] = warnings
    res["failed_steps"] = step.failed
    res["ok"] = not step.failed
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    (Path(out_dir) / "status.json").write_text(json.dumps(status, indent=1))
    res["status"] = str(Path(out_dir) / "status.json")
    return res


def _today() -> date:
    return datetime.now(UTC).date()
