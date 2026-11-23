"""Bald Hills test site (ADR-145): Maligne's logger change and the area lab's wiring."""

from pathlib import Path

import pandas as pd

from snowagent.ingest.fts360 import load_station
from snowagent.lab.settings import load_lab_config
from snowagent.lab.training.area_check import AREAS
from snowagent.newsites.bald_hills import convert, to_new_names

OLD = ("Station name,Station ID,Date,SD,Temp,Rh,Wspd,Precip,Mx_Spd,Dir\n"
       "Maligne,x,2023-01-01T00:00:00Z,49.9,-3.6,72.0,0.9,673.7,3.7,39\n"
       "Maligne,x,2023-01-01T01:00:00Z,50.1,-4.8,86.0,0.1,674.2,4.8,295\n")
NEW = ("Station name,Station ID,Date,SDcm,Temp,Rh,Wspd,PC,Mx_Spd,Dir\n"
       "Maligne,x,2025-01-01T00:00:00Z,36.5,-11.4,86,0.1,236.47,5.4,9\n"
       "Maligne,x,2025-01-01T01:00:00Z,36.6,-11.0,85,0.2,236.67,5.0,10\n")


def test_old_names_renamed_only_where_new_absent():
    d, renamed = to_new_names(pd.DataFrame({"SD": ["1"], "Precip": ["2"]}))
    assert list(d.columns) == ["SDcm", "PC"] and renamed == ["SD->SDcm", "Precip->PC"]
    d, renamed = to_new_names(pd.DataFrame({"SD": ["1"], "SDcm": ["3"]}))
    assert list(d.columns) == ["SDcm"] and d["SDcm"].tolist() == ["3"] and renamed == []


def test_both_loggers_keep_snow_depth_and_gauge(tmp_path: Path):
    raw = tmp_path / "raw" / "maligne"
    raw.mkdir(parents=True)
    (raw / "maligne_2023-01.csv").write_text(OLD)
    (raw / "maligne_2025-01.csv").write_text(NEW)
    # unconverted, the newer columns hide the older months' snow depth and gauge
    plain = load_station("maligne", tmp_path / "raw")
    assert plain.loc[plain.time_utc.dt.year == 2023, "hs_m"].isna().all()
    rep = convert(tmp_path / "raw", tmp_path / "out")
    assert rep == {"months": 2, "renamed_months": 1}
    d = load_station("maligne", tmp_path / "out")
    old = d[d.time_utc.dt.year == 2023]
    assert old["hs_m"].round(3).tolist() == [0.499, 0.501]
    assert old["psum_1h_mm"].round(2).tolist()[1] == 0.5
    assert d.loc[d.time_utc.dt.year == 2025, "hs_m"].round(3).tolist() == [0.365, 0.366]


def test_bald_labs_extend_banff_and_differ_only_in_precipitation():
    assert {"bald", "bald_era5p"} <= set(AREAS)
    a = load_lab_config(Path("config/lab_bald.yaml"))
    b = load_lab_config(Path("config/lab_bald_era5p.yaml"))
    banff = load_lab_config(Path("config/lab.yaml"))
    assert [s.value for s in a.sites] == ["BALD"] and a.scoring_weights == banff.scoring_weights
    assert a.weather.era5_dir == "data/interim/era5_bald_hills"
    pa, pb = (__import__("yaml").safe_load(Path(c.plot_forcing_config).read_text()) for c in (a, b))
    assert pa["plots"]["bald_hills"]["psum"] == ["maligne"] and pb["plots"]["bald_hills"]["psum"] == []
    assert {k: v for k, v in pa["plots"]["bald_hills"].items() if k != "psum"} == \
        {k: v for k, v in pb["plots"]["bald_hills"].items() if k != "psum"}
