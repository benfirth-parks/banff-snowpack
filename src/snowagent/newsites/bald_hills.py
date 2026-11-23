"""Maligne station (FTS360, AvCan station 123) for the Bald Hills test site (ADR-147): raw monthly files -> monthly
files with one name per sensor, so ``ingest.fts360.parse_station`` reads every month.

The logger changed in late 2024: snow depth ``SD`` became ``SDcm`` and the cumulative gauge ``Precip`` became ``PC``.
``parse_station`` concatenates the months and keeps the first column it knows for a variable, so the older months'
``SD`` and ``Precip`` would be lost behind the newer, empty ``SDcm`` and ``PC`` columns (and ``Precip`` is not a name
it reads at all). Each month is rewritten here with the newer names; values are copied unchanged, and the Banff QC
then applies as it is. Outside the lab's prediction code hash, like the rest of ``snowagent.newsites``.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

ALIASES = {"SD": "SDcm", "Precip": "PC"}  # older Maligne logger name -> newer name (both cm / mm)


def to_new_names(raw: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
    """A month's raw table with the older sensor names renamed (only where the newer name is absent)."""
    out, renamed = raw.copy(), []
    for old, new in ALIASES.items():
        if old in out and new not in out:
            out = out.rename(columns={old: new})
            renamed.append(f"{old}->{new}")
        elif old in out:
            out = out.drop(columns=[old])
    return out, renamed


def convert(raw_dir: Path, out_dir: Path, key: str = "maligne") -> dict:
    """``raw_dir/<key>/*.csv`` -> ``out_dir/<key>/*.csv`` (a station folder ``lab.ingest.weather`` reads)."""
    src, dst = Path(raw_dir) / key, Path(out_dir) / key
    dst.mkdir(parents=True, exist_ok=True)
    report = {"months": 0, "renamed_months": 0}
    for f in sorted(src.glob("*.csv")):
        if f.stat().st_size == 0:
            continue
        d, renamed = to_new_names(pd.read_csv(f, dtype=str))
        d.to_csv(dst / f.name, index=False)
        report["months"] += 1
        report["renamed_months"] += bool(renamed)
    return report
