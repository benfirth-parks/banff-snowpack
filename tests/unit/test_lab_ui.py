"""Lab UI smoke test: every page runs without an exception, with no lab data and with imported synthetic data."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

pytest.importorskip("streamlit", reason="lab extra not installed (pip install -e '.[lab]')")
pytest.importorskip("plotly", reason="lab extra not installed (pip install -e '.[lab]')")
pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")
from streamlit.testing.v1 import AppTest  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
PAGES = [REPO / "lab_app/Home.py", REPO / "lab_app/pages/1_Data_Explorer.py",
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
    assert "not an avalanche forecast" in text and "snowagent lab import" in text


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
    multi["Forecast source"].set_value(["archived_gfs"]).run()
    assert not at.exception and "1 cases" in " ".join(str(h.value) for h in at.subheader)
    sel = {s.label: s for s in at.selectbox}
    sel["Agent"].set_value("analogue-default").run()  # an insufficient answer: shown as text, no crash
    assert not at.exception


def test_training_page_without_and_with_runs(tmp_path, monkeypatch):
    from snowagent.lab.benchmark.builder import build_cases
    from snowagent.lab.competition.runner import EngineSpec
    from snowagent.lab.services import jobs
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths
    from snowagent.lab.training.loop import TrainOptions, run_training
    from tests.unit.lab_fixtures import write_multiseason_lab

    page = REPO / "lab_app/pages/4_Training.py"
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(tmp_path / "empty"))
    text = _text(_run(page))
    assert "not an avalanche forecast" in text and "No training run yet" in text and "build-cases" in text

    cfg = load_lab_config(REPO / "config/lab.yaml")
    paths = LabPaths(tmp_path / "lab")
    write_multiseason_lab(paths.root, tmp_path / "checkout", cfg, ("2022-2023", "2023-2024"))
    build_cases(paths, cfg, tmp_path / "checkout", exclude_flagged=True)
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(paths.root))
    launched = []

    class FakePopen:
        def __init__(self, cmd, **kw):
            launched.append((cmd, kw))
            self.pid = 4242

    monkeypatch.setattr(jobs, "_launch", FakePopen)

    def inner(i: int) -> list[str]:  # the training command the job runner runs
        job_dir = Path(launched[i][0][-1])
        return json.loads((job_dir / "job.json").read_text())["steps"][0]["command"]

    at = _run(page)
    assert "No training run yet" in _text(at)
    at.button[0].click().run()  # the form's Start training button
    assert not at.exception and launched
    runner, kw = launched[0]
    assert runner[1:3] == ["-m", "snowagent.lab.services.jobs"] and kw["start_new_session"]  # detached job
    cmd = inner(0)
    assert cmd[1:5] == ["-m", "snowagent.cli", "lab", "train"]
    assert cmd[cmd.index("--rounds") + 1] == "10" and cmd[cmd.index("--data-root") + 1] == str(paths.root.resolve())
    assert any("Started training run" in str(s.value) for s in at.success)
    assert "--screen-cases" not in cmd and "--family-slots" not in cmd  # milestone-5 options off by default
    {n.label: n for n in at.number_input}["Screen cases (0 = off)"].set_value(30)
    {c.label: c for c in at.checkbox}["Family slots"].check()
    at.button[0].click().run()
    cmd = inner(-1)
    assert not at.exception and cmd[cmd.index("--screen-cases") + 1] == "30" and "--family-slots" in cmd

    run_training(paths, cfg, TrainOptions.from_config(cfg, rounds=3, population=4, engine=EngineSpec(kind="fake")),
                 run_id="ui-train", log=lambda m: None)
    at = _run(page)
    sel = {s.label: s for s in at.selectbox}
    sel["Training run"].set_value("ui-train").run()
    assert not at.exception
    text = _text(at)
    assert "warning signal only" in text and "No promotion check yet" in text and "check-loso" in text
    assert len(at.get("plotly_chart")) == 2  # best composite per round, gap with flags
    assert len(at.dataframe) >= 2 and len(at.code) == 2  # leaderboard; the log tail and the lineage
    assert any(m.label == "State" and m.value == "finished" for m in at.metric)

    from snowagent.lab.training.loso import check_loso

    check_loso(paths, cfg, "ui-train/3/1", None, source=tmp_path / "checkout", log=lambda m: None,
               check_id="ui-check")
    at = _run(page)
    {s.label: s for s in at.selectbox}["Training run"].set_value("ui-train").run()
    assert not at.exception
    assert any("pooled held-out composite" in str(x.value) for x in [*at.success, *at.error])


def test_default_run_index_prefers_current_scoring_finished_and_largest(tmp_path):
    import json as _json

    from snowagent.lab.ui.app import default_run_index

    def run(name, version, n, rounds=1, state="finished"):
        d = tmp_path / name
        d.mkdir()
        plan = {"scoring_version": version, "case_ids": [f"c{i}" for i in range(n)], "rounds": rounds}
        (d / "run.json").write_text(_json.dumps({"plan": plan}))
        (d / "status.json").write_text(_json.dumps({"state": state}))

    run("smoke", "v2", 8)                      # newest, tiny
    run("big-running", "v2", 340, 6, "running")
    run("big", "v2", 340, 6)
    run("old-scoring", "v1", 340, 10)
    runs = ["smoke", "big-running", "big", "old-scoring", "missing"]
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
    assert env["SNOWAGENT_LAB_DATA_ROOT"] == str((tmp_path / "lab").resolve())
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
    at = _run(PAGES[0])  # the job's state, steps and log tail are shown
    text = _text(at)
    assert "set up data" in text and "interrupted" in text and "not an avalanche forecast" in text


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
    {n.label: n for n in at.number_input}["Cases (0 = all)"].set_value(5)
    {s.label: s for s in at.selectbox}["Engine"].set_value("none")
    [b for b in at.button if b.label == "Start competition"][0].click().run()
    assert not at.exception and any("Started competition" in str(s.value) for s in at.success)
    (cmd,) = _job_steps(fake_launch[0])
    assert cmd[3:5] == ["lab", "compete"] and cmd[cmd.index("--limit") + 1] == "5"
    assert cmd[cmd.index("--engine") + 1] == "none" and cmd.count("--agents") == 2

    at = _run(jobs_page)
    assert any(m.label == "Finished, stopped or failed" and str(m.value) == "1" for m in at.metric)
    assert "competition" in _text(at) and len(at.code) >= 1


def test_training_page_promotion_check_estimates_then_starts_and_resumes(tmp_path, monkeypatch, fake_launch):
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
    run_training(paths, cfg, TrainOptions.from_config(cfg, rounds=2, population=4, engine=EngineSpec(kind="fake")),
                 run_id="pc", log=lambda m: None)
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(paths.root))
    page = REPO / "lab_app/pages/4_Training.py"
    at = _run(page)
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
