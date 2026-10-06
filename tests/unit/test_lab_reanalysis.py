"""Seasons before the plot stations as training cases (ADR-076): the season switch, the weather import before the
stations, each case's weather provenance (station / mixed / era5_only) and its filters, the unchanged leakage checks
on an ERA5-only case (ERA5 keeps its 120 h latency), and `lab prepare` fetching only the months those cases read."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.lab.benchmark import builder  # noqa: E402
from snowagent.lab.benchmark.leakage import LeakageError, check_case  # noqa: E402
from snowagent.lab.benchmark.loader import case_dirs, load_visible_case, read_manifest  # noqa: E402
from snowagent.lab.competition.runner import EngineSpec, build_leaderboard, run_competition, select_cases  # noqa: E402
from snowagent.lab.genome import default_genome  # noqa: E402
from snowagent.lab.schemas.genome import AgentFamily  # noqa: E402
from snowagent.lab.services import prepare as prep  # noqa: E402
from snowagent.lab.settings import Splits, load_lab_config  # noqa: E402
from snowagent.lab.storage.paths import LabPaths  # noqa: E402
from snowagent.lab.storage.tables import read_table, write_table  # noqa: E402
from tests.unit import lab_fixtures  # noqa: E402
from tests.unit.test_lab_benchmark import CONFIG, _config  # noqa: E402

OLD = [f"{y}-{y + 1}" for y in range(1997, 2015)]
ERA5 = "era5_cell_2244m"


# --------------------------------------------------------------------------------------------- the switch


def test_switch_adds_the_reanalysis_seasons_to_the_lab(tmp_path):
    cfg = load_lab_config(CONFIG)
    s = cfg.splits
    assert s.include_reanalysis_seasons and s.reanalysis_seasons == OLD
    assert s.all_seasons[:18] == OLD and s.all_seasons[18:] == [f"{y}-{y + 1}" for y in range(2015, 2026)]
    assert s.assign("2006-2007").value == "training" and s.development_seasons[:18] == OLD
    assert prep.lab_seasons(cfg)[0] == "1997-1998"
    # off: exactly the station seasons, the older pits are history only
    off = _config(tmp_path, include_reanalysis_seasons=False).splits
    assert off.all_seasons[0] == "2015-2016" and len(off.all_seasons) == 11 and off.assign("2006-2007") is None
    # loso may hold out an older season only while the switch is on
    assert Splits(mode="loso", all_seasons=["2020-2021"], reanalysis_seasons=["2006-2007"],
                  loso_holdout="2006-2007").holdout() == "2006-2007"
    with pytest.raises(ValueError, match="not in all_seasons"):
        Splits(mode="loso", all_seasons=["2020-2021"], reanalysis_seasons=["2006-2007"],
               include_reanalysis_seasons=False, loso_holdout="2006-2007")
    # loading twice (a dumped config) lists each season once; a malformed season key is refused
    again = Splits(**s.model_dump())
    assert again.all_seasons == s.all_seasons
    with pytest.raises(ValueError, match="season key"):
        Splits(reanalysis_seasons=["2006-2008"])


# --------------------------------------------------------------------------------------------- provenance


def _weather(t0: str, t1: str, t_src, p_src) -> pd.DataFrame:
    w = lab_fixtures.synthetic_weather(t0, t1)
    w["air_temperature_k_source"], w["precipitation_mm_source"] = t_src, p_src
    return w


T0, T1 = "2023-11-15T00:00:00+00:00", "2023-12-15T00:00:00+00:00"


def test_weather_provenance_station_mixed_and_era5_only():
    s0, end = pd.Timestamp(T0), pd.Timestamp(T1)
    src, share = builder.weather_provenance(_weather(T0, T1, "bow_summit", "bow_summit"), s0, end)
    assert src == "station" and share == {"air_temperature_k": 1.0, "precipitation_mm": 1.0}
    src, share = builder.weather_provenance(_weather(T0, T1, ERA5, ERA5), s0, end)
    assert src == "era5_only" and share == {"air_temperature_k": 0.0, "precipitation_mm": 0.0}
    # station temperature, ERA5 precipitation (Bow Summit before its gauge, 2014-15): mixed
    src, share = builder.weather_provenance(_weather(T0, T1, "bow_summit", ERA5), s0, end)
    assert src == "mixed" and share["precipitation_mm"] == 0.0
    # 10 % of the hours ERA5-filled is still a station case; hours with no value at all do not count
    w = _weather(T0, T1, "bow_summit", "bow_summit")
    n = len(w)
    w.loc[: n // 10 - 1, "precipitation_mm_source"] = ERA5
    w.loc[n // 2: n // 2 + 50, "air_temperature_k"] = np.nan
    src, share = builder.weather_provenance(w, s0, end)
    assert src == "station" and 0.9 <= share["precipitation_mm"] < 0.91 and share["air_temperature_k"] == 1.0
    w.loc[: n // 5, "precipitation_mm_source"] = ERA5
    assert builder.weather_provenance(w, s0, end)[0] == "mixed"
    assert builder.weather_provenance(None, s0, end)[0] == "era5_only"


# --------------------------------------------------------------------------------------------- ERA5-only cases


@pytest.fixture()
def lab(tmp_path, monkeypatch):
    """The synthetic lab plus a second pit in 2013-14 and that season's weather from ERA5 only (no station)."""
    monkeypatch.setitem(lab_fixtures.PITS, "old2", ("2014-01-25_bow_summit_syn107", "2014-01-25T19:00:00+00:00", 3,
                                                    {}))
    cfg = load_lab_config(CONFIG)
    paths = LabPaths(tmp_path / "lab")
    source = tmp_path / "checkout"
    ids = lab_fixtures.write_synthetic_lab(paths.root, source, cfg)
    old = _weather("2013-09-15T00:00:00+00:00", "2014-02-05T00:00:00+00:00", ERA5, ERA5)
    for v in ("air_temperature_k", "precipitation_mm", "relative_humidity_frac"):
        old[f"{v}_qc"] = "filled"
        old[f"{v}_source"] = ERA5
    old["snow_depth_m"], old["snow_depth_m_source"], old["snow_depth_m_qc"] = np.nan, None, "missing"
    write_table(pd.concat([old, read_table(paths.weather)], ignore_index=True), paths.weather)
    return cfg, paths, source, ids


def _build(lab, **kw):
    cfg, paths, source, _ids = lab
    return builder.build_cases(paths, kw.pop("config", cfg), source, exclude_flagged=True, **kw)


def test_era5_only_cases_are_targets_labelled_and_pass_every_leakage_check(lab):
    cfg, paths, _source, ids = lab
    rep = _build(lab)
    assert rep["leakage"] == {"pass": rep["cases"], "fail": 0}
    cases = {d.name: d for d in case_dirs(paths)}
    old = {n for n in cases if n.startswith("BOW_2014")}
    assert old == {"BOW_20140110T1900Z_H72", "BOW_20140125T1900Z_H72", "BOW_20140125T1900Z_NP"}
    assert rep["weather_sources"] == {"forecast_h72": {"station": 4, "mixed": 0, "era5_only": 2},
                                      "next_pit": {"station": 3, "mixed": 0, "era5_only": 1}}
    assert rep["cases_per_plot_season"]["forecast_h72"]["BOW 2013-2014"] == {
        "archived_gfs": 0, "measured_standin": 2, "era5_only": 2}
    for n in old:
        m = read_manifest(cases[n])
        assert m.season == "2013-2014" and m.split == "training" and m.weather_source == "era5_only"
        assert m.forecast_source == "measured_standin" and m.weather_station_share == {
            "air_temperature_k": 0.0, "precipitation_mm": 0.0}
        report = check_case(cases[n], cfg.benchmark.availability.era5_latency_h, ["snow_depth_m", "swe_mm"])
        assert report.status == "pass", report.failed
        case = load_visible_case(cases[n])
        # ERA5 keeps its 120 h latency: no ERA5 value in the last 120 h before as_of is visible
        late = [h for h in case.weather_observed if h.t_rel_h > -120 and h.air_temperature_k is not None]
        assert not late and m.excluded_counts["weather_observed:era5_values_within_latency"] > 0
        assert all(h.sources.get("air_temperature_k", ERA5) == ERA5 for h in case.weather_observed)
        # the stand-in carries the ERA5 weather to the pit, snowpack variables withheld
        assert case.weather_forecasts and all(h.kind == "perfect_forecast" for h in case.weather_forecasts)
        assert all(h.snow_depth_m is None for h in case.weather_forecasts)
        assert m.target_profile_id not in m.pit_keys.values()
    # the older season's pits are history to the later cases exactly as before, and the target never
    np_ = read_manifest(cases["BOW_20140125T1900Z_NP"])
    assert np_.anchor_profile_id == ids["old"] and ids["old2"] not in np_.pit_keys.values()
    later = read_manifest(cases["BOW_20240110T1900Z_H72"])
    assert {ids["old"], ids["old2"]} <= set(later.pit_keys.values()) and later.weather_source == "station"


def test_era5_value_within_its_latency_fails_an_era5_only_case(lab, monkeypatch):
    _cfg, paths, _source, _ids = lab
    real = builder.visible_weather
    monkeypatch.setattr(builder, "visible_weather",
                        lambda w, start, as_of, lat, era5_lat: real(w, start, as_of, lat, 0.0))  # latency forgotten
    with pytest.raises(LeakageError, match="ERA5 air_temperature_k values within their 120 h latency"):
        _build(lab, case_types=["forecast_h72"], profile_ids=["2014-01-10_bow_summit_syn106"])
    assert not case_dirs(paths)


def test_switch_off_keeps_older_pits_as_history_only(lab, tmp_path):
    cfg = _config(tmp_path, include_reanalysis_seasons=False)
    _c, paths, _source, ids = lab
    rep = _build(lab, config=cfg)
    reasons = {(e["case_type"], e["profile_id"]): e["reason"] for e in rep["exclusions"]}
    assert reasons[("forecast_h72", ids["old2"])] == "season_not_in_split_mode"
    assert not [d for d in case_dirs(paths) if d.name.startswith("BOW_2014")]
    later = read_manifest(next(d for d in case_dirs(paths) if d.name == "BOW_20240110T1900Z_H72"))
    assert {ids["old"], ids["old2"]} <= set(later.pit_keys.values())


# --------------------------------------------------------------------------------------------- filters


def test_scores_filter_and_split_by_weather_source(lab):
    cfg, paths, _source, _ids = lab
    _build(lab)
    era5 = select_cases(paths, "all", weather_sources=["era5_only"])
    assert {m.case_id for _, m in era5} == {"BOW_20140110T1900Z_H72", "BOW_20140125T1900Z_H72",
                                            "BOW_20140125T1900Z_NP"}
    assert len(select_cases(paths, "all", weather_sources=["station", "era5_only"])) == len(select_cases(paths))
    g = default_genome(AgentFamily.persistence)
    res = run_competition(paths, cfg, [g], engine=EngineSpec(kind="fake"))
    assert set(res.leaderboard["by_weather_source"]) == {"station", "era5_only"}
    assert set(res.scores["weather_source"]) == {"station", "era5_only"}
    only = run_competition(paths, cfg, [g], engine=EngineSpec(kind="fake"), weather_sources=["era5_only"])
    assert set(only.scores["case_id"]) == {m.case_id for _, m in era5}
    # a run scored before ADR-076 has no column: grouping skips it
    assert build_leaderboard(res.scores.drop(columns="weather_source"), cfg.scoring_weights)["by_weather_source"] == {}


def test_training_plan_records_the_weather_source_filter(lab):
    from snowagent.lab.training.loop import TrainOptions, _opts_from_plan
    from snowagent.lab.training.loop import prepare as train_prepare

    cfg, paths, _source, _ids = lab
    _build(lab)
    opts = TrainOptions.from_config(cfg, rounds=1, population=2, survivors=1, weather_sources=["era5_only"],
                                    engine=EngineSpec(kind="fake"))
    _run_id, plan, refs = train_prepare(paths, cfg, opts, run_id="t-era5")
    assert plan["weather_sources"] == ["era5_only"] and {r.manifest.weather_source for r in refs} == {"era5_only"}
    assert _opts_from_plan(plan).weather_sources == ["era5_only"]
    _r, plan_all, refs_all = train_prepare(paths, cfg, TrainOptions.from_config(
        cfg, rounds=1, population=2, survivors=1, engine=EngineSpec(kind="fake")), run_id="t-all")
    assert "weather_sources" not in plan_all and len(refs_all) > len(refs)


# --------------------------------------------------------------------------------------------- import


def test_import_reaches_back_to_the_first_cached_era5_month_before_the_stations(tmp_path):
    from snowagent.lab.services.data import era5_start, import_data, load_weather
    from tests.unit.test_lab_import import FIX
    import shutil

    source = tmp_path / "checkout"
    shutil.copytree(FIX / "fts360", source / "data/raw/fts360")
    d = lab_fixtures.write_era5(source, ["2006-12", "2024-01"])
    cfg = load_lab_config(CONFIG)
    files = sorted(d.glob("era5_box_*.npz"))
    assert era5_start(cfg, files) == pd.Timestamp("2006-12-01", tz="UTC")
    # a cache that starts after the reanalysis seasons, or the switch off: the stations' first hour, as before
    assert era5_start(cfg, [f for f in files if "2024" in f.name or "_z" in f.name]) is None
    assert era5_start(_config(tmp_path, include_reanalysis_seasons=False), files) is None
    paths = LabPaths(tmp_path / "lab")
    report = import_data(source, paths, cfg, ("weather",))
    assert report["sites"]["BOW"]["weather_first_hour"].startswith("2006-12-01")
    w = load_weather(paths, "BOW").set_index("observed_at")
    dec = w.loc["2006-12"]
    assert len(dec) == 31 * 24 and (dec["air_temperature_k_source"] == "era5_cell_2100m").all()
    assert (dec["precipitation_mm_qc"] == "filled").all() and dec["snow_depth_m"].isna().all()
    gap = w.loc["2007-01"]  # no ERA5 month in the cache: explicit missing rows, never invented
    assert len(gap) and gap["air_temperature_k"].isna().all() and (gap["air_temperature_k_qc"] == "missing").all()
