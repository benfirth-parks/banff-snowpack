"""Weather series for a real terrain domain from the project's data sources, in the weather/io format (ADR-033).

Three series per forecast case, each with honest availability so that nothing unavailable at issue time can
enter a run (the pipeline enforces it with ``WeatherSeries.available_by``):

- history: station-first plot forcing with ERA5 fill (``baseline.assemble``). ERA5T is published ~5 days behind
  real time by ECMWF, so the series is declared with that latency and can only build a state up to issue - 5 days
  (ADR-033). The mirror this project reads publishes ~3 months late (ADR-037): for a current issue time the
  history series is then incomplete and ``write_plot_series`` refuses it; the live site uses the GFS day-1 fill.
- recent: the same station-first forcing, with the fill taken from each day's 00 UTC GFS run (leads 1-24 h,
  "GFS day-1 composite") instead of ERA5. Each run is available ``gfs_latency_h`` after its initial time, so
  every record is available within that latency of its valid time.
- forecast: one archived GFS run at the GFS point for the plot, available ``gfs_latency_h`` after initial time.

Station and ERA5/GFS values are moved to the plot elevation by ``assemble``; the forcing builder then moves
them to each terrain unit. The series may extend past the issue time on purpose: the pipeline must ignore it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import yaml

from snowagent.contracts import Provenance, WeatherKind, WeatherMeta
from snowagent.forecast.gfs_point import gfs_hourly
from snowagent.weather.io import write_weather

ERA5_LATENCY_S = 5 * 86400  # ERA5T: ~5 days behind real time (ECMWF); what a real-time chain could know (ADR-033)
ERA5_MIRROR_LATENCY_S = 92 * 86400  # NSF NCAR mirror read here: ~3 months behind (ADR-037, ADR-043)
GFS_LATENCY_H = 5  # 00 UTC 0.25 deg run complete on NOMADS ~04:30 UTC; 5 h is conservative
GFS_DIR = Path("archive/forecasts/gfs")


def gfs_run_path(run: pd.Timestamp, gfs_dir: Path = GFS_DIR) -> Path:
    return Path(gfs_dir) / f"gfs_{run:%Y%m%d%H}.csv"


def gfs_day1_series(point: str, idx: pd.DatetimeIndex, gfs_dir: Path = GFS_DIR, fallback_days: int = 0
                    ) -> tuple[pd.DataFrame, float]:
    """Hourly SI series over ``idx`` chained from consecutive 00 UTC runs, each contributing (D, D + 24 h].

    Hours whose run is missing stay NaN (the caller's fill/QC decides) unless ``fallback_days`` > 0: then they
    are taken from the run 1..fallback_days days earlier at the matching longer lead (e.g. leads 25-48 h of
    run D-1), the next-best forecast available at the same time; their count is in ``out.attrs``. Returns the
    GFS surface height of the point (constant between runs; the last run read is used).
    """
    cols = ["ta", "rh", "vw", "dw", "iswr", "ilwr", "psum"]
    out = pd.DataFrame(index=idx, columns=cols, dtype=float)
    elev = float("nan")
    days = pd.date_range((idx[0] - pd.Timedelta(hours=1)).floor("D"), idx[-1].floor("D"), freq="D")
    fallback = 0
    for d in days:
        for back in range(fallback_days + 1):
            run = d - pd.Timedelta(days=back)
            f = gfs_run_path(run, gfs_dir)
            if not f.exists():
                continue
            g, elev = gfs_hourly(pd.read_csv(f), point)
            g = g[(g.index > d) & (g.index <= d + pd.Timedelta(hours=24))].dropna()
            common = out.index.intersection(g.index)
            if back:
                common = common[out.loc[common, "ta"].isna()]
                fallback += len(common)
            out.loc[common, cols] = g.loc[common, cols].to_numpy()
            if not out.loc[out.index.intersection(pd.date_range(d + pd.Timedelta(hours=1), periods=24, freq="h")),
                           "ta"].isna().any():
                break
    out.attrs["fallback_hours"] = fallback
    return out, elev


def _plot_cfg(plot: str) -> dict:
    return yaml.safe_load(Path("config/plot_forcing.yaml").read_text())["plots"][plot]


def write_plot_series(plot: str, start: pd.Timestamp, end: pd.Timestamp, path: Path, reanalysis: str,
                      latency_s: int, series_id: str, description: str, keep_from: pd.Timestamp | None = None,
                      corrected: bool = True) -> dict:
    """Station-first plot forcing (fill from ``reanalysis``) written as a historical_forcing series."""
    from snowagent.baseline.assemble import assemble, source_summary

    p = _plot_cfg(plot)
    pf = assemble(plot, str(start), str(end), reanalysis=reanalysis)
    if corrected and p.get("psum_factor", 1.0) != 1.0:
        pf.data["psum"] = pf.data["psum"] * p["psum_factor"]
        pf.notes.append(f"CORRECTED: precipitation x {p['psum_factor']} (ADR-024)")
    data = pf.data if keep_from is None else pf.data[pf.data.index >= keep_from]
    if data.isna().any().any():
        bad = data.columns[data.isna().any()].tolist()
        raise ValueError(f"{series_id}: incomplete forcing for {bad} (first gap {data.index[data.isna().any(axis=1)][0]})")
    meta = WeatherMeta(
        series_id=series_id, kind=WeatherKind.historical_forcing, source=f"plot forcing {plot} ({reanalysis} fill)",
        station_id=plot, lat=p["lat"], lon=p["lon"], source_elevation_m=float(p["elevation_m"]),
        elevation_adjusted_by_provider=False, timestamp_convention="end_of_interval", accumulation_interval_s=3600,
        availability_latency_s=int(latency_s), wind_height_m=10.0, met_height_m=2.0,
        provenance=Provenance(source="snowagent.baseline.assemble", description=description,
                              created_utc=datetime.now(UTC), synthetic=False,
                              notes=pf.notes + [f"sources: {source_summary(pf)}"]))
    write_weather(Path(path), meta, data)
    return {"path": str(path), "start": str(data.index[0]), "end": str(data.index[-1]), "hours": len(data),
            "sources": source_summary(pf)}


def write_gfs_forecast(run: pd.Timestamp, point: str, lat: float, lon: float, path: Path,
                       gfs_dir: Path = GFS_DIR, latency_h: int = GFS_LATENCY_H) -> dict:
    """One archived GFS run as a forecast series at the GFS surface height of ``point`` (no bias correction)."""
    f = gfs_run_path(run, gfs_dir)
    g, elev = gfs_hourly(pd.read_csv(f), point)
    g = g.dropna()
    meta = WeatherMeta(
        series_id=f"gfs025_{point}_{run:%Y%m%dT%H}Z", kind=WeatherKind.forecast, source="NOAA GFS 0.25 deg",
        model="GFS", model_version="0.25 deg operational (archive: noaa-gfs-bdp-pds)", lat=lat, lon=lon,
        source_elevation_m=elev, elevation_adjusted_by_provider=False, timestamp_convention="end_of_interval",
        accumulation_interval_s=3600, issue_time=run.to_pydatetime(),
        available_time=(run + pd.Timedelta(hours=latency_h)).to_pydatetime(), wind_height_m=10.0, met_height_m=2.0,
        provenance=Provenance(source=str(f), description=f"archived GFS point extract '{point}' (bilinear), "
                              "3-hourly windows de-accumulated to hourly (forecast.gfs_point)",
                              created_utc=datetime.now(UTC), synthetic=False,
                              notes=["not downscaled or bias-corrected; values at the GFS surface height",
                                     f"available_time = initial time + {latency_h} h (assumed dissemination delay)"]))
    write_weather(Path(path), meta, g)
    return {"path": str(path), "series_id": meta.series_id, "start": str(g.index[0]), "end": str(g.index[-1]),
            "gfs_surface_m": round(elev, 1), "available_time": str(meta.available_time)}


def case_inputs(plot: str, gfs_run: pd.Timestamp, out_dir: Path, season_start: pd.Timestamp,
                extend_h: int = 96) -> dict:
    """History, recent and forecast series for one forecast case; returns the paths and the analysis time.

    analysis_time = latest 00 UTC whose ERA5 is available at issue (issue = GFS available time). Series are
    written ``extend_h`` past the issue time so the pipeline's availability filter is exercised, not assumed.
    """
    p = _plot_cfg(plot)
    issue = gfs_run + pd.Timedelta(hours=GFS_LATENCY_H)
    analysis = (issue - pd.Timedelta(seconds=ERA5_LATENCY_S)).floor("D")
    beyond = issue + pd.Timedelta(hours=extend_h)
    out_dir = Path(out_dir)
    hist = write_plot_series(plot, season_start - pd.Timedelta(hours=6), beyond, out_dir / "history.csv", "era5",
                             ERA5_LATENCY_S, f"{plot}_station_era5_{season_start:%Y}",
                             "station-first plot forcing, ERA5 fill (published ~5 days late)")
    # the GFS fill's gauge-plausibility ratio and temperature offset need a few weeks of overlap
    recent = write_plot_series(plot, analysis - pd.Timedelta(days=30), beyond, out_dir / "recent.csv", "gfs_day1",
                               GFS_LATENCY_H * 3600, f"{plot}_station_gfsday1_{analysis:%Y%m%d}",
                               "station-first plot forcing, fill from each day's 00 UTC GFS leads 1-24 h",
                               keep_from=analysis - pd.Timedelta(hours=6))
    fc = write_gfs_forecast(gfs_run, p["gfs_point"], p["lat"], p["lon"], out_dir / f"forecast_{gfs_run:%Y%m%dT%H}Z.csv")
    return {"issue_time": str(issue), "analysis_time": str(analysis), "history": hist, "recent": recent,
            "forecast": fc}
