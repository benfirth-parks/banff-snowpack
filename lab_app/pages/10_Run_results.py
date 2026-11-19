"""Run results (ADR-119; before it the middle of the Training page): the agents of one round of a training run,
best first (ADR-084 names), and the card of one agent: what it changed from its family's standard settings and
where it came from. The agent chosen here is the one the Blind test and site page opens on."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from snowagent.lab.services.names import display
from snowagent.lab.services.training import round_table, run_overview
from snowagent.lab.training.lineage import format_ancestry, lineage_for
from snowagent.lab.ui.app import lab_context, page_header
from snowagent.lab.ui.genes import gene_rows
from snowagent.lab.ui.pages import link
from snowagent.lab.ui.training import AGENT_KEY, locked_verdict, no_runs, pick_run, run_when

page_header(st, "Run results",
            "The agents of a training run, best first, and what the best one changed. For the story in plain "
            "words, read the run's report.",
            page="results")
cfg, paths = lab_context(__file__)

run_id = pick_run(st, paths)
if run_id is None:
    no_runs(st)
    st.stop()
ov = run_overview(paths, run_id)
plan, trace = ov["plan"], ov["rounds"]
if trace.empty:
    st.info("This run has no finished round yet: its agents appear here when round 1 is done.")
    link(st, "watch", "Watch the run")
    st.stop()
if ov["status"].get("state") == "running":
    st.info("This run is still going: these are the rounds finished so far.", icon="🟢")
else:
    st.caption(run_when(ov))
locked_verdict(st, ov)

# ------------------------------------------------------------------------------------------- leaderboard
st.subheader("The agents, best first")
rounds_done = trace["round"].tolist()
r = st.select_slider("Round", options=rounds_done, value=rounds_done[-1]) if len(rounds_done) > 1 \
    else rounds_done[0]
table = round_table(paths, run_id, r)
st.dataframe(table.drop(columns=["genome_hash"]), width="stretch", hide_index=True)
with st.expander("How to read this table"):
    st.caption("Composite = frozen scoring weights (the loop never changes them); the top two (rank 1-2) survive "
               "unchanged into the next round. Survivors keep their scores (cached). Snow depth scores the median "
               "(p50) only; the p10-p90 range is scored in uncertainty, and its coverage is a diagnostic."
               + (" This run ranks by the composite less half its unevenness: how much more it gains in some "
                  "winters and plots than in others (ADR-087), so an agent with a slightly lower composite can "
                  "rank higher." if plan.get("selection") == "consistent" else "")
               + (f" It also takes {plan['drift_penalty']:g} off for each unit of drift (a setting moved across its "
                  "whole allowed range, or a changed choice) from the family's standard settings (ADR-089), so a "
                  "change has to earn its place." if plan.get("drift_penalty") else ""))

# ------------------------------------------------------------------------------------------- lineage
st.subheader("Agent card")
pick = st.selectbox("Agent", table["agent"].tolist(), index=0,
                    format_func=lambda a: display(table.set_index("agent").at[a, "genome_hash"], a),
                    help="the round's leaderboard order: the first is the round's best agent")
best = table[table["agent"] == pick].iloc[0]
st.session_state[AGENT_KEY] = (run_id, int(r), pick)
try:
    rec, chain = lineage_for(paths, best["genome_hash"], run_id)
    st.markdown(f"**{best['name']}**: {best['agent']} (`{best['agent_id']}`, {best['family']})")
    changed = rec.get("changed_vs_default") or {}
    if changed:
        st.caption("How this agent differs from its family's default settings:")
        st.dataframe(pd.DataFrame(gene_rows(changed, cfg.genome)), width="stretch", hide_index=True)
    else:
        st.caption("All settings at the family default.")
    with st.expander("Where it came from (its family tree)"):
        st.code("\n".join(format_ancestry(chain)), language=None)
except KeyError as exc:
    st.info(f"No lineage record: {exc}")

st.markdown(f"**Next with {best['name']}**")
n1, n2, n3, _ = st.columns(4)
with n1:
    link(st, "reports", "Read the run's report")
with n2:
    link(st, "blind", "Freeze it or send it to the site")
with n3:
    link(st, "check", "Run the promotion check")
