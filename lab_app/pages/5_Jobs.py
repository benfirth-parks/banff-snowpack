"""Jobs: every background job the app started (set up data, build cases, competitions, training, promotion checks
and their estimates, re-scores), running first, with state, steps, log tail, Stop and Resume (ADR-077)."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from snowagent.lab.services.jobs import ACTIVE, KINDS, job_info, list_jobs
from snowagent.lab.ui.app import lab_context, page_header
from snowagent.lab.ui.jobs import job_block

page_header(st, "Jobs")
cfg, paths = lab_context(__file__)
st.caption("Work started from these pages runs as its own process: closing the browser or stopping the app does not "
           "stop it. Stop ends a job (a training at its next case); Resume starts it again where it stopped. Jobs "
           f"are recorded under `{paths.root}/outputs/jobs`.")
if st.button("Refresh", key="refresh-list"):
    st.rerun()

jobs = [job_info(paths, j) for j in list_jobs(paths)]
if not jobs:
    st.info("No background job yet. Start one from Home (Set up data), Benchmark Cases (Build cases), Leaderboard "
            "(Run a competition) or Training (Start a training run, Promotion check).")
    st.stop()

jobs.sort(key=lambda j: j["state"] not in ACTIVE)  # running first, then newest first (stable)
running = [j for j in jobs if j["state"] in ACTIVE]
c1, c2 = st.columns(2)
c1.metric("Running", len(running))
c2.metric("Finished, stopped or failed", len(jobs) - len(running))
st.dataframe(pd.DataFrame([{"job": j["job_id"], "what": KINDS.get(j.get("kind"), j.get("kind")),
                            "title": j.get("title"), "state": j["state"],
                            "started (UTC)": str(j.get("created_at", "")).replace("T", " ")[:16],
                            "finished (UTC)": str(j.get("finished_at") or "").replace("T", " ")[:16]}
                           for j in jobs]), width="stretch", hide_index=True)

job_id = st.selectbox("Job", [j["job_id"] for j in jobs],
                      format_func=lambda i: next(f"{j['state']}: {j.get('title')} ({i})" for j in jobs
                                                 if j["job_id"] == i))
info = next(j for j in jobs if j["job_id"] == job_id)
job_block(st, paths, info, key="jobs-page", tail_lines=80)
with st.expander("Commands"):
    for s in info.get("steps", []):
        st.code(" ".join(s["command"]), language="bash")
