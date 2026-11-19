"""Shared pieces of the training pages (ADR-119: Start a run, Watch the run, Run results, Blind test and site,
Promotion check): which run a page shows, when that run ran, and its result on the locked test winters."""

from __future__ import annotations

from datetime import UTC, datetime

from snowagent.lab.services.activity import clock
from snowagent.lab.services.training import real_runs, run_label, running_training
from snowagent.lab.storage.paths import LabPaths
from snowagent.lab.ui.pages import link

RUN_KEY = "lab-run"  # the run chosen on one training page is the one the next page opens on
AGENT_KEY = "lab-agent"  # (run, round, agent label) chosen on Run results: Blind test and site opens on it


def pick_run(st, paths: LabPaths) -> str | None:
    """The training run a page shows: the one running now, else the one chosen on another page, else the newest.
    Promotion-check folds are left out (one that is running now is listed, so it can be watched). None: no run."""
    runs = real_runs(paths)
    live = running_training(paths)
    if live and live not in runs:
        runs = [live, *runs]
    if not runs:
        return None
    kept = st.session_state.get(RUN_KEY)
    default = live or (kept if kept in runs else runs[0])
    run_id = st.selectbox("Training run", runs, index=runs.index(default),
                          format_func=lambda r: ("running now · " if r == live else "") + run_label(paths, r),
                          help="newest first; a run that is going now is chosen by itself")
    st.session_state[RUN_KEY] = run_id
    return run_id


def run_day(run_id: str | None) -> str:
    """'the run of 24 Oct 2026' from a run's id (its start, UTC); the id itself when it carries no date."""
    try:
        t = datetime.strptime(str(run_id).split("-")[1][:13], "%Y%m%dT%H%M")
    except (IndexError, ValueError):
        return f"run `{run_id}`" if run_id else "an unknown run"
    return f"the run of {t.day} {t:%b %Y}"


def no_runs(st) -> None:
    st.info("No training run yet. Start one on the Start a run page.")
    link(st, "start", "Start a training run")


def _when(text: str | None) -> datetime | None:
    try:
        return datetime.fromisoformat(text) if text else None
    except (TypeError, ValueError):
        return None


def run_when(ov: dict, now: datetime | None = None) -> str:
    """'It finished at 03:12 on 25 Oct, after 15 of 15 rounds.': when a run that is not going now ended, so an old
    run is never mistaken for the one just started."""
    now = now or datetime.now(UTC)
    status, done = ov["status"], len(ov["rounds"])
    state = status.get("state", "unknown")
    t1 = _when(status.get("finished_at") or status.get("updated_at"))
    verb = {"finished": "finished", "stopped": "was stopped", "failed": "stopped with an error",
            "interrupted": "was cut short"}.get(state, f"is {state}")
    return (f"It {verb}" + (f" at {clock(t1, now)}" if t1 else "")
            + f", after {done} of {ov['plan'].get('rounds', '?')} rounds.")


def locked_verdict(st, ov: dict) -> None:
    """The line that matters most: the latest round's best agent against standard SNOWPACK on the locked winters."""
    locked, trace = ov.get("locked"), ov["rounds"]
    if not locked or not len(trace):
        return
    lk = trace.dropna(subset=["locked_composite"])
    inc = (locked.get("incumbent") or {}).get("composite")
    if not len(lk) or inc is None:
        return
    last = lk.iloc[-1]
    d = last["locked_composite"] - inc
    (st.success if d > 0 else st.warning)(
        f"Round {int(last['round'])}'s best agent ({last['best_name']}) scores {last['locked_composite']:.4f} on the "
        f"locked winters, against {inc:.4f} for standard SNOWPACK ({d:+.4f}).")
