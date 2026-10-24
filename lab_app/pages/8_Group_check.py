"""Group check (ADR-095, ADR-098): the agents pooled as a group on a training run's locked winters. The page leads with
the answers of the latest check, keeps a running check's log updating itself, and tucks the setup of a new check
into one box."""

from __future__ import annotations

import json

import streamlit as st
import streamlit.components.v1 as components

from snowagent.lab.services.group import (
    NUDGES,
    OTHER_KINDS,
    clean_runs,
    default_members,
    list_group_checks,
    load_group_check,
    locked_runs,
    resolve_member,
    run_plan,
)
from snowagent.lab.services.group_report import advice, check_label, group_report, headline
from snowagent.lab.services.group_report import report_filename as group_filename
from snowagent.lab.services.jobs import ACTIVE, JobBusy, latest_job, log_tail
from snowagent.lab.services.reports import _span
from snowagent.lab.services.training import run_label
from snowagent.lab.services.workflow import start_group_check
from snowagent.lab.training.loop import committed_rounds, training_root
from snowagent.lab.ui.app import config_path, default_workers, lab_context, page_header, repo_root

page_header(st, "Group check")
cfg, paths = lab_context(__file__)

LIVE_EVERY_S = 5  # seconds between updates of a running group check
TONE = {"good": "green", "bad": "red", "neutral": "gray"}

st.caption("Pools a varied group of agents into one answer per pit, on winters none of them trained on, and asks: "
           "is the group better than one agent, are the agents wrong where they disagree, and are weak layers most "
           "of them forecast really in the pits.")


@st.fragment(run_every=LIVE_EVERY_S)
def running_panel() -> None:
    """A running group check's log, updated every few seconds; the whole page reruns when it ends (its answers)."""
    job = latest_job(paths, "group-check")
    if not job or job.get("state") not in ACTIVE:
        st.rerun()
    st.info(f"**A group check is running.** This box updates itself every {LIVE_EVERY_S} seconds; its answers "
            "appear here when it is done. Leaving or refreshing the page does not stop it.", icon="⏳")
    st.code(log_tail(job, 3000) or "starting ...")


def new_check(expanded: bool) -> None:
    """Choose the test winters and the group, and start the check as a background job."""
    runs = locked_runs(paths)
    with st.expander("Run a new group check", expanded=expanded):
        if not runs:
            st.info("The group check needs a training run with locked test winters and at least one finished "
                    "round. Start one on the Training page (Advanced › Locked test winters).")
            return

        def test_label(r: str) -> str:
            try:
                return f"{run_label(paths, r)} · test winters {_span(run_plan(paths, r).get('locked_seasons') or [])}"
            except (ValueError, KeyError, OSError):
                return r

        test_run = st.selectbox("Test pits from the locked winters of", runs, format_func=test_label,
                                help="no agent in the group may have trained on these winters")
        seasons = run_plan(paths, test_run).get("locked_seasons") or []
        options = ["standard", *[f"kind:{k}" for k in OTHER_KINDS], *[f"nudge:{n}" for n in NUDGES]]
        for r in clean_runs(paths, seasons):
            rounds = committed_rounds(training_root(paths) / r)
            if rounds:
                options += [f"{r}/{rounds[-1]}/{k}" for k in (1, 2, 3)]
        labels = {}
        for k in list(options):
            try:
                m = resolve_member(paths, k, seasons, cfg.genome)
            except (ValueError, KeyError, IndexError, StopIteration, FileNotFoundError):
                options.remove(k)
                continue
            if m.run_id:
                labels[k] = f"{m.name} (number {k.rsplit('/', 1)[1]} of the run of {run_label(paths, m.run_id)[:11]})"
            else:
                labels[k] = m.name
        default = [k for k in default_members(paths, seasons) if k in options]
        members = st.multiselect("Agents in the group", options, default=default, format_func=labels.get,
                                 help="Suggested: standard SNOWPACK, the hybrid agent, the top two agents of every "
                                      "training run that never saw these winters, and four weather nudges. Only "
                                      "agents that never trained on the test winters are offered.")
        workers = int(st.number_input("Workers", 1, 16, default_workers(), help="pits worked on at once"))
        train_job = latest_job(paths, "train")
        if train_job and train_job.get("state") in ACTIVE:
            st.warning("A training run is going: the check would compete with it for this computer. Best started "
                       "after the run ends, or with 2 workers.")
        if len(members) < 3:
            st.caption("Choose at least 3 agents.")
        if st.button("Start the group check", type="primary", disabled=len(members) < 3):
            try:
                start_group_check(paths, config_path(__file__), repo_root(__file__), test_run, members,
                                  workers=workers)
            except JobBusy as exc:
                st.error(str(exc))
            else:
                st.rerun()


job = latest_job(paths, "group-check")
running = bool(job and job.get("state") in ACTIVE)
if running:
    running_panel()
elif job and job.get("state") == "failed":
    st.error("The last group check stopped with an error; its log is on the Jobs page.")

checks = list_group_checks(paths)
if not checks:
    if not running:
        new_check(expanded=True)
        st.info("No finished group check yet: start one above.")
    st.stop()

st.subheader("Answers")
if len(checks) == 1:
    check_id = checks[0]
    st.caption(check_label(paths, check_id))
else:
    check_id = st.selectbox("Group check", checks, format_func=lambda c: check_label(paths, c),
                            help="newest first; the page opens on the newest")
try:
    res = load_group_check(paths, check_id)
except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
    st.error(f"Could not read that check: {exc}")
    st.stop()

cards = headline(res)
for col, card in zip(st.columns(len(cards)), cards, strict=True):
    with col.container(border=True):
        st.caption(card["title"])
        st.markdown(f"#### :{TONE[card['tone']]}[{card['answer']}]")
        if card["detail"]:
            st.caption(card["detail"])

tips = advice(res)
if tips:
    st.markdown("**What this means for forecasters**")
    st.markdown("\n".join(f"- {t}" for t in tips))

rep = group_report(res)
html_doc, md_doc = rep.to_html(), rep.to_markdown()
d1, d2, _ = st.columns([1, 1, 2])
d1.download_button("Download the full report (HTML)", html_doc, file_name=group_filename(check_id, "html"),
                   mime="text/html", type="primary",
                   help="opens in any browser or in Word; use Print to save it as a PDF")
d2.download_button("Download (Markdown)", md_doc, file_name=group_filename(check_id, "md"), mime="text/markdown")
with st.expander("Read the full report here"):
    components.html(html_doc, height=1100, scrolling=True)

if not running:
    st.divider()
    new_check(expanded=False)
