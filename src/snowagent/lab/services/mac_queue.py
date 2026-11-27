"""The Mac queue (ADR-148): heavy lab jobs that the cloud asks the owner's Mac to run, without the owner carrying files
between the two.

The cloud writes one JSON file per job to ``queue/`` on the ``mac-queue`` branch (or the owner drops one in
``<data root>/outputs/mac_queue/inbox/``). On the Mac, launchd runs ``snowagent lab mac-queue tick`` every 5 minutes
(``scripts/mac_runner/install.sh``). A tick:

1. resumes a queued job that was cut (sleep, reboot, power) and records the ones that ended;
2. publishes each ended job's small outputs (status, log tail, the json/csv/md files of its run folder; never the
   cache) to ``results/<id>/`` on the ``mac-results`` branch, and keeps the same folder in
   ``outputs/mac_queue/outbox/`` in case the push fails;
3. when no lab job of any kind runs (the owner's own app jobs included), starts the
   next queued job through the lab's own job runner (``services/jobs.py``), so it shows in the lab app and its Stop
   and Resume work as for any other job.

Gentle on the Mac: one job at a time, ``nice -n 10``, at most the performance cores minus two as ``--workers``
(``SNOWAGENT_MAC_WORKERS`` overrides), and ``caffeinate -i -s`` (keeps the Mac awake while a job runs). Jobs start
and resume on battery too: the owner manages the power (ADR-151).

Disk guard (ADR-158, owner 2026-11-24, after a training filled the Mac's disk): jobs get their own temporary folder
(``outputs/mac_queue/tmp``, as ``TMPDIR``), cleared after every job and whenever nothing runs; no job starts or
resumes below ``SNOWAGENT_MAC_MIN_FREE_GB`` free (default 50); a running job's whole process group (workers too) is
stopped below ``SNOWAGENT_MAC_STOP_FREE_GB`` (default 20) and resumed once there is room again; and the training
cache (``outputs/cache``, rebuildable) is cleared when it grows past ``SNOWAGENT_MAC_CACHE_MAX_GB`` (default 30, checked every 6 hours) or
the disk is low. Clearing never holds up a tick: folders are renamed aside and deleted in the background (ADR-160).

Only the lab commands in ``KINDS`` can be queued, as an argument list (never a shell string); ``--data-root``,
``--config`` and ``--workers`` are set by the runner and refused in a queue entry. Research and decision support only.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import tempfile
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
GB = 1_000_000_000
DROP_REPORT_GB = 5.0  # a fall this big between two 5-minute ticks gets a disk report (ADR-159)


def _env_gb(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def min_free_gb() -> float:
    """No job starts or resumes with less than this free."""
    return _env_gb("SNOWAGENT_MAC_MIN_FREE_GB", 50.0)


def stop_free_gb() -> float:
    """A running job is stopped when free space falls below this."""
    return _env_gb("SNOWAGENT_MAC_STOP_FREE_GB", 20.0)


def cache_max_gb() -> float:
    """The training cache is cleared when it grows past this."""
    return _env_gb("SNOWAGENT_MAC_CACHE_MAX_GB", 30.0)


def free_gb(path: Path) -> float:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free / GB


def dir_bytes(path: Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    return total


def du_bytes(path: Path, timeout: int = 120) -> int | None:
    """A folder's size from ``du`` (fast, unlike walking it in Python); None if it is missing or ``du`` is too slow."""
    if not Path(path).exists():
        return 0
    try:
        out = _run(["du", "-sk", str(path)], timeout=timeout).stdout.decode().split()
        return int(out[0]) * 1024 if out else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def discard(path: Path, recreate: bool = True) -> bool:
    """Clear a folder at once: rename it aside (instant), put an empty one in its place, and delete the old one in a
    detached background ``rm`` so nothing waits on it. Leftovers of earlier discards are deleted too."""
    path = Path(path)
    for left in path.parent.glob(f".{path.name}-trash-*"):  # a background delete that a reboot cut short
        _background_rm(left)
    if not path.is_dir() or not any(path.iterdir()):
        return False
    trash = path.with_name(f".{path.name}-trash-{datetime.now(UTC):%Y%m%dT%H%M%S}-{os.getpid()}")
    path.rename(trash)
    if recreate:
        path.mkdir(parents=True, exist_ok=True)
    _background_rm(trash)
    return True


def _background_rm(path: Path) -> None:
    cmd = (["nice", "-n", "19"] if shutil.which("nice") else []) + ["rm", "-rf", str(path)]
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)


def clear_dir(path: Path) -> int:
    """Remove everything inside ``path`` (not the folder itself); returns the bytes freed."""
    path = Path(path)
    if not path.is_dir():
        return 0
    freed = dir_bytes(path)
    for child in path.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child, ignore_errors=True)
        else:
            child.unlink(missing_ok=True)
    return freed


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
    """Wrap a command: lower priority, and keep the Mac awake while it runs."""
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
    def tmp(self) -> Path:
        """The jobs' own temporary folder (their ``TMPDIR``): SNOWPACK work folders land here, not in the system's."""
        return self.root / "tmp"

    @property
    def cache(self) -> Path:
        return self.data_root / "outputs" / "cache"

    def clean_up(self, low: bool) -> list[str]:
        """With nothing running: clear the jobs' temporary folder, stray SNOWPACK work folders older than two hours in
        the system's temporary folder, and the training cache when it is too big (checked every 6 hours) or the disk
        is low. Fast, so a tick never waits on it (ADR-160): sizes come from ``du`` with a time limit, and a folder
        is cleared by renaming it aside and deleting it in the background. Returns what was done."""
        notes = []
        size = du_bytes(self.tmp, timeout=60)
        if discard(self.tmp) and (size or 0) >= GB // 10:
            notes.append(f"clearing {size / GB:.1f} GB of temporary files")
        old = datetime.now().timestamp() - 2 * 3600  # a live SNOWPACK case folder is minutes old; leave those
        for d in Path(tempfile.gettempdir()).glob("sp_*"):
            if d.is_dir() and d.stat().st_mtime < old:
                discard(d, recreate=False)
        stamp = self.root / "cache_checked"
        due = not stamp.exists() or stamp.stat().st_mtime < datetime.now().timestamp() - 6 * 3600
        if self.cache.is_dir() and (low or due):
            stamp.touch()
            size = du_bytes(self.cache, timeout=120)
            if low or (size is not None and size > cache_max_gb() * GB):
                if discard(self.cache):
                    notes.append("clearing the training cache"
                                 + (f" ({size / GB:.1f} GB)" if size is not None else ""))
        for n in notes:
            self.log(n)
        return notes

    def disk_report(self, free: float, why: str) -> Path:
        """A snapshot of where space is going, kept in ``outputs/mac_queue/disk/`` and published to ``disk/`` on
        ``mac-results`` so the cloud can read it (ADR-159): the biggest folders under the data root and the home
        folder, the temporary folders, and files deleted while still open (which only a restart frees)."""
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        out = self.root / "disk" / f"{stamp}.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        parts = [f"{datetime.now(UTC).isoformat(timespec='seconds')} {why}; {free:.1f} GB free", ""]

        def sh(title: str, cmd: list[str], timeout: int = 90) -> None:
            try:
                r = _run(cmd, timeout=timeout)
                text = r.stdout.decode(errors="replace")
            except (OSError, subprocess.SubprocessError) as exc:
                text = f"({exc})"
            parts.extend([f"== {title}", text.strip()[:20000], ""])

        home = Path.home()
        sh("data root folders", ["sh", "-c", f'du -sk "{self.data_root}"/outputs/* 2>/dev/null | sort -rn | head -15'])
        sh("home folders", ["sh", "-c", f'du -sk "{home}"/* "{home}"/.[!.]* 2>/dev/null | sort -rn | head -15'], 150)
        sh("temporary folders", ["sh", "-c", f'du -sk "{tempfile.gettempdir()}" /private/tmp /private/var/vm '
                                              f'"{self.tmp}" 2>/dev/null'])
        sh("deleted but still open (size bytes, command, pid)",
           ["sh", "-c", "lsof -nP +L1 2>/dev/null | awk 'NR>1 {print $7, $1, $2, $NF}' | sort -rn | head -20"])
        sh("swap", ["sysctl", "vm.swapusage"], 10)
        out.write_text("\n".join(parts))
        self.log(f"disk report: {why} ({out.name})")
        self.push_files({f"disk/{out.name}": out}, f"Mac queue: disk report {stamp}")
        return out

    def disk_stop(self, info: dict) -> bool:
        """Stop a job at once, with its whole process group (the runner, the command and every worker)."""
        try:
            os.killpg(int(info["pid"]), signal.SIGTERM)
            return True
        except (KeyError, TypeError, ValueError, ProcessLookupError, PermissionError):
            return False

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
                                  "status": status, "idle": state.get("idle", False),
                                  "idle_since": state.get("idle_since"),
                                  "workers": worker_cap(), "mains_power": on_mains_power(),
                                  "free_gb": round(free_gb(self.data_root), 1),
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
        self.tmp.mkdir(parents=True, exist_ok=True)
        os.environ["TMPDIR"] = str(self.tmp.resolve())  # jobs started below inherit it (ADR-158)
        free = free_gb(self.data_root)
        state = self.load_state()
        last = state.get("free_gb")  # ADR-159: a sharp fall between ticks, or a low disk, gets a report (hourly at most)
        reported = state.get("disk_report_at")
        recent = reported and datetime.fromisoformat(reported) > datetime.now(UTC) - timedelta(hours=1)
        if not recent and ((last is not None and last - free >= DROP_REPORT_GB) or free < min_free_gb()):
            why = f"fell {last - free:.1f} GB since the last check" if last is not None and last - free >= \
                DROP_REPORT_GB else f"below {min_free_gb():.0f} GB"
            self.disk_report(free, why)
            state["disk_report_at"] = datetime.now(UTC).isoformat(timespec="seconds")
        state["free_gb"] = round(free, 1)
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

        for eid, rec in recs.items():  # running jobs: still going, cut, or ended
            if rec.get("state") != "running":
                continue
            info = jobs.job_info(paths, rec["job_id"])
            if info["state"] in jobs.ACTIVE:
                if free < stop_free_gb():  # ADR-158: stop it now, workers too, and hold it until there is room
                    self.disk_stop(info)
                    rec["disk_hold"] = True
                    self.log(f"{eid}: stopped, only {free:.1f} GB free (below {stop_free_gb():.0f} GB)")
                    notes.append(f"{eid}: stopped for low disk ({free:.1f} GB free)")
                else:
                    notes.append(f"{eid}: running ({info['job_id']})")
                continue
            if rec.get("disk_hold") and info["state"] != "finished" and not rec.get("cancel_requested"):
                if free < min_free_gb():
                    notes.append(f"{eid}: held for low disk ({free:.1f} GB free, needs {min_free_gb():.0f})")
                    continue
                new = jobs.resume_job(paths, rec["job_id"])  # a disk stop does not count as an attempt
                rec["job_id"] = new["job_id"]
                rec.pop("disk_hold", None)
                self.log(f"{eid}: resumed after a low-disk stop as {new['job_id']}")
                notes.append(f"{eid}: resumed")
                continue
            if info["state"] == "interrupted" and not rec.get("cancel_requested"):
                if free < min_free_gb():
                    notes.append(f"{eid}: cut, waiting for disk space ({free:.1f} GB free)")
                    continue
                if rec.get("attempts", 1) >= MAX_ATTEMPTS:
                    rec["state"] = "failed"
                    rec["reason"] = f"cut {MAX_ATTEMPTS} times"
                else:
                    new = jobs.resume_job(paths, rec["job_id"])
                    rec |= {"job_id": new["job_id"], "attempts": rec.get("attempts", 1) + 1}
                    self.log(f"{eid}: resumed as {new['job_id']} (attempt {rec['attempts']})")
                    notes.append(f"{eid}: resumed")
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
        if not busy:  # nothing running: clear temporary files, and the cache when big or the disk is low
            notes += self.clean_up(low=free < min_free_gb())
            free = free_gb(self.data_root)
        pending = [e for e in entries if e.id not in recs and not e.cancel]
        if pending and not busy and free < min_free_gb():
            notes.append(f"{len(pending)} queued, not started: only {free:.1f} GB free (needs {min_free_gb():.0f})")
            self.log(notes[-1])
        elif pending and not busy:  # battery or mains alike: the owner manages the power (ADR-151)
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
        # report at once when the Mac runs out of work (and when work arrives again), not only once a day, so the
        # cloud can tell the owner and refill the queue (owner, 2026-11-23: "message me when there isn't anything")
        idle = not pending and not any(r.get("state") == "running" for r in recs.values()) and not busy
        if idle != state.get("idle"):
            state["idle"] = idle
            state["idle_since"] = datetime.now(UTC).isoformat(timespec="seconds") if idle else None
            state.pop("heartbeat_at", None)
        self.heartbeat(state, status)
        self.save_state(state)
        return status


ISSUE_URL = "https://github.com/benfirth-parks/banff-snowpack/issues/104"
QUEUE_URL = f"https://github.com/benfirth-parks/banff-snowpack/tree/{QUEUE_BRANCH}/queue"
RESULTS_URL = f"https://github.com/benfirth-parks/banff-snowpack/tree/{RESULTS_BRANCH}/results"
ORDER = {"running": 0, "waiting": 1, "finished": 2, "stopped": 3, "failed": 4, "rejected": 5, "cancelled": 6}


def overview(q: Queue, fetch: bool = False) -> dict:
    """What the Mac queue holds, for the lab app's Mac queue page: every entry seen (running, waiting, ended,
    refused or cancelled) with its note and times, plus the runner's last check-in. ``fetch`` reads the branch from
    GitHub first; otherwise the copy from the last tick is used."""
    if fetch:
        q.fetch()
    state = q.load_state()
    recs: dict = state.get("entries", {})
    entries, bad = q.entries()
    rows, seen = [], set()
    for e in entries:
        rec = recs.get(e.id) or {}
        st = rec.get("state") or ("cancelled" if e.cancel else "waiting")
        rows.append({"id": e.id, "kind": e.kind, "state": st, "note": e.note, "queued": e.created_at,
                     "started": rec.get("started_at", ""), "ended": rec.get("ended_at", ""),
                     "attempts": rec.get("attempts", 0), "published": rec.get("published"),
                     "lab_job": rec.get("job_id", "")})
        seen.add(e.id)
    for eid, reason in bad:
        if eid not in seen:
            rows.append({"id": eid, "kind": "", "state": "rejected", "note": reason, "queued": "", "started": "",
                         "ended": "", "attempts": 0, "published": (recs.get(eid) or {}).get("published"),
                         "lab_job": ""})
            seen.add(eid)
    for eid, rec in recs.items():  # entries the cloud has since removed from the queue branch
        if eid not in seen:
            rows.append({"id": eid, "kind": rec.get("kind", ""), "state": rec.get("state", ""),
                         "note": rec.get("reason", ""), "queued": "", "started": rec.get("started_at", ""),
                         "ended": rec.get("ended_at", ""), "attempts": rec.get("attempts", 0),
                         "published": rec.get("published"), "lab_job": rec.get("job_id", "")})
    rows.sort(key=lambda r: (ORDER.get(r["state"], 9), r["queued"] if r["state"] == "waiting" else "",
                             "" if r["state"] == "waiting" else r["ended"] or r["started"]))
    ended = [r for r in rows if r["state"] not in ("running", "waiting")]
    ended.sort(key=lambda r: r["ended"] or r["started"], reverse=True)
    rows = [r for r in rows if r["state"] in ("running", "waiting")] + ended
    log = q.root / "runner.log"
    return {"rows": rows, "heartbeat_at": state.get("heartbeat_at"),
            "last_lines": log.read_text().splitlines()[-12:] if log.is_file() else [],
            "workers": worker_cap(), "mains_power": on_mains_power(),
            "free_gb": round(free_gb(q.data_root), 1), "min_free_gb": min_free_gb(),
            "issue_url": ISSUE_URL, "queue_url": QUEUE_URL, "results_url": RESULTS_URL}


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
        from snowagent.obs.pits import keep_pits

        keep_pits(repo)  # pit files stay on the Mac after profiles/ left git (ADR-165)
        print(q.tick())
        return 0
    finally:
        lock.unlink(missing_ok=True)
