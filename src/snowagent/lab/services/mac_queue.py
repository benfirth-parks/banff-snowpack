"""The Mac queue (ADR-147): heavy lab jobs that the cloud asks the owner's Mac to run, without the owner carrying files
between the two.

The cloud writes one JSON file per job to ``queue/`` on the ``mac-queue`` branch (or the owner drops one in
``<data root>/outputs/mac_queue/inbox/``). On the Mac, launchd runs ``snowagent lab mac-queue tick`` every 30 minutes
(``scripts/mac_runner/install.sh``). A tick:

1. resumes a queued job that was cut (sleep, reboot, power) and records the ones that ended;
2. publishes each ended job's small outputs (status, log tail, the json/csv/md files of its run folder; never the
   cache) to ``results/<id>/`` on the ``mac-results`` branch, and keeps the same folder in
   ``outputs/mac_queue/outbox/`` in case the push fails;
3. when no lab job of any kind runs (the owner's own app jobs included) and the Mac is on mains power, starts the
   next queued job through the lab's own job runner (``services/jobs.py``), so it shows in the lab app and its Stop
   and Resume work as for any other job.

Gentle on the Mac: one job at a time, ``nice -n 10``, at most the performance cores minus two as ``--workers``
(``SNOWAGENT_MAC_WORKERS`` overrides), ``caffeinate -i -s`` (keeps the Mac awake only on mains power), and no new
job starts on battery.

Only the lab commands in ``KINDS`` can be queued, as an argument list (never a shell string); ``--data-root``,
``--config`` and ``--workers`` are set by the runner and refused in a queue entry. Research and decision support only.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

QUEUE_BRANCH = "mac-queue"
RESULTS_BRANCH = "mac-results"
ENTRY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
# kind -> (takes --workers, option that names the job so the same command resumes it, takes --config)
KINDS: dict[str, tuple[bool, str | None, bool]] = {
    "train": (True, "--run-id", True),
    "compete": (True, "--run-id", True),
    "check-loso": (True, "--check-id", True),
    "winter-test": (True, "--check-id", True),
    "group-check": (True, "--check-id", True),
    "stability-check": (True, "--check-id", True),
    "sensitivity": (True, None, True),
    "rescore": (False, "--new-run-id", True),
    "build-cases": (False, None, True),
}
RUNNER_OPTIONS = ("--data-root", "--config", "--workers")
MAX_ATTEMPTS = 3
FILE_CAP = 2_000_000  # bytes: a bigger output file is listed in result.json, not published
TOTAL_CAP = 25_000_000
LOG_TAIL = 60_000
PUBLISH_EXT = {".json", ".jsonl", ".md", ".csv", ".txt", ".log", ".yaml"}
SKIP_DIRS = {"cache", "genomes_cache", "__pycache__"}
HEARTBEAT_EVERY = timedelta(hours=24)


class QueueError(ValueError):
    """A queue entry that the runner refuses."""


@dataclass
class QueueEntry:
    id: str
    kind: str
    args: list[str] = field(default_factory=list)
    note: str = ""
    created_at: str = ""
    cancel: bool = False

    def command_args(self, workers: int, data_root: Path, config: Path) -> list[str]:
        """``lab <kind>`` arguments: the entry's own, the id option set to the entry id unless given, then the
        runner's options."""
        takes_workers, id_opt, takes_config = KINDS[self.kind]
        args = list(self.args)
        if id_opt and id_opt not in args:
            args += [id_opt, self.id]
        args += ["--data-root", str(data_root)]
        if takes_config:
            args += ["--config", str(config)]
        if takes_workers:
            args += ["--workers", str(workers)]
        return [self.kind, *args]

    def ref_ids(self) -> list[str]:
        """Ids the job writes its outputs under (the id option's value, and any other ``--*-id`` value)."""
        out = []
        for i, a in enumerate(self.args[:-1]):
            if a.endswith("-id"):
                out.append(self.args[i + 1])
        _, id_opt, _ = KINDS[self.kind]
        if id_opt and id_opt not in self.args:
            out.append(self.id)
        return out


def parse_entry(data: dict, name: str = "") -> QueueEntry:
    """Validate one queue file's content; raise ``QueueError`` with the reason."""
    if not isinstance(data, dict):
        raise QueueError(f"{name}: not a JSON object")
    eid = str(data.get("id") or Path(name).stem)
    if not ENTRY_ID.match(eid):
        raise QueueError(f"{name}: id {eid!r} must be 1-80 letters, digits, '.', '_' or '-'")
    kind = data.get("kind")
    if kind not in KINDS:
        raise QueueError(f"{eid}: kind {kind!r} cannot be queued (allowed: {', '.join(KINDS)})")
    args = data.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise QueueError(f"{eid}: args must be a list of strings")
    for a in args:
        opt = a.split("=", 1)[0]
        if opt in RUNNER_OPTIONS:
            raise QueueError(f"{eid}: {opt} is set by the runner, leave it out")
        if "\n" in a or "\x00" in a:
            raise QueueError(f"{eid}: an argument holds a line break")
    return QueueEntry(id=eid, kind=kind, args=args, note=str(data.get("note", ""))[:500],
                      created_at=str(data.get("created_at", "")), cancel=bool(data.get("cancel", False)))


# --------------------------------------------------------------------------------------------- the Mac


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 120, env: dict | None = None,
         input: bytes | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, capture_output=True, timeout=timeout, env=env, input=input)


def worker_cap() -> int:
    """``SNOWAGENT_MAC_WORKERS``, else the performance cores minus two (at least 1), else half the cores."""
    env = os.environ.get("SNOWAGENT_MAC_WORKERS")
    if env and env.isdigit() and int(env) > 0:
        return int(env)
    try:
        perf = int(_run(["sysctl", "-n", "hw.perflevel0.physicalcpu"], timeout=5).stdout.strip())
        return max(1, perf - 2)
    except (OSError, ValueError, subprocess.SubprocessError):
        return max(1, (os.cpu_count() or 2) // 2)


def on_mains_power() -> bool:
    """True on mains power, or where ``pmset`` does not exist (not a Mac laptop)."""
    if not shutil.which("pmset"):
        return True
    try:
        out = _run(["pmset", "-g", "batt"], timeout=5).stdout.decode(errors="replace")
    except (OSError, subprocess.SubprocessError):
        return True
    return "AC Power" in out or "Battery Power" not in out


def gentle(cmd: list[str]) -> list[str]:
    """Wrap a command: lower priority, and keep the Mac awake while it runs (on mains power only)."""
    pre = ["nice", "-n", "10"] if shutil.which("nice") else []
    if shutil.which("caffeinate"):
        pre += ["caffeinate", "-i", "-s"]
    return pre + cmd


# --------------------------------------------------------------------------------------------- state


@dataclass
class Queue:
    repo: Path
    data_root: Path
    config: Path

    @property
    def root(self) -> Path:
        return self.data_root / "outputs" / "mac_queue"

    @property
    def inbox(self) -> Path:
        return self.root / "inbox"

    @property
    def outbox(self) -> Path:
        return self.root / "outbox"

    @property
    def state_file(self) -> Path:
        return self.root / "state.json"

    def log(self, msg: str) -> None:
        line = f"{datetime.now(UTC).isoformat(timespec='seconds')} {msg}"
        print(line, flush=True)
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self.root / "runner.log", "a") as fh:
            fh.write(line + "\n")

    def load_state(self) -> dict:
        try:
            return json.loads(self.state_file.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {"entries": {}}

    def save_state(self, state: dict) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1))
        os.replace(tmp, self.state_file)

    # ----------------------------------------------------------------------------------------- reading the queue

    def git(self, *args: str, timeout: int = 120, env: dict | None = None,
            input: bytes | None = None) -> subprocess.CompletedProcess:
        return _run(["git", *args], cwd=self.repo, timeout=timeout, env=env, input=input)

    def fetch(self) -> bool:
        ok = True
        for b in (QUEUE_BRANCH, RESULTS_BRANCH):
            r = self.git("fetch", "-q", "origin", f"+refs/heads/{b}:refs/remotes/origin/{b}")
            if r.returncode != 0 and b == QUEUE_BRANCH:
                err = r.stderr.decode(errors="replace").strip()
                if "couldn't find remote ref" not in err:
                    self.log(f"fetch {b} failed: {err[:200]}")
                    ok = False
        return ok

    def raw_entries(self) -> list[tuple[str, str]]:
        """(name, text) of every queue file: the ``mac-queue`` branch's ``queue/*.json``, then the inbox's."""
        out: list[tuple[str, str]] = []
        ls = self.git("ls-tree", "--name-only", f"refs/remotes/origin/{QUEUE_BRANCH}", "queue/")
        if ls.returncode == 0:
            for name in ls.stdout.decode().split():
                if name.endswith(".json"):
                    show = self.git("show", f"refs/remotes/origin/{QUEUE_BRANCH}:{name}")
                    if show.returncode == 0:
                        out.append((name, show.stdout.decode(errors="replace")))
        if self.inbox.is_dir():
            for f in sorted(self.inbox.glob("*.json")):
                out.append((f"inbox/{f.name}", f.read_text(errors="replace")))
        return out

    def entries(self) -> tuple[list[QueueEntry], list[tuple[str, str]]]:
        good, bad = [], []
        for name, text in self.raw_entries():
            try:
                good.append(parse_entry(json.loads(text), name))
            except (json.JSONDecodeError, QueueError) as exc:
                bad.append((Path(name).stem, str(exc)))
        good.sort(key=lambda e: (e.created_at, e.id))
        return good, bad

    # ----------------------------------------------------------------------------------------- results

    def output_dirs(self, entry: QueueEntry) -> list[Path]:
        outputs = self.data_root / "outputs"
        found = []
        for rid in entry.ref_ids():
            if not ENTRY_ID.match(rid):
                continue
            for d in outputs.glob(f"*/{rid}"):
                if d.is_dir() and d.parent.name not in {"jobs", "mac_queue", "cache"}:
                    found.append(d)
        return found

    def build_result(self, entry: QueueEntry | None, eid: str, rec: dict, info: dict | None) -> Path:
        """Write ``outbox/<id>/``: result.json, the entry, the log tail and the run folders' small files."""
        d = self.outbox / eid
        if d.exists():
            shutil.rmtree(d)
        d.mkdir(parents=True)
        skipped: list[str] = []
        total = 0
        if info is not None:
            log = Path(info.get("log") or "")
            if log.is_file():
                with open(log, "rb") as fh:
                    size = fh.seek(0, os.SEEK_END)
                    fh.seek(max(0, size - LOG_TAIL))
                    (d / "log_tail.txt").write_bytes(fh.read())
        if entry is not None:
            (d / "entry.json").write_text(json.dumps(entry.__dict__, indent=1))
            for od in self.output_dirs(entry):
                for f in sorted(od.rglob("*")):
                    rel = f.relative_to(self.data_root / "outputs")
                    if not f.is_file() or SKIP_DIRS & set(rel.parts) or f.suffix not in PUBLISH_EXT:
                        continue
                    size = f.stat().st_size
                    if size > FILE_CAP or total + size > TOTAL_CAP:
                        skipped.append(f"{rel} ({size} bytes)")
                        continue
                    dst = d / "outputs" / rel
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(f, dst)
                    total += size
        head = self.git("rev-parse", "--short", "HEAD").stdout.decode().strip()
        result = rec | {"id": eid, "host": platform.node(), "code_version": head, "workers": rec.get("workers"),
                        "published_at": datetime.now(UTC).isoformat(timespec="seconds"),
                        "not_published_too_big": skipped,
                        "note": "Research and decision support only, not an avalanche forecast."}
        if info is not None:
            result |= {"lab_job_id": info.get("job_id"), "lab_state": info.get("state"),
                       "exit_code": info.get("exit_code"), "finished_at": info.get("finished_at"),
                       "steps": info.get("step_states")}
        (d / "result.json").write_text(json.dumps(result, indent=1, default=str))
        return d

    def push_files(self, files: dict[str, Path], message: str) -> bool:
        """Commit ``files`` (path in branch -> local file) on top of ``mac-results`` and push, without touching the
        working tree or the checkout's own index. One retry after a refetch."""
        for _attempt in range(2):
            parent = self.git("rev-parse", "-q", "--verify", f"refs/remotes/origin/{RESULTS_BRANCH}")
            parent_sha = parent.stdout.decode().strip() if parent.returncode == 0 else ""
            index = self.root / ".results-index"
            index.unlink(missing_ok=True)
            env = os.environ | {"GIT_INDEX_FILE": str(index)}
            if parent_sha:
                self.git("read-tree", parent_sha, env=env)
            for path, local in files.items():
                blob = self.git("hash-object", "-w", str(local)).stdout.decode().strip()
                self.git("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}", env=env)
            tree = self.git("write-tree", env=env).stdout.decode().strip()
            index.unlink(missing_ok=True)
            commit_cmd = ["commit-tree", tree, "-m", message]
            if parent_sha:
                commit_cmd += ["-p", parent_sha]
            cenv = os.environ | {"GIT_AUTHOR_NAME": "Mac queue runner", "GIT_AUTHOR_EMAIL": "mac-queue@localhost",
                                 "GIT_COMMITTER_NAME": "Mac queue runner",
                                 "GIT_COMMITTER_EMAIL": "mac-queue@localhost"}
            commit = self.git(*commit_cmd, env=cenv).stdout.decode().strip()
            if not commit:
                self.log("results commit failed")
                return False
            push = self.git("push", "-q", "origin", f"{commit}:refs/heads/{RESULTS_BRANCH}", timeout=300)
            if push.returncode == 0:
                self.git("update-ref", f"refs/remotes/origin/{RESULTS_BRANCH}", commit)
                return True
            err = push.stderr.decode(errors="replace").strip()
            self.log(f"push to {RESULTS_BRANCH} failed: {err[:300]}")
            if "rejected" not in err and "fetch first" not in err:
                return False
            self.git("fetch", "-q", "origin", f"+refs/heads/{RESULTS_BRANCH}:refs/remotes/origin/{RESULTS_BRANCH}")
        return False

    def publish(self, eid: str) -> bool:
        d = self.outbox / eid
        files = {f"results/{eid}/{f.relative_to(d)}": f for f in d.rglob("*") if f.is_file()}
        return self.push_files(files, f"Mac queue: {eid}")

    def heartbeat(self, state: dict, status: str) -> None:
        last = state.get("heartbeat_at")
        if last and datetime.fromisoformat(last) > datetime.now(UTC) - HEARTBEAT_EVERY:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        hb = self.root / "heartbeat.json"
        hb.write_text(json.dumps({"at": datetime.now(UTC).isoformat(timespec="seconds"), "host": platform.node(),
                                  "status": status, "workers": worker_cap(), "mains_power": on_mains_power(),
                                  "entries": state.get("entries", {})}, indent=1))
        if self.push_files({"heartbeat.json": hb}, "Mac queue: heartbeat"):
            state["heartbeat_at"] = datetime.now(UTC).isoformat(timespec="seconds")

    # ----------------------------------------------------------------------------------------- the tick

    def update_code(self) -> None:
        """Fast-forward ``main`` before a new job, when the checkout is on a clean ``main`` (else leave it)."""
        branch = self.git("branch", "--show-current").stdout.decode().strip()
        dirty = self.git("status", "--porcelain", "--untracked-files=no").stdout.decode().strip()
        if branch != "main" or dirty:
            self.log(f"code not updated (branch {branch or '?'}{', local changes' if dirty else ''})")
            return
        r = self.git("pull", "-q", "--ff-only", "origin", "main", timeout=600)
        if r.returncode != 0:
            self.log(f"git pull failed: {r.stderr.decode(errors='replace').strip()[:200]}")

    def tick(self) -> str:
        from snowagent.lab.services import jobs
        from snowagent.lab.storage.paths import LabPaths

        paths = LabPaths(self.data_root)
        state = self.load_state()
        recs: dict = state.setdefault("entries", {})
        self.fetch()
        entries, bad = self.entries()
        by_id = {e.id: e for e in entries}
        notes = []

        for eid, reason in bad:  # refused entries: say why, once
            if eid not in recs:
                recs[eid] = {"state": "rejected", "reason": reason}
                self.build_result(None, eid, recs[eid], None)
                recs[eid]["published"] = self.publish(eid)
                self.log(f"{eid}: rejected: {reason}")

        for e in entries:  # cancels
            rec = recs.get(e.id)
            if e.cancel and rec and rec.get("state") == "running" and rec.get("job_id"):
                notes.append(f"{e.id}: {jobs.stop_job(paths, rec['job_id'])}")
                rec["cancel_requested"] = True

        mains = on_mains_power()
        for eid, rec in recs.items():  # running jobs: still going, cut, or ended
            if rec.get("state") != "running":
                continue
            info = jobs.job_info(paths, rec["job_id"])
            if info["state"] in jobs.ACTIVE:
                notes.append(f"{eid}: running ({info['job_id']})")
                continue
            if info["state"] == "interrupted" and not rec.get("cancel_requested"):
                if rec.get("attempts", 1) >= MAX_ATTEMPTS:
                    rec["state"] = "failed"
                    rec["reason"] = f"cut {MAX_ATTEMPTS} times"
                elif mains:
                    new = jobs.resume_job(paths, rec["job_id"])
                    rec |= {"job_id": new["job_id"], "attempts": rec.get("attempts", 1) + 1}
                    self.log(f"{eid}: resumed as {new['job_id']} (attempt {rec['attempts']})")
                    notes.append(f"{eid}: resumed")
                    continue
                else:
                    notes.append(f"{eid}: cut, waits for mains power")
                    continue
            else:
                rec["state"] = {"finished": "finished", "stopped": "stopped"}.get(info["state"], "failed")
            rec["ended_at"] = datetime.now(UTC).isoformat(timespec="seconds")
            self.build_result(by_id.get(eid), eid, rec, info)
            rec["published"] = self.publish(eid)
            self.log(f"{eid}: {rec['state']} (exit {info.get('exit_code')}), published={rec['published']}")

        for eid, rec in recs.items():  # earlier pushes that failed
            if rec.get("state") not in ("running", None) and rec.get("published") is False \
                    and (self.outbox / eid).is_dir():
                rec["published"] = self.publish(eid)

        busy = any(jobs.job_info(paths, j)["state"] in jobs.ACTIVE for j in jobs.list_jobs(paths))
        pending = [e for e in entries if e.id not in recs and not e.cancel]
        if pending and not busy:
            if not mains:
                notes.append(f"{len(pending)} queued, waiting for mains power")
            else:
                e = pending[0]
                self.update_code()
                workers = worker_cap()
                cmd = gentle(jobs.lab_command(*e.command_args(workers, self.data_root.resolve(),
                                                              self.config.resolve())))
                refs = {"run_id": e.args[e.args.index("--run-id") + 1] if "--run-id" in e.args else e.id} \
                    if e.kind == "train" else {}
                # training continues a cut run only with --resume (ADR-069); the other kinds resume by their id
                resume = [jobs.step(e.kind, [*cmd, "--resume"], (5,))] if e.kind == "train" else None
                try:
                    job = jobs.start_job(paths, e.kind, f"Mac queue: {e.id}", [jobs.step(e.kind, cmd, (5,))],
                                         cwd=self.repo, key=e.kind, refs=refs, resume=resume)
                except jobs.JobBusy as exc:
                    notes.append(f"{e.id}: not started ({exc})")
                else:
                    recs[e.id] = {"state": "running", "job_id": job["job_id"], "attempts": 1, "workers": workers,
                                  "kind": e.kind, "started_at": datetime.now(UTC).isoformat(timespec="seconds")}
                    self.log(f"{e.id}: started {e.kind} as {job['job_id']} with {workers} workers")
                    notes.append(f"{e.id}: started")
        elif pending:
            notes.append(f"{len(pending)} queued, waiting for the running job")
        status = "; ".join(notes) or "queue empty"
        self.heartbeat(state, status)
        self.save_state(state)
        return status


def acquire_lock(root: Path) -> Path | None:
    """One tick at a time: a lock file holding the tick's pid (a stale one, whose pid is gone, is taken over)."""
    root.mkdir(parents=True, exist_ok=True)
    lock = root / "tick.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            pid = int(lock.read_text().strip() or 0)
            os.kill(pid, 0)
            return None
        except (ValueError, ProcessLookupError, PermissionError):
            lock.unlink(missing_ok=True)
            return acquire_lock(root)
    os.write(fd, str(os.getpid()).encode())
    os.close(fd)
    return lock


def run_tick(repo: Path, data_root: Path, config: Path) -> int:
    q = Queue(repo=repo, data_root=data_root, config=config)
    lock = acquire_lock(q.root)
    if lock is None:
        print("another tick is running", file=sys.stderr)
        return 3
    try:
        print(q.tick())
        return 0
    finally:
        lock.unlink(missing_ok=True)
