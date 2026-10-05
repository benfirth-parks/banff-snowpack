"""Lab adapters on synthetic fixtures: observed profiles -> depth from surface, grain class -> critical class,
QC'd station records -> canonical hourly weather (no fill)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd
import pytest
import yaml

from snowagent.lab.ingest.mapping import CRITICAL_CLASS_BY_GRAIN, critical_class, layer_of_concern
from snowagent.lab.ingest.profiles import import_profiles, parse_aspect, profile_from_observed
from snowagent.lab.ingest.weather import site_weather, station_loader, validate_frame
from snowagent.lab.schemas.profile import CriticalClass
from snowagent.lab.settings import load_lab_config
from snowagent.obs.transcription import GRAIN_FORMS

REPO = Path(__file__).resolve().parents[2]
FIX = REPO / "tests/fixtures/lab"


@pytest.fixture(scope="module")
def cfg():
    return load_lab_config(REPO / "config/lab.yaml")


@pytest.fixture(scope="module")
def records() -> dict[str, dict]:
    recs = [json.loads(line) for line in (FIX / "observed_profiles.jsonl").read_text().splitlines()]
    return {r["profile_id"]: r for r in recs}


def test_critical_class_mapping_is_one_table():
    assert set(CRITICAL_CLASS_BY_GRAIN) <= set(GRAIN_FORMS)
    assert critical_class("SHsu") == (CriticalClass.surface_hoar, "SHsu")
    assert critical_class("FCxr")[0] == CriticalClass.facets
    assert critical_class("DHch")[0] == CriticalClass.depth_hoar
    assert critical_class("MFcr")[0] == critical_class("IFrc")[0] == CriticalClass.crust
    assert critical_class("IFbi")[0] == critical_class("RGxf")[0] == critical_class("MF")[0] == CriticalClass.other
    assert critical_class(None) == (CriticalClass.unknown, None)
    assert critical_class("UNKNOWN", "DH") == (CriticalClass.depth_hoar, "DH")  # secondary only without primary
    assert critical_class("RG", "FC")[0] == CriticalClass.other


def test_layers_of_concern_from_grain_class_or_observer_tag():
    assert layer_of_concern("FC") == (CriticalClass.facets, True, ["grain_class:facets:FC"])
    assert layer_of_concern("RG") == (CriticalClass.other, False, [])
    cls, concern, basis = layer_of_concern("RG", None, "Nov crust")
    assert cls == CriticalClass.other and concern and basis == ["observer_tag:Nov crust"]
    assert layer_of_concern(None, "DH")[2] == ["grain_class:depth_hoar:DH:secondary"]


def test_parse_aspect():
    assert parse_aspect("NE") == 45 and parse_aspect("135° SE") == 135 and parse_aspect("South West") == 225
    assert parse_aspect("inapplicable") is None and parse_aspect("N/A") is None and parse_aspect(None) is None


def test_height_above_ground_converted_to_depth_from_surface(records):
    rec = records["2024-01-10_bow_summit_syn001"]
    p = profile_from_observed(rec, "BOW")
    got = [(round(ly.top_depth_m, 3), round(ly.bottom_depth_m, 3), ly.grain_primary) for ly in p.layers]
    assert got == [(0.0, 0.2, "PP"), (0.2, 0.21, "SH"), (0.21, 0.6, "RG"), (0.6, 0.65, "MFcr"), (0.65, 1.0, "FC"),
                   (1.0, 1.2, "DH")]
    assert p.snow_depth_m == 1.2 and p.validation_warnings == []
    sh = p.layers[1]
    assert sh.is_layer_of_concern and sh.concern_basis == ["grain_class:surface_hoar:SH", "observer_tag:Jan 5"]
    assert (sh.grain_size_mm, sh.grain_size_max_mm) == (5.0, 6.0)
    assert [ly.critical_class for ly in p.layers] == ["other", "surface_hoar", "other", "crust", "facets", "depth_hoar"]
    # raw fields kept beside the normalized ones, unchanged
    assert sh.raw["top_cm"] == 100 and sh.raw["bottom_cm"] == 99 and sh.raw["raw_index"] == 1
    assert p.raw["hs_cm"] == 120.0 and p.raw["height_reference"] == "height_above_ground" and "layers" not in p.raw
    assert [(t.depth_m, t.temperature_c) for t in p.temperatures] == [(0.0, -12.0), (0.2, -9.0), (1.2, -1.0)]
    assert p.aspect_deg == 45 and p.profile_quality == "exact" and p.usable and p.duplicate_of is None


def test_depth_chart_without_hs_stays_depth(records):
    p = profile_from_observed(records["2024-01-24_goats_eye_syn003"], "GOAT")
    assert [(ly.top_depth_m, ly.bottom_depth_m) for ly in p.layers] == [(0.0, 0.15), (0.15, 0.4), (0.4, 0.7)]
    assert p.snow_depth_m is None and "snow_depth_unknown" in p.validation_warnings
    last = p.layers[-1]
    assert last.grain_primary == "UNKNOWN" and last.grain_secondary == "DH" and last.critical_class == "depth_hoar"
    assert "grain_form_unknown_in_1_layers" in p.validation_warnings


def test_unplaceable_layers_left_out_with_warnings(records):
    rec = records["2024-02-02_simpson_syn004"]
    p = profile_from_observed(rec, "SIMP")
    assert [(ly.top_depth_m, ly.bottom_depth_m) for ly in p.layers] == [(0.0, 0.1), (0.1, 0.4)]
    w = p.validation_warnings
    assert "snow_depth_unknown_surface_taken_at_top_of_highest_layer" in w
    assert "raw_layer_2_zero_or_negative_thickness_left_out" in w and "raw_layer_3_missing_boundary_left_out" in w
    assert p.review_reasons == ["location_3.2km_from_site_median"]
    assert len(p.raw["location_qc"]) == 1  # the whole record stays in raw


def test_import_profiles_counts_what_it_skips(records, cfg):
    profiles, tests, rep = import_profiles(records.values(), cfg, "import-test")
    assert [p.profile_id for p in profiles] == ["2024-01-10_bow_summit_syn001", "2024-01-10_bow_summit_syn002",
                                                "2024-01-24_goats_eye_syn003", "2024-02-02_simpson_syn004"]
    assert profiles[1].duplicate_of == profiles[0].profile_id
    assert rep["skipped"] == {"not_a_lab_site:tak_falls": 1, "not_a_lab_site:unknown": 1, "no_observation_time": 1}
    assert len(tests) == 1 and tests[0].observation_type == "stability_test"
    assert tests[0].payload["depth_m"] == pytest.approx(0.21) and tests[0].payload["raw"].startswith("CTM 14")


@pytest.fixture()
def source(tmp_path: Path) -> Path:
    """A checkout layout holding the synthetic inputs."""
    root = tmp_path / "checkout"
    shutil.copytree(FIX / "fts360", root / "data/raw/fts360")
    (root / "data/interim/obs").mkdir(parents=True)
    shutil.copy(FIX / "observed_profiles.jsonl", root / "data/interim/obs/observed_profiles.jsonl")
    return root


def test_weather_first_ok_station_wins_and_nothing_is_filled(source, cfg):
    plots = yaml.safe_load((REPO / "config/plot_forcing.yaml").read_text())["plots"]
    df, summary = site_weather(cfg.sites["BOW"], plots["bow_summit"], station_loader(source), "import-test")
    assert validate_frame(df) == 48 and summary["hours"] == 48
    w = df.set_index("observed_at")
    t = pd.Timestamp
    # out-of-range temperature at the first station: the second station's ok value, named
    assert w.loc[t("2024-01-09T05:00Z"), "air_temperature_k"] == pytest.approx(273.15 - 7.5)
    assert w.loc[t("2024-01-09T05:00Z"), "air_temperature_k_source"] == "bow_summit_precip_ab_env"
    assert w.loc[t("2024-01-09T04:00Z"), "air_temperature_k_source"] == "bow_summit"
    # humidity has one station: its gap hours stay missing, never filled
    gap = w.loc[t("2024-01-10T16:00Z")]
    assert pd.isna(gap["relative_humidity_frac"]) and gap["relative_humidity_frac_qc"] == "missing"
    # snow-depth spike: the first station's value is suspect, the second station's ok value is used
    spike = w.loc[t("2024-01-10T06:00Z")]
    assert spike["snow_depth_m_source"] == "bow_summit_precip_ab_env" and spike["snow_depth_m_qc"] == "ok"
    # gauge reset: a bad increment is null, flagged bad and the gauge named
    reset = w.loc[t("2024-01-10T01:00Z")]
    assert pd.isna(reset["precipitation_mm"]) and reset["precipitation_mm_qc"] == "bad"
    assert reset["precipitation_mm_source"] == "bow_summit_precip_ab_env" and reset["quality_flag"] == "bad"
    assert w["precipitation_mm"].sum() == pytest.approx(2.0)
    # not measured at the plots: always null and missing
    assert w["shortwave_radiation_wm2"].isna().all() and (w["station_pressure_pa_qc"] == "missing").all()
    assert w["wind_speed_ms"].dropna().tolist() == pytest.approx([12.0 / 3.6] * 46)
    assert (df["availability_assumption"] == "observed_at").all() and df["source_recorded_at"].isna().all()


def test_site_without_station_files_gives_empty_table(source, cfg):
    plots = yaml.safe_load((REPO / "config/plot_forcing.yaml").read_text())["plots"]
    df, summary = site_weather(cfg.sites["SIMP"], plots["simpson"], station_loader(source), "import-test")
    assert df.empty and summary["hours"] == 0 and validate_frame(df) == 0
