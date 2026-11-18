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
from functools import lru_cache
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
        st = f.stat()
        df = _read_run(str(f), st.st_mtime_ns, st.st_size).copy()  # cached: the builder reads a run twice
        runs = set(pd.to_datetime(df["run_utc"], utc=True)) if len(df) else set()
        if runs != {pd.Timestamp(issued_at)}:
            raise GfsArchiveError(f"{f.name}: run_utc {sorted(str(r) for r in runs)} differs from the file name")
        return df


@lru_cache(maxsize=64)
def _read_run(path: str, _mtime_ns: int, _size: int) -> pd.DataFrame:
    return pd.read_csv(path)


def candidate_runs(runs: pd.DatetimeIndex, as_of: pd.Timestamp, latency_h: float, max_age_h: float
                   ) -> list[pd.Timestamp]:
    """Runs available at ``as_of`` (issue + latency <= as_of) and issued within ``max_age_h`` of it, latest first."""
    if not len(runs):
        return []
    ok = (runs + pd.Timedelta(hours=latency_h) <= as_of) & (runs >= as_of - pd.Timedelta(hours=max_age_h))
    return list(runs[ok][::-1])


def reaching_run(archive: GfsArchive, runs: pd.DatetimeIndex, valid: pd.Timestamp, point: str, latency_h: float,
                 search_window_h: float, choice: str = "longest_lead") -> tuple[pd.Timestamp, float] | None:
    """The archived run a ``run_reaches_valid`` case uses (ADR-060): available before ``valid`` (issue + latency <
    valid), issued at most ``search_window_h`` before it, carrying ``point`` and with leads reaching ``valid``
    (issue + max lead >= valid). ``longest_lead``: the earliest such run; ``latest``: the last. Returns (issue time,
    max lead h) or None."""
    if not len(runs):
        return None
    ok = (runs + pd.Timedelta(hours=latency_h) < valid) & (runs >= valid - pd.Timedelta(hours=search_window_h))
    cands = list(runs[ok]) if choice == "longest_lead" else list(runs[ok][::-1])
    for issued in cands:
        df = archive.read(issued)
        if point not in set(df["point"]):
            continue
        max_lead = float(df.loc[df["point"] == point, "lead_h"].max())
        if issued + pd.Timedelta(hours=max_lead) >= valid:
            return issued, max_lead
    return None


def forecast_frame(df: pd.DataFrame, point: str, site_code: str, issued_at: pd.Timestamp, latency_h: float,
                   provenance_id: str, hrdps: pd.DataFrame | None = None) -> tuple[pd.DataFrame, float]:
    """One run at one point as canonical forecast rows (``ingest.weather`` table layout) and its surface height.
    With ``hrdps`` (the HRDPS run of the same issue time, ADR-115) its 48 h come first and the GFS run continues
    them (``forecast.hrdps_point.blend_hourly``); each row's source says which model it is."""
    from snowagent.forecast.gfs_point import gfs_hourly

    hourly, elev = gfs_hourly(df, point)
    source = np.full(len(hourly), f"gfs025:{point}", dtype=object)
    if hrdps is not None:
        from snowagent.forecast.hrdps_point import blend_hourly, hrdps_hourly

        hr = hrdps_hourly(hrdps, point)
        n_hr = len(hr[0].dropna())
        hourly, elev, _info = blend_hourly(hr, (hourly, elev))
        source = np.array([f"hrdps:{point}"] * n_hr + [f"gfs025:{point}"] * (len(hourly) - n_hr), dtype=object)
    out = pd.DataFrame({"observed_at": hourly.index})
    out.insert(0, "site_code", site_code)
    out["source_id"] = source
    out["kind"] = "forecast"
    out["issued_at"] = pd.Timestamp(issued_at)
    out["source_recorded_at"] = pd.Timestamp(issued_at) + pd.Timedelta(hours=latency_h)
    out["availability_assumption"] = AvailabilityAssumption.assumed_delay.value
    inverse = {v: k for k, v in VARIABLES.items()}
    for var in WEATHER_VARIABLES:
        vals = hourly[inverse[var]].to_numpy(dtype=float) if var in inverse else np.full(len(out), np.nan)
        has = ~np.isnan(vals)
        out[var] = vals
        out[f"{var}_source"] = np.where(has, source, None)
        out[f"{var}_qc"] = np.where(has, QualityFlag.ok.value, QualityFlag.missing.value)
    out["quality_flag"] = recompute_quality(out)
    out["provenance_id"] = provenance_id
    return out, elev


def hrdps_path(directory: Path, issued_at: pd.Timestamp) -> Path:
    """The archived HRDPS run of an issue time (``ops.hrdps``, ADR-115)."""
    return Path(directory) / f"hrdps_{pd.Timestamp(issued_at):%Y%m%d%H}.csv"


def read_hrdps(directory: Path, issued_at: pd.Timestamp, point: str) -> pd.DataFrame | None:
    """The HRDPS run of ``issued_at`` when it is archived, complete (every lead to 48 h with temperature and
    precipitation) and carries ``point``; otherwise None (the case keeps GFS alone)."""
    f = hrdps_path(directory, issued_at)
    if not f.is_file():
        return None
    st = f.stat()
    df = _read_run(str(f), st.st_mtime_ns, st.st_size)
    sub = df[df["point"] == point] if "point" in df else df.iloc[:0]
    if sub.empty or sub["lead_h"].max() < 48 or sub[["tmp2m_k", "apcp_kgm2"]].isna().any().any():
        return None
    if set(pd.to_datetime(sub["run_utc"], utc=True)) != {pd.Timestamp(issued_at)}:
        raise GfsArchiveError(f"{f.name}: run_utc differs from the file name")
    return df.copy()


def run_info(archive: GfsArchive, issued_at: pd.Timestamp, point: str, latency_h: float, elev: float,
             max_lead_h: float, source_root: Path, file: Path | None = None, source_id: str | None = None
             ) -> ForecastRun:
    f = Path(file) if file is not None else archive.path(issued_at)
    try:
        rel = str(f.resolve().relative_to(Path(source_root).resolve()))
    except ValueError:
        rel = str(f)
    return ForecastRun(source_id=source_id or f"gfs025:{point}", point=point, issued_at=issued_at.to_pydatetime(),
                       available_at=(issued_at + pd.Timedelta(hours=latency_h)).to_pydatetime(),
                       surface_elevation_m=round(elev, 1), max_lead_h=max_lead_h, file=rel, sha256=sha256_file(f))
