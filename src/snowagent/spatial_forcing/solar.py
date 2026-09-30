"""Solar geometry and shortwave decomposition.

- Sun position: NOAA General Solar Position approximation (Spencer 1971 series for
  declination and equation of time). Accuracy ~0.1-0.5 deg, adequate for hourly forcing.
- Direct/diffuse split: Erbs, Klein & Duffie (1982) diffuse-fraction correlation on
  the hourly clearness index. This is an ESTIMATE used because the forcing supplies
  only global horizontal shortwave; it is recorded as an assumption on every run.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

SOLAR_CONSTANT = 1361.0


def sun_position(times: pd.DatetimeIndex, lat_deg: float, lon_deg: float) -> tuple[np.ndarray, np.ndarray]:
    """Return (zenith_deg, azimuth_deg clockwise from north) for UTC times."""
    t = times.tz_convert("UTC")
    doy = t.dayofyear.to_numpy()
    hour = (t.hour + t.minute / 60.0 + t.second / 3600.0).to_numpy()
    g = 2 * np.pi / 365.0 * (doy - 1 + (hour - 12) / 24.0)
    eqtime = 229.18 * (0.000075 + 0.001868 * np.cos(g) - 0.032077 * np.sin(g)
                       - 0.014615 * np.cos(2 * g) - 0.040849 * np.sin(2 * g))
    decl = (0.006918 - 0.399912 * np.cos(g) + 0.070257 * np.sin(g) - 0.006758 * np.cos(2 * g)
            + 0.000907 * np.sin(2 * g) - 0.002697 * np.cos(3 * g) + 0.00148 * np.sin(3 * g))
    tst = hour * 60.0 + eqtime + 4.0 * lon_deg  # true solar time, minutes
    ha = np.radians(tst / 4.0 - 180.0)
    lat = np.radians(lat_deg)
    cosz = np.sin(lat) * np.sin(decl) + np.cos(lat) * np.cos(decl) * np.cos(ha)
    cosz = np.clip(cosz, -1, 1)
    zen = np.arccos(cosz)
    # azimuth clockwise from north
    az = np.degrees(np.arctan2(np.sin(ha), np.cos(ha) * np.sin(lat) - np.tan(decl) * np.cos(lat))) + 180.0
    return np.degrees(zen), az % 360.0


def eccentricity(times: pd.DatetimeIndex) -> np.ndarray:
    doy = times.dayofyear.to_numpy()
    g = 2 * np.pi * (doy - 1) / 365.0
    return (1.000110 + 0.034221 * np.cos(g) + 0.001280 * np.sin(g)
            + 0.000719 * np.cos(2 * g) + 0.000077 * np.sin(2 * g))


def erbs_diffuse_fraction(kt: np.ndarray) -> np.ndarray:
    kt = np.clip(kt, 0.0, 1.0)
    kd = np.where(kt <= 0.22, 1.0 - 0.09 * kt,
                  np.where(kt <= 0.80, 0.9511 - 0.1604 * kt + 4.388 * kt**2 - 16.638 * kt**3 + 12.336 * kt**4,
                           0.165))
    return np.clip(kd, 0.0, 1.0)


def cos_incidence(zen_deg: np.ndarray, az_deg: np.ndarray, slope_deg: float, aspect_deg: float) -> np.ndarray:
    z, a = np.radians(zen_deg), np.radians(az_deg)
    s, asp = np.radians(slope_deg), np.radians(aspect_deg)
    return np.cos(z) * np.cos(s) + np.sin(z) * np.sin(s) * np.cos(a - asp)
