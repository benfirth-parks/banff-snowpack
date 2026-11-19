"""What the lab is doing now, and what to do next, in plain words (ADR-119): the list behind the bar at the top of
every page of the app and the Overview's next-step card. Read-only, apart from ``dismiss`` (a marker file beside a
job's own files): it starts nothing and changes no run, score or case."""

from __future__ import annotations

import json
import statistics
from datetime import UTC, datetime, timedelta
from pathlib import Path

from snowagent.lab.services.jobs import ACTIVE, job_info, jobs_root, list_jobs
from snowagent.lab.services.training import real_runs, run_overview, running_training, time_left
from snowagent.lab.storage.paths import LabPaths

# a job as a person reads it: (while it runs, once it is over), and the page that follows or resumes it
WHAT = {"setup": ("Setting up the data", "Data set-up", "overview"),
        "build-cases": ("Building the benchmark cases", "Building the benchmark cases", "cases"),
        "compete": ("Running a competition", "The competition", "leaderboard"),
        "train": ("Training", "The training run", "watch"),
        "check-estimate": ("Estimating the promotion check", "The promotion check estimate", "check"),
        "check-loso": ("Running the promotion check", "The promotion check", "check"),
        "winter-estimate": ("Estimating the winter-by-winter test", "The winter test estimate", "winter"),
        "winter-test": ("Running the winter-by-winter test", "The winter-by-winter test", "winter"),
        "rescore": ("Re-scoring a competition", "The re-score", "leaderboard"),
        "group-check": ("Running the group check", "The group check", "group"),
        "area-setup": ("Setting up the Parker Ridge check", "The Parker Ridge check set-up", "start"),
        "update": ("Updating the app", "The app update", None),
        "engine": ("Building the SNOWPACK engine", "The SNOWPACK engine build", "overview")}
STEPS = {"prepare": "getting the station files, pits and weather", "init": "making the lab's tables",
         "import": "loading the pits and weather", "build-cases": "building the cases",
         "prepare-area": "copying the Jasper weather and pits", "pull": "getting the new version",
         "install": "installing what the new version needs", "engine": "rebuilding the SNOWPACK engine"}
FIRST_TIME_MIN = {"area-setup": (5, 10)}  # no finished job of the kind yet: the time its page promises
POLL_S = 5            # the bar re-reads the jobs this often while one runs
DONE_FOR_S = 3600     # a finished job stays on the bar this long (or until hidden)
PROBLEM_FOR_S = 86400  # a failed, stopped or interrupted one this long (or until hidden, or started again)
LOOK_BACK = 40        # jobs read, newest first
DISMISSED = "dismissed"
CHECKS = ("check-loso", "winter-test")  # jobs that re-train several times, one part after the other (ADR-125)


def _when(text: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if text else None
    except (TypeError, ValueError):
        return None


def minutes(seconds: float) -> str:
    """'under a minute', '7 min', '2 h 05 min'."""
    m = int(seconds // 60)
    if m < 1:
        return "under a minute"
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def clock(t: datetime, now: datetime | None = None) -> str:
    """'17:55' in the app machine's local time (the Mac the job runs on); with the date when it is not today."""
    local, today = t.astimezone(), (now or datetime.now(UTC)).astimezone().date()
    return f"{local:%H:%M}" if local.date() == today else f"{local:%H:%M} on {local.day} {local:%b}"


def usual_seconds(paths: LabPaths, kind: str, exclude: str | None = None) -> float | None:
    """How long this kind of job took the last few times it finished here (the median of up to three)."""
    took = []
    for job_id in list_jobs(paths)[:LOOK_BACK * 2]:
        if job_id == exclude:
            continue
        info = job_info(paths, job_id)
        t0, t1 = _when(info.get("created_at")), _when(info.get("finished_at"))
        if info.get("kind") == kind and info["state"] == "finished" and t0 and t1:
            took.append((t1 - t0).total_seconds())
            if len(took) == 3:
                break
    return statistics.median(took) if took else None


def dismiss(paths: LabPaths, job_id: str) -> None:
    """Hide a job that is over from the bar (its files and its row on the Jobs page stay)."""
    d = jobs_root(paths) / Path(job_id).name
    if d.is_dir():
        (d / DISMISSED).write_text(datetime.now(UTC).isoformat())


def _training_line(paths: LabPaths, run_id: str, now: datetime) -> tuple[str, float | None]:
    """'round 3 of 20, about 5 h 10 min left, done around 06:10' and the share done (0-1), from the run's own
    rounds."""
    ov = run_overview(paths, run_id)
    status, plan = ov["status"], ov["plan"]
    r, n = int(status.get("round") or 0), plan.get("rounds")
    parts = [f"round {r} of {n}" if n else f"round {r}"]
    left = time_left(ov, now)
    if left is not None:
        parts.append(f"about {minutes(left)} left, done around {clock(now + timedelta(seconds=left), now)}")
    share = None
    if n:
        within = (status.get("done") or 0) / status["total"] if status.get("total") else 0.0
        share = min(1.0, max(0.0, (max(r, 1) - 1 + min(within, 1.0)) / int(n)))
    return ", ".join(parts), share


def _check_line(paths: LabPaths, check_id: str, now: datetime) -> tuple[str, float | None]:
    """ADR-125: 'part 2 of 5, about 1 h 40 min left, done around 23:10' and the share done (0-1) of a promotion
    check or a winter-by-winter test, from its own status file and the part that is training now. The time left
    is that part's own time left, plus as long again for each part still to come."""
    from snowagent.lab.training.loso import checks_root

    try:
        status = json.loads((checks_root(paths) / Path(check_id).name / "status.json").read_text())
    except (OSError, ValueError):
        return "", None
    parts_all, done = status.get("seasons") or [], status.get("done") or []
    if not parts_all:
        return "", None
    n, k = len(parts_all), min(len(done) + 1, len(parts_all))
    if status.get("phase") == "building":
        return f"getting the cases of its {n} parts ready", 0.0
    text, within, left = [f"part {k} of {n}"], 0.0, None
    live = running_training(paths)
    if live and live.startswith(f"{check_id}-"):
        ov = run_overview(paths, live)
        within = _training_line(paths, live, now)[1] or 0.0
        own = time_left(ov, now)
        if own is not None and 0.02 <= within < 1.0:
            left = own + (n - k) * own / (1.0 - within)
    if left is not None:
        text.append(f"about {minutes(left)} left, done around {clock(now + timedelta(seconds=left), now)}")
    return ", ".join(text), min(1.0, (len(done) + within) / n)


def job_item(paths: LabPaths, info: dict, now: datetime) -> dict:
    kind, state = info.get("kind"), info["state"]
    doing, done, page = WHAT.get(kind, (info.get("title") or "A background job",) * 2 + (None,))
    steps = info["step_states"]
    current = next((i for i, s in enumerate(steps) if s.get("state") in ("running", "failed", "stopped")),
                   sum(1 for s in steps if s.get("state") == "done"))
    current = min(current, max(len(steps) - 1, 0))
    name = steps[current]["name"] if steps else ""
    t0, t1 = _when(info.get("created_at")), _when(info.get("finished_at"))
    item = {"job_id": info["job_id"], "kind": kind, "state": state, "active": state in ACTIVE, "page": page,
            "what": doing if state in ACTIVE else done, "started": t0, "ended": t1,
            "step": current + 1, "steps": len(steps), "step_text": STEPS.get(name, name),
            "run_id": (info.get("refs") or {}).get("run_id"), "share": None, "detail": "",
            "check_id": (info.get("refs") or {}).get("check_id") if kind in CHECKS else None}
    if state in ACTIVE:
        line = _check_line(paths, item["check_id"], now) if item["check_id"] else ("", None)
        if kind == "train" and item["run_id"]:
            item["detail"], item["share"] = _training_line(paths, item["run_id"], now)
        elif line[0]:
            item["detail"], item["share"] = line
            if t0:
                item["detail"] += f" · started {clock(t0, now)}"
        else:
            parts = []
            if len(steps) > 1:
                parts.append(f"step {current + 1} of {len(steps)}: {item['step_text']}")
                item["share"] = current / len(steps)
            if t0:
                parts.append(f"started {clock(t0, now)}, {minutes((now - t0).total_seconds())} so far")
            usual = usual_seconds(paths, kind, exclude=info["job_id"])
            if usual:
                parts.append(f"last time it took {minutes(usual)}")
            elif kind in FIRST_TIME_MIN:
                parts.append("usually {}-{} minutes".format(*FIRST_TIME_MIN[kind]))
            item["detail"] = " · ".join(parts)
    else:
        end = t1 or _when((info.get("status") or {}).get("updated_at")) or t0
        item["ended"] = end
        took = f" (took {minutes((end - t0).total_seconds())})" if state == "finished" and t0 and end else ""
        at = f" at {clock(end, now)}" if end else ""
        where = f" in step {current + 1} of {len(steps)} ({item['step_text']})" if len(steps) > 1 else ""
        item["detail"] = {"finished": f"finished{at}{took}",
                          "failed": f"stopped with an error{at}{where}",
                          "stopped": f"was stopped{at}{where}",
                          "interrupted": f"was cut short{where} (the computer slept or restarted, or its window "
                                         "was closed)"}.get(state, state)
    return item


def activity(paths: LabPaths, now: datetime | None = None) -> list[dict]:
    """The jobs worth a line at the top of every page: the ones running now, then the ones that ended lately and
    were not hidden (a problem stays a day, a clean finish an hour; an older try of a job started again is left
    out). A training run started from a terminal has no job: it is listed from its own status file."""
    now = now or datetime.now(UTC)
    items, seen_keys, job_runs = [], set(), set()
    for job_id in list_jobs(paths)[:LOOK_BACK]:
        info = job_info(paths, job_id)
        key = info.get("key") or info.get("kind")
        newer = key in seen_keys  # a newer job of the same kind is listed or was looked at
        seen_keys.add(key)
        state = info["state"]
        if state in ACTIVE:
            items.append(job_item(paths, info, now))
            job_runs.add((info.get("refs") or {}).get("run_id"))
            continue
        if newer or (info["dir"] / DISMISSED).exists():
            continue
        end = _when(info.get("finished_at")) or _when((info.get("status") or {}).get("updated_at")) \
            or _when(info.get("created_at"))
        if end is None or (now - end).total_seconds() > (DONE_FOR_S if state == "finished" else PROBLEM_FOR_S):
            continue
        items.append(job_item(paths, info, now))
    live = running_training(paths)
    # a check's or winter test's own re-training is that job's line, not a training run of its own
    parts = [i["check_id"] for i in items if i["active"] and i.get("check_id")]
    if live and live not in job_runs and not any(live.startswith(f"{c}-") for c in parts):
        detail, share = _training_line(paths, live, now)
        items.append({"job_id": None, "kind": "train", "state": "running", "active": True, "page": "watch",
                      "what": "Training", "started": None, "ended": None, "step": 1, "steps": 1, "step_text": "",
                      "run_id": live, "share": share, "detail": detail})
    return sorted(items, key=lambda i: not i["active"])  # running first; each group stays newest first


def signature(items: list[dict]) -> tuple:
    """Changes when a job starts, ends or is hidden: the pages redraw themselves then."""
    return tuple((i["job_id"] or i["run_id"], i["state"]) for i in items)


def next_step(paths: LabPaths, *, data_ok: bool, engine_ok: bool, cases_ok: bool, items: list[dict] | None = None,
              now: datetime | None = None) -> dict:
    """The one thing to do next, for the Overview: ``tone`` (info, success or warning), ``text`` and ``links`` as
    (page key, label) pairs, the first being the main one."""
    now = now or datetime.now(UTC)
    items = activity(paths, now) if items is None else items
    running = [i for i in items if i["active"]]
    training = next((i for i in running if i["kind"] == "train"), None)
    if training:
        return {"tone": "info", "links": [("watch", "Watch the run")],
                "text": f"**A training run is going**: {training['detail']}. Nothing to do until it ends. You can "
                        "close the browser, the run keeps going; keep the computer awake and plugged in."}
    if running:
        job = running[0]
        return {"tone": "info", "links": [(job["page"], "Open its page")] if job["page"] else [],
                "text": f"**{job['what']}** ({job['detail']}). Nothing to do until it finishes: this page shows "
                        "when it is done."}
    if not data_ok:
        return {"tone": "warning", "links": [],
                "text": "**Next: set up the data.** Press **Run set-up** in the *Set up data* box below. It "
                        "fetches the pits and the weather; you only do this once."}
    if not cases_ok:
        return {"tone": "warning", "links": [("cases", "Build the benchmark cases")],
                "text": "**Next: build the benchmark cases** (the past pits the agents are tested on). One "
                        "button, a few minutes."}
    if not engine_ok:
        return {"tone": "warning", "links": [],
                "text": "**Next: build the SNOWPACK engine.** Press **Build the SNOWPACK engine** just below "
                        "(about 5 minutes, once)."}
    runs = real_runs(paths)
    if not runs:
        return {"tone": "success", "links": [("start", "Start a training run")],
                "text": "**Everything is ready.** Next: start your first training run."}
    ov = run_overview(paths, runs[0])
    state, plan = ov["status"].get("state"), ov["plan"]
    done, of = len(ov["rounds"]), plan.get("rounds", "?")
    started = _when(ov["status"].get("started_at") or ov.get("created_at"))
    day = f" of {started.astimezone().day} {started.astimezone():%b}" if started else ""
    if state in ("interrupted", "stopped", "failed"):
        return {"tone": "warning", "links": [("watch", "Resume the run"), ("start", "Start a new run instead")],
                "text": f"**Your last run{day} stopped after round {done} of {of}.** Next: resume it (it carries "
                        "on where it stopped), or start a new one."}
    return {"tone": "success",
            "links": [("results", "See how it did"), ("reports", "Read its report"),
                      ("start", "Start another run")],
            "text": f"**Your last run{day} is finished** ({done} of {of} rounds). Next: see how it did, or start "
                    "another run."}
