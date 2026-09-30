"""SnowPro parsers: layer convention verified against a matching printout (BS 05 12 27)."""

from __future__ import annotations

from pathlib import Path

from snowagent.obs.snowpro import grain_1990, parse_snowpro

V21 = """[SNOWPROFILE: version]
2.1
[prf_desc,area_cd,prf_dt,prf_tm,tech_id,elevation,units,loc_lat,loc_lon,aspect,incline,prf_no,prf_typ,surf_rgh,foot_pen,ski_pen,wind_spd,wind_dir,sky_cond,ram_ttl,prcp_typ,prcp_rt,air_temp,height,lvl_ind]
"Test Site"
.
98-03-31
12:30
"XX"
2275
M
.
NA
0
98110
Full
a
20
.
2
5
4
.
0
0
5.0
40
0
[TEMPERATURES:no]
2
[temp_hgt,temp]
0,-0.1
40,-3.0
[SURFACE:grain_sf1,grain_sf2,grain_sfs,surf_water,surf_com,bottom_pit]
2
.
1.0
1,""
0
[LAYERS:no]
3
[layer_hgt,water_cnt,grain_fm1,grain_rm1,grain_fm2,grain_rm2,size,hand_hrd,density,dens_est,comments,position]
015, ,5 , ,  , ,3 - 4  ,2,320, ," rounded",1
016, ,9e, ,  , ,       ,4,   , ,"",1
040, ,2 , ,3 , ,0.5    ,1,200, ,"",1
[SHEARS:no]
1
[shr_hgt,shr_test,shr_rslt,comment]
016,O, ,"CTM on crust"
"""

V3 = """[File]
Release=3.0
[Id]
Location=Plot
Date=
Time=09:10
Altitude=2030
AltUnits=Metres
Slope=0
[Surface]
PackHeight=63
[CrystalLLayers]
BottomOfPit=0
Count=4
[CrystalLayerHeight]
1=-1
2=11
3=53
4=63
[CrystalLayerWaterContent]
1=Dry
2=Dry
3=Dry
4=Dry
[CrystalLayerGrainForm1]
1=1f
2=4
3=2
4=1
[CrystalLayerGrainForm2]
2=5
[CrystalLayerGrainDiameter1]
2=2-3
[CrystalLayerHandHardness]
1=N/A
2=1F+
3=4F
4=4F-
[CrystalLayerDensity]
2=280
3=0
4=100
"""


def test_v21_layers_bottom_up_top_heights(tmp_path):
    p = tmp_path / "TT980331.PRO"
    p.write_text(V21)
    o = parse_snowpro(p, "Etc/GMT+7")
    assert o["obs_time_utc"] == "1998-03-31T19:30:00+00:00" and o["hs_cm"] == 40
    got = [(ly["top_cm"], ly["bottom_cm"], ly["grain_form"], ly["hardness"]) for ly in o["layers"]]
    assert got == [(40, 16, "DF", "F"), (16, 15, "MFcr", "P"), (15, 0, "DH", "4F")]
    assert o["layers"][0]["grain_form_2"] == "RG" and o["provenance"]["confidence"] == "exact"
    assert o["tests"][0]["height_cm"] == 16 and o["temperatures"][-1] == {"height_cm": 40, "t_c": -3.0}


def test_v3_surface_entry_dropped_and_missing_date_flagged(tmp_path):
    p = tmp_path / "BS 05 12 27.PRO"
    p.write_text(V3)
    o = parse_snowpro(p, "Etc/GMT+7")
    got = [(ly["top_cm"], ly["bottom_cm"], ly["grain_form"], ly["hardness"], ly["density_kg_m3"]) for ly in o["layers"]]
    assert got == [(63, 53, "PP", "4F-", 100), (53, 11, "DF", "4F", None), (11, 0, "FC", "1F+", 280)]
    assert o["obs_time_utc"] is None and "no_observation_date" in o["flags"]


def test_1990_code_mapping():
    assert grain_1990("4c") == ("FCxr", None) and grain_1990("9e") == ("MFcr", None)
    assert grain_1990("7") == ("SH", None) and grain_1990("PPgp") == ("PPgp", None)
    code, note = grain_1990("9")
    assert code is None and "crust_class_unspecified" in note


def test_real_archive_parses_if_present():
    root = Path(__file__).resolve().parents[2] / "profiles"
    files = [f for f in root.rglob("*") if f.suffix.lower() in (".pro", ".prx")] if root.exists() else []
    if not files:
        import pytest

        pytest.skip("no SnowPro files in checkout")
    for f in files[:60]:
        o = parse_snowpro(f, "Etc/GMT+7")
        for a, b in zip(o["layers"], o["layers"][1:], strict=False):
            assert a["bottom_cm"] == b["top_cm"] and a["top_cm"] > a["bottom_cm"]


def test_locale_dependent_dates_resolved_only_by_independent_hint():
    from snowagent.obs.snowpro import _date_numeric

    assert _date_numeric("23/03/2000", None) == ("2000-03-23", [])  # only one valid order
    assert _date_numeric("12-Dec-06", None)[0] is None  # month names are parsed elsewhere, never here
    d, f = _date_numeric("11/09/2004", None)
    assert d is None and f[0].startswith("date_ambiguous:11/09/2004")
    assert _date_numeric("11/09/2004", "2004-11-09") == ("2004-11-09", ["date_order_resolved_by_filename:11/09/2004"])
    assert _date_numeric("04/03/15", "2004-03-15")[0] == "2004-03-15"  # YY/MM/DD reading
    d, f = _date_numeric("3/8/01", "2001-03-07")
    assert d == "2001-03-08" and f[0].startswith("date_order_resolved_by_nearest_filename_date")
    assert _date_numeric("3/8/01", "2001-05-01")[0] is None  # no reading near the hint


def test_v3_month_name_date(tmp_path):
    f = tmp_path / "x.pro"
    f.write_text("[Id]\nDate=12-Dec-06\nTime=1230\n[Surface]\nPackHeight=50\n[CrystalLLayers]\nCount=0\n")
    r = parse_snowpro(f, "Etc/GMT+7")
    assert r["obs_time_utc"] == "2006-12-12T19:30:00+00:00"
