"""Jasper test set (ADR-117): its own lab config that extends Banff's, Parker Ridge pits imported there and skipped by
Banff's lab, station temperature and humidity moved to the plot's height there and nowhere else."""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from snowagent.lab.ingest.profiles import import_profiles
from snowagent.lab.ingest.weather import FTS_RAW, site_weather, to_plot_height, validate_frame
from snowagent.lab.schemas.common import SiteCode
from snowagent.lab.services.data import station_setup
from snowagent.lab.settings import load_lab_config, read_config_keys

REPO = Path(__file__).resolve().parents[2]
FIX = REPO / "tests/fixtures/lab"


@pytest.fixture(scope="module")
def banff():
    return load_lab_config(REPO / "config/lab.yaml")


@pytest.fixture(scope="module")
def jasper():
    return load_lab_config(REPO / "config/lab_jasper.yaml")


def test_jasper_lab_takes_banffs_rules_and_only_its_own_plots(banff, jasper):
    assert set(jasper.sites) == {SiteCode.PARK} and SiteCode.PARK not in banff.sites
    assert set(banff.sites) == {SiteCode.BOW, SiteCode.GOAT, SiteCode.SIMP}
    # one genome allow-list, one scoring, one set of availability rules: an agent and a score mean the same in both
    assert jasper.genome == banff.genome and jasper.scoring_weights == banff.scoring_weights
    assert jasper.benchmark == banff.benchmark
    park = jasper.sites[SiteCode.PARK]
    plot = yaml.safe_load((REPO / "config/new_sites.yaml").read_text())["plots"]["parker_ridge"]
    assert (park.plot_id, park.latitude, park.longitude, park.elevation_m) == (
        "parker_ridge", plot["lat"], plot["lon"], plot["elevation_m"])
    assert jasper.weather.era5_dir == "data/interim/era5_north_west" and jasper.weather.era5_backfill
    assert banff.weather.era5_dir == "data/interim/era5"
    assert jasper.plot_forcing_config.endswith("new_sites.yaml")
    assert jasper.config_hash() != banff.config_hash()


def test_extends_replaces_whole_top_level_keys(tmp_path):
    (tmp_path / "base.yaml").write_text("a: 1\nweather: {era5_dir: x, era5_backfill: true}\n")
    (tmp_path / "child.yaml").write_text("extends: base.yaml\nweather: {era5_dir: y}\nb: 2\n")
    assert read_config_keys(tmp_path / "child.yaml") == {"a": 1, "weather": {"era5_dir": "y"}, "b": 2}
    (tmp_path / "loop.yaml").write_text("extends: loop.yaml\n")
    with pytest.raises(ValueError, match="nests"):
        read_config_keys(tmp_path / "loop.yaml")


def test_station_setup_only_moves_heights_where_the_plot_file_asks(banff, jasper):
    assert station_setup(banff) == (FTS_RAW, None)  # Banff: the FTS360 API files, values as measured
    station_dir, heights = station_setup(jasper)
    assert station_dir == "data/interim/new_sites/stations"
    assert heights["big_bend"] == 2125 and heights["tangle_ridge"] == 3009


def _station(ta_c, rh, start="2024-01-10T00:00Z") -> pd.DataFrame:
    n = len(ta_c)
    t = pd.date_range(start, periods=n, freq="h", tz="UTC")
    return pd.DataFrame({"time_utc": t, "ta_k": np.asarray(ta_c, float) + 273.15, "ta_k_qc": "ok",
                         "rh_frac": rh, "rh_frac_qc": "ok", "psum_1h_mm": 0.5, "psum_1h_mm_qc": "ok",
                         "hs_m": 1.2, "hs_m_qc": "ok", "vw_ms": 3.0, "vw_ms_qc": "ok", "dw_deg": 250.0,
                         "dw_deg_qc": "ok"})


def test_to_plot_height_moves_temperature_by_the_lapse_rate_and_keeps_the_dew_point():
    d = _station([-10.0, -5.0, np.nan], [0.8, 0.9, 0.7]).set_index("time_utc")
    out, moved = to_plot_height(d, -115.0)  # plot 115 m below the station: warmer, drier
    assert out["ta_k"].iloc[0] == pytest.approx(273.15 - 10.0 + 0.0065 * 115)
    assert out["rh_frac"].iloc[0] < 0.8 and out["rh_frac"].iloc[1] < 0.9
    assert out["rh_frac"].iloc[2] == 0.7  # no station temperature that hour: humidity as measured
    assert moved["ta_k"].tolist() == [True, True, False] and moved["rh_frac"].tolist() == [True, True, False]


def test_parker_weather_is_moved_to_the_plot_and_labelled(jasper):
    park = jasper.sites[SiteCode.PARK]
    plot = yaml.safe_load((REPO / "config/new_sites.yaml").read_text())["plots"]["parker_ridge"]
    bb = _station([-10.0] * 24, [0.8] * 24)
    bb.loc[5:6, "ta_k"] = np.nan  # Big Bend missing: Tangle Ridge, 1000 m higher, moved down
    tr = _station([-16.0] * 24, [0.6] * 24)
    frames = {"big_bend": bb, "tangle_ridge": tr}
    df, _ = site_weather(park, plot, lambda k: frames.get(k, pd.DataFrame()), "test",
                         station_heights={"big_bend": 2125, "tangle_ridge": 3009})
    assert validate_frame(df) == 24
    w = df.set_index("observed_at")
    assert w["air_temperature_k"].iloc[0] == pytest.approx(273.15 - 10.0 + 0.0065 * 115)
    assert w["air_temperature_k_source"].iloc[0] == "big_bend_moved_from_2125m"
    assert w["air_temperature_k"].iloc[5] == pytest.approx(273.15 - 16.0 + 0.0065 * 999)
    assert w["air_temperature_k_source"].iloc[5] == "tangle_ridge_moved_from_3009m"
    assert w["precipitation_mm_source"].iloc[0] == "big_bend" and w["snow_depth_m"].iloc[0] == 1.2
    # without heights (Banff's way) the same records are used as measured, named by their station alone
    df0, _ = site_weather(park, plot, lambda k: frames.get(k, pd.DataFrame()), "test")
    w0 = df0.set_index("observed_at")
    assert w0["air_temperature_k"].iloc[0] == pytest.approx(273.15 - 10.0)
    assert w0["air_temperature_k_source"].iloc[5] == "tangle_ridge"


def test_parker_pits_enter_the_jasper_lab_only(banff, jasper):
    rec = json.loads((FIX / "observed_profiles.jsonl").read_text().splitlines()[0])
    rec = rec | {"profile_id": "2024-01-10_parker_ridge_syn001", "site_key": "parker_ridge", "lat": 52.19587,
                 "lon": -117.10408, "elevation_m": 2010.0}
    profiles, _obs, prep = import_profiles([rec], jasper, "test")
    assert [p.site_code for p in profiles] == [SiteCode.PARK] and profiles[0].plot_id == "parker_ridge"
    profiles, _obs, prep = import_profiles([rec], banff, "test")
    assert profiles == [] and prep["skipped"] == {"not_a_lab_site:parker_ridge": 1}
