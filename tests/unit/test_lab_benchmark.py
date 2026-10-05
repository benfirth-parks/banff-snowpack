"""Benchmark harness on synthetic data (ADR-059): case types, availability rule, split modes, exclusions, anonymous
visible packages, leakage checks (a planted future record fails the build), sealed and held-out seasons."""

from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path

import pandas as pd
import pytest
import yaml
from typer.testing import CliRunner

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.lab.benchmark import builder  # noqa: E402
from snowagent.lab.benchmark import package as pkg  # noqa: E402
from snowagent.lab.benchmark.leakage import LeakageError, check_case  # noqa: E402
from snowagent.lab.benchmark.loader import (  # noqa: E402
    SealedTruthError,
    case_dirs,
    load_hidden_truth,
    load_visible_case,
    read_manifest,
)
from snowagent.lab.schemas.benchmark import VisibleBenchmarkCase  # noqa: E402
from snowagent.lab.settings import load_lab_config  # noqa: E402
from snowagent.lab.storage.paths import LabPaths  # noqa: E402
from snowagent.lab.storage.provenance import sha256_file  # noqa: E402
from snowagent.lab.storage.tables import read_table, write_table  # noqa: E402
from tests.unit.lab_fixtures import write_synthetic_lab  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
CONFIG = REPO / "config/lab.yaml"


def _config(tmp_path: Path, **splits):
    """config/lab.yaml with other split settings (plot and observation configs from the repository)."""
    lab = yaml.safe_load(CONFIG.read_text())
    lab["splits"] |= splits
    lab["plot_forcing_config"] = str(REPO / "config/plot_forcing.yaml")
    lab["observations_config"] = str(REPO / "config/observations.yaml")
    f = tmp_path / "lab.yaml"
    f.write_text(yaml.safe_dump(lab))
    return load_lab_config(f)


@pytest.fixture()
def lab(tmp_path):
    cfg = load_lab_config(CONFIG)
    paths = LabPaths(tmp_path / "lab")
    source = tmp_path / "checkout"
    ids = write_synthetic_lab(paths.root, source, cfg)
    return cfg, paths, source, ids


def _build(lab, **kw):
    cfg, paths, source, _ids = lab
    kw.setdefault("exclude_flagged", True)
    return builder.build_cases(paths, kw.pop("config", cfg), source, **kw)


def _cases(paths: LabPaths) -> dict[str, Path]:
    return {d.name: d for d in case_dirs(paths)}


# --------------------------------------------------------------------------------------------- building


def test_one_case_per_usable_pit_with_forecast_source_and_exclusions(lab):
    cfg, paths, _source, ids = lab
    rep = _build(lab)
    assert rep["case_set"] == "all" and rep["split_mode"] == "all" and not rep["split_provisional"]
    assert rep["leakage"] == {"pass": rep["cases"], "fail": 0}
    cases = _cases(paths)
    h72 = {n for n in cases if n.endswith("_H72")}
    np_ = {n for n in cases if n.endswith("_NP")}
    assert h72 == {"BOW_20231201T1900Z_H72", "BOW_20231220T1900Z_H72", "BOW_20240110T1900Z_H72",
                   "BOW_20240125T1940Z_H72"}
    assert np_ == {"BOW_20231220T1900Z_NP", "BOW_20240110T1900Z_NP", "BOW_20240125T1940Z_NP"}
    m = read_manifest(cases["BOW_20240110T1900Z_H72"])
    assert m.forecast_source == "archived_gfs" and m.forecast_runs[0].issued_at.isoformat() == "2024-01-07T00:00:00+00:00"
    assert m.horizon_hours == 72 and m.split == "training" and m.season == "2023-2024"
    assert read_manifest(cases["BOW_20231220T1900Z_H72"]).forecast_source == "measured_standin"
    # an archived run without the plot's point: the labelled stand-in, not a silent gap
    assert read_manifest(cases["BOW_20231201T1900Z_H72"]).forecast_source == "measured_standin"
    assert rep["forecast_sources"]["forecast_h72"] == {"archived_gfs": 1, "measured_standin": 3}
    reasons = {(e["case_type"], e["profile_id"]): e["reason"] for e in rep["exclusions"]}
    assert reasons[("forecast_h72", ids["p2dup"])] == "duplicate"
    assert reasons[("forecast_h72", ids["flagged"])] == "flagged_review_list"
    assert reasons[("forecast_h72", ids["old"])] == "season_not_in_split_mode"
    assert reasons[("forecast_h72", ids["prev"])] == "standin_weather_coverage_below_min"  # no weather that season
    assert reasons[("next_pit", ids["p1"])] == "no_previous_pit_in_season"
    depth = read_manifest(cases["BOW_20240125T1940Z_H72"])
    assert depth.target_scope == "depth_only" and rep["depth_only_targets"] == 2
    # next_pit: as_of is the previous permitted pit's availability (flagged pit never an anchor)
    n3 = read_manifest(cases["BOW_20240125T1940Z_NP"])
    assert n3.anchor_profile_id == ids["p3"] and n3.as_of_time.isoformat() == "2024-01-11T19:00:00+00:00"
    assert n3.forecast_source == "measured_standin" and n3.forecast_standin["withheld_variables"] == [
        "snow_depth_m", "swe_mm"]
    # traceability and the report's counts
    assert m.config_hash == cfg.config_hash() and len(m.data_hash) == 64 and m.build_run_id == rep["run_id"]
    assert rep["case_counts"]["BOW"]["training"] == {"forecast_h72": 4, "next_pit": 3}
    assert rep["cases_per_plot_season"]["forecast_h72"]["BOW 2023-2024"] == {"archived_gfs": 1,
                                                                             "measured_standin": 3}
    assert (paths.benchmark / "all/build_report.json").is_file()


def test_availability_rule_on_pits_weather_era5_and_forecast(lab):
    _cfg, paths, _source, ids = lab
    _build(lab, case_types=["forecast_h72"])
    d = _cases(paths)["BOW_20240110T1900Z_H72"]
    m = read_manifest(d)
    case = load_visible_case(d)
    # pits: observed + 24 h <= as_of (2024-01-07T19:00Z): p1, p2 and earlier seasons' pits (a season the split mode
    # does not score is still history); never the target
    assert set(m.pit_keys.values()) == {ids["old"], ids["prev"], ids["p1"], ids["p2"]}
    assert sorted(p.season_offset for p in case.permitted_pits) == [-10, -1, 0, 0]
    assert all(p.available_rel_h <= 0 for p in case.permitted_pits)
    assert m.excluded_counts["profiles:duplicate"] == 1 and m.excluded_counts["profiles:target_or_copy"] == 1
    assert m.excluded_counts["profiles:observed_after_as_of"] == 1  # the target and the flagged pit counted apart
    assert m.excluded_counts["profiles:flagged_review_list"] == 1
    # station hours: observed + 1 h <= as_of; ERA5-filled values only once 5 days old
    wo = case.weather_observed
    assert max(h.t_rel_h for h in wo) == -1.0 and all(h.available_rel_h <= 0 for h in wo)
    recent = [h for h in wo if h.t_rel_h > -120]
    assert recent and all(h.shortwave_radiation_wm2 is None for h in recent)
    assert all(h.shortwave_radiation_wm2 == 120.0 for h in wo if h.t_rel_h <= -120)
    assert m.excluded_counts["weather_observed:era5_values_within_latency"] == 2 * len(recent)
    # forecast: issued 19 h before as_of, available 14 h before, trimmed at the valid time
    run = case.forecast_runs[0]
    assert (run.issued_rel_h, run.available_rel_h) == (-19.0, -14.0)
    assert all(h.kind == "forecast" and h.issued_rel_h == -19.0 for h in case.weather_forecasts)
    assert max(h.t_rel_h for h in case.weather_forecasts) <= case.horizon_hours
    assert case.as_of_day_of_year == pytest.approx(7 + 19 / 24)


def test_standin_is_labelled_and_withholds_snowpack_variables(lab):
    _cfg, paths, _source, _ids = lab
    _build(lab, case_types=["next_pit"])
    case = load_visible_case(_cases(paths)["BOW_20240110T1900Z_NP"])
    assert case.forecast_source == "measured_standin" and not case.forecast_runs
    fc = case.weather_forecasts
    assert fc and all(h.kind == "perfect_forecast" and h.issued_rel_h == 0 and h.source_id == "measured_standin"
                      for h in fc)
    assert all(h.snow_depth_m is None and h.swe_mm is None for h in fc)
    assert all(h.air_temperature_k is not None for h in fc)
    assert max(h.t_rel_h for h in fc) == pytest.approx(case.horizon_hours)
    assert min(h.t_rel_h for h in fc) > -1  # starts after the last visible observed hour
    assert max(h.t_rel_h for h in case.weather_observed) <= -1


# --------------------------------------------------------------------------------------------- anonymity

DATE = re.compile(r"(19|20)\d{2}-\d{2}-\d{2}|(19|20)\d{2}\d{2}\d{2}T")


def _visible_text(d: Path) -> str:
    parts = []
    for f in sorted((d / pkg.VISIBLE).iterdir()):
        if f.suffix == ".parquet":
            t = read_table(f)
            parts.append(" ".join(t.columns))
            parts.append(t.astype(str).to_csv(index=False))
        else:
            parts.append(f.read_text())
    return "\n".join(parts)


def test_visible_package_carries_no_ids_dates_or_free_text(lab):
    """Owner, 2026-10-05: agents must not memorize snowpacks. Neither the visible files nor the object an agent
    receives carry a profile id, dated case id, observer or pit identifier, free text, or a calendar date."""
    _cfg, paths, _source, ids = lab
    _build(lab)
    banned_values = [*ids.values(), "obs_X", "Ben", "121106", "Rain Crust", "Jan 4", "SYNTHETIC", "synthetic pit",
                     "51.70946"]
    for name, d in _cases(paths).items():
        m = read_manifest(d)
        text = _visible_text(d)
        dumped = load_visible_case(d).model_dump_json()
        for blob in (text, dumped):
            assert name not in blob and m.target_profile_id not in blob
            assert not DATE.search(blob), (name, DATE.search(blob))
            for v in banned_values:
                if v == "51.70946":
                    continue  # the site's own coordinates are in site.json (the plot, not a pit)
                assert v not in blob, (name, v)
            for field in pkg.FORBIDDEN_FIELDS:
                assert f'"{field}"' not in blob
        for f in (d / pkg.VISIBLE).glob("*.parquet"):
            cols = set(read_table(f).columns)
            assert not cols & (pkg.FORBIDDEN_FIELDS | {"latitude", "longitude"}), (f.name, cols)
            assert not any(pd.api.types.is_datetime64_any_dtype(t) for t in read_table(f).dtypes)
        assert re.fullmatch(r"[0-9a-f]{16}", json.loads((d / pkg.VISIBLE / pkg.CASE).read_text())["case_key"])
        # the real ids stay in the manifest and the hidden package
        assert m.case_key and set(m.pit_keys) == {p.pit_key for p in load_visible_case(d).permitted_pits}
        assert json.loads((d / pkg.HIDDEN / pkg.VERIFICATION).read_text())["season"] == m.season


def test_agent_loader_returns_only_the_visible_case(lab):
    _cfg, paths, _source, _ids = lab
    _build(lab, case_types=["forecast_h72"])
    d = _cases(paths)["BOW_20240110T1900Z_H72"]
    case = load_visible_case(d)
    assert type(case) is VisibleBenchmarkCase
    blob = case.model_dump_json()
    assert "hidden" not in blob and str(paths.root) not in blob and "truth" not in blob
    assert not any(isinstance(v, Path) for v in case.__dict__.values())
    # the loader never opens hidden/ or the manifest: it works with both removed
    copy = d.parent / "copy"
    shutil.copytree(d / pkg.VISIBLE, copy / pkg.VISIBLE)
    assert load_visible_case(copy) == case


# --------------------------------------------------------------------------------------------- leakage


def test_planted_future_weather_record_fails_the_build(lab, monkeypatch):
    _cfg, paths, _source, _ids = lab
    real = builder.visible_weather

    def leaky(weather, start, as_of, latency_h, era5_latency_h):
        w, n = real(weather, start, as_of, latency_h, era5_latency_h)
        future = weather[weather["observed_at"] > as_of + pd.Timedelta(hours=6)].head(1)
        future = builder.stamp(future, "observed_at", latency_h)  # a future hour, honestly stamped
        return pd.concat([w, future], ignore_index=True), n

    monkeypatch.setattr(builder, "visible_weather", leaky)
    with pytest.raises(LeakageError, match="visible_records_available_at_as_of"):
        _build(lab, case_types=["forecast_h72"])
    assert not _cases(paths)  # nothing written
    assert not [p for p in paths.benchmark.rglob(".tmp-*")]


def test_planted_target_or_future_pit_fails_the_build(lab, monkeypatch):
    _cfg, paths, _source, ids = lab
    real = builder.visible_pits

    def leaky(inputs, site, as_of, target_ids, delay_h, holdout_season=None):
        vp = real(inputs, site, as_of, target_ids, delay_h, holdout_season)
        t = inputs.profiles[inputs.profiles["profile_id"].isin(target_ids)].drop(columns=["review_reasons"])
        vp.profiles = pd.concat([vp.profiles, builder.stamp(t, "observed_at", delay_h)], ignore_index=True)
        vp.layers = pd.concat([vp.layers, inputs.layers[inputs.layers["profile_id"].isin(target_ids)]],
                              ignore_index=True)
        return vp  # the target pit (observed after as_of) planted among the visible pits

    monkeypatch.setattr(builder, "visible_pits", leaky)
    with pytest.raises(LeakageError) as err:
        _build(lab, case_types=["forecast_h72"])
    names = {c["name"] for c in err.value.report.failed}
    assert names & {"target_profile_not_visible", "no_profile_observed_after_as_of", "manifest_valid"}
    assert "target" in str(err.value)
    assert not _cases(paths)


def _rehash(d: Path) -> None:
    m = json.loads((d / pkg.MANIFEST).read_text())
    m["visible_hashes"] = {n: sha256_file(d / pkg.VISIBLE / n) for n in m["visible_hashes"]}
    (d / pkg.MANIFEST).write_text(json.dumps(m))


def test_tampered_packages_fail_the_checks(lab):
    cfg, paths, _source, ids = lab
    _build(lab, case_types=["forecast_h72"])
    d = _cases(paths)["BOW_20240110T1900Z_H72"]
    assert check_case(d).status == "pass"

    def tampered(edit) -> dict:
        t = d.parent / f"{d.name}"
        backup = d.parent / "backup"
        shutil.copytree(d, backup)
        try:
            edit(t)
            return {c["name"]: c for c in check_case(t).checks}
        finally:
            shutil.rmtree(t)
            backup.rename(t)

    def future_hour(t):
        f = t / pkg.VISIBLE / pkg.WEATHER_OBSERVED
        w = read_table(f)
        row = w.iloc[[-1]].copy()
        row["t_rel_h"], row["available_rel_h"] = 5.0, 6.0
        write_table(pd.concat([w, row], ignore_index=True), f)

    r = tampered(future_hour)
    assert not r["hashes_complete_and_match"]["ok"]  # without rehashing: the hash check catches it
    r = tampered(lambda t: (future_hour(t), _rehash(t)))
    assert not r["visible_records_available_at_as_of"]["ok"] and not r["visible_case_validates"]["ok"]

    def target_in_text(t):
        f = t / pkg.VISIBLE / pkg.LAYERS
        ly = read_table(f)
        ly.loc[0, "concern_basis_json"] = json.dumps([f"copy of {ids['p3']}"])
        write_table(ly, f)
        _rehash(t)

    r = tampered(target_in_text)
    assert not r["target_profile_not_visible"]["ok"] and not r["visible_package_anonymous"]["ok"]

    def forecast_late(t):
        f = t / pkg.VISIBLE / pkg.FORECAST_RUNS
        runs = json.loads(f.read_text())
        runs[0]["issued_rel_h"], runs[0]["available_rel_h"] = 2.0, 7.0
        f.write_text(json.dumps(runs))
        _rehash(t)

    assert not tampered(forecast_late)["forecasts_issued_before_as_of"]["ok"]

    def dated_column(t):
        f = t / pkg.VISIBLE / pkg.PITS
        p = read_table(f)
        p["observed_at"] = pd.Timestamp("2023-12-01T19:00Z")
        write_table(p, f)
        _rehash(t)

    assert not tampered(dated_column)["visible_package_anonymous"]["ok"]

    def no_hash(t):
        m = json.loads((t / pkg.MANIFEST).read_text())
        m["hidden_hashes"] = {}
        (t / pkg.MANIFEST).write_text(json.dumps(m))

    assert not tampered(no_hash)["manifest_valid"]["ok"]

    def naive_time(t):
        m = json.loads((t / pkg.MANIFEST).read_text())
        m["as_of_time"] = "2024-01-07T19:00:00"
        (t / pkg.MANIFEST).write_text(json.dumps(m))

    assert not tampered(naive_time)["manifest_valid"]["ok"]


# --------------------------------------------------------------------------------------------- split modes


def test_sealed_truth_is_never_read_without_the_typed_unseal(lab, tmp_path):
    cfg = _config(tmp_path, mode="split", development_seasons=["2022-2023"], validation_seasons=[],
                  sealed_test_seasons=["2023-2024"])
    _c, paths, _source, ids = lab
    rep = _build(lab, config=cfg, case_types=["forecast_h72"])
    assert rep["case_set"] == "split" and rep["split_provisional"]
    sealed = [d for d in case_dirs(paths, "split", "sealed_test")]
    assert sealed and all(read_manifest(d).split == "sealed_test" for d in sealed)
    d = sealed[0]
    with pytest.raises(SealedTruthError, match="UNSEAL"):
        load_hidden_truth(d)
    with pytest.raises(SealedTruthError):
        load_hidden_truth(d, unseal="yes")
    assert load_hidden_truth(d, unseal=f"UNSEAL {d.name}").truth_profile.profile_id == read_manifest(d).target_profile_id
    assert all("provisional" in " ".join(read_manifest(x).warnings) for x in sealed)

    from snowagent.cli import app
    from snowagent.lab.services.benchmark import case_index

    assert set(case_index(paths, "split").query("split == 'sealed_test'")["target_profile_id"]) == {"sealed"}
    r = CliRunner().invoke(app, ["lab", "case-truth", "--case-id", d.name, "--data-root", str(paths.root)])
    assert r.exit_code == 4 and "sealed-test case" in r.output


def test_loso_training_cases_never_show_the_held_out_season(lab, tmp_path, monkeypatch):
    cfg = _config(tmp_path, mode="loso")
    _c, paths, _source, ids = lab
    rep = _build(lab, config=cfg, case_types=["forecast_h72"], holdout="2022-2023")
    assert rep["case_set"] == "loso_2022-2023" and rep["holdout_season"] == "2022-2023"
    train = case_dirs(paths, "loso_2022-2023", "training")
    assert train and not case_dirs(paths, "loso_2022-2023", "holdout")  # the 2022-23 pit has no weather
    for d in train:
        m = read_manifest(d)
        assert ids["prev"] not in m.pit_keys.values() and m.holdout_season == "2022-2023"
    assert read_manifest(train[-1]).excluded_counts["profiles:holdout_season"] == 1
    # a builder that forgets the holdout fails the check
    real = builder.visible_pits
    monkeypatch.setattr(builder, "visible_pits", lambda i, s, a, t, dly, h=None: real(i, s, a, t, dly, None))
    with pytest.raises(LeakageError, match="loso_holdout_not_visible"):
        _build(lab, config=cfg, case_types=["forecast_h72"], holdout="2022-2023")


def test_flag_switch_off_makes_flagged_pits_targets_and_inputs(lab):
    _cfg, paths, _source, ids = lab
    rep = _build(lab, case_types=["forecast_h72"], exclude_flagged=False)
    assert "BOW_20240111T1800Z_H72" in _cases(paths)
    assert not any(e["reason"] == "flagged_review_list" for e in rep["exclusions"])
    assert ids["flagged"] in read_manifest(_cases(paths)["BOW_20240125T1940Z_H72"]).pit_keys.values()


def test_rebuild_prunes_stale_cases_and_records_runs(lab):
    _cfg, paths, _source, _ids = lab
    _build(lab)
    stale = paths.benchmark / "all/training/BOW_20200101T0000Z_H72"
    shutil.copytree(_cases(paths)["BOW_20240110T1900Z_H72"], stale)
    rep = _build(lab)
    assert rep["removed_stale_cases"] == 1 and not stale.exists()
    from snowagent.lab.storage.registry import RunRegistry

    runs = RunRegistry(paths.registry).latest(kind="case_build")
    assert len(runs) == 2 and runs[0].counts["cases"] == rep["cases"] and runs[0].case_ids


def test_cli_build_list_check_and_truth(lab):
    from snowagent.cli import app

    cfg, paths, source, ids = lab
    runner = CliRunner()
    cwd = os.getcwd()
    os.chdir(REPO)
    try:
        args = ["--data-root", str(paths.root)]
        r = runner.invoke(app, ["lab", "build-cases", *args, "--source", str(source), "--site", "BOW"])
        assert r.exit_code == 0, r.output
        out = json.loads(r.output)
        assert out["leakage"]["fail"] == 0 and out["cases"] == 7
        r = runner.invoke(app, ["lab", "cases", *args])
        assert json.loads(r.output)["counts"]["all training BOW forecast_h72 archived_gfs"] == 1
        r = runner.invoke(app, ["lab", "check-leakage", *args])
        assert r.exit_code == 0 and json.loads(r.output)["passed"] == 7
        r = runner.invoke(app, ["lab", "case-truth", *args, "--case-id", "BOW_20240110T1900Z_H72"])
        assert r.exit_code == 0 and json.loads(r.output)["truth_profile"]["profile_id"] == ids["p3"]
        r = runner.invoke(app, ["lab", "build-case", *args, "--source", str(source), "--profile-id", ids["p3"],
                                "--case-type", "next_pit"])
        assert r.exit_code == 0 and json.loads(r.output)["cases"] == 1
    finally:
        os.chdir(cwd)
