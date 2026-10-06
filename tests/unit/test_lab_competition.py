"""Competition harness (ADR-063, ADR-065): the truth gate (sealed truth never read), the analogue library of other
seasons, SNOWPACK site-run reuse, the resumable parallel runner, the held-out gap and the CLI."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.lab.agents.analogue import AnalogueEntry, CaseDigest  # noqa: E402
from snowagent.lab.benchmark import builder, loader  # noqa: E402
from snowagent.lab.benchmark.loader import case_dirs, load_hidden_truth, read_manifest  # noqa: E402
from snowagent.lab.competition import runner, scoring  # noqa: E402
from snowagent.lab.competition import truth as truth_mod  # noqa: E402
from snowagent.lab.competition.incumbent import site_run_check  # noqa: E402
from snowagent.lab.competition.library import SeasonEntry, library_for  # noqa: E402
from snowagent.lab.competition.runner import EngineSpec, heldout_gap, run_competition  # noqa: E402
from snowagent.lab.competition.truth import TruthNotScorable, scoring_truth  # noqa: E402
from snowagent.lab.genome import default_genome, default_genomes  # noqa: E402
from snowagent.lab.schemas.genome import AgentFamily  # noqa: E402
from snowagent.lab.settings import load_lab_config  # noqa: E402
from snowagent.lab.storage.paths import LabPaths  # noqa: E402
from snowagent.lab.storage.registry import RunRegistry  # noqa: E402
from tests.unit.lab_fixtures import write_synthetic_lab  # noqa: E402
from tests.unit.test_lab_benchmark import CONFIG, _config  # noqa: E402

# --------------------------------------------------------------------------------------------- harness


@pytest.fixture()
def lab(tmp_path):
    cfg = load_lab_config(CONFIG)
    paths = LabPaths(tmp_path / "lab")
    write_synthetic_lab(paths.root, tmp_path / "checkout", cfg)
    builder.build_cases(paths, cfg, tmp_path / "checkout", exclude_flagged=True)
    return cfg, paths, tmp_path


def test_competition_scores_every_agent_on_every_case_and_resumes(lab, monkeypatch):
    cfg, paths, _ = lab
    genomes = default_genomes()
    res = run_competition(paths, cfg, genomes, engine=EngineSpec(kind="fake"), seed=7)
    n = len(case_dirs(paths, "all"))
    assert len(res.scores) == n * len(genomes) and set(res.scores["agent_id"]) == {g.agent_id for g in genomes}
    board = res.leaderboard["overall"]
    assert len(board) == 5 and all(r["composite"] is not None for r in board)
    assert set(res.leaderboard["by_forecast_source"]) == {"archived_gfs", "measured_standin"}
    # every case was scored on truth and the files are in place
    rec = json.loads((res.run_dir / "cases" / f"{read_manifest(case_dirs(paths, 'all')[0]).case_id}.json")
                     .read_text())
    assert rec["truth_used"] and set(rec["predictions"]) == {g.agent_id for g in genomes}
    assert (res.run_dir / "scores.parquet").is_file() and (res.run_dir / "leaderboard.json").is_file()
    m = RunRegistry(paths.registry).get(res.run_id)
    assert m.kind == "competition" and m.genome_hashes == {g.agent_id: g.genome_hash for g in genomes}
    assert m.case_set_hash and m.seed == 7 and m.scoring_weights == cfg.scoring_weights and m.profile_ids_used
    # the analogue agent never sees its own season: the synthetic set has one season, so it has no analogue
    ana = res.scores[res.scores["family"] == "analogue"]
    assert (ana["status"] == "insufficient_data").all()
    assert json.loads((res.run_dir / "library.json").read_text())  # ... although the library is not empty
    # resume: nothing is predicted again; another plan is refused
    calls = []
    monkeypatch.setattr(runner, "run_case", lambda t: calls.append(t) or [])
    again = run_competition(paths, cfg, genomes, engine=EngineSpec(kind="fake"), seed=7, run_id=res.run_id)
    assert not calls and again.resumed_cases == n
    pd.testing.assert_frame_equal(again.scores, res.scores)
    with pytest.raises(ValueError, match="another plan"):
        run_competition(paths, cfg, genomes, engine=EngineSpec(kind="fake"), seed=8, run_id=res.run_id)


def test_parallel_run_matches_the_serial_run(lab):
    cfg, paths, _ = lab
    g = [default_genome(AgentFamily.persistence), default_genome(AgentFamily.hybrid)]
    a = run_competition(paths, cfg, g, engine=EngineSpec(kind="fake"), workers=1, run_id="serial")
    b = run_competition(paths, cfg, g, engine=EngineSpec(kind="fake"), workers=2, run_id="parallel")
    cols = ["case_id", "agent_id", "composite", "snow_depth", "layer_structure"]
    sa = a.scores[cols].sort_values(cols[:2]).reset_index(drop=True)
    sb = b.scores[cols].sort_values(cols[:2]).reset_index(drop=True)
    pd.testing.assert_frame_equal(sa, sb)


def test_without_an_engine_snowpack_is_skipped_not_scored(lab):
    cfg, paths, _ = lab
    g = [default_genome(AgentFamily.snowpack), default_genome(AgentFamily.hybrid)]
    res = run_competition(paths, cfg, g, engine=EngineSpec(kind="none"), limit=2)
    sp = res.scores[res.scores["family"] == "snowpack"]
    assert (sp["status"] == "skipped").all() and sp["composite"].isna().all()
    row = next(r for r in res.leaderboard["overall"] if r["family"] == "snowpack")
    assert row["skipped"] == 2 and row["scored"] == 0 and row["composite"] is None
    hy = res.scores[res.scores["family"] == "hybrid"]
    assert (hy["status"] == "ok").all()


def test_sealed_truth_is_never_read(lab, tmp_path, monkeypatch):
    _cfg, _paths, _ = lab
    cfg = _config(tmp_path, mode="split", development_seasons=[], validation_seasons=[],
                  sealed_test_seasons=["2023-2024"])
    paths = LabPaths(tmp_path / "sealed")
    write_synthetic_lab(paths.root, tmp_path / "checkout2", cfg)
    builder.build_cases(paths, cfg, tmp_path / "checkout2", exclude_flagged=True)
    sealed = case_dirs(paths)
    assert sealed and all(read_manifest(d).split == "sealed_test" for d in sealed)
    opened = []
    spy = lambda *a, **k: opened.append(a) or load_hidden_truth(*a, **k)  # noqa: E731
    monkeypatch.setattr(loader, "load_hidden_truth", spy)
    monkeypatch.setattr(truth_mod, "load_hidden_truth", spy)
    with pytest.raises(TruthNotScorable):
        scoring_truth(sealed[0], read_manifest(sealed[0]))
    with pytest.raises(ValueError, match="no scorable case"):
        run_competition(paths, cfg, default_genomes(), engine=EngineSpec(kind="fake"), case_set="split")
    assert not opened


def test_library_for_a_case_holds_only_other_seasons():
    def entry(season, hs):
        d = CaseDigest(site_code="BOW", case_type="next_pit", horizon_h=72, hs_now_m=hs, precip_before_mm=[1.0],
                       temp_before_c=[-5.0], wind_before_ms=[2.0])
        return SeasonEntry(season=season, entry=AnalogueEntry(digest=d, truth_hs_m=hs))

    entries = [entry("2022-2023", 1.0), entry("2023-2024", 2.0), entry("2023-2024", 3.0)]
    lib = library_for(entries, "2023-2024")
    assert [e.truth_hs_m for e in lib.entries] == [1.0]
    assert "season" not in lib.model_dump_json()


def test_heldout_gap_compares_train_and_heldout_seasons():
    w = load_lab_config(CONFIG).scoring_weights
    rows = [{"agent_id": "a", "family": "persistence", "label": "a", "status": "ok", "season": s, "composite": c,
             "runtime_s": 0.1, **{k: c for k in scoring.COMPONENTS}}
            for s, c in [("2022-2023", 0.8)] * 5 + [("2023-2024", 0.5)] * 5]
    gap = heldout_gap(pd.DataFrame(rows), "2023-2024", w)["a"]
    assert gap["train_composite"] > gap["heldout_composite"] and gap["gap"] == pytest.approx(
        gap["train_composite"] - gap["heldout_composite"], abs=1e-3)
    with pytest.raises(ValueError):
        heldout_gap(pd.DataFrame(rows), "2019-2020", w)


def _site_run(root, season, era5=False, pit="2024-01-10_bow_summit_syn103", forecasts=None):
    d = root / "web" / "data" / "bow_summit"
    d.mkdir(parents=True, exist_ok=True)
    run = {"mode": "station", "run_id": "web-x", "forcing_hash": "f", "engine": {"version": "SP 1", "config_hash": "c"},
           "forcing_sources": {"ta": {"bow_summit": 1.0}, "vw": {"era5": 1.0 if era5 else 0.0, "bow_summit": 1.0}},
           "steer": {"updates": [{"pit": pit, "time_utc": "2024-01-11T00:00:00+00:00"}]},
           "nowcast": [{"t": "2024-01-25T18", "hs": 150.0, "L": [[150.0, 100.0, "DF", 1.5, 150], [100.0, 0.0, "FC",
                                                                                                 2.5, 250]]}]}
    (d / f"{season}.json").write_text(json.dumps(run))
    if forecasts:
        (d / f"{season}_forecasts.json").write_text(json.dumps(forecasts))


def test_site_run_reuse_only_when_the_run_used_information_available_at_as_of(lab):
    cfg, paths, tmp = lab
    d = next(x for x in case_dirs(paths) if x.name == "BOW_20240125T1940Z_NP")
    m = read_manifest(d)
    root = tmp / "site"
    _site_run(root, m.season)
    res, prov = site_run_check(root, m, "bow_summit")
    assert res is not None and res.source == "site_run_reuse" and not prov["reasons"]
    assert [ly.grain for ly in res.layers] == ["DF", "FC"] and res.layers[0].bottom_depth_m == pytest.approx(0.5)
    assert res.profile_lag_h == pytest.approx(1 + 40 / 60, abs=0.01)
    _site_run(root, m.season, era5=True)
    res, prov = site_run_check(root, m, "bow_summit")
    assert res is None and any("ERA5" in r for r in prov["reasons"])
    _site_run(root, m.season, pit="2024-01-25_bow_summit_syn105")  # the target pit itself
    res, prov = site_run_check(root, m, "bow_summit")
    assert res is None and any("may not see" in r for r in prov["reasons"])


def test_cli_compete_prints_the_leaderboard(lab):
    from snowagent.cli import app

    _cfg, paths, _ = lab
    r = CliRunner().invoke(app, ["lab", "compete", "--data-root", str(paths.root), "--config", str(CONFIG),
                                 "--engine", "fake", "--agents", "persistence", "--agents", "weather_rule",
                                 "--limit", "3", "--run-id", "cli-run"])
    assert r.exit_code == 0, r.output
    assert "Leaderboard (all cases)" in r.output and "persistence-default" in r.output
    assert "forecast source:" in r.output
    r = CliRunner().invoke(app, ["lab", "leaderboard", "--data-root", str(paths.root), "--config", str(CONFIG)])
    assert r.exit_code == 0 and "cli-run" in r.output


def test_rescore_re_scores_stored_predictions_without_running_an_agent(lab, monkeypatch):
    """ADR-074: a competition scored under an older scoring version is re-scored from its stored predictions into a
    new run; no agent runs, the source run is unchanged and the depth score no longer counts coverage."""
    from snowagent.cli import app

    cfg, paths, _ = lab
    real = scoring.score_case

    def v1(pred, truth, scope):  # the scoring-1 depth rule: 0.25 x coverage on top of 0.75 x the closeness
        s = real(pred, truth, scope)
        if s.get("depth_error_m") is not None:
            s["snow_depth"] = 0.75 * s["snow_depth"] + 0.25 * s["depth_covered"]
        return s

    with monkeypatch.context() as mp:
        mp.setattr(scoring, "SCORING_VERSION", "lab-scoring-1")
        mp.setattr(scoring, "score_case", v1)
        g = [default_genome(AgentFamily.persistence), default_genome(AgentFamily.snowpack)]
        old = run_competition(paths, cfg, g, engine=EngineSpec(kind="fake"), run_id="old")
    before = (old.run_dir / "scores.parquet").read_bytes()
    monkeypatch.setattr(runner, "make_agent", lambda *a, **k: pytest.fail("an agent ran"))
    r = CliRunner().invoke(app, ["lab", "rescore", "--run-id", "old", "--data-root", str(paths.root),
                                 "--config", str(CONFIG)])
    assert r.exit_code == 0, r.output
    new_dir = paths.outputs / "competitions" / f"old-{scoring.SCORING_VERSION}"
    assert (old.run_dir / "scores.parquet").read_bytes() == before  # the source run is not touched
    plan = json.loads((new_dir / "run.json").read_text())
    assert plan["scoring_version"] == "lab-scoring-2" and plan["source_scoring_version"] == "lab-scoring-1"
    assert plan["rescored_from"] == "old"
    a = old.scores.set_index(["case_id", "agent_id"]).sort_index()
    b = pd.read_parquet(new_dir / "scores.parquet").set_index(["case_id", "agent_id"]).sort_index()
    ok = b["status"] == "ok"
    expect = np.exp(-b.loc[ok, "depth_error_m"].astype(float).abs() / scoring.DEPTH_SCALE_M)
    pd.testing.assert_series_equal(b.loc[ok, "snow_depth"], expect, check_names=False)
    assert (b.loc[ok, "snow_depth"] != a.loc[ok, "snow_depth"]).any()  # re-scored, not copied
    for col in ("layer_structure", "critical_layers", "uncertainty", "depth_covered", "depth_error_m"):
        pd.testing.assert_series_equal(a[col], b[col])
    assert RunRegistry(paths.registry).get(f"old-{scoring.SCORING_VERSION}") is not None
    with pytest.raises(ValueError, match="already scored"):
        runner.rescore_competition(paths, cfg, f"old-{scoring.SCORING_VERSION}")
