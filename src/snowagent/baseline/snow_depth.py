"""The plot's measured snow depth as a snowfall source for the site's runs (ADR-101, ADR-102): the readings (as the
lab picks them) and the plan of a driven run (HS forcing and the switch to precipitation). Outside ``ops/``: the
site build uses it and is part of the lab's code hash (ADR-080)."""

from __future__ import annotations

import pandas as pd


def observed_snow_depth(stations: list[str], load=None) -> pd.Series:
    """Hourly measured snow depth (m) of a plot: per hour the first of ``stations`` with a QC-ok reading, as the lab's
    weather ingest picks it (ADR-056); empty without readings."""
    if load is None:
        from snowagent.ingest.fts360 import load_station as load
    out = pd.Series(dtype=float)
    for st in stations:
        d = load(st)
        if d.empty or "hs_m" not in d:
            continue
        ok = d[d["hs_m_qc"] == "ok"] if "hs_m_qc" in d else d
        hs = pd.Series(ok["hs_m"].to_numpy(dtype=float), index=pd.DatetimeIndex(ok["time_utc"]).round("h"))
        if hs.index.tz is None:
            hs.index = hs.index.tz_localize("UTC")
        hs = hs[~hs.index.duplicated()].dropna()
        out = hs if out.empty else out.combine_first(hs)
    return out.sort_index()


def snow_depth_plan(obs: pd.Series, index: pd.DatetimeIndex, start: pd.Timestamp, end: pd.Timestamp
                    ) -> tuple[pd.Series | None, pd.Timestamp | None]:
    """HS forcing (m) on ``index`` and the switch to precipitation (ADR-101): 00 UTC of the last reading's day before
    ``end``, a day earlier when that leaves under 6 h (the last segment must write a profile). ``(None, None)`` when
    no switch fits inside the run or the readings cover too little of it (lab rule, ADR-100)."""
    from snowagent.lab.agents.snowpack import snow_depth_forcing

    obs = obs[obs.index <= end] if not obs.empty else obs
    if obs.empty:
        return None, None
    switch = obs.index.max().floor("D")
    if end - switch < pd.Timedelta(hours=6):
        switch -= pd.Timedelta(days=1)
    if not start < switch < end:
        return None, None
    hs = snow_depth_forcing(obs, index, start, switch)
    return (None, None) if hs is None else (hs, switch)
