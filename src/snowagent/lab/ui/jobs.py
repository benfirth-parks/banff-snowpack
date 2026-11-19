"""The status block of a background job (ADR-077, ADR-119), shared by every page that starts one: what it is doing
in plain words, its steps as a checklist, Stop and Resume, and the technical log folded away. It updates itself
while the job runs."""

from __future__ import annotations

from datetime import UTC, datetime

from snowagent.lab.services import activity as act
from snowagent.lab.services.jobs import (
    ACTIVE,
    KINDS,
    JobBusy,
    job_info,
    latest_job,
    log_tail,
    resume_job,
    stop_job,
)
from snowagent.lab.storage.paths import LabPaths

STEP_MARK = {"done": "✅", "running": "⏳", "failed": "❌", "stopped": "⏸️"}


def _draw(st, paths: LabPaths, info: dict, *, key: str, tail_lines: int, controls: bool, show_log: bool) -> None:
    state = info["state"]
    item = act.job_item(paths, info, datetime.now(UTC))
    line = f"**{item['what']}**{': ' if item['active'] else ' '}{item['detail']}"
    if state in ACTIVE:
        st.info(line, icon="⏳")
    elif state == "finished":
        st.success(line + ".", icon="✅")
    elif state == "failed":
        st.error(line + ". The end of the log below says why.", icon="❌")
    else:
        st.warning(line + ". Resume continues it where it stopped.", icon="⏸️")
    steps = info["step_states"]
    if len(steps) > 1:
        st.markdown("  \n".join(f"{STEP_MARK.get(s.get('state'), '◻️')} {i}. {act.STEPS.get(s['name'], s['name'])}"
                                for i, s in enumerate(steps, 1)))
    tail = log_tail(info)
    shown = "\n".join(tail.rstrip().splitlines()[-tail_lines:]) if tail else ""
    if show_log and shown:
        st.code(shown, language=None)
    if controls:
        c1, c2, _ = st.columns([1, 1, 4])
        if state in ACTIVE and c1.button("Stop", key=f"stop-{key}"):
            st.info(f"{KINDS.get(info.get('kind'), 'job')}: {stop_job(paths, info['job_id'])}. Resume continues it.")
        if state in ("stopped", "failed", "interrupted") and c2.button("Resume", key=f"resume-{key}"):
            try:
                resume_job(paths, info["job_id"])
                st.rerun()
            except JobBusy as exc:
                st.error(str(exc))
    with st.expander("Technical details (the log)", expanded=state == "failed" and not show_log):
        if not show_log:
            if shown:
                st.code(shown, language=None)
            else:
                st.caption("No output yet.")
        st.caption(f"Job `{info['job_id']}` · log file `{info.get('log')}`. It runs on its own: closing the browser "
                   "or the app does not stop it.")


def job_block(st, paths: LabPaths, info: dict, *, key: str, tail_lines: int = 40, controls: bool = True,
              show_log: bool = False) -> None:
    """One job: the state line, its steps, its buttons (``controls``: off where the page has its own) and the log
    (``show_log``: in the open, where the log is the result; otherwise folded away). ``key`` keeps the widgets of
    several blocks apart."""
    first = info["state"]

    @st.fragment(run_every=act.POLL_S if first in ACTIVE else None)
    def block() -> None:
        now = job_info(paths, info["job_id"])
        if now["state"] != first:
            st.rerun()  # it ended (or began again): the page around it reads differently now
        _draw(st, paths, now, key=key, tail_lines=tail_lines, controls=controls, show_log=show_log)

    block()


def latest_job_block(st, paths: LabPaths, job_key: str, *, key: str | None = None) -> dict | None:
    """The newest job of ``job_key`` (e.g. ``setup``), if any."""
    info = latest_job(paths, job_key)
    if info:
        job_block(st, paths, info, key=key or job_key)
    return info
