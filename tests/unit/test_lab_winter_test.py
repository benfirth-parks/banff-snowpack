"""The winter-by-winter test (ADR-125): winters grouped into folds, the locked test winters kept out of every fold,
a held-out group's pits never visible to training, and per-winter scores with their average and spread."""

from __future__ import annotations

import json
import shutil

import pandas as pd
import pytest
from typer.testing import CliRunner

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.lab.benchmark import builder  # noqa: E402
from snowagent.lab.benchmark.loader import case_dirs, read_manifest  # noqa: E402
from snowagent.lab.competition import truth as truth_mod  # noqa: E402
from snowagent.lab.competition.runner import EngineSpec  # noqa: E402
from snowagent.lab.genome import default_genome  # noqa: E402
from snowagent.lab.schemas.benchmark import SplitMode  # noqa: E402
from snowagent.lab.schemas.genome import AgentFamily  # noqa: E402
from snowagent.lab.services.winter_test import (  # noqa: E402
    plain_summary,
    resume_winter_args,
    signed,
    took,
    winter_args,
)
from snowagent.lab.settings import holdout_group, load_lab_config  # noqa: E402
from snowagent.lab.storage.paths import LabPaths  # noqa: E402
from snowagent.lab.training import loso  # noqa: E402
from snowagent.lab.training.loop import TrainOptions, committed_rounds, run_training  # noqa: E402
from tests.unit.lab_fixtures import write_multiseason_lab  # noqa: E402
from tests.unit.test_lab_benchmark import CONFIG  # noqa: E402

SEASONS = ("2018-2019", "2019-2020", "2020-2021", "2021-2022", "2022-2023", "2023-2024")
LOCKED = ["2021-2022", "2022-2023", "2023-2024"]  # the three most recent: kept out
HELD = ["2018-2019", "2019-2020", "2020-2021"]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("winter")
    cfg = load_lab_config(CONFIG)
    paths = LabPaths(root / "lab")
    write_multiseason_lab(paths.root, root / "checkout", cfg, SEASONS)
    builder.build_cases(paths, cfg, root / "checkout", exclude_flagged=True)
    return cfg, root


@pytest.fixture()
def lab(built, tmp_path):
    cfg, root = built
    shutil.copytree(root / "lab", tmp_path / "lab")
    return cfg, LabPaths(tmp_path / "lab"), root / "checkout"


def opts(cfg, **kw) -> TrainOptions:
    return TrainOptions.from_config(cfg, **({"rounds": 2, "population": 4, "engine": EngineSpec(kind="fake"),
                                             "initial": [default_genome(AgentFamily.persistence, cfg.genome),
                                                         default_genome(AgentFamily.weather_rule, cfg.genome)]}
                                            | kw))


def quiet(_msg: str) -> None:
    pass


def test_fold_groups_spread_winters_over_the_years():
    s = [f"{y}-{y + 1}" for y in range(2000, 2011)]
    g = loso.fold_groups(s, 5)
    assert len(g) == 5 and sorted(x for grp in g for x in grp) == s
    assert g[0] == ["2000-2001", "2005-2006", "2010-2011"] and g[4] == ["2004-2005", "2009-2010"]
    assert loso.fold_groups(s, None) == [[x] for x in s] == loso.fold_groups(s, 20)
    with pytest.raises(ValueError):
        loso.fold_groups(s, 1)
    assert loso.fold_labels([["a"], ["b"]]) == ["a", "b"] and loso.fold_labels([["a", "c"], ["b"]]) == \
        ["fold1of2", "fold2of2"]


def test_a_group_of_seasons_is_held_out_together():
    cfg = load_lab_config(CONFIG)
    sp = cfg.splits.model_copy(update={"mode": SplitMode.loso})
    key = "2015-2016+2018-2019"
    assert holdout_group(key) == ["2015-2016", "2018-2019"] and holdout_group(None) == []
    assert sp.assign("2018-2019", key).value == "holdout" and sp.assign("2016-2017", key).value == "training"
    assert sp.case_set(key) == f"loso_{key}"
    ms = sp.mode_seasons(key)
    assert ms["holdout"] == ["2015-2016", "2018-2019"] and "2018-2019" not in ms["training"]
    with pytest.raises(ValueError):
        sp.holdout("2015-2016+1900-1901")


def test_excluded_seasons_never_reach_a_training_run(lab):
    cfg, paths, _ = lab
    tr = run_training(paths, cfg, opts(cfg, exclude_seasons=LOCKED, locked_seasons=0), run_id="x", log=quiet)
    plan = json.loads((tr.run_dir / "run.json").read_text())["plan"]
    assert plan["excluded_seasons"] == LOCKED and not set(plan["seasons"]) & set(LOCKED)
    for r in committed_rounds(tr.run_dir):
        sc = pd.read_parquet(tr.run_dir / "rounds" / f"r{r:02d}" / "scores.parquet")
        assert not set(sc["season"]) & set(LOCKED)
    # a plan without the option keeps its old form (no key), so earlier runs and their resumes are unchanged
    tr2 = run_training(paths, cfg, opts(cfg, locked_seasons=0, rounds=1), run_id="y", log=quiet)
    assert "excluded_seasons" not in json.loads((tr2.run_dir / "run.json").read_text())["plan"]


def test_winter_test_end_to_end(lab, monkeypatch):
    cfg, paths, source = lab
    reads = []
    real_truth = truth_mod.load_hidden_truth

    def spy(case_dir, *a, **k):
        m = read_manifest(case_dir)
        reads.append((m.case_set, m.split.value, m.season))
        return real_truth(case_dir, *a, **k)

    monkeypatch.setattr(truth_mod, "load_hidden_truth", spy)
    res = loso.check_loso(paths, cfg, None, opts(cfg), source=source, log=quiet, check_id="wt", folds=2,
                          exclude_locked=True)
    assert [f["season"] for f in res.folds] == ["fold1of2", "fold2of2"]
    assert [f["seasons"] for f in res.folds] == [["2018-2019", "2020-2021"], ["2019-2020"]]
    # the locked winters: never read, as training or held out, in any fold
    assert reads and not {r[2] for r in reads} & set(LOCKED)
    # a group's training cases never see the group's pits; its held-out cases are its own winters only
    key = "2018-2019+2020-2021"
    for d in case_dirs(paths, f"loso_{key}"):
        m = read_manifest(d)
        assert m.holdout_season == key
        if m.split.value == "holdout":
            assert m.season in holdout_group(key)
        elif m.season in ("2019-2020",) or m.season > "2020-2021":
            assert m.excluded_counts.get("profiles:holdout_season", 0) > 0
    for f in res.folds:
        fold_run = paths.outputs / "training" / f"wt-{f['season']}"
        plan = json.loads((fold_run / "run.json").read_text())["plan"]
        assert plan["excluded_seasons"] == LOCKED and not set(plan["seasons"]) & (set(LOCKED) | set(f["seasons"]))
        assert f["start"]["family"] in ("persistence", "weather_rule") and f["start_composite"] is not None
        assert f["winner_training_composite"] is not None and f["start_training_composite"] is not None
        assert f["incumbent"]["label"] == "snowpack-era5-fix"  # the bar: the best standard SNOWPACK
    r = res.result
    assert r["kind"] == loso.WINTER_TEST and r["excluded_seasons"] == LOCKED and r["folds"] == 2
    assert sorted(w["season"] for w in r["per_winter"]) == HELD
    assert all(w["evolved"] is not None and w["snowpack"] is not None and w["start"] is not None
               for w in r["per_winter"])
    assert r["vs_snowpack"]["winters"] == 3 and r["training_gain"]["winters"] == 3
    assert r["wins"] + r["losses"] + r["ties"] == 3  # counted per winter, not per fold
    assert r["memorising_gap"] == pytest.approx(r["gain_on_training_winters"] - r["pooled_training_gain"], abs=2e-4)
    assert r["checked_agent_id"] is None
    assert loso.list_checks(paths, loso.WINTER_TEST) == ["wt"] and loso.list_checks(paths, "promotion") == []
    text = " ".join(plain_summary(r))
    assert "3 winters" in text and "locked test winters" in text
    # the computing time: the rounds' own records and the time the two groups' cases took to prepare
    assert r["work_s"] > 0 and r["build_s"] > 0 and "to prepare the cases." in took(r)
    # resumable: finished folds are not re-run; the page's resume options rebuild the same plan
    monkeypatch.setattr(loso, "run_training", lambda *a, **k: (_ for _ in ()).throw(AssertionError("re-ran")))
    again = loso.check_loso(paths, cfg, None, opts(cfg), source=source, log=quiet, check_id="wt", folds=2,
                            exclude_locked=True)
    assert again.result["per_winter"] == r["per_winter"]
    assert again.result["build_s"] == r["build_s"] and again.result["work_s"] == r["work_s"]  # nothing counted twice
    plan = loso.load_check(paths, "wt")["check"]["plan"]
    args = resume_winter_args(plan, workers=2)
    assert args[args.index("--folds") + 1] == "2" and args.count("--initial") == 2 and "--include-locked" not in args
    assert [args[i + 1] for i, x in enumerate(args) if x == "--season"] == HELD and "--plots" not in args


def test_a_locked_winter_cannot_be_held_out(lab):
    cfg, paths, source = lab
    with pytest.raises(ValueError, match="locked"):
        loso.check_loso(paths, cfg, None, opts(cfg), source=source, log=quiet, seasons=["2023-2024"],
                        exclude_locked=True, estimate_only=True)


def test_winter_test_estimate_from_the_command_line(lab):
    from snowagent.cli import app

    cfg, paths, source = lab
    out = CliRunner().invoke(app, ["lab", "winter-test", "--initial", "persistence", "--rounds", "1",
                                   "--population", "3", "--folds", "2", "--engine", "fake", "--estimate-only",
                                   "--source", str(source), "--data-root", str(paths.root),
                                   "--config", str(CONFIG)])
    assert out.exit_code == 0, out.output
    assert "kept out of every fold" in out.output and "2 folds (3 winters dealt out in turn" in out.output
    est = json.loads(out.output[out.output.index("{"):])
    assert set(est["folds"]) == {"2018-2019+2020-2021", "2019-2020"}


def test_estimates_count_only_what_will_run(lab):
    """ADR-125: a later round is estimated for the families its children can have (a column-only training runs no
    SNOWPACK), and a case set to build at what this computer's own build took."""
    from snowagent.lab.training.estimate import Timings, child_families, estimate_child_rounds

    cfg, paths, _ = lab
    col, sp = default_genome(AgentFamily.column, cfg.genome), default_genome(AgentFamily.snowpack, cfg.genome)
    assert child_families([col]) == ["column"]
    assert child_families([col.model_dump(mode="json"), sp]) == ["column", "snowpack"]  # genomes or their stored form
    assert child_families([col], family_slots=True) is None and child_families([]) is None  # any family
    t = Timings()  # nothing measured yet: the defaults
    lo, hi = estimate_child_rounds(100, 8, t, workers=4, physics=True, families=["column"])
    assert lo == hi == pytest.approx((100 * t.load + 100 * 8 * t.agent("column")) / 4)
    # any family, or one that runs the engine: up to a SNOWPACK run for every child and case on top
    assert estimate_child_rounds(100, 8, t, workers=4, physics=True)[1] == pytest.approx(hi + 100 * 8 * t.engine / 4)
    assert estimate_child_rounds(100, 8, t, workers=4, physics=True, families=["column", "snowpack"])[1] > hi
    took_s = json.loads((paths.benchmark / "all" / "build_report.json").read_text())["runtime_s"]
    assert loso.build_seconds(paths) == pytest.approx(took_s if took_s > 0 else loso.BUILD_S)
    assert loso.build_seconds(LabPaths(paths.root / "nowhere")) == loso.BUILD_S  # no build on record


def test_spread_and_plain_words():
    sp = loso.spread([0.01, -0.02, 0.03, 0.0])
    assert sp["winters"] == 4 and sp["better"] == 2 and sp["worse"] == 1 and sp["lowest"] == -0.02
    assert sp["range90"][0] <= sp["mean"] <= sp["range90"][1]
    assert loso.spread([None])["winters"] == 0
    r = {"pooled_evolved_composite": 0.6, "pooled_incumbent_composite": 0.58, "pooled_start_composite": 0.59,
         "vs_snowpack": sp, "training_gain": loso.spread([-0.01, -0.02, -0.03]), "memorising_gap": 0.05,
         "gain_on_training_winters": 0.03, "pooled_training_gain": -0.02, "excluded_seasons": [],
         "per_winter": [{"season": "2019-2020", "cases": 2}, {"season": "2020-2021", "cases": 30}]}
    text = " ".join(plain_summary(r))
    assert "60.0" in text and "58.0" in text and "makes things worse" in text
    assert "by +3.0 points; on the pits of winters they had not seen, by -2.0" in text
    assert "5.0 points between" in text and "-2.0 points over all the held-out pits" in text
    assert "1 of the winters have fewer than 5 pits" in text
    assert "No clear sign of memorising" in " ".join(plain_summary(r | {"memorising_gap": 0.004}))
    assert [signed(x) for x in (0.012, -0.004, -0.0004, 0.0, None)] == ["+1.2", "-0.4", "0.0", "0.0", "-"]
    assert took({"work_s": 6720.0, "round_s_median": 190.0, "build_s": 400.0}) == \
        "Computing time: 1 h 52 min (each training round: 3 min), plus 6 min to prepare the cases."
    assert took({"work_s": 6720.0}) == "Computing time: 1 h 52 min." and took({}) is None
    assert winter_args(initial=["column"], folds=0, rounds=6, population=10, survivors=2)[-3:] == \
        ["--every-winter", "--initial", "column"]


def test_the_estimate_reads_as_a_sentence(tmp_path):
    """The page says a finished estimate in words: how long in all, how long each group of winters takes, and the
    cases to prepare first with the disk they need."""
    from snowagent.lab.services.winter_test import estimate_words

    est = {"builds": ["2019-2020+2021-2022", "2020-2021+2022-2023"], "build_s": 420.0, "workers": 8,
           "folds": {"2019-2020+2021-2022": {"low_s": 3000.0, "high_s": 3600.0},
                     "2020-2021+2022-2023": {"low_s": 3000.0, "high_s": 3600.0}},
           "total_low_s": 6420.0, "total_high_s": 7620.0}
    log = tmp_path / "estimate.log"

    def words(e: dict | None) -> str | None:
        log.write_text("winter test estimate (measured timings, 8 workers): 2 folds\n"
                       + (json.dumps(e, indent=1) if e else "no estimate here") + "\n")
        return estimate_words({"state": "finished", "log": str(log)})

    assert words(est) == (
        "Between 1 h 47 min and 2 h 07 min on this computer with 8 workers: about 55 min for each of the 2 groups of "
        "winters. That includes 7 min at the start to prepare the cases (each needs its own copy; about 0.7 GB of "
        "disk).")
    part = words(est | {"builds": est["builds"][:1], "build_s": 210.0})
    assert "to prepare the cases of 1 of them (the rest are ready; about 0.3 GB of disk)." in part
    # one winter a fold, nothing to build, and a narrow estimate: one figure, no word on preparing cases
    one = words({"builds": [], "build_s": 0.0, "workers": 4, "total_low_s": 1200.0, "total_high_s": 1210.0,
                 "folds": {"2019-2020": {"low_s": 600.0, "high_s": 605.0},
                           "2020-2021": {"low_s": 600.0, "high_s": 605.0}}})
    assert one == "About 20 min on this computer with 4 workers: about 10 min for each of the 2 winters."
    assert words(None) is None  # a log with no estimate in it
    assert estimate_words({"state": "running", "log": str(log)}) is None and estimate_words(None) is None


def test_the_bar_shows_a_running_test_as_one_line_with_its_part_and_time_left(tmp_path):
    """ADR-125: a winter test (or a promotion check) re-trains part after part; the bar at the top of the app says
    which part it is on and when it should end, and does not list the part's training as a run of its own."""
    import os
    import sys
    import time
    from datetime import UTC, datetime

    from snowagent.lab.services import activity as act
    from snowagent.lab.services.jobs import job_info, start_job, step, stop_job

    paths = LabPaths(tmp_path / "lab")
    cdir = loso.checks_root(paths) / "wt"
    cdir.mkdir(parents=True)
    (cdir / "status.json").write_text(json.dumps({"state": "running", "phase": "building",
                                                  "seasons": ["fold1of2", "fold2of2"], "done": []}))
    now = datetime.now(UTC)
    assert act._check_line(paths, "wt", now) == ("getting the cases of its 2 parts ready", 0.0)
    assert act._check_line(paths, "no-such-test", now) == ("", None)
    (cdir / "status.json").write_text(json.dumps({"state": "running", "phase": "fold 2/2: training without x",
                                                  "seasons": ["fold1of2", "fold2of2"], "done": ["fold1of2"]}))
    assert act._check_line(paths, "wt", now) == ("part 2 of 2", 0.5)  # nothing training yet: no time to give
    run = paths.outputs / "training" / "wt-fold2of2"
    run.mkdir(parents=True)
    (run / "run.json").write_text(json.dumps({"created_at": now.isoformat(),
                                              "plan": {"rounds": 2, "case_set": "loso_x", "population": 4}}))
    (run / "status.json").write_text(json.dumps({"state": "running", "pid": os.getpid(), "round": 1, "done": 2,
                                                 "total": 4, "round_started_at": now.isoformat(),
                                                 "estimate_s": 600}))
    text, share = act._check_line(paths, "wt", now)
    assert text.startswith("part 2 of 2, about ") and "left, done around" in text
    assert share == pytest.approx((1 + 0.25) / 2)  # part 1 done; part 2 half way through round 1 of 2
    # started from Terminal there is no job: the part's training is what the bar can show, as before
    (item,) = act.activity(paths)
    assert item["what"] == "Training" and item["run_id"] == "wt-fold2of2"
    job = start_job(paths, "winter-test", "winter test wt",
                    [step("winter-test", [sys.executable, "-c", "import time; time.sleep(60)"])], cwd=tmp_path,
                    refs={"check_id": "wt"})
    try:
        t0 = time.time()
        while job_info(paths, job["job_id"])["state"] != "running" and time.time() - t0 < 20:
            time.sleep(0.1)
        (item,) = act.activity(paths)  # one line: the test, not the test and a training run
        assert item["what"] == "Running the winter-by-winter test" and item["page"] == "winter"
        assert item["detail"].startswith("part 2 of 2, about ") and "started" in item["detail"]
        assert item["share"] == pytest.approx((1 + 0.25) / 2)
    finally:
        stop_job(paths, job["job_id"])
