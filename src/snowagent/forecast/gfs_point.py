"""Archived GFS point extracts -> hourly forcing (SI) for one forecast run.

GFS writes precipitation as accumulations and radiation as averages over windows that reset every 6 h
("0-3 hour acc", "0-6 hour acc", "6-9 hour ave", ...). Every window is turned into an amount
(accumulation, or average x duration) and each 3-h interval [L-3, L] is recovered by differencing two windows
that share a start. Interval amounts are spread evenly over their hours (precipitation, radiation means);
instantaneous fields (2 m temperature/humidity, 10 m wind) are linearly interpolated. Values are at the GFS
grid surface height (``model_elev_m``); elevation transfer is done downstream by the forcing builder.
"""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

WINDOW = re.compile(r"(\d+)-(\d+) hour (acc|ave)")


def _intervals(sub: pd.DataFrame, col: str) -> pd.Series:
    """Amount per 3-h interval ending at each lead (index = lead hour).

    Builds the running total C(L) from the forecast start: a window (a, b) with known C(a) gives
    C(b) = C(a) + amount, so any mix of "0-N" and "6k-N" windows resolves.
    """
    windows: dict[int, list[tuple[int, float]]] = {}
    for _, r in sub.iterrows():
        m = WINDOW.search(str(r.get(f"{col}_desc", "")))
        if not m or pd.isna(r[col]):
            continue
        a, b, kind = int(m.group(1)), int(m.group(2)), m.group(3)
        windows.setdefault(b, []).append((a, float(r[col]) * ((b - a) if kind == "ave" else 1.0)))
    cum: dict[int, float] = {0: 0.0}
    for lead in sorted(windows):
        for a, amount in windows[lead]:
            if a in cum:
                cum[lead] = cum[a] + amount
                break
    out = {}
    for lead in sorted(sub["lead_h"]):
        if lead == 0:
            continue
        out[lead] = cum[lead] - cum[lead - 3] if lead in cum and (lead - 3) in cum else np.nan
    return pd.Series(out, dtype=float)


def gfs_hourly(df: pd.DataFrame, point: str) -> tuple[pd.DataFrame, float]:
    """Hourly forcing (ta K, rh 0-1, vw, dw, iswr, ilwr W m-2, psum mm h-1) and the grid surface height."""
    sub = df[df["point"] == point].sort_values("lead_h")
    run = pd.Timestamp(sub["run_utc"].iloc[0])
    t = run + pd.to_timedelta(sub["lead_h"], unit="h")
    idx = pd.date_range(t.iloc[0], t.iloc[-1], freq="h")
    inst = pd.DataFrame({"ta": sub["tmp2m_k"].to_numpy(), "rh": sub["rh2m_pct"].to_numpy() / 100.0,
                         "u": sub["u10_ms"].to_numpy(), "v": sub["v10_ms"].to_numpy()}, index=t.to_numpy())
    inst.index = pd.DatetimeIndex(inst.index)
    inst = inst.reindex(idx).interpolate(limit_area="inside")
    out = pd.DataFrame(index=idx)
    out["ta"] = inst["ta"]
    out["rh"] = inst["rh"].clip(0.05, 1.0)
    out["vw"] = np.hypot(inst["u"], inst["v"])
    out["dw"] = (np.degrees(np.arctan2(-inst["u"], -inst["v"])) + 360) % 360
    for col, name, per_hour in (("apcp_kgm2", "psum", True), ("dswrf_wm2", "iswr", False),
                                ("dlwrf_wm2", "ilwr", False)):
        iv = _intervals(sub, col)
        s = pd.Series(np.nan, index=idx)
        for lead, amount in iv.items():
            if amount is None or np.isnan(amount):
                continue
            hours = pd.date_range(run + pd.Timedelta(hours=lead - 2), run + pd.Timedelta(hours=lead), freq="h")
            s.loc[s.index.isin(hours)] = max(amount, 0.0) / 3.0 if per_hour else max(amount, 0.0) / 3.0
        out[name] = s
    elev = float(sub["model_elev_m"].iloc[0])
    return out.iloc[1:], elev  # first hour (analysis time) has no flux interval
