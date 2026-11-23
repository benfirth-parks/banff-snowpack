"""Mac queue (ADR-148, ADR-150): the heavy jobs the cloud queues for this Mac. What is running, what waits next,
what has ended and whether its results reached the cloud, with a button that checks the queue now instead of at
the next half-hourly check."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

from snowagent.lab.services.mac_queue import Queue, overview
from snowagent.lab.ui.app import lab_context, page_header
from snowagent.lab.ui.pages import link

page_header(st, "Mac queue",
            "Heavy jobs (training with SNOWPACK, promotion checks, winter tests) that the cloud queues for this Mac. "
            "The Mac checks the queue every 30 minutes, runs one job at a time gently, and sends the results back. "
            "You don't need to start anything here.", page="macqueue")
cfg, paths = lab_context(__file__)
REPO = Path(__file__).resolve().parents[2]
q = Queue(repo=REPO, data_root=paths.root, config=REPO / "config" / "lab.yaml")

c1, c2 = st.columns(2)
refresh = c1.button("Refresh from GitHub", help="Read the queue branch again (otherwise this shows the last check)")
check_now = c2.button("Check the queue now", type="primary",
                      help="Run the Mac's queue check now: start the next job if nothing is running, send finished "
                           "results. Otherwise it runs by itself every 30 minutes.")
if check_now:
    with st.spinner("Checking the queue..."):
        r = subprocess.run([sys.executable, "-m", "snowagent.cli", "lab", "mac-queue", "tick",
                            "--data-root", str(paths.root), "--config", str(q.config), "--repo", str(REPO)],
                           capture_output=True, text=True, cwd=REPO, timeout=600)
    if r.returncode == 3:
        st.info("A check is already running; look again in a minute.")
    elif r.returncode != 0:
        st.error("The check failed: " + (r.stderr or r.stdout)[-500:])
    else:
        st.success((r.stdout.strip().splitlines() or ["done"])[-1])

view = overview(q, fetch=refresh)
rows = view["rows"]
n = {s: sum(r["state"] == s for r in rows) for s in ("running", "waiting")}
m1, m2, m3 = st.columns(3)
m1.metric("Running", n["running"])
m2.metric("Waiting", n["waiting"])
m3.metric("Ended", len(rows) - n["running"] - n["waiting"])
power = "on mains power" if view["mains_power"] else "on battery (jobs run anyway; keep an eye on the charge)"
st.caption(f"This Mac: {view['workers']} workers per job, {power}. Last report to the cloud: "
           f"{(view['heartbeat_at'] or 'not yet')[:16].replace('T', ' ')} UTC.")

STATE = {"running": "▶️ running", "waiting": "⏳ waiting", "finished": "✅ finished", "failed": "❌ failed",
         "stopped": "⏹️ stopped", "rejected": "🚫 refused", "cancelled": "cancelled"}
if not rows:
    st.info("The queue is empty. The cloud's Snowpack Mac queue task adds jobs at 5:47 and 17:47 Alberta Time; "
            "you can also ask for one on GitHub issue #104.")
else:
    st.dataframe(pd.DataFrame([{
        "job": r["id"], "kind": r["kind"], "state": STATE.get(r["state"], r["state"]),
        "why": r["note"], "started": r["started"][:16].replace("T", " "),
        "ended": r["ended"][:16].replace("T", " "),
        "results sent": {True: "yes", False: "not yet", None: ""}[r["published"]],
    } for r in rows]), width="stretch", hide_index=True)
    st.caption("Times are UTC. A running job also shows on every page's bar and under Jobs, where Stop works.")

st.markdown(f"Ask for a job, or write **stop**, on [GitHub issue #104]({view['issue_url']}) · "
            f"[the queue]({view['queue_url']}) · [the results]({view['results_url']})")
link(st, "jobs", "Open Jobs (Stop, Resume and logs)")
with st.expander("The runner's last decisions"):
    st.code("\n".join(view["last_lines"]) or "No check has run on this Mac yet.", language="text")
