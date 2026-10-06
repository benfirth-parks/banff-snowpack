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
