"""CaSR v3.2 (Canadian Surface Reanalysis, ECCC, ~10 km, hourly) for the study plots (README §6).

Source: tiled NetCDF archive https://hpfx.collab.science.gc.ca/~scar700/rcas-casr/data/CaSRv3.2/netcdf_tile/,
one file per variable and 4-year period (1968-2024). All three plots lie in tile rlon211-245_rlat421-455. Files
are chunked one full tile per hour, so partial (range) reads save nothing: whole files are downloaded unchanged
to data/raw/casr with a manifest, and the nearest-cell series per plot are extracted from them.

Variables (CaSR naming): A_PR0_SFC = CaPA precipitation analysis disaggregated to hours (m); P_* are 6-18 h
lead RDRS model fields: TT_1.5m and TD_1.5m (deg C), UVC_10m (wind speed, kt), WDC_10m (direction),
FB_SFC (downward solar, W m-2), FI_SFC (incoming longwave, W m-2), GZ_SFC (surface geopotential height, dam).
Units are converted to SI at extraction (CLAUDE.md: SI internally) and recorded in the sidecar.
"""

from __future__ import annotations

import json
from pathlib import Path

import h5py
import numpy as np
import pandas as pd

from snowagent.ingest.fetch import fetch

BASE = "https://hpfx.collab.science.gc.ca/~scar700/rcas-casr/data/CaSRv3.2/netcdf_tile/"
TILE = "rlon211-245_rlat421-455"
VARS = ["A_PR0_SFC", "P_TT_1.5m", "P_TD_1.5m", "P_UVC_10m", "P_WDC_10m", "P_FB_SFC", "P_FI_SFC"]
PERIODS = ["1996-1999", "2000-2003", "2004-2007", "2008-2011", "2012-2015", "2016-2019", "2020-2023", "2024-2024"]
KT_TO_MS = 0.514444


def file_name(var: str, period: str, tile: str = TILE) -> str:
    return f"CaSR_v3.2_{var}_{tile}_{period}.nc"


def download(raw_dir: Path, variables: list[str] = VARS, periods: list[str] = PERIODS) -> list[dict]:
    jobs = [(v, p) for v in variables for p in periods] + [("P_GZ_SFC", "2020-2023")]
    out = []
    for v, p in jobs:
        name = file_name(v, p)
        out.append(fetch(f"{BASE}{TILE}/{name}", raw_dir / name, raw_dir / "manifest.jsonl", timeout=600))
    return out


def nearest_cell(lat2d: np.ndarray, lon2d: np.ndarray, lat: float, lon: float) -> tuple[int, int, float]:
    lon2d = ((np.asarray(lon2d) + 180.0) % 360.0) - 180.0  # CaSR stores 0-360 degrees east
    d = np.hypot((lat2d - lat) * 111.2, (lon2d - lon) * 111.2 * np.cos(np.radians(lat)))
    i, j = np.unravel_index(int(np.argmin(d)), d.shape)
    return int(i), int(j), float(d[i, j])


def _read(path: Path, var: str, i: int, j: int) -> pd.Series:
    with h5py.File(path, "r") as h:
        units = h["time"].attrs["units"].decode()
        t0 = pd.Timestamp(units.split("since ")[1], tz="UTC")
        t = t0 + pd.to_timedelta(h["time"][:], unit="h")
        v = h[f"CaSR_v3.2_{var}"][:, i, j].astype(float)
    return pd.Series(v, index=pd.DatetimeIndex(t))


def extract_point(raw_dir: Path, lat: float, lon: float, periods: list[str] = PERIODS) -> tuple[pd.DataFrame, dict]:
    """Hourly SI series (ta, rh, vw, dw, iswr, ilwr, psum) at the nearest CaSR cell, and cell metadata."""
    with h5py.File(raw_dir / file_name("P_GZ_SFC", "2020-2023"), "r") as h:
        i, j, dist = nearest_cell(h["lat"][:], h["lon"][:], lat, lon)
        cell_lat, cell_lon = float(h["lat"][i, j]), float(((h["lon"][i, j] + 180.0) % 360.0) - 180.0)
        gz = h["CaSR_v3.2_P_GZ_SFC"]
        elev = float(np.nanmean(gz[: min(24, gz.shape[0]), i, j])) * 10.0  # dam -> m
    cols = {}
    for v in VARS:
        parts = [_read(raw_dir / file_name(v, p), v, i, j) for p in periods if (raw_dir / file_name(v, p)).exists()]
        if parts:
            s = pd.concat(parts)
            cols[v] = s[~s.index.duplicated()].sort_index()
    df = pd.DataFrame(cols)
    tc, tdc = df["P_TT_1.5m"], df["P_TD_1.5m"]
    es = lambda c: 6.112 * np.exp(17.62 * c / (243.12 + c))  # noqa: E731 - Magnus (WMO), hPa
    out = pd.DataFrame({
        "ta": tc + 273.15,
        "rh": np.clip(es(np.minimum(tdc, tc)) / es(tc), 0.05, 1.0),
        "vw": df["P_UVC_10m"] * KT_TO_MS,
        "dw": df["P_WDC_10m"] % 360,
        "iswr": df["P_FB_SFC"].clip(lower=0),
        "ilwr": df["P_FI_SFC"],
        "psum": (df["A_PR0_SFC"] * 1000.0).clip(lower=0),  # m -> mm per hour
    }, index=df.index)
    meta = {"cell_ij": [i, j], "cell_lat": cell_lat, "cell_lon": cell_lon, "distance_km": round(dist, 2),
            "cell_elevation_m": round(elev, 1), "source": BASE + TILE,
            "units_in": {"TT/TD": "degC", "UVC": "kt", "PR0": "m per hour", "FB/FI": "W m-2", "GZ": "dam"},
            "time": "validity time, UTC; values are hourly"}
    return out, meta


def write_point(df: pd.DataFrame, meta: dict, out_csv: Path) -> None:
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_csv, index_label="time_utc", float_format="%.4f")
    out_csv.with_suffix(".json").write_text(json.dumps(meta, indent=1))
