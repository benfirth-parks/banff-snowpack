"""ADR-133: a training run with SNOWPACK agents starts from the best standard SNOWPACK and is measured against it.

The best standard joins round 1 beside plain SNOWPACK; the drift penalty and the evenness yardstick count from its
settings; the Watch the run page, the training report and the promotion check compare with it. Older plans, and new
runs started with ``--standard none``, keep plain SNOWPACK."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

from snowagent.lab.genome import default_genome, make_genome, named_genome
from snowagent.lab.schemas.genome import AgentFamily

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

REPO = Path(__file__).resolve().parents[2]
SEASONS = ("2021-2022", "2022-2023", "2023-2024")
PLAN = {"rounds": 1, "population": 4, "survivors": 2, "mutation_strength": 0.2, "crossover_share": 0.25,
        "seed": 0, "plots": None, "case_types": None, "case_set": "all", "splits": None, "initial": [],
        "monitor_season": None, "gap_flag_rounds": 3, "gap_tolerance": 0.0, "max_redraws": 100,
        "engine": {"kind": "fake"}}


def test_drift_counts_from_the_standard_so_its_fixes_cost_nothing():
    """Under plain SNOWPACK's settings the best standard's three fixes count as about 2.75 units of drift, a
    0.55-point handicap at the default penalty: as large as the agents' whole lead over it on unseen winters."""
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.training.loop import DRIFT_K, drift, rank_agents
    from tests.unit.test_lab_selection import _rows

    cfg = load_lab_config()
    spec, w = cfg.genome, cfg.scoring_weights
    plain, std = default_genome(AgentFamily.snowpack, spec), named_genome("snowpack-era5-fix", spec)
    assert drift(std, spec) == pytest.approx(2.75)  # two choices and new-snow weak factor 1.0 -> 0.4 of 0.2..1.0
    assert drift(std, spec, std) == 0.0 and drift(plain, spec, std) == pytest.approx(2.75)
    _, wind = spec.family_genes(AgentFamily.snowpack)["sp_wind_mult"]
    moved = make_genome(AgentFamily.snowpack, std.genes | {"sp_wind_mult": wind.min}, spec)
    assert drift(moved, spec, std) == pytest.approx((wind.default - wind.min) / (wind.max - wind.min))
    col = default_genome(AgentFamily.column, spec)
    assert drift(col, spec, std) == drift(col, spec) == 0.0  # another family keeps its own defaults

    # an agent with the fixes that scores a little better than plain SNOWPACK loses under the old anchor
    df = pd.DataFrame([r for a, v in ((plain.agent_id, 0.500), (moved.agent_id, 0.504))
                       for r in _rows(a, {("2020-2021", "BOW"): v})])
    old = rank_agents(df, w, [plain, moved], drift_k=DRIFT_K, spec=spec)
    new = rank_agents(df, w, [plain, moved], drift_k=DRIFT_K, spec=spec, standard=std)
    assert old[0]["agent_id"] == plain.agent_id and new[0]["agent_id"] == moved.agent_id


def test_new_runs_have_the_best_standard_and_old_plans_plain_snowpack():
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.training.loop import (
        LOCKED_REFERENCE,
        STANDARD,
        TrainOptions,
        _opts_from_plan,
        run_standard,
        standard_label,
        uses_standard,
    )

    cfg = load_lab_config()
    o = TrainOptions.from_config(cfg)
    assert o.standard == STANDARD == LOCKED_REFERENCE == "snowpack-era5-fix"
    with pytest.raises(ValueError, match="standard SNOWPACK"):
        TrainOptions.from_config(cfg, standard="column-old-facets").validate()
    assert uses_standard(o, [default_genome(AgentFamily.snowpack)])
    assert not uses_standard(o, [default_genome(AgentFamily.column)])  # no SNOWPACK agent to start from it
    assert _opts_from_plan(PLAN).standard is None  # a run started before ADR-133 resumes unchanged
    assert run_standard(PLAN) is None and standard_label(PLAN) == "snowpack-default"
    p = PLAN | {"standard": "snowpack-era5-fix"}
    assert _opts_from_plan(p).standard == "snowpack-era5-fix"
    assert run_standard(p).genome_hash == named_genome("snowpack-era5-fix").genome_hash


def test_the_cli_turns_the_standard_off_with_none():
    from snowagent.lab.cli import _train_options
    from snowagent.lab.settings import load_lab_config

    cfg = load_lab_config()
    args = (cfg, 1, 4, 2, None, None, 0, None, None, None, None, None, "fake", None)
    assert _train_options(*args).standard == "snowpack-era5-fix"
    assert _train_options(*args, standard="none").standard is None
    assert _train_options(*args, standard="snowpack-new-snow-check").standard == "snowpack-new-snow-check"


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    from snowagent.lab.benchmark import builder
    from snowagent.lab.competition.runner import EngineSpec
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths
    from snowagent.lab.training.loop import TrainOptions, run_training
    from tests.unit.lab_fixtures import write_multiseason_lab

    tmp = tmp_path_factory.mktemp("standard")
    cfg = load_lab_config(REPO / "config/lab.yaml")
    paths = LabPaths(tmp / "lab")
    write_multiseason_lab(paths.root, tmp / "checkout", cfg, SEASONS)
    builder.build_cases(paths, cfg, tmp / "checkout", exclude_flagged=True)
    base = {"rounds": 2, "population": 5, "engine": EngineSpec(kind="fake"), "locked_seasons": 1}
    new = run_training(paths, cfg, TrainOptions.from_config(cfg, **base), run_id="new", log=lambda m: None)
    old = run_training(paths, cfg, _plain(TrainOptions.from_config(cfg, **base)), run_id="old", log=lambda m: None)
    return paths, cfg, new, old


def _plain(o):
    from dataclasses import replace

    return replace(o, standard=None)


def test_round_one_has_the_standard_and_the_page_and_report_compare_with_it(runs):
    from snowagent.lab.services.reports import training_report
    from snowagent.lab.services.training import run_overview
    from snowagent.lab.training.loop import load_round

    paths, cfg, new, old = runs
    plan = json.loads((new.run_dir / "run.json").read_text())["plan"]
    assert plan["standard"] == "snowpack-era5-fix"
    labels = [g["label"] for g in plan["initial"]]
    assert "snowpack-default" in labels and "snowpack-era5-fix" in labels
    r1 = load_round(new.run_dir, 1)["round"]
    assert "snowpack-era5-fix" in {a["label"] for a in r1["locked_test"]["agents"]}
    ov = run_overview(paths, "new")
    assert ov["locked"]["incumbent"]["label"] == "snowpack-era5-fix"
    ranked = load_round(new.run_dir, 1)["leaderboard"]["ranked"]
    rank = next(i for i, x in enumerate(ranked, 1) if x["label"] == "snowpack-default")
    rep = training_report(paths, "new", cfg.genome, round_no=1, rank=rank).to_markdown()
    assert "Compared with | best standard SNOWPACK" in rep or "best standard SNOWPACK" in rep.split("\n## ")[0]

    oplan = json.loads((old.run_dir / "run.json").read_text())["plan"]
    assert "standard" not in oplan  # the old plan, unchanged (same plan hash as before ADR-133)
    assert "snowpack-era5-fix" not in [g["label"] for g in oplan["initial"]]
    assert run_overview(paths, "old")["locked"]["incumbent"]["label"] == "snowpack-default"
