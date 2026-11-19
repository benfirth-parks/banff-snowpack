"""Jobs: every background job the app started (set up data, build cases, competitions, training, promotion checks
and their estimates, re-scores, app updates and engine builds), running first, in plain words, with Stop and Resume
and the technical log of one job (ADR-077, ADR-119)."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
import streamlit as st

from snowagent.lab.services.activity import clock, job_item, minutes
from snowagent.lab.services.jobs import ACTIVE, job_info, list_jobs
from snowagent.lab.ui.app import lab_context, page_header
from snowagent.lab.ui.jobs import job_block

page_header(st, "Jobs",
            "Everything the app has run in the background, newest first. You rarely need this page: what is running "
            "shows at the top of every page. Come here when something failed and you want to see why.", page="jobs")
cfg, paths = lab_context(__file__)
st.caption("Work started from these pages runs on its own: closing the browser or stopping the app does not stop "
           "it. Stop ends a job (a training at its next case); Resume starts it again where it stopped.")

jobs = [job_info(paths, j) for j in list_jobs(paths)]
if not jobs:
    st.info("No background job yet. Start one from Overview (Set up data), Benchmark cases (Build), Leaderboard "
            "(Run a competition), Start a run or Promotion check.")
    st.stop()

now = datetime.now(UTC)
jobs.sort(key=lambda j: j["state"] not in ACTIVE)  # running first, then newest first (stable)
items = [job_item(paths, j, now) for j in jobs]
running = [j for j in jobs if j["state"] in ACTIVE]
c1, c2 = st.columns(2)
c1.metric("Running", len(running))
c2.metric("Finished, stopped or failed", len(jobs) - len(running))
STATE = {"starting": "starting", "running": "running", "finished": "finished", "failed": "failed (error)",
         "stopped": "stopped", "interrupted": "cut short"}


def _took(i: dict) -> str:
    end = i["ended"] or (now if i["active"] else None)
    return minutes((end - i["started"]).total_seconds()) if end and i["started"] else ""


st.dataframe(pd.DataFrame([{"what": i["what"], "state": STATE.get(i["state"], i["state"]),
                            "started": clock(i["started"], now) if i["started"] else "",
                            "ended": clock(i["ended"], now) if i["ended"] and not i["active"] else "",
                            "took": _took(i)} for i in items]), width="stretch", hide_index=True)

labels = {j["job_id"]: f"{i['what']} · {STATE.get(i['state'], i['state'])} · started "
          f"{clock(i['started'], now) if i['started'] else '?'}" for j, i in zip(jobs, items, strict=True)}
job_id = st.selectbox("Look at one job", list(labels), format_func=labels.get)
info = next(j for j in jobs if j["job_id"] == job_id)
job_block(st, paths, info, key="jobs-page", tail_lines=80, show_log=True)
with st.expander("The commands it ran (for Terminal)"):
    st.caption(f"Jobs are recorded under `{paths.root}/outputs/jobs`.")
    for s in info.get("steps", []):
        st.code(" ".join(s["command"]), language="bash")
