"""``snowagent lab prepare``: everything a fresh clone needs before ``lab import``, from the sources the project
already uses (ADR-075). Run from the repository root; nothing that exists is overwritten.

1. ``update bootstrap``: station files restored from ``archive/fts360``, the logger exports and dashboard history
   converted into ``data/interim``, and the ERA5 surface-height file (``era5_box_z.npz``).
2. Observed profiles (``data/interim/obs/observed_profiles.jsonl``) from ``profiles/`` and the transcriptions in
   ``observations/``, as ``snowagent obs profiles`` builds them.
3. The ERA5 months the lab reads to fill station gaps (``weather.era5_backfill``): September to June of every season
   in ``config/lab.yaml`` ``splits`` up to the current month, the months the project's ERA5 cache holds (``snowagent ingest era5``), from the
   NSF NCAR ERA5 mirror on AWS Open Data. Each variable-month is kept as it completes, so an interrupted fetch
   resumes; a month the mirror has not published yet is reported, not an error of the others.
"""

from __future__ import annotations

import contextlib
import json
import time
from collections.abc import Callable
from datetime import UTC, date, datetime
from pathlib import Path

from snowagent.lab.settings import LabConfig

ERA5_MONTHS = (9, 10, 11, 12, 1, 2, 3, 4, 5, 6)  # as `snowagent ingest era5` (snow seasons; Jul-Aug not cached)
OBSERVED = Path("data/interim/obs/observed_profiles.jsonl")


def lab_seasons(cfg: LabConfig) -> list[str]:
    s = cfg.splits
    return sorted({*s.all_seasons, *s.development_seasons, *s.validation_seasons, *s.sealed_test_seasons})


def era5_months(cfg: LabConfig, today: date | None = None) -> list[tuple[int, int]]:
    """(year, month) of every ERA5 month the lab's seasons can read: September to June of each season, up to the
    current month (a configured season still to come has no weather yet)."""
    today = today or datetime.now(UTC).date()
    out = set()
    for season in lab_seasons(cfg):
        y0 = int(season[:4])
        out |= {(y0 if m >= 9 else y0 + 1, m) for m in ERA5_MONTHS}
    return sorted(ym for ym in out if ym <= (today.year, today.month))


def _fetch(task: tuple[int, int, str]) -> tuple[int, int, str | None]:
    from snowagent.ingest.era5 import extract_month

    y, m, out = task
    try:
        extract_month(y, m, Path(out))
        return y, m, None
    except Exception as exc:  # noqa: BLE001 - reported per month; a rerun retries
        return y, m, f"{type(exc).__name__}: {str(exc)[:160]}"


def fetch_era5(cfg: LabConfig, era5_dir: Path, workers: int = 4, log: Callable[[str], None] = print,
               fetch: Callable[[tuple[int, int, str]], tuple[int, int, str | None]] = _fetch) -> dict:
    """Fetch the lab's ERA5 months missing from ``era5_dir`` (``workers`` processes)."""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    months = era5_months(cfg)
    todo = [(y, m, str(era5_dir)) for y, m in months if not (era5_dir / f"era5_box_{y}{m:02d}.npz").is_file()]
    log(f"ERA5: {len(months)} months of seasons {lab_seasons(cfg)[0]} to {lab_seasons(cfg)[-1]} up to now, "
        f"{len(months) - len(todo)} cached, {len(todo)} to fetch ({workers} workers, about 5 min per month each)")
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
            log: Callable[[str], None] = print) -> dict:
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
            report["era5"] = fetch_era5(cfg, Path(cfg.weather.era5_dir), workers, log)
        else:
            report["era5"] = "skipped (station values only; gaps stay missing)"
    report["wall_s"] = round(time.time() - t0, 1)
    log(json.dumps(report, indent=1, default=str))
    return report
