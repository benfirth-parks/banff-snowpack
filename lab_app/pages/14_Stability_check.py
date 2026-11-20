"""Stability check (ADR-127): do SNOWPACK's stability numbers point at the layers that broke in the pits' stability
tests? The page leads with the answers of the latest check, keeps a running check's log updating itself, and tucks
the setup of a new check into one box."""

from __future__ import annotations

import json

import streamlit as st

from snowagent.lab.services.jobs import ACTIVE, JobBusy, latest_job, log_tail
from snowagent.lab.services.stability import (
    INDICATORS,
    agent_choices,
    checks_root,
    list_checks,
    load_check,
    pct,
    report_lines,
    verdict,
)
from snowagent.lab.services.training import run_label
from snowagent.lab.services.workflow import start_stability_check
from snowagent.lab.ui.app import config_path, default_workers, lab_context, page_header, repo_root

page_header(st, "Stability check", page="stability")
cfg, paths = lab_context(__file__)

LIVE_EVERY_S = 5
TONE = {"better than chance": "green", "worse than chance": "red"}

st.caption("Asks whether SNOWPACK's stability numbers (SK38, critical crack length, RTA) and a simple count of weak-"
           "layer signs point at the layers that broke in your pits' stability tests. It runs SNOWPACK again for "
           "every pit with a test, on the open winters only; the locked test winters are left out. It changes no "
           "score and no agent.")


@st.fragment(run_every=LIVE_EVERY_S)
def running_panel() -> None:
    job = latest_job(paths, "stability-check")
    if not job or job.get("state") not in ACTIVE:
        st.rerun()
    st.info(f"**A stability check is running.** This box updates itself every {LIVE_EVERY_S} seconds; its answers "
            "appear here when it is done. Leaving or refreshing the page does not stop it.", icon="⏳")
    st.code(log_tail(job, 3000) or "starting ...")


def agent_options() -> dict[str, str]:
    """Standard SNOWPACK, the standard models and the top agents of the newest training runs with SNOWPACK agents
    (promotion-check folds left out, each agent once)."""

    def when(run_id: str) -> str:
        try:
            return run_label(paths, run_id)[:11]
        except (ValueError, KeyError, OSError):
            return run_id

    return agent_choices(paths, cfg.genome, label=when)


def new_check(expanded: bool) -> None:
    with st.expander("Run a new stability check", expanded=expanded):
        options = agent_options()
        keys = list(options)
        default = ["standard"] + [k for k in keys if k.count("/") == 2][:1]
        agents = st.multiselect("Agents to check", keys, default=default, format_func=options.get,
                                help="Only agents that run SNOWPACK have stability numbers. Each agent adds about "
                                     "as much time as the first.")
        workers = int(st.number_input("Workers", 1, 16, default_workers(), help="pits worked on at once"))
        train_job = latest_job(paths, "train")
        if train_job and train_job.get("state") in ACTIVE:
            st.warning("A training run is going: the check would compete with it for this computer. Best started "
                       "after the run ends, or with 2 workers.")
        st.caption("Takes about as long as one round of a SNOWPACK training run per agent (SNOWPACK runs once for "
                   "each of several hundred pits). If it is stopped, starting it again from Terminal with the same "
                   "check name keeps the runs already done.")
        if st.button("Start the stability check", type="primary", disabled=not agents):
            try:
                start_stability_check(paths, config_path(__file__), repo_root(__file__), agents, workers=workers)
            except JobBusy as exc:
                st.error(str(exc))
            else:
                st.rerun()


job = latest_job(paths, "stability-check")
running = bool(job and job.get("state") in ACTIVE)
if running:
    running_panel()
elif job and job.get("state") == "failed":
    st.error("The last stability check stopped with an error; its log is on the Jobs page.")

checks = list_checks(paths)
if not running:
    new_check(expanded=not checks)
if not checks:
    if not running:
        st.info("No finished stability check yet: start one above.")
    st.stop()

st.subheader("Answers")
check_id = st.selectbox("Stability check", checks, help="newest first") if len(checks) > 1 else checks[0]
try:
    res = load_check(paths, check_id)
except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
    st.error(f"Could not read that check: {exc}")
    st.stop()

names = {a["key"]: a["name"] for a in res["agents"]}
st.caption(f"{res['cases']} pits with a test that broke, {res['breaks']} broken layers "
           f"({res['easy_sudden_breaks']} easy and sudden), winters {res['winters'][0]} to {res['winters'][-1]}."
           + (f" Locked winters left out: {', '.join(res['locked_seasons_left_out'])}."
              if res.get("locked_seasons_left_out") else ""))
for key, summ in res["summary"].items():
    st.markdown(f"**{names.get(key, key)}**: does the layer each number calls weakest sit where the pit broke?")
    rates = summ["subsets"].get("all") or {}
    cols = st.columns(max(1, len(rates)))
    for col, (name, r) in zip(cols, rates.items(), strict=False):
        with col.container(border=True):
            st.caption(INDICATORS[name][0])
            v = verdict(r)
            st.markdown(f"#### :{TONE.get(v, 'gray')}[{pct(r['hit'])} hits]")
            st.caption(f"chance {pct(r['chance'])}: {v}. Flags {pct(r['found_share'])} of broken layers and "
                       f"{pct(r['false_flag_share'])} of the rest.")

with st.expander("The full report", expanded=False):
    st.markdown("\n".join(report_lines(res)[2:]))
    st.caption(f"Files: {checks_root(paths) / check_id}")
