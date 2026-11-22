"""Train and test on one part of the winter only (ADR-136; owner 2026-11-22: "train and test on specific periods
where there is warming or spring").

A run with a period keeps only the cases whose pit falls in it, for training, the locked test winters and the
Parker Ridge check alike, so every score the run reports is on that kind of pit. The definitions are fixed here,
before any run used them:

- ``spring``: the pit was dug from March to June (the valid time's month, UTC).
- ``warm``: a warm spell before the pit: at least ``WARM_HOURS`` hours with the air at or above 0 degC in the
  ``WARM_WINDOW_H`` hours before the pit's time, from the case's own visible weather (measured or reanalysis hours
  first, forecast hours where those are missing). Only the visible package is read, the same data the agents see.
- ``spring_or_warm``: either of the two.

Selecting cases this way uses no score and no hidden pit, so it cannot leak a result into training."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from snowagent.lab.benchmark import package as pkg
from snowagent.lab.schemas.benchmark import CaseManifest

PERIODS: dict[str, str] = {
    "spring": "pits dug from March to June",
    "warm": "pits after a warm spell (air at or above 0 degC for at least 6 hours in the 3 days before)",
    "spring_or_warm": "spring pits and pits after a warm spell",
}
SPRING_MONTHS = (3, 4, 5, 6)
WARM_WINDOW_H = 72.0
WARM_HOURS = 6
FREEZING_K = 273.15
OBSERVED_KINDS = ("observed", "reanalysis")


def is_spring(m: CaseManifest) -> bool:
    return m.valid_time.month in SPRING_MONTHS


def warm_hours(case_dir: Path, m: CaseManifest) -> int:
    """Hours with the air at or above 0 degC in the ``WARM_WINDOW_H`` hours before the pit, from the visible weather
    (measured or reanalysis hours first, forecast hours for the rest)."""
    end = float(m.horizon_hours)  # the pit's time, in hours after as-of
    frames = []
    for name in (pkg.WEATHER_OBSERVED, pkg.WEATHER_FORECASTS):
        f = Path(case_dir) / pkg.VISIBLE / name
        if f.is_file():
            d = pd.read_parquet(f, columns=["t_rel_h", "kind", "air_temperature_k"])
            frames.append(d)
    if not frames:
        return 0
    w = pd.concat(frames, ignore_index=True).dropna(subset=["air_temperature_k"])
    w = w[(w.t_rel_h > end - WARM_WINDOW_H) & (w.t_rel_h <= end)]
    if w.empty:
        return 0
    w = w.assign(first=~w.kind.isin(OBSERVED_KINDS), hour=w.t_rel_h.round(3))
    w = w.sort_values(["hour", "first"]).drop_duplicates("hour")  # one value an hour, measured before forecast
    return int((w.air_temperature_k >= FREEZING_K).sum())


def in_period(case_dir: Path, m: CaseManifest, period: str) -> bool:
    if period not in PERIODS:
        raise ValueError(f"period must be one of {', '.join(PERIODS)}")
    if period in ("spring", "spring_or_warm") and is_spring(m):
        return True
    if period in ("warm", "spring_or_warm"):
        return warm_hours(case_dir, m) >= WARM_HOURS
    return False


def filter_cases(cases: list[tuple[Path, CaseManifest]], period: str | None) -> list[tuple[Path, CaseManifest]]:
    """The cases in ``period`` (all of them when it is None)."""
    if not period:
        return cases
    return [(d, m) for d, m in cases if in_period(d, m, period)]


def filter_refs(refs: list, period: str | None) -> list:
    """``filter_cases`` for case references (``CaseRef``: ``case_dir`` and ``manifest``)."""
    if not period:
        return refs
    return [r for r in refs if in_period(r.case_dir, r.manifest, period)]


__all__ = ["PERIODS", "WARM_HOURS", "WARM_WINDOW_H", "filter_cases", "filter_refs", "in_period", "is_spring",
           "warm_hours"]
