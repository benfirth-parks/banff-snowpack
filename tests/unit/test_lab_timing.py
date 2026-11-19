"""Time estimates rest on this computer's own measured speeds (ADR-126): a family never timed here is timed before
an estimate relies on a built-in number from another machine, an estimate names what it still guesses, and an engine
profile made before an update of the prediction code is not counted as cached."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.lab.agents.snowpack import FakeEngine  # noqa: E402
from snowagent.lab.benchmark import builder  # noqa: E402
from snowagent.lab.benchmark.loader import case_dirs, load_visible_case  # noqa: E402
from snowagent.lab.competition.runner import EngineSpec, select_cases  # noqa: E402
from snowagent.lab.genome import default_genome  # noqa: E402
from snowagent.lab.schemas.genome import AgentFamily  # noqa: E402
from snowagent.lab.settings import load_lab_config  # noqa: E402
from snowagent.lab.storage.paths import LabPaths  # noqa: E402
from snowagent.lab.training import cache as cache_mod  # noqa: E402
from snowagent.lab.training import calibrate as cal  # noqa: E402
from snowagent.lab.training import estimate, loso  # noqa: E402
from snowagent.lab.training.cache import DiskEngineCache, TrainingCache, engine_stamp  # noqa: E402
from snowagent.lab.training.estimate import DEFAULT_AGENT_S, Timings, load_timings  # noqa: E402
from snowagent.lab.training.evaluate import case_refs  # noqa: E402
from snowagent.lab.training.loop import TrainOptions, run_training, training_root  # noqa: E402
from tests.unit.lab_fixtures import write_multiseason_lab  # noqa: E402
from tests.unit.test_lab_benchmark import CONFIG  # noqa: E402

SEASONS = ("2018-2019", "2019-2020", "2020-2021", "2021-2022", "2022-2023", "2023-2024")
LOCKED = ["2021-2022", "2022-2023", "2023-2024"]  # the three most recent: never run by a timing
GROUPS = ["2018-2019+2020-2021", "2019-2020"]


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("timing")
    cfg = load_lab_config(CONFIG)
    paths = LabPaths(root / "lab")
    write_multiseason_lab(paths.root, root / "checkout", cfg, SEASONS)
    builder.build_cases(paths, cfg, root / "checkout", exclude_flagged=True)
    return cfg, root


@pytest.fixture()
def lab(built, tmp_path):
    cfg, root = built
    shutil.copytree(root / "lab", tmp_path / "lab")
    return cfg, LabPaths(tmp_path / "lab")


@pytest.fixture(autouse=True)
def small_timing(monkeypatch):
    """A timing on few cases and agents: these tests check what is timed and what is kept, not how well."""
    monkeypatch.setattr(cal, "MIN_CASES", 4)
    monkeypatch.setattr(cal, "AGENTS", 3)


def column_opts(cfg, **kw) -> TrainOptions:
    """A training of the column model alone with SNOWPACK switched off: nothing but its agents to time."""
    return TrainOptions.from_config(cfg, **({"rounds": 6, "population": 10, "engine": EngineSpec(kind="none"),
                                             "initial": [default_genome(AgentFamily.column, cfg.genome)],
                                             "exclude_seasons": LOCKED, "locked_seasons": 0} | kw))


def test_timings_know_what_this_computer_measured():
    t = Timings()
    assert not t.timed("column") and t.agent("column") == DEFAULT_AGENT_S["column"]  # another machine's number
    assert t.guessed(["column", "snowpack", "column"], engine=True) == ["column", "snowpack", "engine"]
    assert t.guessed([AgentFamily.column]) == ["column"]
    t.update([{"pairs": 2, "agent_s": {"column": [0.1, 0.3]}, "engine_runs_s": [4.0], "load_s": 0.2}])
    assert t.timed("column") and t.agent("column") == pytest.approx(0.2) and t.engine == pytest.approx(4.0)
    assert t.guessed(["column", "story"], engine=True) == ["story"]
    # what a timing can measure here: families that run without SNOWPACK and without the analogue library
    assert cal.can_time(Timings(), ["column", "snowpack", "analogue", AgentFamily.story, "column", "hybrid"]) == \
        ["column", "story"]
    assert cal.can_time(t, ["column", "story"]) == ["story"]
    # timed, but by fewer agent runs than one round of the run to estimate (a small trial on other cases, say)
    assert cal.can_time(t, ["column", "story", "snowpack"], enough=100) == ["column", "story"]
    assert cal.can_time(t, ["column"], enough=2) == []


def test_a_timing_leaves_each_workers_first_case_out():
    stats = [{"pid": 1, "i": 0}, {"pid": 2, "i": 1}, {"pid": 1, "i": 2}, {"pid": 2, "i": 3}, {"pid": 1, "i": 4}]
    assert [s["i"] for s in cal.steady(stats)] == [2, 3, 4]  # the first case of a worker also loads the code
    assert cal.steady(stats[:3]) == stats[:3] and cal.steady([]) == []  # too few would remain: all are counted


def test_a_family_never_timed_here_is_timed_and_nothing_of_it_is_kept(lab, monkeypatch):
    cfg, paths = lab
    cache = TrainingCache(paths.outputs / "cache")
    train = [r for r in case_refs(select_cases(paths, "all")) if r.manifest.season not in LOCKED]
    t, said = Timings(), []
    out = cal.calibrate(paths, cfg, t, ["column", "snowpack"], train, workers=1, log=said.append)
    assert set(out) == {"column"} and out["column"]["agent_s"] > 0 and out["column"]["agents"] == cal.AGENTS
    assert 0 < out["column"]["cases"] < len(train)  # the first case is left out of the means
    assert t.timed("column") and not t.timed("snowpack")  # SNOWPACK's cost is the engine run, timed by the rounds
    assert t.agent_s["column"].n == out["column"]["cases"] * cal.AGENTS
    saved = load_timings(paths, cache.timings)
    assert saved.timed("column") and saved.source == "measured" and saved.agent("column") == pytest.approx(
        t.agent("column"))
    assert any("timed the column agents on this computer" in m for m in said)
    # the predictions and scores went to a throwaway cache: none is in the lab's cache, and the throwaway is gone
    assert not list(paths.outputs.glob("timing-*")) and not (cache.root / "predictions").exists()
    assert cal.calibrate(paths, cfg, t, ["column"], train) == {}  # timed now: nothing to do
    assert cal.calibrate(paths, cfg, Timings(), ["column"], []) == {}  # no case to time on
    # a mean from a few runs on other cases (a small trial on one plot, say) gives way to a timing on the run's
    # own cases, which then weighs as one round of that run: the same estimate made again does not time again
    small = Timings()
    small.update([{"pairs": 4, "agent_s": {"column": [60.0] * 4}, "load_s": 0.2}])
    assert cal.calibrate(paths, cfg, small, ["column"], train, enough=4) == {}  # enough runs behind it: kept
    again = cal.calibrate(paths, cfg, small, ["column"], train, enough=5000)
    assert set(again) == {"column"} and small.agent("column") < 30 and small.agent_s["column"].n == 5000
    assert cal.calibrate(paths, cfg, small, ["column"], train, enough=5000) == {}

    # a timing never stops an estimate: a failure is said and the family stays on its built-in number
    def broken(*_a, **_k):
        raise RuntimeError("no workers")

    monkeypatch.setattr(cal, "evaluate_population", broken)
    t2, said2 = Timings(), []
    assert cal.calibrate(paths, cfg, t2, ["story"], train, log=said2.append) == {} and not t2.timed("story")
    assert "could not time the story agents here" in said2[0] and not list(paths.outputs.glob("timing-*"))


def test_an_estimate_times_an_untimed_family_instead_of_using_another_machines_number(lab, monkeypatch):
    """The owner's Mac on 2026-11-19: it had only ever trained SNOWPACK agents, and its first winter test of the
    column model was estimated from a built-in number several times too slow (3 h 18 min for 53 min)."""
    cfg, paths = lab
    monkeypatch.setitem(estimate.DEFAULT_AGENT_S, "column", 5.0)
    seen: dict = {}
    real = cal.calibrate

    def spy(paths_, cfg_, timings, families, refs, workers, **kw):
        seen["seasons"] = {r.manifest.season for r in refs}
        return real(paths_, cfg_, timings, families, refs, workers, **kw)

    monkeypatch.setattr(cal, "calibrate", spy)
    o = column_opts(cfg)
    guess = loso.estimate_check(paths, cfg, o, GROUPS, 1, time_here=False)
    assert guess["guessed"] == ["column"] and not guess["timed_here"] and not seen  # it says what it guessed
    said = []
    est = loso.estimate_check(paths, cfg, o, GROUPS, 1, log=said.append)
    assert set(est["timed_here"]) == {"column"} and est["guessed"] == [] and est["timings"] == "measured"
    assert est["total_high_s"] < guess["total_high_s"] / 5  # this computer's speed, not the built-in number
    assert seen["seasons"] and not seen["seasons"] & set(LOCKED)  # the locked winters are never run for a timing
    assert any("timed the column agents" in m for m in said)
    again = loso.estimate_check(paths, cfg, o, GROUPS, 1)
    assert not again["timed_here"] and again["total_high_s"] == pytest.approx(est["total_high_s"])  # timed once
    # with SNOWPACK switched off there is no engine run to count, for the bar or anything else
    assert est["engine_runs"] == 0 and loso.estimate_check(
        paths, cfg, column_opts(cfg, engine=EngineSpec(kind="fake")), GROUPS, 1)["engine_runs"] > 0


def test_a_short_estimate_is_not_worth_a_timing_and_says_it_guesses(lab):
    cfg, paths = lab
    est = loso.estimate_check(paths, cfg, column_opts(cfg, rounds=1, population=2), GROUPS, 64)
    assert est["total_high_s"] < cal.WORTH_S and not est["timed_here"] and est["guessed"] == ["column"]
    assert not TrainingCache(paths.outputs / "cache").timings.is_file()  # nothing was measured or written


def test_a_training_run_estimate_times_an_untimed_family_too(lab, monkeypatch):
    cfg, paths = lab
    monkeypatch.setitem(estimate.DEFAULT_AGENT_S, "column", 5.0)
    said = []
    res = run_training(paths, cfg, column_opts(cfg), run_id="est", log=said.append, estimate_only=True)
    e = res.summary["estimate"]
    assert set(e["timed_here"]) == {"column"} and e["guessed"] == []
    assert any("timed the column agents" in m for m in said) and not any("has not timed" in m for m in said)
    assert not (res.run_dir / "run.json").exists()  # an estimate still starts no run
    # where the timing is not worth it (or fails), the estimate says that it guesses
    monkeypatch.setattr(cal, "WORTH_S", 1e12)
    said2 = []
    o = column_opts(cfg, initial=[default_genome(AgentFamily.story, cfg.genome)])
    e2 = run_training(paths, cfg, o, run_id="est2", log=said2.append, estimate_only=True).summary["estimate"]
    assert e2["guessed"] == ["story"] and not e2["timed_here"]
    assert any("this computer has not timed the story agents yet" in m for m in said2)
    assert "has not timed the column agents or a SNOWPACK run yet" in estimate.guess_note(["column", "engine"])


def test_agents_are_not_timed_beside_a_training_run_that_is_going(lab, monkeypatch):
    """Another run's workers have the cores: a timing beside it would measure a busy computer, call the agents
    slower than they are and keep that as the family's mean. It is left for the estimate made after the run."""
    cfg, paths = lab
    monkeypatch.setitem(estimate.DEFAULT_AGENT_S, "column", 5.0)
    assert cal.run_going(paths) is None  # no run at all
    other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
    try:
        for run_id, status in (("training-mine", json.dumps({"state": "running", "pid": os.getpid()})),
                               ("training-done", json.dumps({"state": "finished", "pid": other.pid})),
                               ("training-other", json.dumps({"state": "running", "pid": other.pid})),
                               ("training-torn", "{")):
            (training_root(paths) / run_id).mkdir(parents=True)
            (training_root(paths) / run_id / "status.json").write_text(status)
        # this process's own run, a finished run and a half-written status are not a run going beside this one
        assert cal.run_going(paths) == "training-other"
        said: list[str] = []
        est = loso.estimate_check(paths, cfg, column_opts(cfg), GROUPS, 1, log=said.append)
        assert est["busy"] == {"run_id": "training-other", "families": ["column"]}
        assert not est["timed_here"] and est["guessed"] == ["column"]  # it still guesses, and says so
        assert any("training-other is going" in m and "column agents are not timed" in m for m in said)
        assert not TrainingCache(paths.outputs / "cache").timings.is_file()  # nothing was measured or written
        e = run_training(paths, cfg, column_opts(cfg), run_id="est", log=said.append,
                         estimate_only=True).summary["estimate"]
        assert e["busy"]["run_id"] == "training-other" and not e["timed_here"] and e["guessed"] == ["column"]
        # the run is killed: its status still says running, and until its parent collects it the process still
        # takes a signal, but it is not going any more
        other.terminate()
        deadline = time.time() + 20
        while cal.run_going(paths) and time.time() < deadline:
            time.sleep(0.05)
        assert cal.run_going(paths) is None
    finally:
        other.kill()
        other.wait()
    # so the next estimate is measured
    est = loso.estimate_check(paths, cfg, column_opts(cfg), GROUPS, 1)
    assert set(est["timed_here"]) == {"column"} and est["busy"] is None and est["guessed"] == []


def test_the_engine_index_tells_a_profile_of_this_code_from_an_older_one(lab, tmp_path, monkeypatch):
    """After an update of the prediction code every engine profile has a new key and is run again. The index
    notes the code and engine a case's profile was made under, so an estimate counts that run instead of taking
    the older profile for it (in a winter test on the Mac: 5 to 15 minutes of the bar's runs)."""
    cfg, paths = lab
    case = load_visible_case(case_dirs(paths, "all")[0])
    cache, h = TrainingCache(tmp_path / "cache"), "ab" * 32
    stamp = engine_stamp("fake")
    assert cache.engine_cached_for(h, stamp=stamp) is None  # never seen
    disk = DiskEngineCache(FakeEngine(), cache, "fake", case_hash=h)
    disk.simulate(case)
    assert cache.engine_cached_for(h) is True and cache.engine_cached_for(h, stamp=stamp) is True
    assert cache.engine_cached_for(h, stamp=engine_stamp("snowpack:another build")) is False
    monkeypatch.setattr(cache_mod, "code_hash", lambda: "the prediction code after an update")
    new = engine_stamp("fake")
    assert new != stamp and cache.engine_cached_for(h, stamp=new) is False  # it will be run again
    assert cache.engine_cached_for(h) is True  # asked without a stamp, as before: some profile of the case exists
    disk2 = DiskEngineCache(FakeEngine(), cache, "fake", case_hash=h)
    disk2.simulate(case)
    assert disk2.hit is False and cache.engine_cached_for(h, stamp=new) is True  # the index follows the new profile
    # an entry written before the index noted stamps (the key alone) says nothing about the code that made it
    cache.engine_index_path(h).write_text(disk2.key)
    assert cache.engine_cached_for(h) is True and cache.engine_cached_for(h, stamp=new) is None
    disk2.simulate(case)  # served from the cache, and the entry gets its stamp
    assert disk2.hit is True and cache.engine_cached_for(h, stamp=new) is True

    class ResetTest(FakeEngine):  # ADR-124: a test of the pit reset has profiles of its own
        reset_test = {"pit_reset": "depth"}

    DiskEngineCache(ResetTest(), cache, "fake", case_hash="cd" * 32).simulate(case)
    assert cache.engine_cached_for("cd" * 32) is None  # it leaves the index, which describes the adopted run, alone
