"""The lab app's guidance (ADR-119): the menu's page list, the names of the evolutions and competitions, the bar of
what is running, the next-step card, and the app's own update and restart."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]


def _wait(pred, timeout=20.0):
    import time

    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.1)
    return False


# --------------------------------------------------------------------------------------------- pages and names


def test_every_page_file_is_in_the_menu_exactly_once():
    from snowagent.lab.ui.pages import DEFAULT, MENU, PAGES, where

    files = sorted(p.name for p in (REPO / "lab_app/pages").glob("*.py"))
    assert sorted(f for f, _, _ in PAGES.values()) == files  # no page left out, none listed that is not there
    listed = [key for keys in MENU.values() for key in keys]
    assert sorted(listed) == sorted(PAGES) and DEFAULT in PAGES
    assert where("watch") == "Evolutions (training) › Watch the run"
    assert where("blind") == "Competitions and checks › Blind test and site"


def test_the_names_of_evolutions_and_competitions():
    from snowagent.lab.services import labels
    from snowagent.lab.services.training import PRESETS

    assert labels.name("tuned") == "Tuned SNOWPACK" and labels.name("llyk") == "Lake Louise, Yoho and Kootenay"
    assert labels.titled("jasper") == "Jasper: the Parker Ridge check"
    assert labels.evolution_of(["column"]) == "column" and labels.evolution_of(["snowpack", "analogue"]) == "tuned"
    assert labels.evolution_of(["column", "snowpack"]) == "tuned" and labels.evolution_of([]) is None
    assert labels.run_evolution({"initial": [{"family": "column"}, {"family": "column"}]}) == "column"
    assert labels.run_evolution({"initial": [{"family": "snowpack"}]}) == "tuned"
    assert labels.run_evolution({}) is None and labels.run_evolution(None) is None
    by_preset = {name: labels.preset_evolution(p) for name, p in PRESETS.items()}
    assert by_preset["Column model, no SNOWPACK"] == "column"  # ADR-118's preset is the column evolution
    assert set(by_preset.values()) <= {"tuned", "column", "process", "process_melt", "story", "recipe", "column_site", None}
    # ADR-120: each structure on the column model is an evolution of its own, named once in its kind of run
    for family, preset in (("process", "Process switches, no SNOWPACK"), ("story", "Layer life stories, no SNOWPACK"),
                           ("recipe", "Weather recipes, no SNOWPACK")):
        assert by_preset[preset] == family and labels.evolution_of([family, family]) == family
        assert labels.prefixed(family, preset) == preset
        assert labels.evolution_of([family, "column"]) == "tuned"
    # ADR-122: the site scorecard is the "Column + site traits" evolution
    assert by_preset["Column + site traits, no SNOWPACK"] == "column_site" == labels.evolution_of(["scorecard"])
    assert labels.prefixed("column_site", "Column + site traits, no SNOWPACK") == "Column + site traits, no SNOWPACK"
    assert sum(v == "tuned" for v in by_preset.values()) >= 2
    # a kind of run carries its evolution's name once
    assert labels.prefixed("tuned", "Quick check") == "Tuned SNOWPACK: Quick check"
    assert labels.prefixed("column", "Column model, no SNOWPACK") == "Column model, no SNOWPACK"
    assert labels.prefixed(None, "Custom") == "Custom"


def test_a_persons_own_names_stay_in_the_lab_data_folder_which_git_ignores(tmp_path):
    """Names that should stay private (a colleague's, say) are typed in the app and kept beside the lab's data, never
    in a file that git follows; the repository itself holds only the plain names."""
    import shutil

    from snowagent.lab.services import labels
    from snowagent.lab.storage.paths import LabPaths

    paths = LabPaths(tmp_path / "lab")
    assert labels.local_names(paths) == {} and labels.name("tuned", paths) == "Tuned SNOWPACK"  # no file yet
    kept = labels.save_local_names(paths, {"tuned": "  Ada  ", "column": "", "nonsense": "x", "column_site": "y" * 99})
    assert kept == {"tuned": "Ada", "column_site": "y" * labels.MAX_NAME}  # trimmed; empty and unknown ones dropped
    assert (paths.root / labels.LOCAL_FILE).exists() and labels.local_names(paths) == kept
    assert labels.name("tuned", paths) == "Ada" and labels.name("column", paths) == "Column model"
    assert labels.name("tuned") == "Tuned SNOWPACK"  # without the lab's folder: the plain name
    assert labels.prefixed("tuned", "Quick check", paths) == "Ada: Quick check"
    assert labels.prefixed("column", "Column model, no SNOWPACK", paths) == "Column model, no SNOWPACK"
    labels.save_local_names(paths, {"tuned": ""})
    assert labels.local_names(paths) == {}  # an emptied box goes back to the plain name
    (paths.root / labels.LOCAL_FILE).write_text("not json")
    assert labels.local_names(paths) == {} and labels.name("tuned", paths) == "Tuned SNOWPACK"  # unreadable: plain
    (paths.root / labels.LOCAL_FILE).write_text('["a list"]')
    assert labels.local_names(paths) == {}

    repo = Path(__file__).resolve().parents[2]
    if shutil.which("git") and (repo / ".git").exists():  # the app's default folder, data/lab, is never committed
        ignored = subprocess.run(["git", "check-ignore", "-q", f"data/lab/{labels.LOCAL_FILE}"], cwd=repo)
        assert ignored.returncode == 0


# --------------------------------------------------------------------------------------------- what is running


def test_minutes_and_clock_read_plainly():
    from snowagent.lab.services.activity import clock, minutes

    assert minutes(20) == "under a minute" and minutes(7 * 60 + 5) == "7 min" and minutes(2 * 3600 + 300) == "2 h 05 min"
    now = datetime.now(UTC)
    assert clock(now, now) == f"{now.astimezone():%H:%M}"
    old = now - timedelta(days=3)
    assert clock(old, now) == f"{old.astimezone():%H:%M} on {old.astimezone().day} {old.astimezone():%b}"


def test_the_bar_lists_running_jobs_then_recent_ones_and_forgets_old_or_hidden_ones(tmp_path):
    from snowagent.lab.services import activity as act
    from snowagent.lab.services.jobs import job_info, start_job, step, stop_job
    from snowagent.lab.storage.paths import LabPaths

    paths = LabPaths(tmp_path / "lab")
    assert act.activity(paths) == [] and act.signature([]) == ()
    ok = [sys.executable, "-c", "print('one')"]
    bad = [sys.executable, "-c", "import sys; sys.exit(3)"]
    first = start_job(paths, "setup", "set up data", [step("prepare", ok), step("init", bad), step("import", ok)],
                      cwd=tmp_path)
    assert _wait(lambda: job_info(paths, first["job_id"])["state"] == "failed")
    (item,) = act.activity(paths)
    assert item["what"] == "Data set-up" and not item["active"] and item["page"] == "overview"
    assert "stopped with an error" in item["detail"] and "in step 2 of 3 (making the lab's tables)" in item["detail"]
    now = datetime.now(UTC)
    assert act.activity(paths, now + timedelta(hours=2))  # a problem stays a day
    assert act.activity(paths, now + timedelta(hours=25)) == []

    job_file = Path(first["dir"]) / "job.json"  # jobs are ordered by their start, kept to the second: an earlier one
    job_file.write_text(json.dumps(json.loads(job_file.read_text()) | {
        "created_at": (now - timedelta(seconds=30)).isoformat(timespec="seconds")}))
    done = start_job(paths, "setup", "set up data", [step("prepare", ok)], cwd=tmp_path)
    assert _wait(lambda: job_info(paths, done["job_id"])["state"] == "finished")
    (item,) = act.activity(paths)  # the failed try is left out once the job was started again
    assert item["job_id"] == done["job_id"] and item["detail"].startswith("finished at ")
    assert "(took under a minute)" in item["detail"]
    assert act.activity(paths, now + timedelta(hours=2)) == []  # a clean finish stays an hour
    assert act.usual_seconds(paths, "setup") is not None and act.usual_seconds(paths, "compete") is None

    sleeper = [sys.executable, "-c", "import time; time.sleep(60)"]
    live = start_job(paths, "area-setup", "Parker Ridge", [step("prepare-area", sleeper), step("init", ok)],
                     cwd=tmp_path)
    try:
        assert _wait(lambda: job_info(paths, live["job_id"])["state"] == "running")
        items = act.activity(paths)
        assert [i["active"] for i in items] == [True, False]  # running first
        run = items[0]
        assert run["what"] == "Setting up the Parker Ridge check" and run["share"] == 0.0 and run["page"] == "start"
        assert "step 1 of 2: copying the Jasper weather and pits" in run["detail"]
        assert "usually 5-10 minutes" in run["detail"]  # no finished job of the kind yet
        before = act.signature(items)
        act.dismiss(paths, done["job_id"])  # Hide: the finished one leaves the bar, its files stay
        assert [i["job_id"] for i in act.activity(paths)] == [live["job_id"]]
        assert act.signature(act.activity(paths)) != before and job_info(paths, done["job_id"])["state"] == "finished"
    finally:
        stop_job(paths, live["job_id"])
    assert _wait(lambda: job_info(paths, live["job_id"])["state"] == "stopped")
    (item,) = act.activity(paths)
    assert "was stopped" in item["detail"] and "in step 1 of 2" in item["detail"]


@pytest.fixture
def lab_with_a_run(tmp_path):
    pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")
    from snowagent.lab.benchmark.builder import build_cases
    from snowagent.lab.competition.runner import EngineSpec
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths
    from snowagent.lab.training.loop import TrainOptions, run_training
    from tests.unit.lab_fixtures import write_multiseason_lab

    cfg = load_lab_config(REPO / "config/lab.yaml")
    paths = LabPaths(tmp_path / "lab")
    write_multiseason_lab(paths.root, tmp_path / "checkout", cfg, ("2022-2023", "2023-2024"))
    build_cases(paths, cfg, tmp_path / "checkout", exclude_flagged=True)

    def train(run_id: str = "run-a", rounds: int = 2) -> None:
        run_training(paths, cfg, TrainOptions.from_config(cfg, rounds=rounds, population=4,
                                                          engine=EngineSpec(kind="fake")),
                     run_id=run_id, log=lambda m: None)

    return paths, train


def test_the_next_step_follows_the_order_a_person_works_in(lab_with_a_run):
    from snowagent.lab.services import activity as act

    paths, train = lab_with_a_run

    def step(**ok) -> dict:
        return act.next_step(paths, **({"data_ok": True, "engine_ok": True, "cases_ok": True} | ok))

    assert "set up the data" in step(data_ok=False, cases_ok=False, engine_ok=False)["text"]
    cases = step(cases_ok=False, engine_ok=False)
    assert "build the benchmark cases" in cases["text"] and cases["links"][0][0] == "cases"
    assert "build the SNOWPACK engine" in step(engine_ok=False)["text"]
    ready = step()
    assert "Everything is ready" in ready["text"] and ready["links"] == [("start", "Start a training run")]
    assert ready["tone"] == "success"

    train()
    done = step()
    assert "is finished** (2 of 2 rounds)" in done["text"]
    assert [page for page, _ in done["links"]] == ["results", "reports", "start"]

    sf = paths.outputs / "training" / "run-a" / "status.json"
    status = json.loads(sf.read_text())
    sf.write_text(json.dumps(status | {"state": "stopped"}))
    stopped = step()
    assert "stopped after round 2 of 2" in stopped["text"] and stopped["links"][0] == ("watch", "Resume the run")
    assert stopped["tone"] == "warning"

    # going now (its process: this one), started from a terminal: no job, still on the bar and the card
    sf.write_text(json.dumps(status | {"state": "running", "pid": os.getpid(), "round": 2, "done": 1, "total": 4,
                                       "round_started_at": status["updated_at"], "estimate_s": 60}))
    (item,) = act.activity(paths)
    assert item["active"] and item["what"] == "Training" and item["job_id"] is None and item["page"] == "watch"
    assert item["detail"].startswith("round 2 of 2") and item["share"] == pytest.approx((1 + 0.25) / 2)
    going = step(data_ok=False)  # whatever else is missing, a run that is going comes first
    assert "A training run is going" in going["text"] and going["links"] == [("watch", "Watch the run")]


def test_a_run_is_labelled_with_its_evolution_and_its_best_agents_name(lab_with_a_run):
    from snowagent.lab.services.training import run_label, run_overview

    paths, train = lab_with_a_run
    train()
    label = run_label(paths, "run-a")
    assert " · Tuned SNOWPACK · 2 of 2 rounds · finished · best " in label
    from snowagent.lab.services import labels

    labels.save_local_names(paths, {"tuned": "Ada"})  # this computer's own name for the evolution
    assert " · Ada · 2 of 2 rounds · finished · best " in run_label(paths, "run-a")
    labels.save_local_names(paths, {})
    rounds = run_overview(paths, "run-a")["rounds"]
    assert rounds["best_name"].notna().all() and label.endswith(str(rounds["best_name"].iloc[-1]))


# --------------------------------------------------------------------------------------------- update and restart


def _git(cwd: Path, *args: str) -> str:
    env = os.environ | {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@example.com", "GIT_CONFIG_GLOBAL": os.devnull,
                        "GIT_CONFIG_SYSTEM": os.devnull}
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True,
                          env=env).stdout.strip()


def _clean_update_env(monkeypatch) -> None:
    """No update setting from the environment, and none left behind: the app keeps its started version there."""
    from snowagent.lab.services import update

    for key in (update.ENV, update.STARTED_ENV, update.STARTED_AT_ENV, update.SUPERVISED_ENV):
        monkeypatch.setenv(key, "")  # so that the end of the test removes whatever the code under test stored
        monkeypatch.delenv(key)
    update.forget()


@pytest.fixture
def checkouts(tmp_path, monkeypatch):
    """A 'GitHub' (a bare repository), the app's copy of it, and a second copy that merges pull requests."""
    _clean_update_env(monkeypatch)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", os.devnull)
    origin, work, dev = tmp_path / "origin.git", tmp_path / "work", tmp_path / "dev"
    origin.mkdir()
    _git(origin, "init", "-q", "--bare", "-b", "main")
    _git(tmp_path, "clone", "-q", str(origin), str(dev))
    (dev / "scripts").mkdir()
    (dev / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (dev / "scripts/build_snowpack.sh").write_text("echo build\n")
    (dev / "app.py").write_text("v1\n")
    _git(dev, "add", "-A")
    _git(dev, "commit", "-q", "-m", "first")
    _git(dev, "push", "-q", "origin", "HEAD:main")
    _git(tmp_path, "clone", "-q", str(origin), str(work))

    def merge(number: int, title: str, files: dict[str, str]) -> None:
        _git(dev, "checkout", "-q", "-b", f"pr-{number}", "main")
        for name, text in files.items():
            (dev / name).parent.mkdir(parents=True, exist_ok=True)
            (dev / name).write_text(text)
        _git(dev, "add", "-A")
        _git(dev, "commit", "-q", "-m", f"work for {number}")
        _git(dev, "checkout", "-q", "main")
        _git(dev, "merge", "-q", "--no-ff", f"pr-{number}", "-m", f"Merge pull request #{number} from x/y", "-m",
             title)
        _git(dev, "push", "-q", "origin", "main")

    return work, merge


def test_update_check_says_what_is_new_and_what_the_update_must_redo(checkouts, tmp_path):
    from snowagent.lab.services import update

    work, merge = checkouts
    assert update.check(work)["state"] == "current"
    merge(7, "A clearer Overview", {"app.py": "v2\n"})
    info = update.check(work)
    assert info["state"] == "behind" and info["behind"] == 2 and info["branch"] == "main"
    assert info["news"] == ["A clearer Overview"] and not info["reinstall"] and not info["rebuild"]
    assert [s["name"] for s in update.update_steps(work, info)] == ["pull"]
    assert update.update_steps(work, info)[0]["command"][-4:] == ["snowagent.lab.services.update", "fast-forward",
                                                                  str(work.resolve()), "main"]

    merge(8, "New requirement", {"pyproject.toml": "[project]\nname = 'y'\n"})
    merge(9, "Engine patch", {"scripts/snowpack-patches/new.patch": "patch\n"})
    info = update.check(work)
    assert info["news"] == ["Engine patch", "New requirement", "A clearer Overview"]  # newest first
    assert info["reinstall"] and info["rebuild"] and info["version"] == update.version_text(work, "origin/main")
    steps = update.update_steps(work, info)
    assert [s["name"] for s in steps] == ["pull", "install", "engine"]
    assert steps[1]["command"][1:5] == ["-m", "pip", "install", "-q"] and steps[1]["command"][-1].endswith("[dev,lab]")
    assert steps[2]["command"] == ["bash", str(work.resolve() / "scripts/build_snowpack.sh")]

    (work / "mine.txt").write_text("a local change\n")  # a copy with commits of its own is never updated by itself
    _git(work, "add", "-A")
    _git(work, "commit", "-q", "-m", "local")
    assert update.check(work)["state"] == "diverged"
    assert update.check(tmp_path / "nowhere")["state"] == "unknown"
    _git(work, "checkout", "-q", "--detach")
    assert update.check(work)["reason"] == "this copy is not on a branch"


def test_update_check_stays_off_and_the_restart_refuses_unless_the_launcher_asked(checkouts, monkeypatch):
    from snowagent.lab.services import update

    work, merge = checkouts
    merge(7, "Something new", {"app.py": "v2\n"})
    assert not update.enabled() and update.latest(work, wait=True) is None  # tests, a plain `streamlit run`
    assert not update.can_restart()
    with pytest.raises(RuntimeError):
        update.restart()

    monkeypatch.setenv(update.ENV, "1")
    assert update.latest(work, wait=True)["state"] == "behind"
    assert update.latest(work)["state"] == "behind"  # kept: GitHub is not asked at every page view
    update.forget()

    codes = []
    monkeypatch.setenv(update.SUPERVISED_ENV, "1")
    monkeypatch.setattr(update.os, "_exit", codes.append)
    update.restart()
    assert codes == [update.RESTART_CODE]


def test_the_app_knows_when_its_files_changed_and_restarts_by_itself_only_after_its_own_update(checkouts, tmp_path):
    from snowagent.lab.services import update
    from snowagent.lab.services.jobs import job_info
    from snowagent.lab.storage.paths import LabPaths

    work, merge = checkouts
    paths = LabPaths(tmp_path / "lab")
    assert update.started_head(work) == update.head(work) and not update.stale(work)
    assert not update.just_updated(paths)  # no update job at all
    merge(7, "Something new", {"app.py": "v2\n"})
    info = update.check(work)
    job = update.start_update(paths, work, info)  # the real job: fetch, then a fast-forward merge
    assert job["kind"] == "update" and update.busy_jobs(paths) or job_info(paths, job["job_id"])["state"] == "finished"
    assert _wait(lambda: job_info(paths, job["job_id"])["state"] == "finished")
    assert (work / "app.py").read_text() == "v2\n" and update.busy_jobs(paths) == []
    assert update.stale(work) and update.just_updated(paths)  # this app ran it: restart without asking
    assert update.check(work)["state"] == "current"

    os.environ[update.STARTED_AT_ENV] = (datetime.now(UTC) + timedelta(minutes=5)).isoformat()
    assert not update.just_updated(paths)  # an update from before this app started: the Restart button instead


def test_fast_forward_ignores_a_crowded_fetch_head_and_retries_a_failed_fetch(checkouts, monkeypatch):
    """2026-11-19 on the Mac: `git pull --ff-only` stopped with "Cannot fast-forward to multiple branches" when the
    app's background check fetched at the same moment. The update now fetches into origin/<branch> and merges that
    ref, so whatever FETCH_HEAD holds does not matter, and a fetch that fails is tried again."""
    from snowagent.lab.services import update

    work, merge = checkouts
    merge(7, "Something new", {"app.py": "v2\n"})
    git_dir = Path(update.git(work, "rev-parse", "--absolute-git-dir")[1])
    real_git = update.git
    calls = {"fetch": 0}

    def flaky(root, *args, **kw):
        if args and args[0] == "fetch":
            calls["fetch"] += 1
            if calls["fetch"] == 1:
                return 1, "error: cannot lock ref 'refs/remotes/origin/main'"
            out = real_git(root, *args, **kw)
            (git_dir / "FETCH_HEAD").write_text("a" * 40 + "\t\tbranch 'main' of x\n" + "b" * 40 + "\t\tbranch 'other' of x\n")
            return out
        return real_git(root, *args, **kw)

    monkeypatch.setattr(update, "git", flaky)
    said: list[str] = []
    assert update.fast_forward(work, "main", pause_s=0, say=said.append) == 0
    assert calls["fetch"] == 2 and "try 1 of 4" in said[0]
    assert (work / "app.py").read_text() == "v2\n"
    monkeypatch.setattr(update, "git", real_git)
    assert update.check(work)["state"] == "current"


def test_engine_stale_names_the_patches_the_engine_lacks(tmp_path, monkeypatch):
    from snowagent.lab.agents import physics
    from snowagent.lab.services import update

    (tmp_path / "scripts/snowpack-patches").mkdir(parents=True)
    for name in ("lab-facet-knobs", "lab-grain-size"):
        (tmp_path / "scripts/snowpack-patches" / f"{name}.patch").write_text("x")
    monkeypatch.setattr(physics, "engine_patches", lambda binary: {"lab-facet-knobs"})
    assert update.engine_stale(tmp_path, "/usr/local/bin/snowpack") == ["lab-grain-size"]
    assert update.engine_stale(tmp_path, None) == []  # no engine at all: that is "not built", not "old"


def test_lab_app_starts_the_app_again_when_it_asks_for_a_restart(monkeypatch):
    from snowagent.lab import cli
    from snowagent.lab.services.update import RESTART_CODE, SUPERVISED_ENV

    calls, codes = [], [RESTART_CODE, RESTART_CODE, 3]
    monkeypatch.setattr(subprocess, "call", lambda cmd, env: calls.append((cmd, env)) or codes.pop(0))
    assert cli._serve(["app"], {"A": "1"}) == 3  # any other exit code ends `snowagent lab app` as before
    assert len(calls) == 3 and all(env == {"A": "1", SUPERVISED_ENV: "1"} for _, env in calls)

    def interrupted(cmd, env):
        raise KeyboardInterrupt

    monkeypatch.setattr(subprocess, "call", interrupted)
    assert cli._serve(["app"], {}) == 0  # Ctrl-C


# --------------------------------------------------------------------------------------------- on the pages


def _page(name: str):
    pytest.importorskip("streamlit", reason="lab extra not installed (pip install -e '.[lab]')")
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(REPO / "lab_app/pages" / name), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def test_every_page_shows_what_ended_and_a_job_can_be_hidden_from_the_bar(tmp_path, monkeypatch):
    from snowagent.lab.services.jobs import job_info, start_job, step
    from snowagent.lab.storage.paths import LabPaths

    _clean_update_env(monkeypatch)
    paths = LabPaths(tmp_path / "lab")
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(paths.root))
    bad = [sys.executable, "-c", "import sys; sys.exit(3)"]
    job = start_job(paths, "compete", "a competition", [step("compete", bad)], cwd=tmp_path)
    assert _wait(lambda: job_info(paths, job["job_id"])["state"] == "failed")

    line = "**The competition** stopped with an error at "
    for name in ("0_Overview.py", "4_Training.py", "9_Run_progress.py", "5_Arena.py"):
        at = _page(name)
        assert [str(e.value) for e in at.error if line in str(e.value)
                and str(e.value).endswith("The details are on the Jobs page.")], name
    at = _page("6_Jobs.py")
    assert any(line in str(e.value) and str(e.value).endswith("The details are below.") for e in at.error)
    assert not [w for w in at.info if "newer version" in str(w.value)]  # no update check outside `snowagent lab app`
    assert not [b for b in at.button if b.label == "Check for a newer version"]

    at = _page("0_Overview.py")
    at.button(f"bar-hide-{job['job_id']}").click().run()
    assert not at.exception and not [e for e in at.error if line in str(e.value)]
    assert job_info(paths, job["job_id"])["state"] == "failed"  # hidden from the bar only


def test_the_update_notice_says_what_is_new_waits_for_running_jobs_and_starts_the_update(tmp_path, monkeypatch):
    from snowagent.lab.services import jobs, update
    from snowagent.lab.storage.paths import LabPaths

    _clean_update_env(monkeypatch)
    paths = LabPaths(tmp_path / "lab")
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(paths.root))
    monkeypatch.setenv(update.ENV, "1")
    behind = {"state": "behind", "branch": "main", "behind": 3, "ahead": 0, "news": ["A clearer Overview"],
              "more_news": 2, "reinstall": False, "rebuild": True, "version": "19 Nov 2026 (abc1234)"}
    monkeypatch.setattr(update, "latest", lambda root, **kw: behind)
    launched = []

    class FakePopen:
        def __init__(self, cmd, **kw):
            launched.append(cmd)
            self.pid = 4242

    monkeypatch.setattr(jobs, "_launch", FakePopen)

    monkeypatch.setattr(update, "busy_jobs", lambda paths: [{"state": "running"}])
    at = _page("0_Overview.py")
    (notice,) = [str(i.value) for i in at.info if "newer version" in str(i.value)]
    assert notice.startswith("**A newer version of the app is ready** (of 19 Nov 2026). It also rebuilds the "
                             "SNOWPACK engine, so it takes about 5 minutes.")
    assert "What is new:\n- A clearer Overview\n- and 2 more" in notice
    assert at.button("update-app").disabled  # no run gets new code half way
    assert any("Something is running, so the update waits" in str(c.value) for c in at.caption)
    assert any("newer version ready" in str(c.value) for c in at.sidebar.caption)

    monkeypatch.setattr(update, "busy_jobs", lambda paths: [])
    at = _page("0_Overview.py")
    at.button("update-app").click().run()
    assert not at.exception and len(launched) == 1
    job = json.loads((Path(launched[0][-1]) / "job.json").read_text())
    assert job["kind"] == "update" and [s["name"] for s in job["steps"]] == ["pull", "engine"]
    assert job["steps"][0]["command"][-3] == "fast-forward" and job["steps"][0]["command"][-1] == "main"

    for state, words in (("current", "up to date"), ("diverged", "update it from Terminal")):
        monkeypatch.setattr(update, "latest", lambda root, state=state, **kw: {"state": state})
        at = _page("6_Jobs.py")
        assert any(words in str(c.value) for c in at.sidebar.caption)
        assert not [i for i in at.info if "newer version" in str(i.value)]


def test_changed_files_offer_a_restart_and_the_apps_own_update_restarts_by_itself(tmp_path, monkeypatch):
    from snowagent.lab.services import update
    from snowagent.lab.ui import status

    _clean_update_env(monkeypatch)
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(tmp_path / "lab"))
    restarts = []
    monkeypatch.setattr(update, "restart", lambda: restarts.append(1))
    monkeypatch.setattr(status, "RESTART_PAUSE_S", 0)
    monkeypatch.setattr(update, "stale", lambda root: True)  # e.g. a `git pull` in Terminal while the app runs
    monkeypatch.setattr(update, "just_updated", lambda paths: False)

    at = _page("6_Jobs.py")  # started with a plain `streamlit run`: it cannot start itself again
    (notice,) = [str(i.value) for i in at.info if "files have changed" in str(i.value)]
    assert "Stop the app (Ctrl-C in its Terminal window) and start it again" in notice
    assert not [b for b in at.button if b.label == "Restart the app"] and not restarts

    monkeypatch.setenv(update.SUPERVISED_ENV, "1")  # started by `snowagent lab app`
    at = _page("6_Jobs.py")
    assert any("Restart the app to use it" in str(i.value) for i in at.info) and not restarts
    at.button("restart-app").click().run()
    assert restarts == [1]

    monkeypatch.setattr(update, "updating", lambda paths: True)  # its own update is still going (say, the engine)
    at = _page("6_Jobs.py")
    assert restarts == [1] and not [i for i in at.info if "files have changed" in str(i.value)]
    monkeypatch.setattr(update, "updating", lambda paths: False)

    monkeypatch.setattr(update, "just_updated", lambda paths: True)  # its own Update and restart just finished
    at = _page("6_Jobs.py")
    assert len(restarts) >= 2 and any("The new version is in." in str(i.value) for i in at.info)
