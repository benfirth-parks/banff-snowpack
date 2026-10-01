"""External-data ingest: ISD parsing (QC codes, sentinels), DEM tile names, GFS interpolation weights."""

from __future__ import annotations

import pytest

from snowagent.ingest.copernicus_dem import tile_name
from snowagent.ingest.gfs_archive import _weights
from snowagent.ingest.noaa_isd import parse

ISD = '''"STATION","DATE","REPORT_TYPE","TMP","DEW","WND","MA1","AA1"
"71122099999","2020-01-01T00:00:00","FM-12","-0041,1","-0060,1","230,1,N,0036,1","99999,9,08440,1","06,0012,9,1"
"71122099999","2020-01-01T00:00:00","SAO  ","-0041,1","-0060,1","230,1,9,0036,1",,
"71122099999","2020-01-01T01:00:00","SAO  ","+9999,9","-0061,1","999,9,9,9999,9",,
"71122099999","2020-01-01T02:00:00","SAO  ","-0050,3","-0070,1","250,1,9,0020,2",,
'''


def test_isd_parse_units_qc_and_sentinels(tmp_path):
    f = tmp_path / "x.csv"
    f.write_text(ISD)
    d = parse([f]).set_index("time_utc")
    assert len(d) == 3  # the FM-12 synoptic report is merged into the hourly row
    r0 = d.iloc[0]
    assert r0["ta_k"] == pytest.approx(269.05) and r0["vw_ms"] == pytest.approx(3.6) and r0["p_pa"] == 84400
    assert r0["psum_mm"] == pytest.approx(1.2) and r0["psum_period_h"] == 6
    assert 0.8 < r0["rh_frac"] < 0.9
    r1 = d.iloc[1]
    assert r1["ta_qc"] == "missing" and r1["vw_ms"] != r1["vw_ms"]  # NaN, never filled
    assert d.iloc[2]["ta_qc"] == "bad"


def test_dem_tile_name():
    assert tile_name(51, -116) == "Copernicus_DSM_COG_10_N51_00_W116_00_DEM"


def test_gfs_bilinear_weights():
    idx, w = _weights(51.1, -115.8)
    assert sum(w) == pytest.approx(1.0) and len(idx) == 4 and all(0 <= x <= 1 for x in w)
    assert idx[0] == (int((90 - 51.1) // 0.25), int((360 - 115.8) // 0.25))


def test_fts360_month_windows_cover_range_without_gaps():
    from snowagent.ingest.fts360 import month_windows

    w = month_windows("2020-01-15", "2020-03-10")
    assert [(str(a.date()), str(b.date())) for a, b in w] == [
        ("2020-01-15", "2020-02-01"), ("2020-02-01", "2020-03-01"), ("2020-03-01", "2020-03-10")]


def test_fts360_parse_units_flags_and_gauge_increments(tmp_path):
    from snowagent.ingest.fts360 import parse_station

    f = tmp_path / "s_2024-01.csv"
    f.write_text("Station name,Station ID,Date,Temp,Rh,Wspd,HS,PC\n"
                 "X,1,2024-01-01T00:00:00Z,-10.0,80,36,100.0,400.0\n"
                 "X,1,2024-01-01T00:15:00Z,-10.0,80,36,100.0,400.5\n"
                 "X,1,2024-01-01T01:00:00Z,-11.0,85,18,//////,401.0\n"
                 "X,1,2024-01-01T02:00:00Z,-12.0,90,0,-50.0,300.0\n")
    d = parse_station([f]).set_index("time_utc")
    assert len(d) == 3  # 15-min rows dropped, top of hour kept
    assert d["ta_k"].iloc[0] == 263.15 and d["rh_frac"].iloc[0] == 0.8 and d["vw_ms"].iloc[0] == 10.0
    assert d["hs_m_qc"].tolist() == ["ok", "missing", "bad"]
    assert d["psum_1h_mm"].iloc[1] == 1.0 and d["psum_1h_mm_qc"].iloc[2] == "bad"  # gauge reset flagged


def test_casr_nearest_cell_handles_0_360_longitudes():
    import numpy as np

    from snowagent.ingest.casr import nearest_cell

    lat = np.array([[51.0, 51.0], [51.1, 51.1]])
    lon = np.array([[244.0, 244.2], [244.0, 244.2]])  # = -116.0, -115.8
    i, j, d = nearest_cell(lat, lon, 51.09, -115.79)
    assert (i, j) == (1, 1) and d < 2.0
