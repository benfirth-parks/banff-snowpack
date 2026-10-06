"""The status block of a background job (ADR-077), shared by every page that starts one: state, steps, the live log
tail, and Refresh, Stop and Resume buttons."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from snowagent.lab.services.jobs import ACTIVE, KINDS, JobBusy, latest_job, log_tail, resume_job, stop_job
from snowagent.lab.storage.paths import LabPaths

STATE_TEXT = {"starting": "starting", "running": "running", "finished": "finished", "failed": "failed",
              "stopped": "stopped", "interrupted": "interrupted (the process ended without finishing)"}


def _elapsed(info: dict) -> str:
    try:
        t0 = datetime.fromisoformat(info["created_at"])
        t1 = datetime.fromisoformat(info["finished_at"]) if info.get("finished_at") else datetime.now(UTC)
    except (KeyError, TypeError, ValueError):
        return ""
    m = int((t1 - t0).total_seconds() // 60)
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def job_block(st, paths: LabPaths, info: dict, *, key: str, tail_lines: int = 40) -> None:
    """One job: state line, steps, log tail and its buttons. ``key`` keeps the widgets of several blocks apart."""
    state = info["state"]
    line = f"**{info.get('title', info['job_id'])}**: {STATE_TEXT.get(state, state)} · started " \
           f"{str(info.get('created_at', '?')).replace('T', ' ')[:16]} UTC · {_elapsed(info)}"
    if state in ACTIVE:
        st.info(line, icon="⏳")
    elif state == "finished":
        st.success(line, icon="✅")
    elif state == "failed":
        st.error(line + " (the end of the log below says why)", icon="❌")
    else:
        st.warning(line + ": Resume continues where it stopped", icon="⏸️")
    if len(info["step_states"]) > 1:
        st.dataframe(pd.DataFrame([{"step": s["name"], "state": s.get("state"), "exit code": s.get("exit_code")}
                                   for s in info["step_states"]]), hide_index=True)
    tail = log_tail(info)
    if tail:
        st.code("\n".join(tail.rstrip().splitlines()[-tail_lines:]), language=None)
    else:
        st.caption("No output yet.")
    c1, c2, c3, _ = st.columns([1, 1, 1, 3])
    if c1.button("Refresh", key=f"refresh-{key}"):
        st.rerun()
    if state in ACTIVE and c2.button("Stop", key=f"stop-{key}"):
        st.info(f"{KINDS.get(info.get('kind'), 'job')}: {stop_job(paths, info['job_id'])}. Resume continues it.")
    if state in ("stopped", "failed", "interrupted") and c3.button("Resume", key=f"resume-{key}"):
        try:
            new = resume_job(paths, info["job_id"])
            st.success(f"Resumed as job `{new['job_id']}`. Refresh to follow it.")
        except JobBusy as exc:
            st.error(str(exc))
    st.caption(f"Job `{info['job_id']}` · log `{info.get('log')}` · runs on its own: closing the browser or the app "
               "does not stop it.")


def latest_job_block(st, paths: LabPaths, job_key: str, *, key: str | None = None) -> dict | None:
    """The newest job of ``job_key`` (e.g. ``setup``), if any."""
    info = latest_job(paths, job_key)
    if info:
        job_block(st, paths, info, key=key or job_key)
    return info
