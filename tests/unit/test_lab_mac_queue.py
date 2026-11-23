"""The Mac queue (ADR-148): entries are validated, the runner sets its own options, a queued job starts only when
nothing else runs and the Mac is on mains power, a cut job resumes, and an ended job's small outputs reach the
mac-results branch without touching the checkout."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from snowagent.lab.services import mac_queue as mq


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
                          env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                               "GIT_COMMITTER_EMAIL": "t@t", "PATH": "/usr/bin:/bin:/usr/local/bin",
                               "HOME": str(cwd)}).stdout.strip()


# --------------------------------------------------------------------------------------------- entries


def test_parse_entry_accepts_a_training_and_sets_the_runner_options():
    e = mq.parse_entry({"id": "night-r6-p8", "kind": "train", "args": ["--rounds", "6", "--population", "8"]})
    args = e.command_args(6, Path("/d"), Path("/c.yaml"))
    assert args[0] == "train"
    assert args[args.index("--run-id") + 1] == "night-r6-p8"
    assert args[args.index("--workers") + 1] == "6"
    assert args[args.index("--data-root") + 1] == "/d"
    assert e.ref_ids() == ["night-r6-p8"]


def test_an_entry_that_names_its_own_id_keeps_it():
    e = mq.parse_entry({"id": "q1", "kind": "check-loso", "args": ["--genome", "r/6/1", "--check-id", "r-loso"]})
    args = e.command_args(4, Path("d"), Path("c"))
    assert args.count("--check-id") == 1 and "r-loso" in args
    assert e.ref_ids() == ["r-loso"]


def test_build_cases_takes_no_workers():
    args = mq.parse_entry({"id": "b", "kind": "build-cases"}).command_args(4, Path("d"), Path("c"))
    assert "--workers" not in args and "--config" in args


@pytest.mark.parametrize("data, reason", [
    ({"id": "x", "kind": "app"}, "cannot be queued"),
    ({"id": "x", "kind": "update"}, "cannot be queued"),
    ({"id": "x", "kind": "train", "args": ["--workers", "16"]}, "set by the runner"),
    ({"id": "x", "kind": "train", "args": ["--data-root=/tmp"]}, "set by the runner"),
    ({"id": "x", "kind": "train", "args": "--rounds 6"}, "list of strings"),
    ({"id": "../x", "kind": "train"}, "must be"),
    ({"id": "x", "kind": "train", "args": ["a\nb"]}, "line break"),
])
def test_parse_entry_refuses(data, reason):
    with pytest.raises(mq.QueueError, match=reason):
        mq.parse_entry(data)


def test_gentle_lowers_priority(monkeypatch):
    monkeypatch.setattr(mq.shutil, "which", lambda c: f"/usr/bin/{c}")
    assert mq.gentle(["x"]) == ["nice", "-n", "10", "caffeinate", "-i", "-s", "x"]


def test_worker_cap_override(monkeypatch):
    monkeypatch.setenv("SNOWAGENT_MAC_WORKERS", "3")
    assert mq.worker_cap() == 3


# --------------------------------------------------------------------------------------------- the tick


class FakeJobs:
    """Stands in for services.jobs: records starts, and reports the states the test sets."""

    ACTIVE = ("starting", "running")

    class JobBusy(RuntimeError):
        pass

    def __init__(self, root: Path):
        self.root, self.started, self.states, self.resumed = root, [], {}, []

    def lab_command(self, *args):
        return ["snowagent", "lab", *args]

    def step(self, name, command, stopped_codes=()):
        return {"name": name, "command": command}

    def start_job(self, paths, kind, title, steps, **kw):
        jid = f"job-{len(self.started) + 1}"
        self.started.append({"job_id": jid, "kind": kind, "steps": steps, **kw})
        self.states[jid] = "running"
        log = self.root / f"{jid}.log"
        log.write_text("engine runs...\nround 6 done\n")
        return {"job_id": jid}

    def resume_job(self, paths, job_id):
        self.resumed.append(job_id)
        return self.start_job(paths, "train", "resume", [])

    def stop_job(self, paths, job_id):
        return "stopped"

    def list_jobs(self, paths):
        return list(self.states)

    def job_info(self, paths, job_id):
        return {"job_id": job_id, "state": self.states[job_id], "log": str(self.root / f"{job_id}.log"),
                "exit_code": 0 if self.states[job_id] == "finished" else None, "finished_at": None,
                "step_states": []}


@pytest.fixture
def setup(tmp_path, monkeypatch):
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", str(origin))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "remote", "add", "origin", str(origin))
    (repo / "README.md").write_text("x")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    _git(repo, "push", "-q", "origin", "main")
    # the cloud's side: a queue entry on the mac-queue branch
    cloud = tmp_path / "cloud"
    _git(tmp_path, "clone", "-q", str(origin), str(cloud))
    _git(cloud, "checkout", "-q", "--orphan", "mac-queue")
    _git(cloud, "rm", "-rqf", "--ignore-unmatch", ".")
    (cloud / "queue").mkdir()
    (cloud / "queue" / "night1.json").write_text(json.dumps(
        {"id": "night1", "kind": "train", "args": ["--rounds", "6"], "created_at": "2026-11-23T18:00:00Z"}))
    (cloud / "queue" / "bad.json").write_text(json.dumps({"id": "bad", "kind": "update"}))
    _git(cloud, "add", ".")
    _git(cloud, "commit", "-qm", "queue")
    _git(cloud, "push", "-q", "origin", "mac-queue")

    data = repo / "data" / "lab"
    fake = FakeJobs(tmp_path)
    import snowagent.lab.services as services

    monkeypatch.setattr(services, "jobs", fake, raising=False)
    monkeypatch.setitem(__import__("sys").modules, "snowagent.lab.services.jobs", fake)
    monkeypatch.setattr(mq, "on_mains_power", lambda: True)
    monkeypatch.setattr(mq, "worker_cap", lambda: 6)
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                 "GIT_COMMITTER_EMAIL": "t@t"}.items():
        monkeypatch.setenv(k, v)
    q = mq.Queue(repo=repo, data_root=data, config=repo / "config" / "lab.yaml")
    return q, fake, origin, data


def _results(origin: Path, path: str) -> str:
    return subprocess.run(["git", "--git-dir", str(origin), "show", f"mac-results:{path}"], capture_output=True,
                          text=True).stdout


def test_tick_starts_publishes_and_refuses(setup):
    q, fake, origin, data = setup
    status = q.tick()
    assert "night1: started" in status
    cmd = fake.started[0]["steps"][0]["command"]
    assert cmd[cmd.index("--workers") + 1] == "6" and cmd[cmd.index("--run-id") + 1] == "night1"
    assert "--resume" in fake.started[0]["resume"][0]["command"]
    assert "cannot be queued" in _results(origin, "results/bad/result.json")

    # a second tick while it runs starts nothing
    assert "night1: running" in q.tick() and len(fake.started) == 1

    # the run writes its outputs, then ends
    run = data / "outputs" / "training" / "night1"
    (run / "rounds" / "6").mkdir(parents=True)
    (run / "summary.json").write_text('{"winner": "night1/6/1"}')
    (run / "rounds" / "6" / "round.json").write_text("{}")
    (data / "outputs" / "cache").mkdir(parents=True)
    (data / "outputs" / "cache" / "big.json").write_text("{}")
    fake.states["job-1"] = "finished"
    q.tick()
    result = json.loads(_results(origin, "results/night1/result.json"))
    assert result["state"] == "finished" and result["exit_code"] == 0
    assert "winner" in _results(origin, "results/night1/outputs/training/night1/summary.json")
    assert "round 6 done" in _results(origin, "results/night1/log_tail.txt")
    assert _results(origin, "results/night1/outputs/cache/big.json") == ""
    assert q.load_state()["entries"]["night1"]["published"] is True
    # the checkout itself is untouched: still on main, nothing staged
    assert _git(q.repo, "branch", "--show-current") == "main"
    assert _git(q.repo, "status", "--porcelain", "--untracked-files=no") == ""


def test_tick_waits_for_mains_power_and_for_other_jobs(setup, monkeypatch):
    q, fake, origin, data = setup
    monkeypatch.setattr(mq, "on_mains_power", lambda: False)
    assert "waiting for mains power" in q.tick() and not fake.started
    monkeypatch.setattr(mq, "on_mains_power", lambda: True)
    fake.states["owner-job"] = "running"  # the owner's own job in the lab app
    assert "waiting for the running job" in q.tick() and not fake.started


def test_a_cut_job_resumes(setup):
    q, fake, origin, data = setup
    q.tick()
    fake.states["job-1"] = "interrupted"
    assert "night1: resumed" in q.tick()
    assert fake.resumed == ["job-1"]
    assert q.load_state()["entries"]["night1"]["attempts"] == 2


def test_one_tick_at_a_time(tmp_path):
    lock = mq.acquire_lock(tmp_path)
    assert lock is not None and mq.acquire_lock(tmp_path) is None
    lock.unlink()
    lock.write_text("999999999")  # a stale lock whose process is gone is taken over
    assert mq.acquire_lock(tmp_path) is not None


def test_overview_lists_running_waiting_and_ended(setup):
    q, fake, origin, data = setup
    # a second entry queued by the cloud, waiting behind the first
    cloud = origin.parent / "cloud"
    (cloud / "queue" / "night2.json").write_text(json.dumps(
        {"id": "night2", "kind": "check-loso", "args": ["--genome", "night1/6/1"], "note": "check the winner",
         "created_at": "2026-11-23T19:00:00Z"}))
    _git(cloud, "add", ".")
    _git(cloud, "commit", "-qm", "queue 2")
    _git(cloud, "push", "-q", "origin", "mac-queue")
    q.tick()
    view = mq.overview(q, fetch=True)
    states = {r["id"]: r["state"] for r in view["rows"]}
    assert states == {"night1": "running", "night2": "waiting", "bad": "rejected"}
    assert [r["id"] for r in view["rows"]][:2] == ["night1", "night2"]  # running first, then the queue in order
    assert next(r for r in view["rows"] if r["id"] == "night2")["note"] == "check the winner"
    assert view["issue_url"].endswith("/issues/104") and view["workers"] == 6
    fake.states["job-1"] = "finished"
    q.tick()  # night1 ends and is published; night2 starts
    states = {r["id"]: r["state"] for r in mq.overview(q)["rows"]}
    assert states["night1"] == "finished" and states["night2"] == "running"


def test_an_empty_queue_is_reported_at_once(setup):
    q, fake, origin, data = setup
    q.tick()  # night1 starts: not idle
    hb = json.loads(_results(origin, "heartbeat.json"))
    assert hb["idle"] is False
    fake.states["job-1"] = "finished"
    q.tick()  # night1 ends and nothing else waits: the heartbeat says so straight away
    hb = json.loads(_results(origin, "heartbeat.json"))
    assert hb["idle"] is True and hb["idle_since"]
    assert q.load_state()["idle"] is True
