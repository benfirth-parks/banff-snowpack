"""Out-of-area check during training (ADR-115; owner 2026-11-18 "Score only, never train"): each round's tested agents
are also scored on another lab's cases (Jasper), which never train, choose or stop anything; the plan fixes those
cases; the Training page and the summary show the result; the bundle branch brings the area's inputs to a machine
that cannot fetch them."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

REPO = Path(__file__).resolve().parents[2]
SEASONS = ("2020-2021", "2021-2022", "2022-2023", "2023-2024")
AREA_SEASONS = ("2018-2019", "2019-2020")


@pytest.fixture(scope="module")
def runs(tmp_path_factory):
    from snowagent.lab.benchmark.builder import build_cases
    from snowagent.lab.competition.runner import EngineSpec
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths
    from snowagent.lab.training.loop import TrainOptions, run_training
    from tests.unit.lab_fixtures import write_multiseason_lab

    tmp = tmp_path_factory.mktemp("area")
    cfg = load_lab_config(REPO / "config/lab.yaml")
    paths, area = LabPaths(tmp / "data" / "lab"), LabPaths(tmp / "data" / "lab_jasper")
    write_multiseason_lab(paths.root, tmp / "checkout", cfg, SEASONS)
    build_cases(paths, cfg, tmp / "checkout", exclude_flagged=True)
    write_multiseason_lab(area.root, tmp / "area_checkout", cfg, AREA_SEASONS)
    build_cases(area, cfg, tmp / "area_checkout", exclude_flagged=True)
    base = {"rounds": 3, "population": 4, "engine": EngineSpec(kind="fake"), "locked_seasons": 1}
    run_training(paths, cfg, TrainOptions.from_config(cfg, **base), run_id="plain", log=lambda m: None)
    opts = TrainOptions.from_config(cfg, **base, area_check="jasper", area_config=str(REPO / "config/lab.yaml"),
                                    area_data_root=str(area.root))
    run_training(paths, cfg, opts, run_id="area", log=lambda m: None)
    return paths, area, cfg


def test_area_cases_are_scored_each_round_and_never_steer(runs):
    import pandas as pd

    from snowagent.lab.training.loop import load_round, training_root

    paths, area, _ = runs
    d, plain = training_root(paths) / "area", training_root(paths) / "plain"
    plan = json.loads((d / "run.json").read_text())["plan"]
    ac = plan["area_check"]
    assert ac["lab"] == "jasper" and ac["seasons"] == list(AREA_SEASONS) and ac["case_ids"]
    assert not set(ac["case_ids"]) & set(plan["case_ids"])  # not one area case trains
    for r in (1, 2, 3):
        info, base = load_round(d, r)["round"], load_round(plain, r)["round"]
        # the same agents survive with and without the check: it never chooses
        assert info["survivors_next"] == base["survivors_next"] and info["best"] == base["best"]
        assert {a["agent_id"] for a in info["area_check"]["agents"]} == {a["agent_id"] for a in
                                                                         info["locked_test"]["agents"]}
        scores = pd.read_parquet(d / f"rounds/r0{r}/area_scores.parquet")
        assert set(scores["season"]) == set(AREA_SEASONS) and set(scores["case_id"]) == set(ac["case_ids"])
        assert info["area_check"]["note"].startswith("pits from outside the training area")
    summary = json.loads((d / "summary.json").read_text())
    assert summary["area_check"]["incumbent"]["label"] == "snowpack-default"
    assert [x["round"] for x in summary["area_check"]["best_per_round"]] == [1, 2, 3]
    assert json.loads((plain / "summary.json").read_text())["area_check"] is None


def test_overview_and_command_carry_the_check(runs):
    from snowagent.lab.services.training import run_overview, training_command

    paths, _, _ = runs
    ov = run_overview(paths, "area")
    assert ov["area"]["cases"] and ov["area"]["incumbent"]["label"] == "snowpack-default"
    assert ov["rounds"]["area_composite"].notna().all()
    assert run_overview(paths, "plain")["area"] is None
    cmd = training_command(paths, REPO / "config/lab.yaml", "x", rounds=1, population=2, survivors=1,
                           mutation_strength=0.2, crossover_share=0.25, seed=0, workers=1, area_check="jasper")
    assert cmd[-2:] == ["--area-check", "jasper"]


def test_resume_keeps_the_area_cases_and_skips_a_missing_area(runs, tmp_path):
    from snowagent.lab.competition.runner import EngineSpec
    from snowagent.lab.training.loop import TrainOptions, _area_context, prepare

    paths, area, cfg = runs
    _, plan, _, _ = prepare(paths, cfg, TrainOptions.from_config(cfg), "area", resume=True)
    a, ctx = _area_context(plan, cfg.scoring_weights, EngineSpec(kind="fake"), None, lambda m: None)
    assert sorted(r.case_id for r in a.refs) == plan["area_check"]["case_ids"] and ctx.paths.root == area.root
    gone = plan | {"area_check": plan["area_check"] | {"data_root": str(tmp_path / "nowhere")}}
    said = []
    assert _area_context(gone, cfg.scoring_weights, EngineSpec(kind="fake"), None, said.append) == (None, None)
    assert "skipped" in said[0]


def test_a_new_run_refuses_an_area_without_cases(runs, tmp_path):
    from snowagent.lab.training.loop import TrainOptions, prepare

    paths, _, cfg = runs
    opts = TrainOptions.from_config(cfg, rounds=1, population=4, area_check="jasper",
                                    area_config=str(REPO / "config/lab.yaml"), area_data_root=str(tmp_path / "empty"))
    with pytest.raises(ValueError, match="prepare them first"):
        prepare(paths, cfg, opts)
    with pytest.raises(ValueError, match="--area-check"):
        TrainOptions.from_config(cfg, area_check="mars").validate()


def test_area_locations_default_beside_the_runs_own():
    from snowagent.lab.training.area_check import resolve, status

    cpath, root = resolve("jasper", REPO / "data" / "lab")
    assert cpath == REPO / "config" / "lab_jasper.yaml" and root == REPO / "data" / "lab_jasper"
    assert status("jasper", Path("/nonexistent/data/lab"))["cases"] == 0


def test_area_setup_job_runs_prepare_import_and_build_with_the_area_config():
    from snowagent.lab.services.workflow import area_setup_steps

    steps = area_setup_steps(REPO)
    assert [s["name"] for s in steps] == ["prepare-area", "init", "import", "build-cases"]
    imp = steps[2]["command"]
    assert imp[imp.index("--config") + 1].endswith("config/lab_jasper.yaml")
    assert imp[imp.index("--data-root") + 1].endswith("data/lab_jasper")
    assert imp[imp.index("--profiles-file") + 1].endswith("data/lab_jasper/observed_profiles.jsonl")


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_bundle_restore_adds_months_once_and_refreshes_station_tables(tmp_path):
    from snowagent.lab.services.prepare import restore_area_bundle

    remote = tmp_path / "remote"
    remote.mkdir()
    _git(remote, "init", "-q", "-b", "claude/lab-jasper-data")
    (remote / "era5_north_west").mkdir()
    (remote / "era5_north_west" / "era5_box_201901.npz").write_bytes(b"month")
    (remote / "era5_north_west" / "era5_box_z.npz").write_bytes(b"z")
    (remote / "stations" / "big_bend").mkdir(parents=True)
    (remote / "stations" / "big_bend" / "big_bend.csv").write_text("new\n")
    (remote / "other.txt").write_text("not restored")
    _git(remote, "add", ".")
    _git(remote, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "bundle")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "remote", "add", "origin", str(remote))
    st = repo / "data/interim/new_sites/stations/big_bend"
    st.mkdir(parents=True)
    (st / "big_bend.csv").write_text("old\n")
    rep = restore_area_bundle("jasper", repo, log=lambda m: None)
    assert rep["era5_months_added"] == 1 and rep["station_files_updated"] == 1
    assert (repo / "data/interim/era5_north_west/era5_box_201901.npz").read_bytes() == b"month"
    assert (st / "big_bend.csv").read_text() == "new\n" and not (repo / "other.txt").exists()
    (repo / "data/interim/era5_north_west/era5_box_201901.npz").write_bytes(b"kept")
    again = restore_area_bundle("jasper", repo, log=lambda m: None)
    assert again["era5_months_added"] == 0 and again["station_files_updated"] == 0
    assert (repo / "data/interim/era5_north_west/era5_box_201901.npz").read_bytes() == b"kept"  # never overwritten
    _git(repo, "remote", "set-url", "origin", str(tmp_path / "missing"))
    assert "error" in restore_area_bundle("jasper", repo, log=lambda m: None)


def test_the_training_report_has_a_parker_ridge_section_only_with_the_check(runs):
    from snowagent.lab.services.reports import training_report

    paths, _, cfg = runs
    md = training_report(paths, "area", cfg.genome).to_markdown()
    assert "## Somewhere it has never been: Parker Ridge in Jasper" in md and "Verdict at Parker Ridge" in md
    assert "Parker Ridge" not in training_report(paths, "plain", cfg.genome).to_markdown()
