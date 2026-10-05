"""Synthetic inputs for the lab's tests, written into a temporary checkout (no real data; ADR-059).

- ``write_era5``: an ERA5 cache box (``data/interim/era5``) of constant fields for the given months.
- ``write_gfs``: an archived GFS run (``archive/forecasts/gfs/gfs_YYYYMMDDHH.csv``) with the archive's 3-hourly
  accumulation/average windows, at the given points.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ERA5_LAT = np.array([50.75, 51.0, 51.25, 51.5, 51.75])
ERA5_LON = np.array([-116.75, -116.5, -116.25, -116.0, -115.75])


def write_era5(root: Path, months: list[str], ta_k: float = 263.0, precip_mm_h: float = 0.2) -> Path:
    d = Path(root) / "data/interim/era5"
    d.mkdir(parents=True, exist_ok=True)
    shape2 = (len(ERA5_LAT), len(ERA5_LON))
    np.savez(d / "era5_box_z.npz", lat=ERA5_LAT, lon=ERA5_LON, z=np.full(shape2, 2100.0 * 9.80665))
    for ym in months:
        t = pd.date_range(f"{ym}-01", periods=pd.Period(ym).days_in_month * 24, freq="h", tz="UTC")
        n = len(t)
        full = np.ones((n, *shape2))
        np.savez(d / f"era5_box_{ym.replace('-', '')}.npz", time_utc=t.tz_convert(None).values.astype("datetime64[ns]"),
                 lat=ERA5_LAT, lon=ERA5_LON, **{"2t": full * ta_k, "2d": full * (ta_k - 3), "10u": full * 2.0,
                                                "10v": full * 0.0, "sp": full * 78000.0, "tcc": full * 0.5,
                                                "mtpr": full * precip_mm_h / 3600.0, "msdwswrf": full * 150.0,
                                                "msdwlwrf": full * 250.0})
    return d


def write_gfs(root: Path, issued: str, points: list[str], ta_k: float = 265.0, max_lead: int = 72) -> Path:
    d = Path(root) / "archive/forecasts/gfs"
    d.mkdir(parents=True, exist_ok=True)
    run = pd.Timestamp(issued)
    rows = []
    for lead in range(0, max_lead + 1, 3):
        for pt in points:
            r = {"run_utc": run.isoformat(), "lead_h": lead, "point": pt, "pres_pa": 78000.0, "model_elev_m": 2100.0,
                 "tmp2m_k": ta_k + lead / 72, "rh2m_pct": 80.0, "u10_ms": 1.0, "v10_ms": 1.0}
            if lead:
                a = lead - 3  # 3-hour windows: "(L-3)-L hour acc"
                r |= {"apcp_kgm2": 0.3, "apcp_kgm2_desc": f"{a}-{lead} hour acc fcst",
                      "dswrf_wm2": 100.0, "dswrf_wm2_desc": f"{a}-{lead} hour ave fcst",
                      "dlwrf_wm2": 240.0, "dlwrf_wm2_desc": f"{a}-{lead} hour ave fcst"}
            rows.append(r)
    f = d / f"gfs_{run:%Y%m%d%H}.csv"
    pd.DataFrame(rows).to_csv(f, index=False)
    return f
