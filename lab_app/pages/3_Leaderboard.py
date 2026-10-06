"""Leaderboard: run a competition (`snowagent lab compete`, a background job, ADR-077) and read competition runs:
composite and component scores per agent, filtered by plot, case type, forecast source and weather source (ADR-076);
per case, an agent's predicted profile beside the observed pit (scored training/development cases only; sealed-test
truth is never read; ADR-064/065)."""

from __future__ import annotations

import json
import os

import pandas as pd
import streamlit as st

from snowagent.lab.benchmark.loader import find_case, read_manifest
from snowagent.lab.competition.runner import leaderboard, list_runs, load_run
from snowagent.lab.competition.scoring import SCORING_VERSION
from snowagent.lab.competition.truth import scoring_truth
from snowagent.lab.schemas.genome import AgentFamily
from snowagent.lab.services.benchmark import case_sets
from snowagent.lab.services.data import data_status
from snowagent.lab.services.jobs import ACTIVE, JobBusy, latest_job
from snowagent.lab.services.workflow import start_competition, start_rescore
from snowagent.lab.ui.app import (
    config_path,
    default_run_index,
    empty_state,
    lab_context,
    page_header,
    repo_root,
)
from snowagent.lab.ui.jobs import job_block
from snowagent.lab.ui.plots import prediction_frame, profile_figure

TRUTH_SPLITS = {"training", "development"}  # as on the Benchmark Cases page: no validation/holdout/sealed truth
COLUMNS = {"label": "agent", "family": "family", "composite": "composite", "snow_depth": "snow depth",
           "layer_structure": "layer structure", "critical_layers": "critical layers", "uncertainty": "uncertainty",
           "robustness": "robustness", "depth_mae_m": "depth MAE (m)", "depth_bias_m": "depth bias (m)",
           "depth_coverage": "p10-p90 coverage", "scored": "scored", "skipped": "skipped", "failures": "failures",
           "runtime_s_mean": "s / case"}

page_header(st, "Leaderboard")
cfg, paths = lab_context(__file__)
runs = list_runs(paths)
sets = case_sets(paths)

# ------------------------------------------------------------------------------------------- run a competition
comp_job = latest_job(paths, "compete")
with st.expander("Run a competition", expanded=not runs or bool(comp_job and comp_job["state"] != "finished")):
    st.caption("Every chosen agent predicts every scorable case and each prediction is scored (sealed-test truth is "
               "never read). Same as `snowagent lab compete`, run as a background job; Resume continues a stopped "
               "run (finished cases are kept).")
    if not sets:
        st.info("No benchmark cases yet: build them on the Benchmark Cases page first.")
    with st.form("compete"):
        families = [f.value for f in AgentFamily]
        agents = st.multiselect("Agents", families, default=families,
                                help="the default agent of each family; snowpack is the incumbent")
        c1, c2 = st.columns(2)
        plots_c = c1.multiselect("Plots", [c.value for c in cfg.sites], default=[c.value for c in cfg.sites])
        types_c = c2.multiselect("Case types", ["forecast_h72", "next_pit"], default=["forecast_h72", "next_pit"])
        c3, c4, c5, c6, c7 = st.columns(5)
        case_set_c = c3.selectbox("Case set", sets or ["all"], index=(sets or ["all"]).index("all")
                                  if "all" in (sets or ["all"]) else 0)
        workers_c = c4.number_input("Workers", 1, max(1, os.cpu_count() or 1), min(4, os.cpu_count() or 1))
        engine_c = c5.selectbox("Engine", ["auto", "none"], help="auto: the SNOWPACK binary (site-run reuse when "
                                "it qualifies); none: SNOWPACK skipped, the hybrid predicts from its other members")
        limit_c = c6.number_input("Cases (0 = all)", 0, 100000, 0, help="the first N cases by case id: a quick try")
        seed_c = c7.number_input("Seed", 0, 2**31 - 1, 0)
        go_c = st.form_submit_button("Start competition", disabled=not sets,
                                     type="primary")
    if go_c:
        if not agents or not plots_c or not types_c:
            st.error("Choose at least one agent, plot and case type.")
        else:
            try:
                comp_job = start_competition(
                    paths, config_path(__file__), repo_root(__file__), agents=None if len(agents) == len(families)
                    else agents, case_set=case_set_c, plots=None if len(plots_c) == len(cfg.sites) else plots_c,
                    case_types=None if len(types_c) == 2 else types_c, workers=int(workers_c), engine=engine_c,
                    limit=int(limit_c) or None, seed=int(seed_c))
                st.success(f"Started competition `{comp_job['refs']['run_id']}`. Press Refresh to follow it; it "
                           "appears in the run list when it finishes.")
            except JobBusy as exc:
                st.error(str(exc))
    if comp_job:
        job_block(st, paths, comp_job, key="compete")

if not runs:
    if not any(data_status(paths).values()):
        empty_state(st, paths)
    else:
        st.info("No competition run yet. Start one above (Run a competition), or from the repository root run "
                "`snowagent lab compete` (every agent on every scorable case; `--engine none` skips SNOWPACK when "
                "the binary is not built).")
    st.stop()

run_id = st.sidebar.selectbox("Competition run", runs,
                              index=default_run_index(paths.outputs / "competitions", runs, SCORING_VERSION))
df, summary = load_run(paths, run_id)
run_dir = paths.outputs / "competitions" / run_id
plan = json.loads((run_dir / "run.json").read_text())
st.caption(f"Run `{run_id}` · case set `{plan['case_set']}` ({plan['split_mode']}) · {len(plan['case_ids'])} cases · "
           f"seed {plan['seed']} · scoring {plan['scoring_version']} · engine {plan['engine']['kind']}"
           + (f" · site-run reuse {summary['engine'].get('site_run_reused', 0)} cases" if plan["engine"].get(
               "site_run_reuse") else ""))

if plan["scoring_version"] != SCORING_VERSION:
    st.warning(f"This run was scored under `{plan['scoring_version']}`; current runs use `{SCORING_VERSION}` and the "
               "two do not compare (ADR-074). Re-scoring makes a new run from the stored predictions (no agent runs; "
               "this run is not changed).", icon="🔁")
    rs_job = latest_job(paths, "rescore")
    if st.button("Re-score under the current version", disabled=bool(rs_job and rs_job["state"] in ACTIVE)):
        try:
            rs_job = start_rescore(paths, config_path(__file__), repo_root(__file__), run_id)
        except JobBusy as exc:
            st.error(str(exc))
    if rs_job and (rs_job.get("refs") or {}).get("run_id") == run_id:
        job_block(st, paths, rs_job, key="rescore")

plots = sorted(df["site_code"].unique())
plot_sel = st.sidebar.multiselect("Plot", plots, default=plots,
                                  format_func=lambda c: f"{cfg.sites[c].display_name} ({c})" if c in cfg.sites else c)
ctypes = sorted(df["case_type"].unique())
ctype_sel = st.sidebar.multiselect("Case type", ctypes, default=ctypes)
sources = sorted(df["forecast_source"].dropna().unique())
source_sel = st.sidebar.multiselect("Forecast source", sources, default=sources)
wsources = sorted(df["weather_source"].fillna("unrecorded").unique())  # unrecorded: cases built before ADR-076
wsource_sel = st.sidebar.multiselect("Weather source", wsources, default=wsources,
                                     help="station: plot stations; era5_only: the seasons before the stations "
                                          "(1997-98 to 2014-15), ERA5 alone; mixed: some of each")
sel = df[df["site_code"].isin(plot_sel) & df["case_type"].isin(ctype_sel) & df["forecast_source"].isin(source_sel)
         & df["weather_source"].fillna("unrecorded").isin(wsource_sel)]
if sel.empty:
    st.info("No scored case for this selection.")
    st.stop()

w = cfg.scoring_weights
st.subheader(f"{sel['case_id'].nunique()} cases")
st.caption("Components in [0, 1], 1 = perfect; composite = "
           + " + ".join(f"{getattr(w, k):g} × {k.replace('_', ' ')}" for k in w.model_dump())
           + " (robustness on the leaderboard: failure rate and the worst tenth of cases). Snow depth = "
           "exp(-|p50 - observed| / 0.15 m), the median only; the p10-p90 range is scored in uncertainty (interval "
           "score) and its coverage is a diagnostic (scoring version 2, ADR-074; version-1 runs do not compare). "
           "Skipped = the agent could not run (no SNOWPACK binary), not scored.")
board = pd.DataFrame(leaderboard(sel, w))
st.dataframe(board[[c for c in COLUMNS if c in board]].rename(columns=COLUMNS), width="stretch", hide_index=True)
if len(board):
    st.bar_chart(board.set_index("label")[["snow_depth", "layer_structure", "critical_layers", "uncertainty",
                                           "robustness"]], stack=False, height=280)

for col, title in (("forecast_source", "By forecast source"), ("weather_source", "By weather source")):
    with st.expander(title):
        for src, d in sel.groupby(sel[col].fillna("unrecorded")):
            st.markdown(f"**{src}** ({d['case_id'].nunique()} cases)")
            b = pd.DataFrame(leaderboard(d, w))
            st.dataframe(b[[c for c in COLUMNS if c in b]].rename(columns=COLUMNS), width="stretch", hide_index=True)

# ------------------------------------------------------------------------------------------- one case
st.subheader("Prediction beside the observed pit")
viewable = sel[sel["split"].isin(TRUTH_SPLITS)]
if viewable.empty:
    st.info("Profiles are shown for scored training/development cases only.")
    st.stop()
c1, c2 = st.columns(2)
case_id = c1.selectbox("Case", sorted(viewable["case_id"].unique()))
agents = viewable[viewable["case_id"] == case_id]
label = c2.selectbox("Agent", agents["label"].tolist())
row = agents[agents["label"] == label].iloc[0]
rec = json.loads((run_dir / "cases" / f"{case_id}.json").read_text())
pred = rec["predictions"].get(row["agent_id"])
scores = {k: row.get(k) for k in ("composite", "snow_depth", "layer_structure", "critical_layers", "uncertainty")}
m1, *ms = st.columns(5)
for col, (k, v) in zip([m1, *ms], scores.items(), strict=True):
    col.metric(k.replace("_", " "), "-" if v is None or pd.isna(v) else f"{v:.2f}")
left, right = st.columns(2)
with left:
    if pred is None or pred.get("status") != "ok":
        st.info(f"{label}: {row['status']}" + (f" ({row['reason']})" if isinstance(row.get("reason"), str) else ""))
    else:
        hs = pred["bulk_state"]["snow_depth_m"]
        st.plotly_chart(profile_figure(prediction_frame(pred), hs["p50"],
                                       f"{label} · HS {hs['p50'] * 100:.0f} cm (p10 {hs['p10'] * 100:.0f}, "
                                       f"p90 {hs['p90'] * 100:.0f})"), width="stretch", theme="streamlit")
        st.caption("; ".join(pred.get("confidence", {}).get("main_limits", [])))
with right:
    dirs = find_case(paths, case_id, plan["case_set"])
    m = read_manifest(dirs[0]) if dirs else None
    if m is None or m.split.value not in TRUTH_SPLITS:
        st.info("The observed pit of this case is not shown in the browser.")
    else:
        tp = scoring_truth(dirs[0], m).truth_profile
        tl = pd.DataFrame([ly.model_dump(mode="json") for ly in tp.layers])
        if len(tl):
            tl["concern_basis_json"] = tl["concern_basis"].map(json.dumps)
        st.plotly_chart(profile_figure(tl, tp.snow_depth_m, f"observed pit · HS "
                                       f"{(tp.snow_depth_m or 0) * 100:.0f} cm"), width="stretch", theme="streamlit")
        st.caption(f"{m.case_type.value} · {m.forecast_source.value if m.forecast_source else '-'} · horizon "
                   f"{m.horizon_hours:.0f} h · target {m.target_scope.value}")
