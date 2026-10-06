"""``snowagent lab prepare``: everything a fresh clone needs before ``lab import``, from the sources the project
already uses (ADR-075). Run from the repository root; nothing that exists is overwritten.

1. ``update bootstrap``: station files restored from ``archive/fts360``, the logger exports and dashboard history
   converted into ``data/interim``, and the ERA5 surface-height file (``era5_box_z.npz``).
2. Observed profiles (``data/interim/obs/observed_profiles.jsonl``) from ``profiles/`` and the transcriptions in
   ``observations/``, as ``snowagent obs profiles`` builds them.
3. The ERA5 months the lab reads to fill station gaps (``weather.era5_backfill``): September to June of every season
   in ``config/lab.yaml`` ``splits`` up to the current month, the months the project's ERA5 cache holds (``snowagent ingest era5``), from the
   NSF NCAR ERA5 mirror on AWS Open Data. For a reanalysis season (``splits.reanalysis_seasons`` with the switch on,
   ADR-076: no station weather, ERA5 drives its cases) only September to the month of the season's last pit at a lab
   plot, and nothing for a season without such a pit: no case reads the later months. Each variable-month is kept
   as it completes, so an interrupted fetch resumes; a month the mirror has not published yet is reported, not an
   error of the others.
"""

from __future__ import annotations

import contextlib
import io
import json
import re
import subprocess
import tarfile
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

from snowagent.lab.settings import LabConfig, season_key

ERA5_BUNDLE_BRANCH = "claude/lab-era5-box"  # extracted box months, ADR-079
ERA5_BUNDLE_FILE = re.compile(r"era5_box_(\d{6}|z)\.(npz|json)")
ERA5_MONTHS = (9, 10, 11, 12, 1, 2, 3, 4, 5, 6)  # as `snowagent ingest era5` (snow seasons; Jul-Aug not cached)
OBSERVED = Path("data/interim/obs/observed_profiles.jsonl")


def lab_seasons(cfg: LabConfig) -> list[str]:
    return cfg.splits.every_season()


def reanalysis_seasons(cfg: LabConfig) -> list[str]:
    """The seasons whose cases run on ERA5 (ADR-076), when the switch is on."""
    s = cfg.splits
    return sorted(s.reanalysis_seasons) if s.include_reanalysis_seasons else []


def last_pit_months(cfg: LabConfig, observed: Path) -> dict[str, tuple[int, int]]:
    """Season -> (year, month) of its last pit at one of the lab's plots, from the observed-profile file."""
    plots = {s.plot_id for s in cfg.sites.values()}
    out: dict[str, tuple[int, int]] = {}
    for line in Path(observed).read_text().splitlines():
        if not line.strip():
            continue
        rec = json.loads(line)
        if rec.get("site_key") not in plots or not rec.get("obs_time_utc"):
            continue
        t = datetime.fromisoformat(rec["obs_time_utc"])
        season = season_key(t, cfg.season_start)
        out[season] = max(out.get(season, (0, 0)), (t.astimezone(UTC).year, t.astimezone(UTC).month))
    return out


def era5_months(cfg: LabConfig, today: date | None = None,
                last_pit: dict[str, tuple[int, int]] | None = None) -> list[tuple[int, int]]:
    """(year, month) of every ERA5 month the lab's seasons can read: September to June of each season, up to the
    current month (a configured season still to come has no weather yet). With ``last_pit`` (season -> month of its
    last pit at a lab plot), a reanalysis season is cut after its last pit's month, and left out without a pit."""
    today = today or datetime.now(UTC).date()
    trim = set(reanalysis_seasons(cfg)) if last_pit is not None else set()
    out = set()
    for season in lab_seasons(cfg):
        y0 = int(season[:4])
        months = {(y0 if m >= 9 else y0 + 1, m) for m in ERA5_MONTHS}
        if season in trim:
            end = last_pit.get(season)  # type: ignore[union-attr]
            months = {ym for ym in months if end is not None and ym <= end}
        out |= months
    return sorted(ym for ym in out if ym <= (today.year, today.month))


def _fetch(task: tuple[int, int, str]) -> tuple[int, int, str | None]:
    from snowagent.ingest.era5 import extract_month

    y, m, out = task
    try:
        extract_month(y, m, Path(out))
        return y, m, None
    except Exception as exc:  # noqa: BLE001 - reported per month; a rerun retries
        return y, m, f"{type(exc).__name__}: {str(exc)[:160]}"


def restore_era5_bundle(era5_dir: Path, remote: str = "origin", branch: str = ERA5_BUNDLE_BRANCH,
                        log: Callable[[str], None] = print) -> int:
    """Copy the extracted ERA5 box months from the repository's bundle branch into ``era5_dir`` (ADR-079): one
    ``git fetch`` of about 0.2 GB instead of hours of range requests to the mirror, which a home connection often
    cannot finish. Run in the repository root. Only ``era5/era5_box_*.npz|json`` files are taken, nothing that exists
    is overwritten, and any failure (no git, no network, no branch) leaves the mirror fetch to do the work. Returns the
    number of months added."""
    try:
        subprocess.run(["git", "fetch", "--quiet", remote, branch], check=True, capture_output=True, timeout=3600)
        tar = subprocess.run(["git", "archive", "--format=tar", "FETCH_HEAD", "era5"], check=True,
                             capture_output=True, timeout=600).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        err = getattr(exc, "stderr", b"") or b""
        log(f"ERA5 bundle: not available ({type(exc).__name__} {err.decode(errors='replace').strip()[:120]}); "
            "fetching from the mirror")
        return 0
    added = 0
    era5_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(tar)) as tf:
        for member in tf.getmembers():
            name = Path(member.name).name
            if not (member.isfile() and ERA5_BUNDLE_FILE.fullmatch(name)) or (era5_dir / name).exists():
                continue
            (era5_dir / name).write_bytes(tf.extractfile(member).read())
            added += name.endswith(".npz") and name != "era5_box_z.npz"
    log(f"ERA5 bundle: {added} months added from branch {branch}")
    return added


def fetch_era5(cfg: LabConfig, era5_dir: Path, workers: int = 4, log: Callable[[str], None] = print,
               fetch: Callable[[tuple[int, int, str]], tuple[int, int, str | None]] = _fetch,
               observed: Path | None = None) -> dict:
    """Fetch the lab's ERA5 months missing from ``era5_dir`` (``workers`` processes). ``observed``: the observed-
    profile file that limits the reanalysis seasons to the months up to their last pit (all of September to June
    without it)."""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    last_pit = last_pit_months(cfg, observed) if observed is not None and Path(observed).is_file() else None
    months = era5_months(cfg, last_pit=last_pit)
    old = set(reanalysis_seasons(cfg))
    n_old = sum(season_key(datetime(y, m, 20, tzinfo=UTC), cfg.season_start) in old for y, m in months)
    todo = [(y, m, str(era5_dir)) for y, m in months if not (era5_dir / f"era5_box_{y}{m:02d}.npz").is_file()]
    log(f"ERA5: {len(months)} months of seasons {lab_seasons(cfg)[0]} to {lab_seasons(cfg)[-1]} up to now "
        f"({n_old} of them for the reanalysis seasons, ADR-076), {len(months) - len(todo)} cached, {len(todo)} to "
        f"fetch ({workers} workers, about 5 min per month each)")
    failed: list[str] = []
    done = 0
    if todo:
        era5_dir.mkdir(parents=True, exist_ok=True)
        if workers <= 1:
            results = (fetch(t) for t in todo)
        else:
            ex = ProcessPoolExecutor(workers)  # h5py serialises threads: processes
            results = (f.result() for f in as_completed([ex.submit(fetch, t) for t in todo]))
        for y, m, err in results:
            done += 1
            if err:
                failed.append(f"{y}-{m:02d}: {err}")
            log(f"  ERA5 {y}-{m:02d} {'FAILED ' + err if err else 'ok'} ({done}/{len(todo)})")
        if workers > 1:
            ex.shutdown()
    return {"months": len(months), "cached": len(months) - len(todo), "fetched": len(todo) - len(failed),
            "failed": failed}


def prepare(root: Path, cfg: LabConfig, era5: bool = True, workers: int = 4,
            log: Callable[[str], None] = print, bundle: bool = True) -> dict:
    """The three steps of the module doc, in ``root`` (the repository root)."""
    from snowagent.ops.update import bootstrap

    t0 = time.time()
    report: dict = {}
    with contextlib.chdir(root):
        log("bootstrap: station files, logger exports, dashboard history, ERA5 heights")
        report["bootstrap"] = bootstrap()
        if OBSERVED.is_file():
            report["observed_profiles"] = "present"
        else:
            from snowagent.obs.observed import build_observed, summarise_observed, write_observed

            log("observed profiles: building from profiles/ and observations/transcriptions")
            obs, stats = build_observed(Path("observations/transcriptions"), Path("profiles"))
            write_observed(obs, OBSERVED)
            summarise_observed(obs).to_csv(OBSERVED.with_name("observed_summary.csv"), index=False)
            report["observed_profiles"] = stats
        if era5 and cfg.weather.era5_backfill:
            if bundle:
                report["era5_bundle_months"] = restore_era5_bundle(Path(cfg.weather.era5_dir), log=log)
            report["era5"] = fetch_era5(cfg, Path(cfg.weather.era5_dir), workers, log, observed=OBSERVED)
        else:
            report["era5"] = "skipped (station values only; gaps stay missing)"
    report["wall_s"] = round(time.time() - t0, 1)
    log(json.dumps(report, indent=1, default=str))
    return report
