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


def test_byk_csv_export_shifts_mst_to_utc_and_masks_sentinels():
    from snowagent.ingest.byk_export import read_csv_export

    raw = (b"StationName,DateTime,Mx_Dir,Mx_Spd,Temp,Wspd,H2O_Eq_1hr_mm,HS,Dir\n"
           b"Avi - BYK Bow Summit,2016/01/01 05:00:00,6999,6999,-2.9,6999,6999,52,-999\n")
    d = read_csv_export(raw)
    assert str(d["time_utc"].iloc[0]) == "2016-01-01 12:00:00+00:00"
    assert d["Temp"].iloc[0] == -2.9 and d["HS"].iloc[0] == 52
    assert d[["Wspd", "Dir", "H2O_Eq_1hr_mm"]].isna().all(axis=None)


def test_byk_xml_export_reads_access_records():
    from snowagent.ingest.byk_export import read_xml_export

    raw = (b'<?xml version="1.0" encoding="UTF-8"?><dataroot generated="2019-06-13T08:33:11">'
           b"<Avi__BYK_Bow_Summit_Precip_Gauge><DateTimeNum>2016-03-22T14:00:00</DateTimeNum><PC>517.8</PC>"
           b"<TA>2.4</TA><VB>14.1</VB></Avi__BYK_Bow_Summit_Precip_Gauge>"
           b"<Avi__BYK_Bow_Summit_Precip_Gauge><DateTimeNum>2016-03-22T15:00:00</DateTimeNum><PC>518.3</PC>"
           b"</Avi__BYK_Bow_Summit_Precip_Gauge></dataroot>")
    d = read_xml_export(raw)
    assert d["logger"].unique().tolist() == ["Avi__BYK_Bow_Summit_Precip_Gauge"]
    assert str(d["time_utc"].iloc[1]) == "2016-03-22 22:00:00+00:00"
    assert d["PC"].tolist() == [517.8, 518.3] and "VB" not in d


def test_byk_convert_merges_files_and_load_station_combines_archives(tmp_path):
    from snowagent.ingest.byk_export import convert
    from snowagent.ingest.fts360 import load_station

    raw, interim, fts = tmp_path / "raw", tmp_path / "interim", tmp_path / "fts"
    raw.mkdir()
    (raw / "all.csv").write_text("StationName,DateTime,Temp,H2O_Eq_1hr_mm,HS\n"
                                 "Avi - BYK Simpson Lower,2016/01/01 05:00:00,-3.0,6999,100\n"
                                 "Avi - BYK Simpson Lower,2016/01/01 06:00:00,-4.0,6999,101\n")
    (raw / "simpson_lower.csv").write_text("StationName,DateTime,Temp,Rh,HS\n"
                                           "Avi - BYK Simpson Lower,2016/01/01 06:00:00,-4.0,80,101\n"
                                           "Avi - BYK Simpson Lower,2016/01/01 07:00:00,-5.0,85,101\n")
    s = convert(raw, interim)
    assert s["simpson_lower"]["rows"] == 3 and s["simpson_lower"]["overlap_disagreements"] == 0
    (fts / "simpson_lower").mkdir(parents=True)
    (fts / "simpson_lower" / "simpson_lower_2021-06.csv").write_text(
        "Station name,Station ID,Date,Temp,Rh,HS\nX,1,2021-06-01T00:00:00Z,5.0,50,0.0\n")
    d = load_station("simpson_lower", fts, interim).set_index("time_utc")
    assert len(d) == 4 and str(d.index[0]) == "2016-01-01 12:00:00+00:00"
    assert d["rh_frac"].isna().tolist() == [True, False, False, False]  # humidity only in the full table
    assert (d["ta_k_qc"] == "ok").all()


def test_parse_frame_uses_hourly_gauge_increment_without_cumulative_pc():
    import pandas as pd

    from snowagent.ingest.fts360 import parse_frame

    d = parse_frame(pd.DataFrame({"Date": ["2016-01-01T00:00:00Z", "2016-01-01T01:00:00Z", "2016-01-01T02:00:00Z"],
                                  "H2O_Eq_1hr_mm": [0.5, -574.4, None]})).set_index("time_utc")
    assert d["psum_1h_mm"].iloc[0] == 0.5
    assert d["psum_1h_mm_qc"].tolist() == ["ok", "bad", "missing"]
