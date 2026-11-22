"""Named-layer check (ADR-138): where an observer named a layer by the day it formed ("Nov 5 crust"), did each agent
of a finished competition forecast a layer of that kind there? It reads the competition's stored forecasts, so it
takes seconds and runs no agent."""

from __future__ import annotations

import json

import streamlit as st

from snowagent.lab.competition.runner import list_runs
from snowagent.lab.services.named_layers import named_layer_table, report_lines, run_named_layer_check
from snowagent.lab.ui.app import lab_context, page_header

page_header(st, "Named-layer check", page="named")
cfg, paths = lab_context(__file__)

st.caption("Observers often write the day a layer formed beside it, such as \"Nov 5 crust\" or \"April 10th "
           "suncrust\". This check asks whether each agent of a competition forecast a layer of that kind where the "
           "named layer was found, split into layers the agent had to build from the weather since the last pit "
           "and layers it could carry forward. The names are never shown to any agent. It changes no score.")

runs = list_runs(paths)
if not runs:
    st.info("Run a competition first (Banff trials), with the agents you want to compare.")
    st.stop()

run = st.selectbox("Competition to check", runs, help="Its stored forecasts are read; nothing is run again.")
out = paths.outputs / "named_layer_checks" / run
if st.button("Check named layers", type="primary"):
    with st.spinner("Reading the forecasts ..."):
        run_named_layer_check(paths, run)

if (out / "result.json").is_file():
    st.markdown("\n".join(report_lines(json.loads((out / "result.json").read_text()))))
else:
    st.write("This competition has not been checked yet.")

with st.expander("Every named layer in the pits (for crust studies)"):
    table = named_layer_table(paths)
    st.write(f"{len(table)} named layers in {table['profile_id'].nunique() if len(table) else 0} pits.")
    if len(table):
        st.download_button("Download as CSV", table.to_csv(index=False), "named_layers.csv", "text/csv")
