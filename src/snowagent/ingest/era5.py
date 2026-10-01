"""ERA5 hourly reanalysis for a small box over the study plots (user approval 2026-10-01).

Source: NSF NCAR mirror of ERA5 on AWS Open Data (s3://nsf-ncar-era5, netCDF4/HDF5). Read remotely with
HTTP range requests; only the box is kept. Two file families:
- analysis (e5.oper.an.sfc, monthly files, chunked in space): 2t, 2d, 10u, 10v, sp, tcc (cheap);
- forecast mean fluxes (e5.oper.fc.sfc.meanflux, half-month files, one global chunk per forecast):
  mtpr (precipitation rate), msdwswrf / msdwlwrf (downward short/longwave). Each value is the mean over the
  hour ENDING at valid time = initial time + forecast hour.
Grid cells are kept as-is (0.25 deg, ~28 x 18 km) with their surface geopotential height; downscaling to the
plots happens downstream. Units: K, m s-1, Pa, fraction, kg m-2 s-1, W m-2; times UTC.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

BASE = "https://nsf-ncar-era5.s3.amazonaws.com"
AN = {"2t": "128_167_2t", "2d": "128_168_2d", "10u": "128_165_10u", "10v": "128_166_10v", "sp": "128_134_sp",
      "tcc": "128_164_tcc"}
MF = {"mtpr": "235_055_mtpr", "msdwswrf": "235_035_msdwswrf", "msdwlwrf": "235_036_msdwlwrf"}
BOX = (50.5, 52.0, -117.0, -115.25)  # lat_min, lat_max, lon_min, lon_max


def _open(url: str):
    import fsspec
    import h5py

    return h5py.File(fsspec.open(url, block_size=2**22).open(), "r")


def _box_index(h, box=BOX):
    lat, lon = h["latitude"][:], h["longitude"][:]
    lon_w = np.where(lon > 180, lon - 360, lon)
    ii = np.where((lat >= box[0]) & (lat <= box[1]))[0]
    jj = np.where((lon_w >= box[2]) & (lon_w <= box[3]))[0]
    return slice(ii[0], ii[-1] + 1), slice(jj[0], jj[-1] + 1), lat[ii], lon_w[jj]


def _hours(h, name):
    return pd.to_datetime(h[name][:], unit="h", origin="1900-01-01", utc=True)


def read_an(var: str, year: int, month: int) -> tuple[pd.DatetimeIndex, np.ndarray, np.ndarray, np.ndarray]:
    end = (pd.Timestamp(year, month, 1) + pd.offsets.MonthEnd(0)).strftime("%Y%m%d")
    url = (f"{BASE}/e5.oper.an.sfc/{year}{month:02d}/e5.oper.an.sfc.{AN[var]}.ll025sc."
           f"{year}{month:02d}0100_{end}23.nc")
    with _open(url) as h:
        si, sj, la, lo = _box_index(h)
        key = [k for k in h.keys() if k.startswith("VAR_") or k.upper() == var.upper()][0]
        return _hours(h, "time"), h[key][:, si, sj], la, lo


def read_mf(var: str, year: int, month: int) -> tuple[pd.DatetimeIndex, np.ndarray, np.ndarray, np.ndarray]:
    """Both half-month files that hold valid times inside the month."""
    import re

    import requests

    def keys_for(y, m):
        xml = requests.get(f"{BASE}/?list-type=2&prefix=e5.oper.fc.sfc.meanflux/{y}{m:02d}/", timeout=60).text
        return sorted({k for k in re.findall(r"<Key>([^<]+)</Key>", xml) if MF[var] in k})

    prev = pd.Timestamp(year, month, 1) - pd.offsets.MonthBegin(1)
    keys = keys_for(prev.year, prev.month)[-1:] + keys_for(year, month)  # first hours come from last month's run
    times, vals = [], []
    for k in keys:
        with _open(f"{BASE}/{k}") as h:
            si, sj, la, lo = _box_index(h)
            key = [x for x in h.keys() if x not in ("latitude", "longitude", "forecast_hour",
                                                    "forecast_initial_time", "utc_date")][0]
            init = _hours(h, "forecast_initial_time")
            fh = h["forecast_hour"][:]
            arr = h[key][:, :, si, sj]  # (init, hour, lat, lon)
        for a, t0 in enumerate(init):
            for b, f in enumerate(fh):
                times.append(t0 + pd.Timedelta(hours=int(f)))
                vals.append(arr[a, b])
    t = pd.DatetimeIndex(times)
    keep = (t.year == year) & (t.month == month)
    order = np.argsort(t[keep])
    return t[keep][order], np.stack(vals)[keep][order], la, lo


def extract_month(year: int, month: int, out_dir: Path, fluxes: bool = True) -> Path:
    """Write one month of box data to <out_dir>/era5_box_YYYYMM.npz with provenance."""
    out_dir.mkdir(parents=True, exist_ok=True)
    dest = out_dir / f"era5_box_{year}{month:02d}.npz"
    data, times, la, lo = {}, None, None, None
    for v in AN:
        t, a, la, lo = read_an(v, year, month)
        times = t if times is None else times
        data[v] = a.astype("float32")
    if fluxes:
        for v in MF:
            t, a, _, _ = read_mf(v, year, month)
            s = pd.Series(range(len(t)), index=t)
            s = s[~s.index.duplicated()].reindex(times)
            arr = np.full((len(times),) + a.shape[1:], np.nan, dtype="float32")
            ok = s.notna().to_numpy()
            arr[ok] = a[s[ok].astype(int).to_numpy()]
            data[v] = arr
    np.savez_compressed(dest, time_utc=times.astype("int64").to_numpy(), lat=la, lon=lo, **data)
    dest.with_suffix(".json").write_text(json.dumps({
        "source": BASE, "retrieved_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "variables": list(data), "box": BOX, "note": "flux values are means over the hour ending at time_utc"}))
    return dest
