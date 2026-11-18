"""Archived HRDPS point extracts -> hourly forcing (SI), and the 72 h HRDPS + GFS forecast (ADR-115).

HRDPS files are hourly; precipitation and radiation are accumulated from the run start, so each hour's amount is
the difference of consecutive leads (negative differences from packing noise are clipped to 0). Instantaneous
fields (2 m temperature/humidity, 10 m wind speed and direction) are taken as they are. Columns match
``forecast.gfs_point.gfs_hourly`` (ta K, rh 0-1, vw, dw, iswr, ilwr W m-2, psum mm h-1) so every reader of GFS
can take HRDPS unchanged. Values are at the HRDPS surface height (``model_elev_m``).

HRDPS reaches 48 h; ``blend_hourly`` continues it with the GFS run of the same issue time for later hours, with
GFS temperature moved to the HRDPS surface height (same fallback lapse rate as the forcing builder) so the
blended series has one reference height.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

COLS = ["ta", "rh", "vw", "dw", "iswr", "ilwr", "psum"]
LAPSE_K_PER_M = -0.0065  # spatial_forcing.builder.ForcingConfig default


def hrdps_hourly(df: pd.DataFrame, point: str) -> tuple[pd.DataFrame, float]:
    """Hourly forcing over leads 1..max and the HRDPS surface height of ``point``."""
    sub = df[df["point"] == point].sort_values("lead_h")
    if sub.empty:
        raise KeyError(point)
    run = pd.Timestamp(sub["run_utc"].iloc[0])
    idx = pd.DatetimeIndex(run + pd.to_timedelta(sub["lead_h"].to_numpy(), unit="h"))
    leads = sub["lead_h"].to_numpy()
    if not (np.diff(leads) == 1).all():
        full = np.arange(leads.min(), leads.max() + 1)
        sub = sub.set_index("lead_h").reindex(full).reset_index().rename(columns={"index": "lead_h"})
        idx = pd.DatetimeIndex(run + pd.to_timedelta(full, unit="h"))
    out = pd.DataFrame(index=idx)
    out["ta"] = sub["tmp2m_k"].to_numpy(dtype=float)
    out["rh"] = (sub["rh2m_pct"].to_numpy(dtype=float) / 100.0).clip(0.05, 1.0)
    out["vw"] = sub["wind10m_ms"].to_numpy(dtype=float)
    out["dw"] = sub["wdir10m_deg"].to_numpy(dtype=float)
    for col, name, scale in (("apcp_kgm2", "psum", 1.0), ("dswrf_jm2", "iswr", 1 / 3600.0),
                             ("dlwrf_jm2", "ilwr", 1 / 3600.0)):
        acc = pd.Series(sub[col].to_numpy(dtype=float), index=idx)
        out[name] = (acc.diff() * scale).clip(lower=0.0)
    elev = float(pd.to_numeric(sub["model_elev_m"], errors="coerce").dropna().iloc[0])
    return out.iloc[1:], elev  # lead 0 has no interval


def blend_hourly(hrdps: tuple[pd.DataFrame, float] | None, gfs: tuple[pd.DataFrame, float] | None
                 ) -> tuple[pd.DataFrame, float, dict]:
    """HRDPS hours first, then GFS for the hours after the last complete HRDPS hour; returns (hourly, reference
    height, info). Either part may be missing (``None``): GFS alone is the fallback, HRDPS alone ends at 48 h."""
    if hrdps is None and gfs is None:
        raise ValueError("no forecast run")
    if hrdps is None:
        g, ge = gfs
        return g, ge, {"source": "GFS", "hrdps_hours": 0, "gfs_hours": int(g.dropna().shape[0])}
    h, he = hrdps
    h = h.dropna()
    if gfs is None or h.empty:
        if h.empty and gfs is not None:
            g, ge = gfs
            return g, ge, {"source": "GFS", "hrdps_hours": 0, "gfs_hours": int(g.dropna().shape[0])}
        return h, he, {"source": "HRDPS", "hrdps_hours": len(h), "gfs_hours": 0}
    g, ge = gfs
    tail = g[g.index > h.index[-1]].dropna().copy()
    tail["ta"] = tail["ta"] + LAPSE_K_PER_M * (he - ge)
    out = pd.concat([h[COLS], tail[COLS]])
    return out, he, {"source": "HRDPS+GFS" if len(tail) else "HRDPS", "hrdps_hours": len(h),
                     "gfs_hours": len(tail), "gfs_surface_m": round(ge)}
