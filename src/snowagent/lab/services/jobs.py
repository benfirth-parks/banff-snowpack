"""Background jobs of the lab app (ADR-077, extending ADR-069): every step the browser starts (set up data, build
cases, a competition, training, the promotion check and its estimate, a re-score) runs as ``snowagent lab ...``
commands in a detached process group under a small runner, never inside the Streamlit process. Closing the browser
or stopping the app does not stop a job.

A job lives in ``<data root>/outputs/jobs/<job_id>/``: ``job.json`` (kind, title, steps and their commands, working
directory, the ids it produces, the runner's pid), ``status.json`` (written by the runner: the job's state and each
step's state and exit code) and the output log (``output.log``, or the training run's ``stdout.log``). One job per
key runs at a time (the key is the kind: one competition, one training, ... at a time); a second start is refused.
Stop sends SIGTERM to the job's process group (training first asks politely, stopping at its next case); every
command is resumable, so Resume starts the same commands again (training with ``--resume``).

Run as ``python -m snowagent.lab.services.jobs <job_dir>``: the runner itself.
"""

from __future__ import annotations

import json
import os
import shlex
import signal
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path

from snowagent.lab.storage.paths import LabPaths
from snowagent.lab.storage.provenance import new_run_id

KINDS = {"setup": "Set up data", "build-cases": "Build cases", "compete": "Competition", "train": "Training",
         "check-estimate": "Promotion check estimate", "check-loso": "Promotion check", "rescore": "Re-score"}
ACTIVE = ("starting", "running")
RUNNER = "snowagent.lab.services.jobs"
LOG_TAIL_BYTES = 12_000


class JobBusy(RuntimeError):
    """A job with the same key is already running."""


def jobs_root(paths: LabPaths) -> Path:
    return paths.outputs / "jobs"


def lab_command(*args: str) -> list[str]:
    """``snowagent lab <args>`` with this interpreter (the app's venv), whatever the PATH."""
    return [sys.executable, "-m", "snowagent.cli", "lab", *args]


def step(name: str, command: list[str], stopped_codes: tuple[int, ...] = ()) -> dict:
    return {"name": name, "command": [str(c) for c in command], "stopped_codes": list(stopped_codes)}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _read(f: Path) -> dict | None:
    try:
        return json.loads(f.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _write(f: Path, data: dict) -> None:
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str))
    os.replace(tmp, f)


# --------------------------------------------------------------------------------------------- processes


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError, OverflowError):
        return False
    return True


def runner_alive(pid: int | None) -> bool:
    """The job runner with this pid still runs (not a finished zombie, not another process that reused the pid)."""
    if not pid_alive(pid):
        return False
    try:
        out = subprocess.run(["ps", "-ww", "-o", "stat=,command=", "-p", str(pid)], capture_output=True, text=True,
                             timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return True  # no ps: trust the signal check
    return bool(out) and not out.startswith("Z") and RUNNER in out


def _launch(cmd: list[str], **kw) -> subprocess.Popen:
    proc = subprocess.Popen(cmd, **kw)
    threading.Thread(target=proc.wait, daemon=True).start()  # reap it when it ends while the app still runs
    return proc


# --------------------------------------------------------------------------------------------- jobs


def list_jobs(paths: LabPaths) -> list[str]:
    root = jobs_root(paths)
    if not root.is_dir():
        return []
    jobs = [d for d in root.iterdir() if (d / "job.json").is_file()]
    return [d.name for d in sorted(jobs, key=lambda d: (_read(d / "job.json") or {}).get("created_at", ""),
                                   reverse=True)]


def job_info(paths: LabPaths, job_id: str) -> dict:
    """job.json and status.json merged, with ``state`` checked against the process: a job whose runner is gone
    without a final state (machine asleep and killed, reboot, kill -9) is ``interrupted``."""
    d = jobs_root(paths) / job_id
    job = _read(d / "job.json") or {}
    status = _read(d / "status.json") or {}
    state = status.get("state", "starting")
    if state in ACTIVE and not runner_alive(job.get("pid")):
        state = "interrupted"
    steps = status.get("steps") or [{"name": s["name"], "state": "waiting"} for s in job.get("steps", [])]
    return job | {"job_id": job_id, "dir": d, "state": state, "step_states": steps, "status": status,
                  "finished_at": status.get("finished_at"), "exit_code": status.get("exit_code")}


def latest_job(paths: LabPaths, key: str) -> dict | None:
    for j in list_jobs(paths):
        info = job_info(paths, j)
        if info.get("key") == key:
            return info
    return None


def job_for(paths: LabPaths, kind: str, ref: str, value: str) -> dict | None:
    """The newest job of ``kind`` whose ``refs[ref]`` is ``value`` (e.g. the training job of one run)."""
    for j in list_jobs(paths):
        info = job_info(paths, j)
        if info.get("kind") == kind and (info.get("refs") or {}).get(ref) == value:
            return info
    return None


def running_job(paths: LabPaths, key: str) -> dict | None:
    for j in list_jobs(paths):
        info = job_info(paths, j)
        if info.get("key") == key and info["state"] in ACTIVE:
            return info
    return None


def log_tail(info: dict, n_bytes: int = LOG_TAIL_BYTES) -> str:
    f = Path(info.get("log") or "")
    if not f.is_file():
        return ""
    with open(f, "rb") as fh:
        size = fh.seek(0, os.SEEK_END)
        fh.seek(max(0, size - n_bytes))
        text = fh.read().decode("utf-8", errors="replace")
    return text if size <= n_bytes else "…" + text[text.find("\n") + 1:]


def start_job(paths: LabPaths, kind: str, title: str, steps: list[dict], *, cwd: Path, key: str | None = None,
              log: Path | None = None, refs: dict | None = None, resume: list[dict] | None = None,
              resume_of: str | None = None) -> dict:
    """Launch the runner for ``steps`` detached (own session, so it outlives the app); refuse (``JobBusy``) when a
    job with the same key runs. ``resume`` are the steps that continue the job after a stop (default: ``steps``)."""
    key = key or kind
    busy = running_job(paths, key)
    if busy:
        raise JobBusy(f"a {KINDS.get(kind, kind).lower()} job is already running ({busy['job_id']}: "
                      f"{busy.get('title')}); stop it or wait for it to finish")
    job_id = new_run_id(f"job-{kind}", salt=title)
    d = jobs_root(paths) / job_id
    d.mkdir(parents=True, exist_ok=True)
    log = Path(log or d / "output.log")
    log.parent.mkdir(parents=True, exist_ok=True)
    job = {"job_id": job_id, "kind": kind, "key": key, "title": title, "created_at": _now(), "cwd": str(cwd),
           "steps": steps, "resume": resume, "log": str(log), "refs": refs or {}, "resume_of": resume_of,
           "data_root": str(Path(paths.root).resolve())}
    _write(d / "job.json", job)
    _write(d / "status.json", {"state": "starting", "steps": [{"name": s["name"], "state": "waiting"} for s in steps],
                               "updated_at": _now()})
    env = os.environ.copy() | {"PYTHONUNBUFFERED": "1"}  # the log tail follows the job live
    with open(log, "ab") as fh:
        proc = _launch([sys.executable, "-m", RUNNER, str(d)], stdout=fh, stderr=subprocess.STDOUT,
                       stdin=subprocess.DEVNULL, start_new_session=True, cwd=str(cwd), env=env)
    job["pid"] = proc.pid
    _write(d / "job.json", job)
    return job_info(paths, job_id)


def stop_job(paths: LabPaths, job_id: str) -> str:
    """Stop a running job: a training is asked to stop at its next case (its ``stop`` file, ADR-069); every other
    job's process group gets SIGTERM. Returns what was done."""
    info = job_info(paths, job_id)
    if info["state"] not in ACTIVE:
        return f"not running ({info['state']})"
    run_id = (info.get("refs") or {}).get("run_id")
    if info.get("kind") == "train" and run_id:
        from snowagent.lab.services.training import stop_training

        stop_training(paths, run_id)
        return "stop requested: the training stops at its next case"
    try:
        os.killpg(int(info["pid"]), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        return "the process had already ended"
    return "stopped"


def resume_job(paths: LabPaths, job_id: str) -> dict:
    """Start a stopped, failed or interrupted job again with its resume steps (every lab command resumes)."""
    info = job_info(paths, job_id)
    if info["state"] in ACTIVE:
        raise JobBusy(f"{job_id} is still running")
    steps = info.get("resume") or info["steps"]
    if info.get("kind") == "train" and (info.get("refs") or {}).get("run_id"):
        from snowagent.lab.training.loop import training_root

        run_dir = training_root(paths) / info["refs"]["run_id"]
        (run_dir / "stop").unlink(missing_ok=True)
        if not (run_dir / "run.json").is_file():  # it failed before the run existed: start it again
            steps = info["steps"]
    log = Path(info["log"])
    return start_job(paths, info["kind"], info["title"], steps, cwd=Path(info["cwd"]),
                     key=info.get("key"), log=log if log.parent != info["dir"] else None, refs=info.get("refs"),
                     resume=info.get("resume"), resume_of=job_id)


# --------------------------------------------------------------------------------------------- the runner


def run_job(job_dir: Path) -> int:
    """Run the job's steps in order, recording each step's state; a failing step ends the job. SIGTERM (Stop) ends
    the current step (the whole process group receives it) and marks the job stopped."""
    job = json.loads((job_dir / "job.json").read_text())
    stopping = {"flag": False}

    def on_term(signum, frame):  # noqa: ARG001 - signal handler signature
        stopping["flag"] = True

    signal.signal(signal.SIGTERM, on_term)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    steps = [{"name": s["name"], "state": "waiting"} for s in job["steps"]]
    status = {"state": "running", "steps": steps, "started_at": _now(), "runner_pid": os.getpid()}

    def save(**kw) -> None:
        status.update(kw, updated_at=_now())
        _write(job_dir / "status.json", status)

    save()
    final, code = "finished", 0
    for i, s in enumerate(job["steps"]):
        if stopping["flag"]:
            final = "stopped"
            break
        steps[i] |= {"state": "running", "started_at": _now()}
        save(current_step=i)
        with open(job["log"], "ab") as fh:
            fh.write(f"\n=== {_now()} step {i + 1}/{len(steps)}: {s['name']}\n$ {shlex.join(s['command'])}\n"
                     .encode())
            fh.flush()
            try:
                proc = subprocess.Popen(s["command"], stdout=fh, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                        cwd=job["cwd"])
                code = proc.wait()
            except OSError as exc:
                fh.write(f"could not start: {exc}\n".encode())
                code = 127
        steps[i] |= {"exit_code": code, "finished_at": _now()}
        if stopping["flag"] or code in s.get("stopped_codes", []):
            steps[i]["state"], final = "stopped", "stopped"
            break
        if code != 0:
            steps[i]["state"], final = "failed", "failed"
            break
        steps[i]["state"] = "done"
    with open(job["log"], "ab") as fh:
        fh.write(f"\n=== {_now()} job {final}\n".encode())
    save(state=final, exit_code=code, finished_at=_now())
    return 0 if final == "finished" else 1


if __name__ == "__main__":
    sys.exit(run_job(Path(sys.argv[1])))
