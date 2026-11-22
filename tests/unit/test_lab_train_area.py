"""Training on another area's pits with a plot left out (ADR-137; owner 2026-11-22: "can we try training at Parkers and
some others and then test at simpson?"): the area's pits are training cases beside the run's own, its locked winters
join the locked test, and a plot left out with --plots never trains, selects or moves its own genes."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

REPO = Path(__file__).resolve().parents[2]
SEASONS = ("2020-2021", "2021-2022", "2022-2023", "2023-2024")
AREA_SEASONS = ("2021-2022", "2022-2023", "2023-2024")


@pytest.fixture(scope="module")
def run(tmp_path_factory):
    from snowagent.lab.benchmark.builder import build_cases
    from snowagent.lab.competition.runner import EngineSpec
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths
    from snowagent.lab.training.loop import TrainOptions, run_training
    from tests.unit.lab_fixtures import write_multiseason_lab

    tmp = tmp_path_factory.mktemp("trainarea")
    cfg = load_lab_config(REPO / "config/lab.yaml")
    paths, area = LabPaths(tmp / "data" / "lab"), LabPaths(tmp / "data" / "lab_jasper")
    write_multiseason_lab(paths.root, tmp / "checkout", cfg, SEASONS)
    build_cases(paths, cfg, tmp / "checkout", exclude_flagged=True)
    write_multiseason_lab(area.root, tmp / "area_checkout", cfg, AREA_SEASONS)
    build_cases(area, cfg, tmp / "area_checkout", exclude_flagged=True)
    opts = TrainOptions.from_config(cfg, rounds=3, population=4, engine=EngineSpec(kind="fake"), locked_seasons=1,
                                    plots=["BOW", "GOAT"], train_area="jasper",
                                    area_config=str(REPO / "config/lab.yaml"), area_data_root=str(area.root))
    run_training(paths, cfg, opts, run_id="tarea", log=lambda m: None)
    return paths, area, cfg


def test_area_pits_train_and_its_locked_winter_is_tested(run):
    import pandas as pd

    from snowagent.lab.training.loop import load_round, training_root

    paths, _, _ = run
    d = training_root(paths) / "tarea"
    plan = json.loads((d / "run.json").read_text())["plan"]
    ta = plan["train_area"]
    assert ta["lab"] == "jasper" and ta["case_ids"] and ta["locked_case_ids"]
    assert plan["locked_seasons"] == ["2023-2024"] and "area_check" not in plan
    for r in (1, 2, 3):
        scores = pd.read_parquet(d / f"rounds/r0{r}/scores.parquet")
        assert set(ta["case_ids"]) <= set(scores["case_id"])  # the area trains
        assert not set(ta["locked_case_ids"]) & set(scores["case_id"])  # its locked winter never does
        locked = pd.read_parquet(d / f"rounds/r0{r}/locked_scores.parquet")
        assert set(ta["locked_case_ids"]) <= set(locked["case_id"])
        assert set(locked["season"]) == {"2023-2024"}
        assert load_round(d, r)["round"]["train_area"]["cases"] == len(ta["case_ids"])


def test_the_left_out_plot_never_trains_and_keeps_its_genes(run):
    import pandas as pd

    from snowagent.lab.schemas.genome import AgentGenome
    from snowagent.lab.training.loop import load_round, training_root

    paths, _, cfg = run
    d = training_root(paths) / "tarea"
    plan = json.loads((d / "run.json").read_text())["plan"]
    held = plan["held_out_genes"]
    assert "sp_precip_mult_simp" in held and "sp_ta_offset_simp_k" in held and "sp_precip_mult_bow" not in held
    banff = [c for c in plan["case_ids"] if c not in set(plan["train_area"]["case_ids"])]
    assert banff and not any("SIMP" in c.upper() for c in banff)
    for fam, genes in plan["trimmed_genes"].items():
        assert not set(genes) & set(held), fam
    for r in (1, 2, 3):
        df = pd.read_parquet(d / f"rounds/r0{r}/scores.parquet")
        own = df[df["case_id"].isin(banff)]
        assert set(own["site_code"].astype(str)) <= {"BOW", "GOAT"}
        for p in load_round(d, r)["population"]:
            g = AgentGenome.model_validate(p["genome"])
            for n, (_b, gs) in cfg.genome.family_genes(g.family).items():
                if n in held:
                    assert g.genes.get(n, gs.default) == gs.default, (r, g.agent_id, n)


def test_resume_and_bad_options(run):
    from snowagent.lab.training.loop import TrainOptions, prepare

    paths, _, cfg = run
    _, plan, _, _ = prepare(paths, cfg, TrainOptions.from_config(cfg), "tarea", resume=True)
    assert plan["train_area"]["lab"] == "jasper"
    with pytest.raises(ValueError, match="--train-area"):
        TrainOptions.from_config(cfg, train_area="mars").validate()
    with pytest.raises(ValueError, match="both"):
        TrainOptions.from_config(cfg, train_area="jasper", area_check="jasper").validate()


def test_no_plots_option_holds_no_genes():
    from snowagent.lab.genome import default_genome, default_genomes
    from snowagent.lab.schemas.genome import AgentFamily
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.training.loop import held_out_plot_genes

    cfg = load_lab_config(REPO / "config/lab.yaml")
    init = default_genomes(cfg.genome)
    assert held_out_plot_genes(cfg, None, init) == []
    assert held_out_plot_genes(cfg, ["BOW", "GOAT", "SIMP"], init) == []
    assert "sp_precip_mult_goat" in held_out_plot_genes(cfg, ["BOW", "SIMP"], init)
    process = [default_genome(AgentFamily("process"), cfg.genome)]
    assert held_out_plot_genes(cfg, ["BOW", "GOAT"], process) == ["snow_mult_simp"]
