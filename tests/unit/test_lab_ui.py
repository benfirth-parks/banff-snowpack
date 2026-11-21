"""Lab UI smoke test: every page runs without an exception, with no lab data and with imported synthetic data."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

pytest.importorskip("streamlit", reason="lab extra not installed (pip install -e '.[lab]')")
pytest.importorskip("plotly", reason="lab extra not installed (pip install -e '.[lab]')")
pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")
from streamlit.testing.v1 import AppTest  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
PAGES = [REPO / "lab_app/pages/0_Overview.py", REPO / "lab_app/pages/1_Data_Explorer.py",
         REPO / "lab_app/pages/2_Benchmark_Cases.py", REPO / "lab_app/pages/3_Leaderboard.py"]
FIX = REPO / "tests/fixtures/lab"


def _run(page: Path) -> AppTest:
    at = AppTest.from_file(str(page), default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _text(at: AppTest) -> str:
    return " ".join(str(x.value) for kind in ("markdown", "warning", "info", "caption") for x in getattr(at, kind))


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_pages_run_with_no_data(page, tmp_path, monkeypatch):
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(tmp_path / "empty"))
    at = _run(page)
    text = _text(at)
    assert "not an avalanche forecast" in text
    # where to begin, in the app: the Overview's own next step, or the way to it from any other page
    assert ("Next: set up the data" if page.name == "0_Overview.py" else "No lab data yet") in text
    assert "snowagent lab import" in text or page.name == "0_Overview.py"  # the Terminal route, folded away


def test_the_entry_point_opens_the_overview_with_the_next_step(tmp_path, monkeypatch):
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(tmp_path / "empty"))
    at = _run(REPO / "lab_app/Home.py")  # the menu itself: test_lab_app_status.py (every page listed once)
    assert "Snowpack Agent Lab" in [t.value for t in at.title]
    assert "What to do next" in [h.value for h in at.subheader] and "Next: set up the data" in _text(at)
    assert "Pits" in _text(at) and "press Run set-up, below" in _text(at)  # the readiness row


def test_overview_keeps_a_persons_own_names_on_this_computer(tmp_path, monkeypatch):
    from snowagent.lab.services import labels
    from snowagent.lab.storage.paths import LabPaths

    root = tmp_path / "empty"
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(root))
    at = _run(REPO / "lab_app/pages/0_Overview.py")
    boxes = {t.label: t for t in at.text_input}
    assert {"Tuned SNOWPACK", "Column model", "Column + site traits"} <= set(boxes)  # the plain names, as shipped
    assert "never sent to GitHub" in _text(at)
    boxes["Column model"].set_value(" Ada ")
    next(b for b in at.button if b.label == "Save my names").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert json.loads((root / labels.LOCAL_FILE).read_text()) == {"column": "Ada"}  # beside the lab's data only
    assert labels.name("column", LabPaths(root)) == "Ada" and labels.name("column") == "Column model"
    names = next(d.value for d in at.dataframe if "what it is" in d.value.columns)
    assert "Ada" in set(names["name"]) and "Tuned SNOWPACK" in set(names["name"])
    at = _run(REPO / "lab_app/pages/0_Overview.py")  # opened again: the box shows what is kept
    assert {t.label: t for t in at.text_input}["Column model"].value == "Ada"


def test_pages_run_with_imported_data(tmp_path, monkeypatch):
    from snowagent.lab.services.data import import_data
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths

    src = tmp_path / "checkout"
    shutil.copytree(FIX / "fts360", src / "data/raw/fts360")
    (src / "data/interim/obs").mkdir(parents=True)
    shutil.copy(FIX / "observed_profiles.jsonl", src / "data/interim/obs/observed_profiles.jsonl")
    import_data(src, LabPaths(tmp_path / "lab"), load_lab_config(REPO / "config/lab.yaml"))
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(tmp_path / "lab"))

    home = _run(PAGES[0])
    assert any(str(m.value) == "1" for m in home.metric)  # one unique usable pit at Bow Summit
    assert "No station weather imported for this site." in _text(home)  # Goat's Eye and Simpson have none
    explorer = _run(PAGES[1])
    sel = {s.label: s for s in explorer.selectbox}
    assert sel["Profile"].value == "2024-01-10_bow_summit_syn001"
    assert len(explorer.get("plotly_chart")) == 2  # profile plot and weather small multiples
    sel["Site"].set_value("SIMP").run()  # site without weather: empty states, no crash
    assert not explorer.exception
    assert "No station weather at Simpson" in _text(explorer)


def test_benchmark_page_with_built_cases(tmp_path, monkeypatch):
    from snowagent.lab.benchmark.builder import build_cases
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths
    from tests.unit.lab_fixtures import write_synthetic_lab

    cfg = load_lab_config(REPO / "config/lab.yaml")
    paths = LabPaths(tmp_path / "lab")
    write_synthetic_lab(paths.root, tmp_path / "checkout", cfg)
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(paths.root))
    page = REPO / "lab_app/pages/2_Benchmark_Cases.py"
    assert "No cases built yet" in _text(_run(page))

    rep = build_cases(paths, cfg, tmp_path / "checkout", exclude_flagged=True)
    at = _run(page)
    sel = {s.label: s for s in at.selectbox}
    assert sel["Case set"].value == "all" and sel["Split"].value == "training"
    assert any(m.label == "cases" and str(m.value) == str(rep["case_counts"]["BOW"]["training"]["forecast_h72"]) for m in at.metric)
    assert at.get("plotly_chart")  # visible weather and the latest visible pit
    at.toggle[0].set_value(True).run()  # training truth may be shown
    assert not at.exception
    sel = {s.label: s for s in at.selectbox}
    sel["Case type"].set_value("next_pit").run()
    assert not at.exception
    [b for b in at.button if b.label == "Re-run the leakage checks"][0].click().run()
    assert not at.exception and any("pass" in str(s.value) for s in at.success)


def test_leaderboard_page_with_a_competition_run(tmp_path, monkeypatch):
    from snowagent.lab.benchmark.builder import build_cases
    from snowagent.lab.competition.runner import EngineSpec, run_competition
    from snowagent.lab.genome import default_genomes
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths
    from tests.unit.lab_fixtures import write_synthetic_lab

    cfg = load_lab_config(REPO / "config/lab.yaml")
    paths = LabPaths(tmp_path / "lab")
    write_synthetic_lab(paths.root, tmp_path / "checkout", cfg)
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(paths.root))
    page = REPO / "lab_app/pages/3_Leaderboard.py"
    build_cases(paths, cfg, tmp_path / "checkout", exclude_flagged=True)
    assert "No competition run yet" in _text(_run(page))

    run_competition(paths, cfg, default_genomes(), engine=EngineSpec(kind="fake"), run_id="ui-run")
    at = _run(page)
    assert len(at.dataframe) >= 1 and "7 cases" in " ".join(str(h.value) for h in at.subheader)
    assert len(at.get("plotly_chart")) == 2  # the prediction beside the observed pit
    multi = {m.label: m for m in at.multiselect}
    assert multi["Weather source"].value == ["station"]  # ADR-076: the synthetic season is station-driven
    multi["Forecast source"].set_value(["archived_gfs"]).run()
    assert not at.exception and "1 cases" in " ".join(str(h.value) for h in at.subheader)
    sel = {s.label: s for s in at.selectbox}
    sel["Agent"].set_value("analogue-default").run()  # an insufficient answer: shown as text, no crash
    assert not at.exception


START_PAGE = REPO / "lab_app/pages/4_Training.py"
WATCH_PAGE = REPO / "lab_app/pages/9_Run_progress.py"
RESULTS_PAGE = REPO / "lab_app/pages/10_Run_results.py"
CHECK_PAGE = REPO / "lab_app/pages/12_Promotion_check.py"


def _training_lab(tmp_path, monkeypatch):
    from snowagent.lab.benchmark.builder import build_cases
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths
    from tests.unit.lab_fixtures import write_multiseason_lab

    cfg = load_lab_config(REPO / "config/lab.yaml")
    paths = LabPaths(tmp_path / "lab")
    write_multiseason_lab(paths.root, tmp_path / "checkout", cfg, ("2022-2023", "2023-2024"))
    build_cases(paths, cfg, tmp_path / "checkout", exclude_flagged=True)
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(paths.root))
    return cfg, paths


def _train(cfg, paths, run_id: str, rounds: int = 3) -> None:
    from snowagent.lab.competition.runner import EngineSpec
    from snowagent.lab.training.loop import TrainOptions, run_training

    run_training(paths, cfg, TrainOptions.from_config(cfg, rounds=rounds, population=4, engine=EngineSpec(kind="fake")),
                 run_id=run_id, log=lambda m: None)


def test_start_a_run_page_starts_runs_and_sets_up_the_parker_ridge_check(tmp_path, monkeypatch, fake_launch):
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(tmp_path / "empty"))
    text = _text(_run(START_PAGE))
    assert "not an avalanche forecast" in text and "No lab data yet" in text and "No benchmark cases yet" in text

    cfg, paths = _training_lab(tmp_path, monkeypatch)

    def inner(i: int) -> list[str]:  # the training command the job runner runs
        return _job_steps(fake_launch[i])[0]

    def start(at):  # the form's Start training button (the Parker Ridge check's set-up button comes first)
        return next(b for b in at.button if b.label == "Start training").click().run()

    at = _run(START_PAGE)
    text = _text(at)
    assert "Parker Ridge check not set up yet" in text and "No benchmark cases yet" not in text
    assert "**Tuned SNOWPACK** = SNOWPACK with evolved settings" in text and "**Column model** = " in text  # names
    assert [h.value for h in at.subheader] == ["1. Jasper: the Parker Ridge check (optional, set up once)",
                                               "2. The run"]
    assert {c.label: c for c in at.checkbox}[
        "Also score each round on the Parker Ridge pits (Jasper), never training on them"].disabled
    start(at)  # the first kind of run: Overnight
    assert not at.exception and fake_launch
    runner, kw = fake_launch[0]
    assert runner[1:3] == ["-m", "snowagent.lab.services.jobs"] and kw["start_new_session"]  # detached job
    cmd = inner(0)
    assert cmd[1:5] == ["-m", "snowagent.cli", "lab", "train"]
    assert cmd[cmd.index("--rounds") + 1] == "20" and cmd[cmd.index("--population") + 1] == "10"
    assert cmd[cmd.index("--screen-cases") + 1] == "30" and cmd[cmd.index("--data-root") + 1] == str(paths.root.resolve())
    assert any("Started training run" in str(s.value) for s in at.success)  # shown after the page redraws
    assert "--family-slots" not in cmd and "--plots" not in cmd and "--initial" not in cmd

    at = _run(START_PAGE)
    {s.label: s for s in at.selectbox}["Kind of run"].set_value("Custom").run()
    {n.label: n for n in at.number_input}["Screen cases (0 = off)"].set_value(0)
    {c.label: c for c in at.checkbox}["Family slots"].check()
    start(at)
    cmd = inner(-1)
    assert not at.exception and cmd[cmd.index("--rounds") + 1] == "10"  # Custom: the configuration's defaults
    assert "--screen-cases" not in cmd and "--family-slots" in cmd

    at = _run(START_PAGE)
    {s.label: s for s in at.selectbox}["Kind of run"].set_value("Quick check (about 20 minutes, one plot)").run()
    start(at)
    cmd = inner(-1)
    assert cmd[cmd.index("--rounds") + 1] == "2" and cmd[cmd.index("--plots") + 1] == "SIMP"
    assert cmd[cmd.index("--case-types") + 1] == "next_pit" and "--area-check" not in cmd

    at = _run(START_PAGE)  # ADR-118's kind of run, the column evolution: the lab's own model alone
    {s.label: s for s in at.selectbox}["Kind of run"].set_value("Column model, no SNOWPACK").run()
    start(at)
    cmd = inner(-1)
    assert cmd[cmd.index("--initial") + 1] == "column" and cmd[cmd.index("--rounds") + 1] == "30"

    from snowagent.lab.services import labels

    labels.save_local_names(paths, {"tuned": "Ada"})  # this computer's own name for an evolution shows in its place
    text = _text(_run(START_PAGE))
    assert "**Ada** = SNOWPACK with evolved settings" in text and "Tuned SNOWPACK" not in text
    labels.save_local_names(paths, {})

    at = _run(START_PAGE)  # ADR-117: the set-up button starts the area job (bundle, pits, import, cases)
    next(b for b in at.button if b.label == "Set up Parker Ridge check").click().run()
    job = json.loads((Path(fake_launch[-1][0][-1]) / "job.json").read_text())
    assert not at.exception and [s["name"] for s in job["steps"]] == ["prepare-area", "init", "import", "build-cases"]
    assert any("Setting up the Parker Ridge check has started" in str(s.value) for s in at.success)
    # its state shows on the page itself, not inside a folded box, and in plain words (the runner here is a fake
    # that is not alive, so the job reads as cut short)
    assert any("The Parker Ridge check set-up" in str(w.value) and "was cut short" in str(w.value)
               for w in at.warning)

    # while a run is going (its process: this one): Start is refused, and the run can be stopped from here
    _train(cfg, paths, "ui-train", rounds=1)
    sf = paths.outputs / "training" / "ui-train" / "status.json"
    done = json.loads(sf.read_text())
    sf.write_text(json.dumps(done | {"state": "running", "pid": os.getpid(), "round": 1, "done": 5, "total": 10,
                                     "round_started_at": done["updated_at"], "estimate_s": 60}))
    at = _run(START_PAGE)
    assert "One run at a time" in _text(at) and at.button("FormSubmitter:train-Start training").disabled
    assert any("Training" in str(i.value) and "round 1 of 1" in str(i.value) for i in at.info)  # the bar on top
    at.button("train-stop-busy").click().run()
    assert not at.exception and (sf.parent / "stop").exists()
    assert any("Stop requested" in str(s.value) for s in at.success)


def test_watch_the_run_page_without_runs_finished_running_and_stopped(tmp_path, monkeypatch, fake_launch):
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(tmp_path / "empty"))
    for page in (WATCH_PAGE, RESULTS_PAGE, CHECK_PAGE):
        text = _text(_run(page))
        assert "not an avalanche forecast" in text and "No training run yet" in text

    cfg, paths = _training_lab(tmp_path, monkeypatch)
    _train(cfg, paths, "ui-train")
    at = _run(WATCH_PAGE)
    assert {s.label: s for s in at.selectbox}["Training run"].value == "ui-train"
    # an old run is never mistaken for one that is going: it says when it ended
    over = " ".join(str(s.value) for s in at.success)
    assert "This run is over." in over and "after 3 of 3 rounds. Nothing is running here now." in over
    text = _text(at)
    assert "warning signal only" in text and "Promotion check page" in text
    assert len(at.get("plotly_chart")) == 2  # under More charts: best composite per round, gap with flags
    assert len(at.dataframe) == 1  # every round in numbers
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["State"] == "finished" and metrics["Round"] == "3 / 3" and "Best score so far" in metrics
    assert "Time left" not in metrics and not [b for b in at.button if b.label == "Stop the run"]
    labels = {e.label for e in at.expander}
    assert "Technical details (the run's settings and its log)" in labels
    from snowagent.lab.competition.scoring import SCORING_VERSION
    from snowagent.lab.services.training import best_so_far

    best = best_so_far(paths, SCORING_VERSION)
    assert best["run_id"] == "ui-train" and best["round"] == 3 and best["composite"] >= best["round1"] - 1e-9
    assert best_so_far(paths, "another-version") is None
    overview = _run(REPO / "lab_app/pages/0_Overview.py")
    assert any("Best agent so far" in str(s.value) for s in overview.success)

    # the same run while it runs (its process: this one): time left, Stop, and the bar at the top of the page
    sf = paths.outputs / "training" / "ui-train" / "status.json"
    done = json.loads(sf.read_text())
    sf.write_text(json.dumps(done | {"state": "running", "pid": os.getpid(), "round": 3, "done": 5, "total": 10,
                                     "round_started_at": done["updated_at"], "estimate_s": 60}))
    at = _run(WATCH_PAGE)
    assert not at.exception
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["State"] == "running" and metrics["Time left"]
    assert any("This run is going now." in str(i.value) for i in at.info)
    assert any("**Training**: round 3 of 3" in str(i.value) for i in at.info)  # the bar
    overview = _run(REPO / "lab_app/pages/0_Overview.py")
    assert any("A training run is going" in str(i.value) for i in overview.info)
    at.button("train-stop").click().run()
    assert not at.exception and (sf.parent / "stop").exists() and "Stop requested" in _text(at)
    (sf.parent / "stop").unlink()

    # stopped: it says so, and Resume starts the run again from where it stopped
    sf.write_text(json.dumps(done | {"state": "stopped"}))
    at = _run(WATCH_PAGE)
    assert any("This run is not going now." in str(w.value) and "was stopped" in str(w.value) for w in at.warning)
    at.button("train-resume").click().run()
    assert not at.exception
    cmd = _job_steps(fake_launch[-1])[0]
    assert "--resume" in cmd and cmd[cmd.index("--run-id") + 1] == "ui-train"


def test_run_results_page_lists_the_agents_and_remembers_the_one_chosen(tmp_path, monkeypatch):
    cfg, paths = _training_lab(tmp_path, monkeypatch)
    _train(cfg, paths, "ui-train")
    at = _run(RESULTS_PAGE)
    assert [h.value for h in at.subheader] == ["The agents, best first", "Agent card"]
    assert len(at.dataframe) >= 1 and len(at.code) == 1  # the round's table; the family tree, folded away
    assert "after 3 of 3 rounds" in _text(at)  # when the run ended
    agent = {s.label: s for s in at.selectbox}["Agent"]
    assert at.session_state["lab-agent"] == ("ui-train", 3, agent.value)  # Blind test and site opens on it
    assert any(str(m.value).startswith("**Next with ") for m in at.markdown)
    at.select_slider[0].set_value(1).run()  # an earlier round
    assert not at.exception and at.session_state["lab-agent"][1] == 1


def test_default_run_index_prefers_current_scoring_finished_and_largest(tmp_path):
    import json as _json

    from snowagent.lab.ui.app import default_run_index

    def run(name, version, n, rounds=1, state="finished", case_set="all"):
        d = tmp_path / name
        d.mkdir()
        plan = {"scoring_version": version, "case_ids": [f"c{i}" for i in range(n)], "rounds": rounds,
                "case_set": case_set}
        (d / "run.json").write_text(_json.dumps({"plan": plan}))
        (d / "status.json").write_text(_json.dumps({"state": state}))

    run("big-check-2022-2023", "v2", 900, 5, case_set="loso_2022-2023")  # a promotion check's fold, newest
    run("smoke", "v2", 8)                      # tiny
    run("big-running", "v2", 340, 6, "running")
    run("big", "v2", 340, 6)
    run("old-scoring", "v1", 340, 10)
    runs = ["big-check-2022-2023", "smoke", "big-running", "big", "old-scoring", "missing"]
    assert runs[default_run_index(tmp_path, runs, "v2")] == "big"
    assert default_run_index(tmp_path, [], "v2") == 0
    short = ["missing", "smoke"]
    assert short[default_run_index(tmp_path, short, "v2")] == "smoke"


# --------------------------------------------------------------------------------------------- browser controls


class _FakePopen:
    """Records the job runner launch instead of starting it (the job then reads as interrupted: pid not alive)."""

    launched: list = []

    def __init__(self, cmd, **kw):
        _FakePopen.launched.append((cmd, kw))
        self.pid = 4242


def _job_steps(launch) -> list[list[str]]:
    job_dir = Path(launch[0][-1])
    return [s["command"] for s in json.loads((job_dir / "job.json").read_text())["steps"]]


@pytest.fixture
def fake_launch(monkeypatch):
    from snowagent.lab.services import jobs

    _FakePopen.launched = []
    monkeypatch.setattr(jobs, "_launch", _FakePopen)
    return _FakePopen.launched


def _wait(pred, timeout=20.0):
    import time

    t0 = time.time()
    while time.time() - t0 < timeout:
        if pred():
            return True
        time.sleep(0.1)
    return False


def test_lab_app_command_launches_streamlit_local_only(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from snowagent.lab import cli

    calls = []
    monkeypatch.setattr(cli, "_serve", lambda cmd, env: calls.append((cmd, env)) or 0)
    monkeypatch.chdir(tmp_path)  # any working directory
    res = CliRunner().invoke(cli.lab_app, ["app", "--no-open", "--data-root", str(tmp_path / "lab")])
    assert res.exit_code == 0, res.output
    assert "http://localhost:8501" in res.output and "not an avalanche forecast" in res.output
    cmd, env = calls[0]
    assert cmd[1:4] == ["-m", "streamlit", "run"] and Path(cmd[4]) == REPO / "lab_app/Home.py"
    assert cmd[cmd.index("--server.address") + 1] == "127.0.0.1" and cmd[cmd.index("--server.port") + 1] == "8501"
    assert cmd[cmd.index("--browser.gatherUsageStats") + 1] == "false"
    # ADR-119: one version of the code until the app is restarted, and the update check on unless switched off
    assert cmd[cmd.index("--server.fileWatcherType") + 1] == "none"
    assert env["SNOWAGENT_LAB_DATA_ROOT"] == str((tmp_path / "lab").resolve())
    assert env["SNOWAGENT_LAB_UPDATE_CHECK"] == "1" and "SNOWAGENT_LAB_STARTED_HEAD" not in env
    CliRunner().invoke(cli.lab_app, ["app", "--no-open", "--no-update-check"])
    assert calls.pop()[1]["SNOWAGENT_LAB_UPDATE_CHECK"] == "0"
    res = CliRunner().invoke(cli.lab_app, ["app", "--no-open", "--host", "0.0.0.0", "--port", "8600"])
    cmd, env = calls[1]
    assert cmd[cmd.index("--server.address") + 1] == "0.0.0.0" and "http://localhost:8600" in res.output
    assert "no login" in res.output


def test_job_runs_detached_refuses_a_duplicate_stops_and_resumes(tmp_path):
    import sys

    from snowagent.lab.services.jobs import (
        JobBusy,
        job_info,
        list_jobs,
        resume_job,
        start_job,
        step,
        stop_job,
    )
    from snowagent.lab.storage.paths import LabPaths

    paths = LabPaths(tmp_path / "lab")
    sleeper = [sys.executable, "-c", "import time; print('working', flush=True); time.sleep(60)"]
    job = start_job(paths, "compete", "sleeper", [step("sleep", sleeper)], cwd=tmp_path)
    jid = job["job_id"]
    try:
        assert _wait(lambda: job_info(paths, jid)["state"] == "running")
        assert _wait(lambda: "working" in Path(job_info(paths, jid)["log"]).read_text())
        with pytest.raises(JobBusy):
            start_job(paths, "compete", "again", [step("sleep", sleeper)], cwd=tmp_path)  # one per kind
        assert "stopped" in stop_job(paths, jid)
        assert _wait(lambda: job_info(paths, jid)["state"] == "stopped")
        info = job_info(paths, jid)
        assert info["step_states"][0]["state"] == "stopped" and "job stopped" in Path(info["log"]).read_text()
        again = resume_job(paths, jid)  # the same steps again, linked to the stopped job
        assert again["resume_of"] == jid and len(list_jobs(paths)) == 2
        assert _wait(lambda: job_info(paths, again["job_id"])["state"] == "running")
    finally:
        for j in list_jobs(paths):
            stop_job(paths, j)
    assert _wait(lambda: all(job_info(paths, j)["state"] == "stopped" for j in list_jobs(paths)))

    # steps run in order; a failing step ends the job and is reported with its exit code
    ok = [sys.executable, "-c", "print('one')"]
    bad = [sys.executable, "-c", "import sys; print('two'); sys.exit(3)"]
    job = start_job(paths, "setup", "three steps", [step("a", ok), step("b", bad), step("c", ok)], cwd=tmp_path)
    assert _wait(lambda: job_info(paths, job["job_id"])["state"] == "failed")
    info = job_info(paths, job["job_id"])
    states = info["step_states"]
    assert [s["state"] for s in states] == ["done", "failed", "waiting"] and states[1]["exit_code"] == 3
    log = Path(info["log"]).read_text()
    assert "one" in log and "two" in log and "step 2/3: b" in log
    job = start_job(paths, "setup", "fine", [step("a", ok), step("c", ok)], cwd=tmp_path)
    assert _wait(lambda: job_info(paths, job["job_id"])["state"] == "finished")


def test_job_state_interrupted_when_the_runner_is_gone_and_training_stops_politely(tmp_path, fake_launch):
    from snowagent.lab.services.jobs import job_info, stop_job
    from snowagent.lab.services.training import resume_training, start_training
    from snowagent.lab.storage.paths import LabPaths

    paths = LabPaths(tmp_path / "lab")
    info = start_training(paths, REPO / "config/lab.yaml", run_id="t1", cwd=tmp_path, rounds=2, population=4,
                          survivors=2, mutation_strength=0.2, crossover_share=0.25, seed=0, workers=2)
    assert job_info(paths, info["job_id"])["state"] == "interrupted"  # pid 4242 is not a job runner
    run_dir = paths.outputs / "training" / "t1"
    status = json.loads((Path(fake_launch[0][0][-1]) / "status.json").read_text())
    (Path(fake_launch[0][0][-1]) / "status.json").write_text(json.dumps(status | {"state": "running"}))
    import os

    job = json.loads((Path(fake_launch[0][0][-1]) / "job.json").read_text())
    (Path(fake_launch[0][0][-1]) / "job.json").write_text(json.dumps(job | {"pid": os.getpid()}))
    from snowagent.lab.services import jobs

    jobs_runner_alive = jobs.runner_alive
    try:
        jobs.runner_alive = lambda pid: True
        assert "stop requested" in stop_job(paths, info["job_id"]) and (run_dir / "stop").is_file()
    finally:
        jobs.runner_alive = jobs_runner_alive
    again = resume_training(paths, REPO / "config/lab.yaml", "t1", workers=3, cwd=tmp_path)
    cmd = _job_steps(fake_launch[-1])[0]
    assert not (run_dir / "stop").exists() and "--resume" in cmd and cmd[cmd.index("--workers") + 1] == "3"
    assert cmd[cmd.index("--data-root") + 1] == str(paths.root.resolve()) and again["run_id"] == "t1"


def test_home_set_up_data_panel_starts_prepare_init_import(tmp_path, monkeypatch, fake_launch):
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(tmp_path / "empty"))
    at = _run(PAGES[0])
    assert "Set up data" in [e.label for e in at.expander]
    assert any("ERA5 months to fetch" in str(c.value) for c in at.caption)
    {c.label: c for c in at.checkbox}["Fetch ERA5 months"].uncheck()
    [b for b in at.button if b.label.startswith("Run set-up")][0].click().run()
    assert not at.exception and fake_launch
    prepare, init, imp = _job_steps(fake_launch[0])
    assert prepare[3:6] == ["lab", "prepare", "--no-era5"] and init[3:5] == ["lab", "init"]
    assert imp[3:5] == ["lab", "import"] and imp[imp.index("--data-root") + 1] == str((tmp_path / "empty").resolve())
    at = _run(PAGES[0])  # the job's state in plain words (on the bar and in the box), its steps and its log
    text = _text(at)
    assert text.count("**Data set-up** was cut short in step 1 of 3 (getting the station files, pits and weather)") == 2
    assert "1. getting the station files, pits and weather" in text and "3. loading the pits and weather" in text
    assert "Technical details (the log)" in [e.label for e in at.expander] and "not an avalanche forecast" in text
    assert [b.label for b in at.button if b.label in ("Resume", "Hide")] == ["Hide", "Resume"]


def test_leaderboard_runs_a_competition_and_the_jobs_page_lists_it(tmp_path, monkeypatch, fake_launch):
    from snowagent.lab.benchmark.builder import build_cases
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths
    from tests.unit.lab_fixtures import write_synthetic_lab

    jobs_page = REPO / "lab_app/pages/6_Jobs.py"
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(tmp_path / "empty"))
    assert "No background job yet" in _text(_run(jobs_page))
    cfg = load_lab_config(REPO / "config/lab.yaml")
    paths = LabPaths(tmp_path / "lab")
    write_synthetic_lab(paths.root, tmp_path / "checkout", cfg)
    build_cases(paths, cfg, tmp_path / "checkout", exclude_flagged=True)
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(paths.root))
    at = _run(REPO / "lab_app/pages/3_Leaderboard.py")
    assert "Run a competition" in [e.label for e in at.expander]
    {m.label: m for m in at.multiselect}["Agents"].set_value(["persistence", "weather_rule"])
    {s.label: s for s in at.selectbox}["Cases"].set_value("100")
    {s.label: s for s in at.selectbox}["Engine"].set_value("none")
    [b for b in at.button if b.label == "Start competition"][0].click().run()
    assert not at.exception and any("Started competition" in str(s.value) for s in at.success)
    (cmd,) = _job_steps(fake_launch[0])
    assert cmd[3:5] == ["lab", "compete"] and cmd[cmd.index("--limit") + 1] == "100"
    assert cmd[cmd.index("--engine") + 1] == "none" and cmd.count("--agents") == 2

    at = _run(jobs_page)
    assert any(m.label == "Finished, stopped or failed" and str(m.value) == "1" for m in at.metric)
    assert "**The competition** was cut short" in _text(at) and len(at.dataframe) == 1
    assert any("snowagent.cli lab compete" in str(c.value) for c in at.code)  # the command, folded away
    assert not [b for b in at.button if b.label == "Refresh"]  # the pages update themselves


def test_promotion_check_page_estimates_then_starts_resumes_and_shows_the_verdict(tmp_path, monkeypatch, fake_launch):
    cfg, paths = _training_lab(tmp_path, monkeypatch)
    _train(cfg, paths, "pc", rounds=2)
    page = CHECK_PAGE
    at = _run(page)
    assert "No promotion check yet" in _text(at) and "check-loso --genome pc/2/1" in _text(at)
    start = [b for b in at.button if b.label == "2. Start the check"][0]
    assert start.disabled  # estimate first
    [b for b in at.button if b.label == "1. Estimate the time"][0].click().run()
    assert not at.exception
    (est,) = _job_steps(fake_launch[0])
    assert est[3:5] == ["lab", "check-loso"] and "--estimate-only" in est and est[est.index("--genome") + 1] == "pc/2/1"
    job_dir = Path(fake_launch[0][0][-1])
    (job_dir / "status.json").write_text(json.dumps({"state": "finished", "steps": [{"name": "estimate",
                                                                                      "state": "done"}]}))
    Path(json.loads((job_dir / "job.json").read_text())["log"]).write_text("check-loso estimate: about 3 min\n")
    at = _run(page)
    assert "check-loso estimate: about 3 min" in " ".join(str(c.value) for c in at.code)
    start = [b for b in at.button if b.label == "2. Start the check"][0]
    assert not start.disabled
    start.click().run()
    assert not at.exception
    (chk,) = _job_steps(fake_launch[1])
    assert "--estimate-only" not in chk and chk[chk.index("--check-id") + 1] == "pc-r2-k1-loso"
    assert chk[chk.index("--genome") + 1] == "pc/2/1"

    # an unfinished check is resumed from its stored plan under the same id
    from snowagent.lab.training.loso import checks_root

    d = checks_root(paths) / "half"
    d.mkdir(parents=True)
    (d / "check.json").write_text(json.dumps({"plan": {"genome_ref": "pc/2/1", "seasons": ["2022-2023"],
                                                        "differs_from_training_run": {"rounds": {
                                                            "training_run": 2, "check": 1}}}}))
    (d / "status.json").write_text(json.dumps({"state": "running", "phase": "fold 1/1", "pid": 999999}))
    at = _run(page)
    assert any("Interrupted" in str(w.value) for w in at.warning) or "half" not in str(
        {s.label: s.value for s in at.selectbox})
    [b for b in at.button if b.label == "Resume check"][0].click().run()
    assert not at.exception
    (res,) = _job_steps(fake_launch[-1])
    assert res[res.index("--check-id") + 1] == "half" and res[res.index("--rounds") + 1] == "1"
    assert res[res.index("--season") + 1] == "2022-2023"

    from snowagent.lab.training.loso import check_loso

    check_loso(paths, cfg, "pc/2/1", None, source=tmp_path / "checkout", log=lambda m: None, check_id="ui-check")
    at = _run(page)
    assert not at.exception and "Results" in [h.value for h in at.subheader]
    {s.label: s for s in at.selectbox}["Check"].set_value("ui-check").run()
    assert not at.exception
    assert any("pooled held-out composite" in str(x.value) for x in [*at.success, *at.error])


def test_winter_test_page_estimates_then_starts_and_shows_each_winter(tmp_path, monkeypatch, fake_launch):
    """ADR-125: the winter-by-winter test's page; estimate first, then start, then every winter's three scores."""
    cfg, paths = _training_lab(tmp_path, monkeypatch)
    page = REPO / "lab_app/pages/13_Winter_test.py"
    at = _run(page)
    assert "not an avalanche forecast" in _text(at) and "No winter-by-winter test yet" in _text(at)
    start = [b for b in at.button if b.label == "2. Start the test"][0]
    assert start.disabled  # estimate first
    [b for b in at.button if b.label == "1. Estimate the time"][0].click().run()
    assert not at.exception
    (est,) = _job_steps(fake_launch[0])
    assert est[3:5] == ["lab", "winter-test"] and "--estimate-only" in est
    assert est[est.index("--initial") + 1] == "column" and est[est.index("--folds") + 1] == "5"
    assert est[est.index("--rounds") + 1] == "6" and "--include-locked" not in est
    job_dir = Path(fake_launch[0][0][-1])
    (job_dir / "status.json").write_text(json.dumps({"state": "finished", "steps": [{"name": "estimate",
                                                                                      "state": "done"}]}))
    Path(json.loads((job_dir / "job.json").read_text())["log"]).write_text(
        "winter test estimate: about 2.0 h\n" + json.dumps({
            "builds": ["a+b", "c+d"], "build_s": 300.0, "workers": 8, "total_low_s": 7200.0, "total_high_s": 7200.0,
            "folds": {"a+b": {"low_s": 3450.0, "high_s": 3450.0}, "c+d": {"low_s": 3450.0, "high_s": 3450.0}}},
            indent=1) + "\n=== job finished\n")
    at = _run(page)
    # the estimate is said in a sentence, not left as a log to read
    assert any("About 2 h 00 min on this computer with 8 workers: about 57 min for each of the 2 groups of winters"
               in str(i.value) and "0.7 GB of disk" in str(i.value) for i in at.info)
    start = [b for b in at.button if b.label == "2. Start the test"][0]
    assert not start.disabled
    start.click().run()
    assert not at.exception
    (run,) = _job_steps(fake_launch[1])
    assert "--estimate-only" not in run and run[run.index("--check-id") + 1] == "winter-column-5f-r6-p10"

    from snowagent.lab.competition.runner import EngineSpec
    from snowagent.lab.genome import default_genome
    from snowagent.lab.schemas.genome import AgentFamily
    from snowagent.lab.training.loop import TrainOptions
    from snowagent.lab.training.loso import check_loso

    opts = TrainOptions.from_config(cfg, rounds=1, population=3, engine=EngineSpec(kind="fake"),
                                    initial=[default_genome(AgentFamily.persistence, cfg.genome),
                                             default_genome(AgentFamily.weather_rule, cfg.genome)])
    check_loso(paths, cfg, None, opts, source=tmp_path / "checkout", log=lambda m: None, check_id="ui-winter",
               folds=5, exclude_locked=True)
    at = _run(page)
    assert "Results" in [h.value for h in at.subheader]
    text = _text(at)
    assert "Each of 2 winters was forecast by agents trained only on the other winters" in text
    assert "What training added" in text and len(at.dataframe) == 1
    # a winter test is not a promotion check: the Promotion check page does not list it
    assert "No promotion check yet" in _text(_run(CHECK_PAGE)) or "No training run yet" in _text(_run(CHECK_PAGE))


# --------------------------------------------------------------------------------------------- arena (ADR-078)


def test_arena_page_without_runs_with_a_feed_and_replayed_from_files(tmp_path, monkeypatch):
    import os

    from snowagent.lab import events
    from snowagent.lab.benchmark.builder import build_cases
    from snowagent.lab.competition.runner import EngineSpec, run_competition
    from snowagent.lab.genome import default_genomes
    from snowagent.lab.services.arena import arena_runs
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths
    from snowagent.lab.training.loop import TrainOptions, run_training
    from tests.unit.lab_fixtures import write_multiseason_lab

    page = REPO / "lab_app/pages/5_Arena.py"
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(tmp_path / "empty"))
    text = _text(_run(page))
    assert "not an avalanche forecast" in text and "No competition or training run yet" in text

    cfg = load_lab_config(REPO / "config/lab.yaml")
    paths = LabPaths(tmp_path / "lab")
    write_multiseason_lab(paths.root, tmp_path / "checkout", cfg, ("2022-2023", "2023-2024"))
    build_cases(paths, cfg, tmp_path / "checkout", exclude_flagged=True)
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(paths.root))
    feed = events.CompetitionFeed(paths.outputs / "competitions" / "arena-c")
    run_competition(paths, cfg, default_genomes(), engine=EngineSpec(kind="fake"), run_id="arena-c",
                    progress=feed.progress)
    feed.finish()
    at = _run(page)
    assert {m.label: m.value for m in at.metric}["State"] == "finished"
    assert len(at.get("plotly_chart")) >= 3  # race, heat strip, duel profiles
    assert len(at.tabs) == 3  # no evolution tab for a competition
    slider = at.slider[0]
    slider.set_value(3).run()  # replay position
    assert not at.exception and {m.label: m.value for m in at.metric}["Results shown"].startswith("3 of")

    run_training(paths, cfg, TrainOptions.from_config(cfg, rounds=2, population=4, engine=EngineSpec(kind="fake")),
                 run_id="arena-t", log=lambda m: None)
    monkeypatch.setenv(events.ENV, "0")  # a run recorded without a feed: replayed from its round files
    run_training(paths, cfg, TrainOptions.from_config(cfg, rounds=2, population=4, seed=3,
                                                      engine=EngineSpec(kind="fake")), run_id="arena-old",
                 log=lambda m: None)
    names = [f"{r['kind']} · {r['run_id']}" for r in arena_runs(paths)]
    for run_id, rebuilt in (("arena-t", False), ("arena-old", True)):
        at = _run(page)
        at.selectbox[0].set_value(names.index(f"training · {run_id}")).run()
        assert not at.exception, [e.value for e in at.exception]
        metrics = {m.label: m.value for m in at.metric}
        assert metrics["Round"] == "2 of 2" and metrics["State"] == "finished"
        assert len(at.tabs) == 4  # race, heat strip, duel, evolution
        assert len(at.get("plotly_chart")) >= 5  # + family tree, best per round, gap
        assert ("no live feed" in _text(at)) == rebuilt
    at.slider[0].set_value(1).run()  # back to round 1's first result
    assert not at.exception and {m.label: m.value for m in at.metric}["Round"] == "1 of 2"
    [b for b in at.button if b.label == "▶ Play"][0].click().run()
    assert not at.exception

    # a running training (its process alive) is live
    st_file = paths.outputs / "training" / "arena-t" / "status.json"
    st_file.write_text(json.dumps(json.loads(st_file.read_text()) | {"state": "running", "pid": os.getpid()}))
    assert next(r for r in arena_runs(paths) if r["run_id"] == "arena-t")["live"]
    assert arena_runs(paths)[0]["run_id"] == "arena-t"  # live runs first


def test_resume_of_a_training_that_failed_before_its_run_existed_starts_it_again(tmp_path, fake_launch):
    from snowagent.lab.services.jobs import resume_job
    from snowagent.lab.services.training import start_training
    from snowagent.lab.storage.paths import LabPaths

    paths = LabPaths(tmp_path / "lab")
    info = start_training(paths, REPO / "config/lab.yaml", run_id="t0", cwd=tmp_path, rounds=2, population=4,
                          survivors=2, mutation_strength=0.2, crossover_share=0.25, seed=0, workers=2)
    resume_job(paths, info["job_id"])  # no run.json yet: the original command, not --resume
    cmd = _job_steps(fake_launch[-1])[0]
    assert "--resume" not in cmd and cmd[cmd.index("--rounds") + 1] == "2"
    (paths.outputs / "training" / "t0" / "run.json").write_text("{}")
    resume_job(paths, info["job_id"])
    assert "--resume" in _job_steps(fake_launch[-1])[0]


def test_default_workers_is_between_one_and_eight():
    from snowagent.lab.ui.app import default_workers

    assert 1 <= default_workers() <= 8


def test_time_left_uses_the_runs_own_rounds_and_the_current_round_so_far():
    from datetime import UTC, datetime, timedelta

    import pandas as pd

    from snowagent.lab.services.training import time_left
    from snowagent.lab.ui.app import finish_text

    now = datetime(2026, 10, 6, 6, 0, tzinfo=UTC)
    started = (now - timedelta(minutes=30)).isoformat()
    trace = pd.DataFrame({"round": [1, 2, 3], "wall_s": [600.0, 3600.0, 5400.0]})
    ov = {"plan": {"rounds": 6}, "rounds": trace,
          "status": {"state": "running", "round": 4, "round_started_at": started, "estimate_s": 9000}}
    # rounds 2-3: median 75 min; round 4 has 45 min left; rounds 5 and 6 to go
    assert time_left(ov, now) == 2700 + 2 * 4500
    ov["rounds"] = trace.iloc[:1]  # only round 1 so far: its time
    assert time_left(ov, now) == 0 + 2 * 600
    ov["rounds"] = trace.iloc[:0]  # nothing measured: the round's own estimate
    assert time_left(ov, now) == 7200 + 2 * 9000
    assert time_left(ov | {"status": {"state": "stopped"}}, now) is None
    # a round running longer than usual: what its own progress implies (30 min for a quarter: 90 min to go)
    ov = {"plan": {"rounds": 4}, "rounds": trace.iloc[:2],
          "status": {"state": "running", "round": 4, "round_started_at": started, "done": 25, "total": 100}}
    assert time_left(ov, now) == 5400
    # a round that screened new settings on a few cases first: its full scoring counts from 0 again, and the time
    # left is that scoring's own pace (10 cases in its first minute, 820 to go), not the round's whole 30 minutes
    # spread over its first 10 cases, which made a promotion check on the owner's Mac show 33 hours (2026-11-19)
    ov["status"] |= {"step_started_at": (now - timedelta(minutes=1)).isoformat(), "done": 10, "total": 830}
    assert time_left(ov, now) == pytest.approx(60 * 82)
    del ov["status"]["step_started_at"]  # the status of a run of older code: as before
    assert time_left(ov, now) == pytest.approx(30 * 60 * 82)
    assert finish_text(5400, datetime(2026, 10, 6, 22, 15)) == "about 1 h 30 min, done around 23:45"
    assert finish_text(None) == "not known yet"


def test_default_run_index_prefers_a_run_that_is_running(tmp_path):
    from snowagent.lab.ui.app import default_run_index

    for run_id, state, pid, cases in (("new-running", "running", os.getpid(), 1), ("old-done", "finished", 1, 900),
                                      ("dead", "running", 999_999_999, 900)):
        d = tmp_path / run_id
        d.mkdir()
        (d / "run.json").write_text(json.dumps({"plan": {"scoring_version": "v", "case_ids": list(range(cases))}}))
        (d / "status.json").write_text(json.dumps({"state": state, "pid": pid}))
    assert default_run_index(tmp_path, ["dead", "old-done", "new-running"], "v") == 2
    (tmp_path / "new-running" / "status.json").write_text(json.dumps({"state": "finished"}))
    assert default_run_index(tmp_path, ["dead", "old-done", "new-running"], "v") == 1


def test_gene_rows_name_each_change_in_plain_words():
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.ui.genes import gene_rows

    spec = load_lab_config(REPO / "config/lab.yaml").genome
    rows = {r["gene"]: r for r in gene_rows({"sp_precip_mult_simp": [1.0, 1.12], "sp_rain_snow_mid_c": [1.2, 1.6],
                                             "sp_viscosity_model": ["DEFAULT", "KOJIMA"],
                                             "storm_gap_h": [12, 18]}, spec)}
    assert rows["sp_precip_mult_simp"]["setting"] == "Simpson precipitation"
    assert rows["sp_precip_mult_simp"]["change"] == "×1.12"
    assert rows["sp_rain_snow_mid_c"]["change"] == "+0.4°C" and rows["sp_rain_snow_mid_c"]["this agent"] == "1.6°C"
    assert rows["sp_viscosity_model"]["change"] == "DEFAULT → KOJIMA"
    assert rows["storm_gap_h"]["setting"] == "dry spell that starts a new layer" and rows["storm_gap_h"]["change"] == "+6 h"
    assert all(r["what it does"] for r in rows.values())


GENES_PAGE = REPO / "lab_app/pages/15_Agent_genes.py"


def test_how_agents_change_page_shows_settings_drift_and_family_tree_live_and_finished(tmp_path, monkeypatch):
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(tmp_path / "empty"))
    assert "No training run yet" in _text(_run(GENES_PAGE))

    cfg, paths = _training_lab(tmp_path, monkeypatch)
    _train(cfg, paths, "ui-train")
    at = _run(GENES_PAGE)
    heads = [h.value for h in at.subheader]
    assert heads[0] == "What this run changes" and "One setting, round by round" in heads
    assert heads[-1] == "One agent: its settings and its family tree"
    assert "Kind of model" in {s.label for s in at.selectbox}  # the default population mixes families
    weights = at.dataframe[0].value
    assert weights.set_index("part of the score").at["Layering", "this run, %"] == 35
    assert at.get("plotly_chart")  # the value-by-round chart at least
    agent = {s.label: s for s in at.selectbox}["Agent"]
    assert agent.value and not at.exception
    kind = {s.label: s for s in at.selectbox}["Kind of model"]
    for fam in kind.options:  # every family's view draws
        kind.set_value(fam).run()
        assert not at.exception, fam
        kind = {s.label: s for s in at.selectbox}["Kind of model"]
    at.toggle[0].set_value(False).run()  # every setting, not only the changed ones
    assert not at.exception

    # while the run is going, the round being tested comes from its event log, unscored
    run = paths.outputs / "training" / "ui-train"
    sf = run / "status.json"
    sf.write_text(json.dumps(json.loads(sf.read_text()) | {"state": "running", "pid": os.getpid()}))
    pop = json.loads((run / "rounds" / "r03" / "population.json").read_text())
    agents = [{"agent_id": p["lineage"]["agent_id"], "label": "r04-m01-x", "family": p["lineage"]["family"],
               "genome_hash": "f" * 63 + str(i), "role": "child", "operator": "mutation",
               "parents": [p["lineage"]["genome_hash"]], "changed_genes": {},
               "changed_vs_default": p["lineage"].get("changed_vs_default") or {}} for i, p in enumerate(pop[:2])]
    with open(run / "events.jsonl", "a") as fh:
        fh.write(json.dumps({"ev": "round_started", "round": 4, "agents": agents}) + "\n")
        fh.write(json.dumps({"ev": "case_started", "round": 4}) + "\n")
    at = _run(GENES_PAGE)
    assert any("is being tested now" in str(i.value) for i in at.info)
    assert "updates itself" in _text(at)
