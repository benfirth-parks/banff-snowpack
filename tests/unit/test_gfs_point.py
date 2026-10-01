"""GFS window de-accumulation keeps totals and averages."""

from __future__ import annotations

import pandas as pd
import pytest

from snowagent.forecast.gfs_point import gfs_hourly


def test_deaccumulation_and_averages():
    rows = []
    acc = {3: ("0-3 hour acc fcst", 1.0), 6: ("0-6 hour acc fcst", 3.0), 9: ("6-9 hour acc fcst", 0.5),
           12: ("6-12 hour acc fcst", 0.5)}
    ave = {3: ("0-3 hour ave fcst", 30.0), 6: ("0-6 hour ave fcst", 60.0), 9: ("6-9 hour ave fcst", 90.0),
           12: ("6-12 hour ave fcst", 75.0)}
    for lead in (0, 3, 6, 9, 12):
        r = {"run_utc": "2024-01-01T00:00:00+00:00", "lead_h": lead, "point": "p", "model_elev_m": 2000.0,
             "tmp2m_k": 260.0 + lead, "rh2m_pct": 80.0, "u10_ms": 1.0, "v10_ms": 0.0}
        if lead:
            r |= {"apcp_kgm2": acc[lead][1], "apcp_kgm2_desc": acc[lead][0],
                  "dswrf_wm2": ave[lead][1], "dswrf_wm2_desc": ave[lead][0],
                  "dlwrf_wm2": 200.0, "dlwrf_wm2_desc": ave[lead][0]}
        rows.append(r)
    h, elev = gfs_hourly(pd.DataFrame(rows), "p")
    assert elev == 2000.0 and len(h) == 12
    assert h["psum"].sum() == pytest.approx(3.5)  # 1 + 2 + 0.5 + 0
    assert h["psum"].iloc[3:6].sum() == pytest.approx(2.0)
    # 0-6 ave 60 with 0-3 ave 30 -> 3-6 interval mean 90; 6-12 ave 75 with 6-9 ave 90 -> 9-12 mean 60
    assert h["iswr"].iloc[3:6].mean() == pytest.approx(90.0) and h["iswr"].iloc[9:12].mean() == pytest.approx(60.0)
    assert h["ta"].iloc[0] == pytest.approx(261.0)
