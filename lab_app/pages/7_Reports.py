"""Reports: a plain-language analysis of a training run, shown here and offered as a download (HTML that opens in a
browser or Word and prints to PDF, or Markdown), with an optional appendix of technical tables (ADR-082); and the
group check (ADR-095): the agents pooled as a group on a run's locked winters, started here as a background job."""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

from snowagent.lab.competition.scoring import SCORING_VERSION
from snowagent.lab.services.reports import report_filename, save_report, training_report
from snowagent.lab.services.training import list_training_runs, run_overview
from snowagent.lab.ui.app import (
    config_path,
    default_run_index,
    default_workers,
    lab_context,
    page_header,
    repo_root,
)

page_header(st, "Reports")
cfg, paths = lab_context(__file__)


def group_section() -> None:
    """The group check: choose the test winters and the group, start the check, read its report."""
    import json

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
    from snowagent.lab.services.group_report import group_report
    from snowagent.lab.services.group_report import report_filename as group_filename
    from snowagent.lab.services.jobs import ACTIVE, JobBusy, latest_job, log_tail
    from snowagent.lab.services.reports import _span
    from snowagent.lab.services.workflow import start_group_check
    from snowagent.lab.training.loop import committed_rounds, training_root

    st.caption("The group check pools a varied group of agents into one answer per pit on winters none of them "
               "trained on, and asks three questions: is the pooled answer better than one agent, are the agents "
               "more often wrong where they disagree, and are weak layers most of them forecast really in the pits. "
               "It reuses saved predictions where it can; the rest take about as long as one training round on "
               "those winters.")
    flash = st.session_state.pop("group-flash", None)
    if flash:
        st.success(flash)
    runs = locked_runs(paths)
    if not runs:
        st.info("The group check needs a training run with locked test winters and at least one finished round. "
                "Start one on the Training page (Advanced › Locked test winters).")
        return
    with st.expander("Start a group check", expanded=not list_group_checks(paths)):
        def run_label(r: str) -> str:
            try:
                return f"{r} (locked {_span(run_plan(paths, r).get('locked_seasons') or [])})"
            except ValueError:
                return r

        test_run = st.selectbox("Test pits from", runs, format_func=run_label,
                                help="the locked test winters of this training run are the test")
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
                labels[k] = m.name + (f" (best {k.rsplit('/', 1)[1]} of {k.split('/')[0]})" if m.run_id else "")
            except (ValueError, KeyError, IndexError, StopIteration, FileNotFoundError):
                options.remove(k)
        default = [k for k in default_members(paths, seasons) if k in options]
        members = st.multiselect("Agents in the group", options, default=default, format_func=labels.get,
                                 help="Suggested: standard SNOWPACK, the hybrid agent, the top two agents of every "
                                      "training run that never saw these winters, and four weather nudges. Only "
                                      "agents that never trained on the test winters are offered.")
        workers = int(st.number_input("Workers", 1, 16, default_workers(), help="parallel pits"))
        train_job = latest_job(paths, "train")
        if train_job and train_job.get("state") in ACTIVE:
            st.warning("A training run is going: the check would compete with it for this computer. Best started "
                       "after the run ends, or with 2 workers.")
        job = latest_job(paths, "group-check")
        if job and job.get("state") in ACTIVE:
            st.info(f"A group check is running ({job['title']}). The Jobs page follows it.")
            st.code(log_tail(job, 3000) or "starting ...")
        elif st.button("Start the group check", type="primary", disabled=len(members) < 3):
            try:
                start_group_check(paths, config_path(__file__), repo_root(__file__), test_run, members,
                                  workers=workers)
            except JobBusy as exc:
                st.error(str(exc))
            else:
                st.session_state["group-flash"] = "Group check started: its report appears below when it is done."
                st.rerun()
        if len(members) < 3:
            st.caption("Choose at least 3 agents.")
    checks = list_group_checks(paths)
    if not checks:
        st.info("No finished group check yet.")
        return
    check_id = st.selectbox("Finished group checks", checks)
    try:
        res = load_group_check(paths, check_id)
    except (OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        st.error(f"Could not read that check: {exc}")
        return
    rep = group_report(res)
    html_doc, md_doc = rep.to_html(), rep.to_markdown()
    d1, d2, _ = st.columns([1, 1, 2])
    d1.download_button("Download (HTML)", html_doc, file_name=group_filename(check_id, "html"), mime="text/html",
                       type="primary", help="opens in any browser or in Word; use Print to save it as a PDF")
    d2.download_button("Download (Markdown)", md_doc, file_name=group_filename(check_id, "md"),
                       mime="text/markdown")
    components.html(html_doc, height=1100, scrolling=True)


if st.radio("Report on", ["A training run", "The agents as a group"], horizontal=True,
            help="A training run's report, or the group check on winters no agent trained on") \
        == "The agents as a group":
    group_section()
    st.stop()
st.caption("A training report explains, in plain words, how good the evolved agent is, whether it may just be "
           "memorising past winters, what it changed, how long the run took and what to do next. Download it to "
           "read, print or share.")

runs = [r for r in list_training_runs(paths) if not r.startswith("loso_check-")]
if not runs:
    st.info("No training run yet. Start one on the Training page; its report appears here once a round is done.")
    st.stop()

run_id = st.selectbox("Training run", runs, index=default_run_index(paths.outputs / "training", runs,
                                                                     SCORING_VERSION))
ov = run_overview(paths, run_id)
rounds = ov["rounds"]["round"].tolist() if len(ov["rounds"]) else []
if not rounds:
    st.info("This run has no finished round yet. Its report can be written once round 1 is done.")
    st.stop()
c1, c2, c3 = st.columns([1, 1, 2])
round_no = int(c1.number_input("Round", min(rounds), max(rounds), max(rounds),
                               help="the last finished round by default"))
rank = int(c2.number_input("Agent rank", 1, int(ov["plan"].get("population") or 10), 1,
                           help="1 = the round's best agent"))
technical = c3.checkbox("Add an appendix with the technical tables", value=False,
                        help="every score component, the season-by-season table and every changed setting")
if ov["status"].get("state") == "running":
    st.caption("This run is still going: the report covers the rounds finished so far.")

key = (run_id, round_no, rank, technical)
if st.button("Write the report", type="primary"):
    try:
        rep = training_report(paths, run_id, cfg.genome, round_no=round_no, rank=rank, technical=technical,
                              cfg=cfg)
    except (ValueError, FileNotFoundError, KeyError) as exc:
        st.error(f"Could not write the report: {exc}")
    else:
        save_report(paths, rep, run_id, round_no, rank)
        st.session_state["report"] = (key, rep.to_html(), rep.to_markdown())

stored = st.session_state.get("report")
if stored and stored[0] == key:
    _, html_doc, md_doc = stored
    d1, d2, _ = st.columns([1, 1, 2])
    d1.download_button("Download (HTML)", html_doc, file_name=report_filename(run_id, round_no, rank, "html"),
                       mime="text/html", type="primary",
                       help="opens in any browser or in Word; use Print to save it as a PDF")
    d2.download_button("Download (Markdown)", md_doc, file_name=report_filename(run_id, round_no, rank, "md"),
                       mime="text/markdown", help="plain text, for notes or a Git repository")
    st.caption(f"A copy is also saved in `{paths.outputs / 'reports'}`.")
    components.html(html_doc, height=1100, scrolling=True)
elif stored:
    st.caption("The choices changed: press **Write the report** again.")
