"""Training: start a local training run (`snowagent lab train`, run as a detached process, never inside this app),
follow it round by round (leaderboard, best composite, the train-vs-held-out gap with its flags), see the lineage
of the current best agent, and the leave-one-season-out promotion check (ADR-066 to ADR-069)."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from snowagent.lab.schemas.genome import AgentFamily
from snowagent.lab.services.data import data_status
from snowagent.lab.services.training import (
    list_training_runs,
    round_table,
    run_overview,
    start_training,
    stop_training,
)
from snowagent.lab.training.lineage import format_ancestry, lineage_for
from snowagent.lab.training.loso import RULE, list_checks, load_check
from snowagent.lab.ui.app import CONFIG_ENV, empty_state, lab_context, page_header, repo_root
from snowagent.lab.ui.plots import CONCERN, NEUTRAL, SERIES

GAP_WARNING = ("The per-round gap (composite on the other seasons minus composite on the monitor season) is a "
               "**warning signal only**: in split mode `all` the monitor season is also training data, so a small "
               "gap proves nothing. The evidence that an evolved agent generalises is the leave-one-season-out "
               "promotion check below (`snowagent lab check-loso`).")

page_header(st, "Training")
cfg, paths = lab_context(__file__)
st.caption("Each round every agent predicts every training case and is scored; the top two survive unchanged and "
           "are mutated and crossed to make the next population. Evolved agents are research entries: the site "
           "keeps SNOWPACK unless an agent passes the promotion check.")

built = (paths.benchmark / "all").is_dir() and any((paths.benchmark / "all").glob("*/*/manifest.json"))

# ------------------------------------------------------------------------------------------- start a run
with st.expander("Start a training run", expanded=not list_training_runs(paths)):
    if not built:
        if not any(data_status(paths).values()):
            empty_state(st, paths)
        st.info("No benchmark cases yet: run `snowagent lab build-cases` first (Benchmark Cases page).")
    t = cfg.training
    with st.form("train"):
        c1, c2, c3, c4 = st.columns(4)
        rounds = c1.number_input("Rounds", 1, 1000, t.rounds)
        population = c2.number_input("Population", 2, 500, t.population)
        survivors = c3.number_input("Survivors", 1, 100, t.survivors)
        seed = c4.number_input("Seed", 0, 2**31 - 1, 0)
        c5, c6, c7, c8 = st.columns(4)
        strength = c5.number_input("Mutation strength", 0.01, 1.0, t.mutation_strength, 0.05)
        cross = c6.number_input("Crossover share", 0.0, 1.0, t.crossover_share, 0.05)
        workers = c7.number_input("Workers", 1, max(1, os.cpu_count() or 1), min(4, os.cpu_count() or 1))
        engine = c8.selectbox("Engine", ["auto", "none"], help="auto: the SNOWPACK binary (SNOWPACK_BIN or PATH); "
                              "none: SNOWPACK skipped, the hybrid predicts from its other members")
        plots = st.multiselect("Plots", [c.value for c in cfg.sites], default=[c.value for c in cfg.sites])
        case_types = st.multiselect("Case types", ["forecast_h72", "next_pit"], default=["forecast_h72", "next_pit"])
        initial = st.multiselect("Initial population", [f.value for f in AgentFamily],
                                 default=[f.value for f in AgentFamily],
                                 help="the default genome of each chosen family (genome files: use the CLI)")
        c9, c10 = st.columns(2)
        screen = c9.number_input("Screen cases (0 = off)", 0, 10000, 0,
                                 help="score a child with new SNOWPACK physics on this many cases first; only one "
                                 "beating the worst survivor there runs on every case (ADR-072)")
        family_slots = c10.checkbox("Family slots", False,
                                    help="each round, one mutant of every family's best agent (ADR-073)")
        start = st.form_submit_button("Start training", disabled=not built)
    if start:
        if survivors >= population or len(initial) < survivors:
            st.error("Survivors must be fewer than the population and no more than the initial agents.")
        elif family_slots and population - survivors < len(AgentFamily):
            st.error(f"Family slots need at least {len(AgentFamily)} children per round (population - survivors).")
        else:
            root = repo_root(__file__)
            info = start_training(
                paths, Path(os.environ.get(CONFIG_ENV) or root / "config" / "lab.yaml"), cwd=root,
                rounds=int(rounds), population=int(population), survivors=int(survivors),
                mutation_strength=float(strength), crossover_share=float(cross), seed=int(seed),
                workers=int(workers), engine=engine,
                plots=None if len(plots) == len(cfg.sites) else plots,
                case_types=None if len(case_types) == 2 else case_types,
                initial=None if len(initial) == len(AgentFamily) else initial,
                screen_cases=int(screen) or None, family_slots=bool(family_slots))
            st.success(f"Started training run `{info['run_id']}` (process {info['pid']}). It runs on its own: "
                       "closing this page does not stop it. Refresh to follow it.")
    st.caption("The same from a terminal: `snowagent lab train --rounds 10 --population 10 --seed 0 --workers 4` "
               "(see docs/lab/training.md for times; the first round runs SNOWPACK once per case, and so does every "
               "child with new SNOWPACK physics genes).")

runs = list_training_runs(paths)
if not runs:
    st.info("No training run yet. Start one above, or from the repository root run `snowagent lab train`.")
    st.stop()

# ------------------------------------------------------------------------------------------- one run
run_id = st.sidebar.selectbox("Training run", runs)
ov = run_overview(paths, run_id)
plan, status = ov["plan"], ov["status"]
st.subheader(f"Run `{run_id}`")
if ov["fold_of_check"]:
    st.caption("This run is one fold of a leave-one-season-out check (one season held out).")
if plan:
    st.caption(f"case set `{plan['case_set']}` · {len(plan['case_ids'])} cases · seasons {plan['seasons'][0]} to "
               f"{plan['seasons'][-1]} · {plan['rounds']} rounds · population {plan['population']} · survivors "
               f"{plan['survivors']} · mutation {plan['mutation_strength']} · crossover {plan['crossover_share']} · "
               f"seed {plan['seed']} · monitor season {plan['monitor_season']}")
state = status.get("state", "unknown")
c1, c2, c3 = st.columns([2, 2, 1])
c1.metric("State", state)
c2.metric("Round", f"{status.get('round', 0)} / {plan.get('rounds', '?')}")
if state == "running":
    if status.get("total"):
        st.progress(min(1.0, status.get("done", 0) / status["total"]),
                    text=f"round {status.get('round')}: {status.get('done', 0)} of {status['total']} cases with work")
    if c3.button("Stop"):
        stop_training(paths, run_id)
        st.info("Stop requested: the run stops at its next case. Continue it with "
                f"`snowagent lab train --resume --run-id {run_id}`.")
elif state in ("interrupted", "stopped", "failed"):
    st.warning(f"The run is {state}" + (f" ({status.get('message')})" if status.get("message") else "")
               + f". Continue it with `snowagent lab train --resume --run-id {run_id}`.")
if ov["estimate"]:
    e = ov["estimate"]
    st.caption(f"Estimate before the start ({e['workers']} workers, {e['timings']} timings): round 1 "
               f"{e['round1']['wall_s'] / 60:.0f} min, total {e['total_s'][0] / 60:.0f}-{e['total_s'][1] / 60:.0f}"
               " min.")
if st.button("Refresh"):
    st.rerun()

trace = ov["rounds"]
if trace.empty:
    st.info("No round committed yet.")
    st.stop()

left, right = st.columns(2)
with left:
    fig = go.Figure(go.Scatter(x=trace["round"], y=trace["best_composite"], mode="lines+markers",
                               line={"color": SERIES, "width": 2}, marker={"size": 8}, name="best composite",
                               customdata=trace[["best"]],
                               hovertemplate="round %{x}<br>%{customdata[0]}<br>composite %{y:.4f}<extra></extra>"))
    fig.update_layout(title="Best composite per round", height=320, margin={"l": 10, "r": 10, "t": 40, "b": 10},
                      xaxis_title="round", yaxis_title="composite (0-1)", showlegend=False)
    fig.update_xaxes(dtick=1)
    st.plotly_chart(fig, width="stretch", theme="streamlit")
with right:
    g = trace.dropna(subset=["gap"])
    fig = go.Figure()
    fig.add_hline(y=0, line={"color": NEUTRAL, "width": 1})
    if len(g):
        fig.add_trace(go.Scatter(x=g["round"], y=g["gap"], mode="lines+markers", line={"color": SERIES, "width": 2},
                                 marker={"size": 8}, name="gap",
                                 hovertemplate="round %{x}<br>gap %{y:+.4f}<extra></extra>"))
        fl = g[g["gap_flag"]]
        if len(fl):
            fig.add_trace(go.Scatter(x=fl["round"], y=fl["gap"], mode="markers+text", text=["flag"] * len(fl),
                                     textposition="top center", marker={"size": 12, "color": CONCERN,
                                                                         "symbol": "triangle-up"},
                                     name="widened K rounds in a row",
                                     hovertemplate="round %{x}: gap widened K rounds in a row<extra></extra>"))
    fig.update_layout(title=f"Train vs held-out gap, top two (monitor {plan.get('monitor_season')})", height=320,
                      margin={"l": 10, "r": 10, "t": 40, "b": 10}, xaxis_title="round",
                      yaxis_title="composite gap", showlegend=False)
    fig.update_xaxes(dtick=1)
    st.plotly_chart(fig, width="stretch", theme="streamlit")
st.warning(GAP_WARNING, icon="🔍")
flags = trace[trace["gap_flag"]]["round"].tolist()
if flags:
    st.error(f"Gap flagged in round(s) {', '.join(map(str, flags))}: it widened {plan.get('gap_flag_rounds')} "
             "rounds in a row. Treat the latest gains with suspicion until check-loso confirms them.")
with st.expander("Per-round trace"):
    st.dataframe(trace, width="stretch", hide_index=True)

# ------------------------------------------------------------------------------------------- leaderboard
st.subheader("Leaderboard")
rounds_done = trace["round"].tolist()
r = st.select_slider("Round", options=rounds_done, value=rounds_done[-1]) if len(rounds_done) > 1 \
    else rounds_done[0]
table = round_table(paths, run_id, r)
st.dataframe(table.drop(columns=["genome_hash"]), width="stretch", hide_index=True)
st.caption("Composite = frozen scoring weights (the loop never changes them); the top two (rank 1-2) survive "
           "unchanged into the next round. Survivors keep their scores (cached).")

# ------------------------------------------------------------------------------------------- lineage
st.subheader("Lineage of the current best agent")
best = table.iloc[0]
try:
    rec, chain = lineage_for(paths, best["genome_hash"], run_id)
    st.markdown(f"**{best['agent']}** (`{best['agent_id']}`, {best['family']})")
    changed = rec.get("changed_vs_default") or {}
    if changed:
        st.dataframe(pd.DataFrame([{"gene": k, "default": a, "this agent": b} for k, (a, b) in changed.items()]),
                     width="stretch", hide_index=True)
    else:
        st.caption("All genes at the family default.")
    st.code("\n".join(format_ancestry(chain)), language=None)
except KeyError as exc:
    st.info(f"No lineage record: {exc}")

# ------------------------------------------------------------------------------------------- promotion check
st.subheader("Promotion check (leave one season out)")
checks = list_checks(paths)
if not checks:
    ref = f"{run_id}/{rounds_done[-1]}/1"
    st.info("No promotion check yet. An evolved agent can reach site output only if it beats SNOWPACK on held-out "
            f"seasons (CLAUDE.md principle 3). Run:\n\n```\nsnowagent lab check-loso --genome {ref} --workers 4\n```"
            "\n\nIt re-runs this whole training once per season with that season held out; it may take hours "
            "(it prints an estimate first).")
else:
    cid = st.selectbox("Check", checks)
    chk = load_check(paths, cid)
    res, folds = chk["result"], chk["folds"]
    st.caption(f"Genome `{chk['check']['plan']['genome_ref']}` · seasons {', '.join(chk['check']['plan']['seasons'])}"
               f" · rule: {RULE}")
    if res is None:
        stt = chk.get("status") or {}
        st.info(f"In progress: {stt.get('phase', 'starting')} ({len(folds)} of "
                f"{len(chk['check']['plan']['seasons'])} folds done).")
    else:
        verdict = "PASS" if res["passed"] else "FAIL"
        (st.success if res["passed"] else st.error)(
            f"{verdict}: pooled held-out composite, evolved {res['pooled_evolved_composite']} vs SNOWPACK "
            f"{res['pooled_incumbent_composite']} on {res['pooled_cases']} cases; wins {res['wins']}, losses "
            f"{res['losses']}, ties {res['ties']}."
            + ("" if res["passed"] else " The evolved agent stays a research entry; SNOWPACK remains the site "
                                        "model."))
    if folds:
        st.dataframe(pd.DataFrame([{"held-out season": f["season"], "cases": f.get("holdout_cases"),
                                    "fold winner": f["winner"]["label"], "family": f["winner"]["family"],
                                    "evolved": f.get("winner_composite"), "SNOWPACK": f.get("incumbent_composite"),
                                    "difference": f.get("difference"), "outcome": f.get("outcome")}
                                   for f in folds]), width="stretch", hide_index=True)
