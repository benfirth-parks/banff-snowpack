"""ERA5 nearest-cell hourly series for the lab's weather backfill (owner, 2026-10-05: "FTS360, else ERA5 backfill";
ADR-059). Read from the project's ERA5 cache (``data/interim/era5/era5_box_YYYYMM.npz``, read only) by the site's
own reader (``baseline.assemble.era5_cell_series``: temperature, humidity, wind, radiation, precipitation) plus
surface pressure. Values are at the ERA5 cell's surface height (named in the source label), not moved to the plot.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

# era5_cell_series column -> canonical variable
VARIABLES = {"ta": "air_temperature_k", "rh": "relative_humidity_frac", "psum": "precipitation_mm",
             "vw": "wind_speed_ms", "dw": "wind_direction_deg", "iswr": "shortwave_radiation_wm2",
             "ilwr": "longwave_radiation_wm2"}


def era5_files(era5_dir: Path) -> list[Path]:
    d = Path(era5_dir)
    return sorted(d.glob("era5_box_*.npz")) if d.is_dir() else []


def era5_site_series(lat: float, lon: float, idx: pd.DatetimeIndex, era5_dir: Path) -> tuple[pd.DataFrame, float]:
    """Canonical-variable ERA5 series over ``idx`` (NaN where the cache has no month) and the cell height (m)."""
    from snowagent.baseline.assemble import era5_cell_series

    era5_dir = Path(era5_dir)
    e5, elev = era5_cell_series(lat, lon, idx, era5_dir)
    out = pd.DataFrame({VARIABLES[c]: e5[c].astype(float) for c in VARIABLES}, index=idx)
    z = np.load(era5_dir / "era5_box_z.npz")
    i, j = int(np.argmin(np.abs(z["lat"] - lat))), int(np.argmin(np.abs(z["lon"] - lon)))
    sp = []
    for y, m in sorted({(t.year, t.month) for t in idx}):
        f = era5_dir / f"era5_box_{y}{m:02d}.npz"
        if f.exists():
            d = np.load(f)
            sp.append(pd.Series(d["sp"][:, i, j].astype(float), index=pd.to_datetime(d["time_utc"], utc=True)))
    if sp:
        s = pd.concat(sp).sort_index()
        out["station_pressure_pa"] = s[~s.index.duplicated()].reindex(idx)
    else:
        out["station_pressure_pa"] = np.nan
    return out, elev


def source_label(elev: float) -> str:
    return f"era5_cell_{elev:.0f}m"
