"""Archived GFS runs (``archive/forecasts/gfs/gfs_YYYYMMDDHH.csv``, read only) as forecast weather records.

The issue (initial) time comes from the file name and is checked against the file's ``run_utc`` column; a run is
available ``latency_h`` after it (ADR-059; the site's ``weather.sources.GFS_LATENCY_H``). 3-hourly windows are
de-accumulated to hourly by ``forecast.gfs_point.gfs_hourly`` (the site's own reader). Values are at the GFS
surface height of the plot's point (``config/plot_forcing.yaml`` ``gfs_point``), not moved to the plot elevation,
not bias-corrected; hours the run does not cover stay null and flagged ``missing``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from snowagent.lab.benchmark.availability import recompute_quality
from snowagent.lab.schemas.benchmark import ForecastRun
from snowagent.lab.schemas.common import AvailabilityAssumption, QualityFlag
from snowagent.lab.schemas.weather import WEATHER_VARIABLES
from snowagent.lab.storage.provenance import sha256_file

FILE = re.compile(r"^gfs_(\d{10})\.csv$")
# gfs_hourly column -> canonical variable
VARIABLES = {"ta": "air_temperature_k", "rh": "relative_humidity_frac", "psum": "precipitation_mm",
             "vw": "wind_speed_ms", "dw": "wind_direction_deg", "iswr": "shortwave_radiation_wm2",
             "ilwr": "longwave_radiation_wm2"}


class GfsArchiveError(ValueError):
    """An archive file whose name and content disagree."""


@dataclass(frozen=True)
class GfsArchive:
    directory: Path

    def runs(self) -> pd.DatetimeIndex:
        """Issue times of the archived runs (from the file names), sorted."""
        d = Path(self.directory)
        times = [pd.Timestamp(m[1][:8] + "T" + m[1][8:], tz="UTC") for f in (d.glob("gfs_*.csv") if d.is_dir() else [])
                 if (m := FILE.match(f.name))]
        return pd.DatetimeIndex(sorted(times), name="issued_at") if times else pd.DatetimeIndex([], tz="UTC")

    def path(self, issued_at: pd.Timestamp) -> Path:
        return Path(self.directory) / f"gfs_{pd.Timestamp(issued_at):%Y%m%d%H}.csv"

    def read(self, issued_at: pd.Timestamp) -> pd.DataFrame:
        f = self.path(issued_at)
        df = pd.read_csv(f)
        runs = set(pd.to_datetime(df["run_utc"], utc=True)) if len(df) else set()
        if runs != {pd.Timestamp(issued_at)}:
            raise GfsArchiveError(f"{f.name}: run_utc {sorted(str(r) for r in runs)} differs from the file name")
        return df


def candidate_runs(runs: pd.DatetimeIndex, as_of: pd.Timestamp, latency_h: float, max_age_h: float
                   ) -> list[pd.Timestamp]:
    """Runs available at ``as_of`` (issue + latency <= as_of) and issued within ``max_age_h`` of it, latest first."""
    if not len(runs):
        return []
    ok = (runs + pd.Timedelta(hours=latency_h) <= as_of) & (runs >= as_of - pd.Timedelta(hours=max_age_h))
    return list(runs[ok][::-1])


def forecast_frame(df: pd.DataFrame, point: str, site_code: str, issued_at: pd.Timestamp, latency_h: float,
                   provenance_id: str) -> tuple[pd.DataFrame, float]:
    """One run at one point as canonical forecast rows (``ingest.weather`` table layout) and its surface height."""
    from snowagent.forecast.gfs_point import gfs_hourly

    hourly, elev = gfs_hourly(df, point)
    out = pd.DataFrame({"observed_at": hourly.index})
    out.insert(0, "site_code", site_code)
    out["source_id"] = f"gfs025:{point}"
    out["kind"] = "forecast"
    out["issued_at"] = pd.Timestamp(issued_at)
    out["source_recorded_at"] = pd.Timestamp(issued_at) + pd.Timedelta(hours=latency_h)
    out["availability_assumption"] = AvailabilityAssumption.assumed_delay.value
    inverse = {v: k for k, v in VARIABLES.items()}
    for var in WEATHER_VARIABLES:
        vals = hourly[inverse[var]].to_numpy(dtype=float) if var in inverse else np.full(len(out), np.nan)
        has = ~np.isnan(vals)
        out[var] = vals
        out[f"{var}_source"] = np.where(has, f"gfs025:{point}", None)
        out[f"{var}_qc"] = np.where(has, QualityFlag.ok.value, QualityFlag.missing.value)
    out["quality_flag"] = recompute_quality(out)
    out["provenance_id"] = provenance_id
    return out, elev


def run_info(archive: GfsArchive, issued_at: pd.Timestamp, point: str, latency_h: float, elev: float,
             max_lead_h: float, source_root: Path) -> ForecastRun:
    f = archive.path(issued_at)
    try:
        rel = str(f.resolve().relative_to(Path(source_root).resolve()))
    except ValueError:
        rel = str(f)
    return ForecastRun(source_id=f"gfs025:{point}", point=point, issued_at=issued_at.to_pydatetime(),
                       available_at=(issued_at + pd.Timedelta(hours=latency_h)).to_pydatetime(),
                       surface_elevation_m=round(elev, 1), max_lead_h=max_lead_h, file=rel, sha256=sha256_file(f))
