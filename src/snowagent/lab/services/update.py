"""Keeping the lab app's own files current from inside the app (ADR-119): is a newer version on GitHub, fetch it
as a background job (``git pull --ff-only``, then ``pip install -e`` and the engine build only when the new version
changed what they read), and restart the app. Nothing here touches lab data, runs or scores.

The check is off unless ``SNOWAGENT_LAB_UPDATE_CHECK=1`` and the restart is off unless ``SNOWAGENT_LAB_SUPERVISED=1``
(``snowagent lab app`` sets both), so tests and a plain ``streamlit run`` never reach the network and are never
stopped."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from snowagent.lab.services.jobs import ACTIVE, job_info, list_jobs, start_job, step
from snowagent.lab.storage.paths import LabPaths

ENV = "SNOWAGENT_LAB_UPDATE_CHECK"
STARTED_ENV = "SNOWAGENT_LAB_STARTED_HEAD"
STARTED_AT_ENV = "SNOWAGENT_LAB_STARTED_AT"
SUPERVISED_ENV = "SNOWAGENT_LAB_SUPERVISED"  # `snowagent lab app` is waiting on this app and starts it again
RESTART_CODE = 75     # the exit code that asks it to (any other code ends `snowagent lab app` as before)
CHECK_EVERY_S = 900   # GitHub is asked at most this often
FETCH_TIMEOUT_S = 25
EXTRAS = "dev,lab"    # as scripts/setup_env.sh installs
ENGINE_FILES = ("scripts/build_snowpack.sh", "scripts/snowpack-patches/")
MAX_NEWS = 6


def enabled() -> bool:
    return os.environ.get(ENV) == "1"


def git(root: Path, *args: str, timeout: float = 20) -> tuple[int, str]:
    """(exit code, output) of one git command in ``root``; never prompts for a password, never raises."""
    env = os.environ | {"GIT_TERMINAL_PROMPT": "0"}
    try:
        p = subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, timeout=timeout,
                           env=env, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return p.returncode, (p.stdout if p.returncode == 0 else p.stderr).strip()


def head(root: Path) -> str | None:
    """The commit of the files on disk."""
    code, out = git(root, "rev-parse", "HEAD")
    return out if code == 0 and out else None


def started_head(root: Path) -> str | None:
    """The commit the running app loaded its code from: read the first time a page is drawn and kept for the life
    of the app process (in its environment, so it outlives a module reload; a restarted app reads its own)."""
    if STARTED_ENV not in os.environ:
        os.environ[STARTED_ENV] = head(root) or ""
        os.environ[STARTED_AT_ENV] = datetime.now(UTC).isoformat(timespec="seconds")  # as jobs record times
    return os.environ[STARTED_ENV] or None


def stale(root: Path) -> bool:
    """The files on disk are another version than the one the running app loaded: it needs a restart."""
    started, disk = started_head(root), head(root)
    return bool(started and disk and started != disk)


def version_text(root: Path, commit: str = "HEAD") -> str:
    """'18 Nov 2026 (67c1e60)': the date of the newest change in a version, and its short id."""
    code, out = git(root, "log", "-1", "--format=%cI %h", commit)
    if code != 0 or not out:
        return "unknown"
    when, short = out.split()
    t = datetime.fromisoformat(when).astimezone()
    return f"{t.day} {t:%b %Y} ({short})"


def check(root: Path, fetch: bool = True) -> dict:
    """Is a newer version of this branch on GitHub? ``state``: ``current``, ``behind`` (with ``behind`` commits,
    ``news`` the titles of what was merged, ``reinstall`` and ``rebuild`` when the new version changed the Python
    requirements or the engine's build), ``diverged`` (this copy has commits of its own: no automatic update) or
    ``unknown`` (``reason`` says why: no git, no branch, GitHub not reached)."""
    code, branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if code != 0 or not branch:
        return {"state": "unknown", "reason": "this copy of the project is not a git checkout"}
    if branch == "HEAD":
        return {"state": "unknown", "reason": "this copy is not on a branch"}
    remote = f"origin/{branch}"
    if fetch and git(root, "fetch", "--quiet", "origin", branch, timeout=FETCH_TIMEOUT_S)[0] != 0:
        return {"state": "unknown", "branch": branch, "reason": "GitHub could not be reached"}
    if git(root, "rev-parse", "--verify", "--quiet", remote)[0] != 0:
        return {"state": "unknown", "branch": branch, "reason": f"GitHub has no branch {branch}"}
    counts = [git(root, "rev-list", "--count", r)[1] for r in (f"HEAD..{remote}", f"{remote}..HEAD")]
    if not all(c.isdigit() for c in counts):
        return {"state": "unknown", "branch": branch, "reason": "the versions could not be compared"}
    behind, ahead = int(counts[0]), int(counts[1])
    out = {"state": "current", "branch": branch, "behind": behind, "ahead": ahead}
    if behind == 0:
        return out
    files = git(root, "diff", "--name-only", f"HEAD...{remote}")[1].splitlines()
    bodies = git(root, "log", "--merges", "--first-parent", "--format=%b%x00", f"HEAD..{remote}")[1].split("\x00")
    news = [b.strip().splitlines()[0].strip() for b in bodies if b.strip()]
    if not news:  # no merged pull requests in the range: the commits' own first lines
        news = git(root, "log", "--no-merges", "--format=%s", f"HEAD..{remote}")[1].splitlines()
    return out | {"state": "diverged" if ahead else "behind", "news": news[:MAX_NEWS],
                  "more_news": max(0, len(news) - MAX_NEWS), "reinstall": "pyproject.toml" in files,
                  "rebuild": any(f.startswith(ENGINE_FILES) for f in files),
                  "version": version_text(root, remote)}


# --------------------------------------------------------------------------------------------- checked in the background

_LOCK = threading.Lock()
_LATEST: dict = {"result": None, "at": 0.0, "busy": False}


def latest(root: Path, max_age_s: float = CHECK_EVERY_S, wait: bool = False) -> dict | None:
    """The last check's result without waiting for GitHub: a check older than ``max_age_s`` is renewed on a
    background thread and shows on a later page view. None until the first check ends, or when checks are off."""
    if not enabled():
        return None

    def work() -> None:
        try:
            result = check(root)
        finally:
            with _LOCK:
                _LATEST["busy"] = False
        with _LOCK:
            _LATEST.update(result=result, at=time.monotonic())

    with _LOCK:
        stale = _LATEST["result"] is None or time.monotonic() - _LATEST["at"] > max_age_s
        start = stale and not _LATEST["busy"]
        if start:
            _LATEST["busy"] = True
    if start:
        t = threading.Thread(target=work, name="lab-update-check", daemon=True)
        t.start()
        if wait:
            t.join(FETCH_TIMEOUT_S + 10)
    with _LOCK:
        return _LATEST["result"]


def forget() -> None:
    """Ask GitHub again at the next page view (after an update, or the Check now button)."""
    with _LOCK:
        _LATEST.update(result=None, at=0.0)


# --------------------------------------------------------------------------------------------- update and restart


def busy_jobs(paths: LabPaths) -> list[dict]:
    """Jobs running now: an update or an engine build waits for them, so no run gets new code half way."""
    return [i for i in (job_info(paths, j) for j in list_jobs(paths)[:40]) if i["state"] in ACTIVE]


def update_steps(root: Path, info: dict) -> list[dict]:
    """Fast-forward to GitHub's version of the branch; then only what that version needs."""
    root = Path(root).resolve()
    steps = [step("pull", ["git", "-C", str(root), "pull", "--ff-only", "origin", info["branch"]])]
    if info.get("reinstall"):
        steps.append(step("install", [sys.executable, "-m", "pip", "install", "-q", "-e", f"{root}[{EXTRAS}]"]))
    if info.get("rebuild"):
        steps.append(engine_step(root))
    return steps


def engine_step(root: Path) -> dict:
    return step("engine", ["bash", str(Path(root).resolve() / "scripts" / "build_snowpack.sh")])


def start_update(paths: LabPaths, root: Path, info: dict) -> dict:
    """The update as one background job (it shows on the bar at the top of every page); the app restarts itself
    when it has finished."""
    return start_job(paths, "update", "update the app from GitHub", update_steps(root, info), cwd=Path(root))


def start_engine_build(paths: LabPaths, root: Path) -> dict:
    """Build (or rebuild) the pinned SNOWPACK engine with this repository's patches: scripts/build_snowpack.sh as a
    background job, about five minutes."""
    return start_job(paths, "engine", "build the SNOWPACK engine", [engine_step(root)], cwd=Path(root))


def engine_stale(root: Path, binary: str | None) -> list[str]:
    """The repository's engine patches the engine at ``binary`` was built without (it then needs a rebuild)."""
    from snowagent.lab.agents.physics import engine_patches

    wanted = {p.stem for p in (Path(root) / "scripts" / "snowpack-patches").glob("*.patch")}
    return sorted(wanted - engine_patches(binary)) if binary else []


def updating(paths: LabPaths) -> bool:
    """The app's own update job is going (it may be past the pull, installing or rebuilding the engine)."""
    from snowagent.lab.services.jobs import latest_job

    job = latest_job(paths, "update")
    return bool(job and job["state"] in ACTIVE)


def just_updated(paths: LabPaths) -> bool:
    """The app's own update job finished since this app started (the person pressed Update and restart in it): the
    one case in which it restarts without being asked. A `git pull` in Terminal, or an update that an earlier
    start of the app ran, gets the Restart button instead."""
    from snowagent.lab.services.jobs import latest_job

    job = latest_job(paths, "update")
    if not job or job["state"] != "finished":
        return False
    try:
        ended = datetime.fromisoformat(job["finished_at"])
        began = datetime.fromisoformat(os.environ[STARTED_AT_ENV])
        return ended >= began
    except (KeyError, TypeError, ValueError):  # no end time, or one without a time zone
        return False


def can_restart() -> bool:
    """`snowagent lab app` started this app and starts it again when it asks; a plain ``streamlit run`` cannot."""
    return os.environ.get(SUPERVISED_ENV) == "1"


def restart() -> None:
    """End this app process with the code that makes `snowagent lab app` start a fresh one on the same address
    (a new process: nothing of the old version stays in memory); the browser reconnects by itself. Background
    jobs are separate processes: they go on."""
    if not can_restart():
        raise RuntimeError("this app was not started by `snowagent lab app`: stop it and start it again by hand")
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(RESTART_CODE)  # at once, from whatever thread drew the page; the server's own shutdown would return 0
