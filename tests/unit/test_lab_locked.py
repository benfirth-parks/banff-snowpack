"""Locked test winters (ADR-083): the most recent seasons never train or select agents, analogues never draw on them,
and each round's leaders are scored on them after selection; the Training page and the report show the result."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

REPO = Path(__file__).resolve().parents[2]
SEASONS = ("2020-2021", "2021-2022", "2022-2023", "2023-2024")


@pytest.fixture(scope="module")
def locked_run(tmp_path_factory):
    from snowagent.lab.benchmark.builder import build_cases
    from snowagent.lab.competition.runner import EngineSpec
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths
    from snowagent.lab.training.loop import TrainOptions, run_training
    from tests.unit.lab_fixtures import write_multiseason_lab

    tmp = tmp_path_factory.mktemp("locked")
    cfg = load_lab_config(REPO / "config/lab.yaml")
    paths = LabPaths(tmp / "lab")
    write_multiseason_lab(paths.root, tmp / "checkout", cfg, SEASONS)
    build_cases(paths, cfg, tmp / "checkout", exclude_flagged=True)
    opts = TrainOptions.from_config(cfg, rounds=3, population=4, engine=EngineSpec(kind="fake"), locked_seasons=1)
    run_training(paths, cfg, opts, run_id="locked", log=lambda m: None)
    return paths, cfg


def test_locked_seasons_never_train_or_select(locked_run):
    import pandas as pd

    from snowagent.lab.training.loop import load_round, training_root

    paths, _ = locked_run
    d = training_root(paths) / "locked"
    plan = json.loads((d / "run.json").read_text())["plan"]
    assert plan["locked_seasons"] == ["2023-2024"] and plan["seasons"] == list(SEASONS[:3])
    assert plan["monitor_season"] == "2022-2023"  # the warning monitor is a training season
    assert all(c.startswith("BOW_2023") or c.startswith("BOW_2024") for c in plan["locked_case_ids"])
    assert not set(plan["locked_case_ids"]) & set(plan["case_ids"])
    for r in (1, 2, 3):
        train = pd.read_parquet(d / f"rounds/r0{r}/scores.parquet")
        locked = pd.read_parquet(d / f"rounds/r0{r}/locked_scores.parquet")
        assert "2023-2024" not in set(train["season"]) and set(locked["season"]) == {"2023-2024"}
        info = load_round(d, r)["round"]
        tested = {a["agent_id"] for a in info["locked_test"]["agents"]}
        if r == 1:
            assert len(tested) == 5 and any(a["default"] for a in info["locked_test"]["agents"])  # every initial agent
        else:
            assert tested == set(info["survivors_next"])  # the round's leaders, chosen before the test


def test_the_analogue_library_leaves_locked_seasons_out(locked_run):
    from snowagent.lab.training.cache import TrainingCache
    from snowagent.lab.training.evaluate import build_library

    paths, _ = locked_run
    cache = TrainingCache(paths.outputs / "cache")
    full = json.loads(build_library(paths, "all", cache).read_text())
    cut = json.loads(build_library(paths, "all", cache, exclude_seasons=["2023-2024"]).read_text())
    assert {e["season"] for e in full} >= {"2023-2024"} and "2023-2024" not in {e["season"] for e in cut}


def test_resume_keeps_the_locked_split_and_small_selections_lock_nothing(locked_run, tmp_path):
    from snowagent.lab.training.loop import TrainOptions, prepare, split_locked

    paths, cfg = locked_run
    run_id, plan, refs, locked_refs = prepare(paths, cfg, TrainOptions.from_config(cfg), "locked", resume=True)
    assert plan["locked_seasons"] == ["2023-2024"] and len(locked_refs) == len(plan["locked_case_ids"])
    assert TrainOptions.from_config(cfg).locked_seasons == 3  # the default for new runs
    cases = [(None, type("M", (), {"season": s})()) for s in ("2022-2023", "2023-2024", "2024-2025")]
    train, locked, seasons = split_locked(cases, 3)
    assert seasons == [] and len(train) == 3 and not locked  # fewer than n + 2 seasons: none locked


def test_the_check_and_the_command_line(locked_run):
    from snowagent.lab.services.training import training_command
    from snowagent.lab.storage.paths import LabPaths

    cmd = training_command(LabPaths(Path("/x")), Path("/c.yaml"), "r", rounds=2, population=4, survivors=2,
                           mutation_strength=0.2, crossover_share=0.25, seed=0, workers=1, locked_seasons=0)
    assert cmd[cmd.index("--locked-seasons") + 1] == "0"
    src = (REPO / "src/snowagent/lab/training/loso.py").read_text()
    assert '"locked_seasons": 0' in src  # a promotion-check fold holds out only its own season


def test_page_and_report_show_the_locked_score(locked_run, monkeypatch):
    pytest.importorskip("streamlit", reason="lab extra not installed (pip install -e '.[lab]')")
    from streamlit.testing.v1 import AppTest

    from snowagent.lab.services.reports import training_report

    paths, cfg = locked_run
    md = training_report(paths, "locked", cfg.genome).to_markdown()
    assert "Better on winters it never trained on (winter 2023-24)" in md
    assert "## The fair test: winters it never trained on" in md
    md2 = training_report(paths, "locked", cfg.genome, rank=4).to_markdown()
    assert "not tested for this agent" in md2

    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(paths.root))
    at = AppTest.from_file(str(REPO / "lab_app/pages/4_Training.py"), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert any(h.value == "Locked test winters" for h in at.subheader)
    assert len(at.get("plotly_chart")) == 3  # the locked score, best composite, the gap


def test_a_run_can_start_from_an_earlier_runs_agents_and_says_when_they_saw_the_locked_winters(locked_run):
    """ADR-085: --seed-from adds the best evolved agents of an earlier run; when that run trained on the winters the
    new run locks, the plan, the Training page and the report say the locked-winter result is not a clean test."""
    from typer.testing import CliRunner

    from snowagent.cli import app
    from snowagent.lab.schemas.genome import AgentGenome
    from snowagent.lab.services.reports import training_report
    from snowagent.lab.services.training import seen_locked, training_command
    from snowagent.lab.training.loop import load_round, training_root

    paths, cfg = locked_run
    base = ["lab", "train", "--rounds", "2", "--population", "4", "--engine", "fake", "--data-root", str(paths.root),
            "--config", str(REPO / "config/lab.yaml")]
    out = CliRunner().invoke(app, [*base, "--run-id", "src", "--locked-seasons", "0"])  # trains on every winter
    assert out.exit_code == 0, out.stdout
    assert seen_locked(paths, "src", 1) == ["2023-2024"] and seen_locked(paths, "src", 0) == []
    assert seen_locked(paths, "locked", 1) == []  # that run never trained on its locked winter
    out = CliRunner().invoke(app, [*base, "--run-id", "seeded", "--locked-seasons", "1", "--seed-from", "src",
                                   "--seed-top", "2"])
    assert out.exit_code == 0, out.stdout
    assert "initial population adds" in out.stdout

    d = training_root(paths)
    plan = json.loads((d / "seeded" / "run.json").read_text())["plan"]
    top = [x for x in load_round(d / "src", 2)["leaderboard"]["ranked"] if not x["label"].endswith("-default")][:2]
    assert [x["genome_hash"] for x in plan["seeded_from"]] == [x["genome_hash"] for x in top]
    initial = [AgentGenome.model_validate(g).genome_hash for g in plan["initial"]]
    assert len(initial) == 7 and {x["genome_hash"] for x in top} <= set(initial)  # five family defaults + two seeds
    assert plan["seeded_from"][0]["seasons"] == list(SEASONS) and plan["seeded_saw_locked"] == ["2023-2024"]
    out = CliRunner().invoke(app, [*base, "--run-id", "seeded", "--resume"])  # a resume keeps the seeds
    assert out.exit_code == 0, out.stdout

    md = training_report(paths, "seeded", cfg.genome).to_markdown()
    assert "not a clean test" in md and "src" in md
    cmd = training_command(paths, REPO / "config/lab.yaml", "x", rounds=1, population=4, survivors=2,
                           mutation_strength=0.2, crossover_share=0.25, seed=0, workers=1, seed_from="src", seed_top=3)
    assert cmd[-4:] == ["--seed-from", "src", "--seed-top", "3"]


def test_flat_rounds_counts_rounds_without_a_locked_winter_gain():
    from snowagent.lab.training.loop import flat_rounds

    def r(*xs):
        return {"locked_test": {"agents": [{"composite": x} for x in xs]}}

    assert flat_rounds([]) == 0 and flat_rounds([{}]) == 0  # no locked test: nothing to watch
    assert flat_rounds([r(0.50), r(0.52), r(0.5205), r(0.51)]) == 2  # +0.0005 is below the 0.001 bar
    assert flat_rounds([r(0.50), r(0.50), r(0.49, 0.53)]) == 0  # a new best resets the count
    assert flat_rounds([r(0.50), {}, r(0.50)]) == 1  # a round without a locked test is skipped


def test_a_run_stops_on_its_own_when_the_locked_score_is_flat(locked_run, monkeypatch):
    from snowagent.lab.competition.runner import EngineSpec
    from snowagent.lab.services import training as svc
    from snowagent.lab.storage.paths import LabPaths
    from snowagent.lab.training import loop
    from snowagent.lab.training.loop import TrainOptions, run_training, training_root

    paths, cfg = locked_run
    monkeypatch.setattr(loop, "STOP_MIN_GAIN", 10.0)  # no gain can count: the run stops once round 3 is flat
    opts = TrainOptions.from_config(cfg, rounds=6, population=4, engine=EngineSpec(kind="fake"), locked_seasons=1,
                                    stop_when_flat=2)
    res = run_training(paths, cfg, opts, run_id="flat", log=lambda m: None)
    d = training_root(paths) / "flat"
    assert json.loads((d / "run.json").read_text())["plan"]["stop_when_flat"] == 2
    assert len(res.rounds) == 3 and res.summary["rounds"] == 3 and res.summary["rounds_planned"] == 6
    assert res.summary["stopped_early"]["round"] == 3 and res.summary["winner"]["reference"] == "flat/3/1"
    status = json.loads((d / "status.json").read_text())
    assert status["state"] == "finished" and "stopped on its own" in status["message"]
    again = run_training(paths, cfg, TrainOptions.from_config(cfg), run_id="flat", resume=True, log=lambda m: None)
    assert len(again.rounds) == 3  # a resume does not carry on past the stop
    assert TrainOptions.from_config(cfg).stop_when_flat == 8  # the default for new runs
    cmd = svc.training_command(LabPaths(Path("/x")), Path("/c.yaml"), "r", rounds=2, population=4, survivors=2,
                               mutation_strength=0.2, crossover_share=0.25, seed=0, workers=1, stop_when_flat=0)
    assert cmd[cmd.index("--stop-when-flat") + 1] == "0"
