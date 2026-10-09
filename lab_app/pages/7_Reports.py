"""Reports: a plain-language analysis of a training run, shown here and offered as a download (HTML that opens in a
browser or Word and prints to PDF, or Markdown), with an optional appendix of technical tables (ADR-082)."""

from __future__ import annotations

import streamlit as st
import streamlit.components.v1 as components

from snowagent.lab.competition.scoring import SCORING_VERSION
from snowagent.lab.services.reports import report_filename, save_report, training_report
from snowagent.lab.services.training import list_training_runs, run_overview
from snowagent.lab.ui.app import default_run_index, lab_context, page_header

page_header(st, "Reports")
cfg, paths = lab_context(__file__)
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
