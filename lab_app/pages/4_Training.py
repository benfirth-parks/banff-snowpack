"""Training: start, stop and resume a local training run (`snowagent lab train`, a background job, never inside this
app), follow it round by round (leaderboard, best composite, the train-vs-held-out gap with its flags, the log), see
the lineage of any agent, and start, follow and resume the leave-one-season-out promotion check (`snowagent lab
check-loso`, estimate first) (ADR-066 to ADR-069, ADR-077)."""

from __future__ import annotations

import os

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from snowagent.lab.competition.scoring import SCORING_VERSION
from snowagent.lab.schemas.genome import AgentFamily
from snowagent.lab.services.data import data_status
from snowagent.lab.services.jobs import ACTIVE, JobBusy, job_for, latest_job, pid_alive
from snowagent.lab.services.training import (
    list_training_runs,
    resume_training,
    round_table,
    run_overview,
    start_training,
    stop_training,
)
from snowagent.lab.services.workflow import (
    check_args,
    estimate_for,
    resume_check_args,
    start_check,
    start_check_estimate,
)
from snowagent.lab.training.lineage import format_ancestry, lineage_for
from snowagent.lab.training.loso import RULE, list_checks, load_check
from snowagent.lab.ui.app import (
    config_path,
    default_run_index,
    empty_state,
    lab_context,
    page_header,
    repo_root,
)
from snowagent.lab.ui.jobs import job_block
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
            try:
                info = start_training(
                    paths, config_path(__file__), cwd=repo_root(__file__),
                    rounds=int(rounds), population=int(population), survivors=int(survivors),
                    mutation_strength=float(strength), crossover_share=float(cross), seed=int(seed),
                    workers=int(workers), engine=engine,
                    plots=None if len(plots) == len(cfg.sites) else plots,
                    case_types=None if len(case_types) == 2 else case_types,
                    initial=None if len(initial) == len(AgentFamily) else initial,
                    screen_cases=int(screen) or None, family_slots=bool(family_slots))
                st.success(f"Started training run `{info['run_id']}` (process {info['pid']}). It runs on its own: "
                           "closing this page does not stop it. Choose it under Training run in the sidebar and "
                           "press Refresh to follow it.")
            except JobBusy as exc:
                st.error(str(exc))
    st.caption("The same from a terminal: `snowagent lab train --rounds 10 --population 10 --seed 0 --workers 4` "
               "(see docs/lab/training.md for times; the first round runs SNOWPACK once per case, and so does every "
               "child with new SNOWPACK physics genes).")

runs = list_training_runs(paths)
if not runs:
    st.info("No training run yet. Start one above, or from the repository root run `snowagent lab train`.")
    st.stop()

# ------------------------------------------------------------------------------------------- one run
run_id = st.sidebar.selectbox("Training run", runs,
                              index=default_run_index(paths.outputs / "training", runs, SCORING_VERSION))
ov = run_overview(paths, run_id)
plan, status = ov["plan"], ov["status"]
st.subheader(f"Run `{run_id}`")
if ov["fold_of_check"]:
    st.caption("This run is one fold of a leave-one-season-out check (one season held out).")
if plan:
    st.caption(f"case set `{plan['case_set']}` · {len(plan['case_ids'])} cases · seasons {plan['seasons'][0]} to "
               f"{plan['seasons'][-1]} · {plan['rounds']} rounds · population {plan['population']} · survivors "
               f"{plan['survivors']} · mutation {plan['mutation_strength']} · crossover {plan['crossover_share']} · "
               f"seed {plan['seed']} · monitor season {plan['monitor_season']} · scoring "
               f"{plan.get('scoring_version', '?')} (runs of different scoring versions do not compare, ADR-074)")
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
               + f". Resume continues it at the first unfinished round (finished work comes from the cache); from a "
               f"terminal: `snowagent lab train --resume --run-id {run_id}`.")
    if not ov["fold_of_check"]:
        rw = st.number_input("Workers for the resumed run", 1, max(1, os.cpu_count() or 1),
                             min(4, os.cpu_count() or 1))
        if c3.button("Resume"):
            try:
                info = resume_training(paths, config_path(__file__), run_id, workers=int(rw), cwd=repo_root(__file__))
                st.success(f"Resumed `{run_id}` as job `{info['job_id']}`. Press Refresh to follow it.")
            except JobBusy as exc:
                st.error(str(exc))
if ov["estimate"]:
    e = ov["estimate"]
    st.caption(f"Estimate before the start ({e['workers']} workers, {e['timings']} timings): round 1 "
               f"{e['round1']['wall_s'] / 60:.0f} min, total {e['total_s'][0] / 60:.0f}-{e['total_s'][1] / 60:.0f}"
               " min.")
if st.button("Refresh"):
    st.rerun()
train_job = job_for(paths, "train", "run_id", run_id)
with st.expander("Output (log)", expanded=state == "running"):
    if train_job:
        job_block(st, paths, train_job, key="train")
    elif (ov["dir"] / "train.log").is_file():
        st.code("\n".join((ov["dir"] / "train.log").read_text().splitlines()[-40:]), language=None)
    else:
        st.caption("No log yet.")

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
           "unchanged into the next round. Survivors keep their scores (cached). Snow depth scores the median (p50) "
           "only; the p10-p90 range is scored in uncertainty, and its coverage is a diagnostic (ADR-074).")

# ------------------------------------------------------------------------------------------- lineage
st.subheader("Lineage")
pick = st.selectbox("Agent", table["agent"].tolist(), index=0,
                    help="the round's leaderboard order: the first is the round's best agent")
best = table[table["agent"] == pick].iloc[0]
try:
    rec, chain = lineage_for(paths, best["genome_hash"], run_id)
    st.markdown(f"**{best['agent']}** (`{best['agent_id']}`, {best['family']})")
    changed = rec.get("changed_vs_default") or {}
    if changed:
        st.dataframe(pd.DataFrame([{"gene": k, "default": str(a), "this agent": str(b)} for k, (a, b) in changed.items()]),
                     width="stretch", hide_index=True)
    else:
        st.caption("All genes at the family default.")
    st.code("\n".join(format_ancestry(chain)), language=None)
except KeyError as exc:
    st.info(f"No lineage record: {exc}")

# ------------------------------------------------------------------------------------------- promotion check
st.subheader("Promotion check (leave one season out)")
st.caption("An evolved agent can reach site output only if it beats SNOWPACK on seasons it never trained on "
           "(CLAUDE.md principle 3), and even then only by the owner's decision (ADR-058): nothing is promoted "
           f"automatically. The check re-runs this training once per season with that season held out. Rule: {RULE}")
checks = list_checks(paths)
check_job = latest_job(paths, "check-loso")
with st.expander("Start or resume a promotion check",
                 expanded=not checks or bool(check_job and check_job["state"] in ACTIVE)):
    if plan.get("case_set") != "all":
        st.info("Choose a training run of the case set `all` in the sidebar: this run is itself a fold of a check.")
    else:
        root = repo_root(__file__)
        k1, k2, k3, k4 = st.columns(4)
        c_round = k1.number_input("Round", 1, max(rounds_done), max(rounds_done))
        c_rank = k2.number_input("Rank", 1, int(plan.get("population") or 2), 1,
                                 help="1 = the round's best agent")
        c_workers = k3.number_input("Workers", 1, max(1, os.cpu_count() or 1), min(4, os.cpu_count() or 1),
                                    key="check-workers")
        c_engine = k4.selectbox("Engine", ["auto", "none"], key="check-engine")
        k5, k6 = st.columns(2)
        c_rounds = k5.number_input(f"Rounds per fold (0 = as the run: {plan.get('rounds')})", 0, 1000, 0,
                                   help="fewer rounds: a cheaper check, but a weaker test of this run (it says so)")
        c_pop = k6.number_input(f"Population (0 = as the run: {plan.get('population')})", 0, 500, 0)
        all_seasons = list(plan.get("seasons") or [])
        c_seasons = st.multiselect("Held-out seasons", all_seasons, default=all_seasons,
                                   help="one fold per season; the verdict needs every season, so fewer seasons "
                                   "is a partial check (or one night's share of it)")
        ref = f"{run_id}/{int(c_round)}/{int(c_rank)}"
        args = check_args(ref, seasons=None if len(c_seasons) == len(all_seasons) else c_seasons,
                          rounds=int(c_rounds) or None, population=int(c_pop) or None, workers=int(c_workers),
                          engine=c_engine)
        cid = st.text_input("Check id (the same id resumes the check)",
                            f"{run_id}-r{int(c_round)}-k{int(c_rank)}-loso"
                            + ("-reduced" if int(c_rounds) or int(c_pop) else "")
                            + ("" if len(c_seasons) == len(all_seasons) else f"-{len(c_seasons)}s"))
        est_job = estimate_for(paths, args)
        b1, b2, _ = st.columns([1, 1, 2])
        if b1.button("1. Estimate the time", disabled=not c_seasons):
            try:
                est_job = start_check_estimate(paths, config_path(__file__), root, args)
                st.info("Estimating (seconds to a minute); press Refresh.")
            except JobBusy as exc:
                st.error(str(exc))
        running_check = bool(check_job and check_job["state"] in ACTIVE)
        if b2.button("2. Start the check", type="primary",
                     disabled=not (est_job and est_job["state"] == "finished") or running_check or not c_seasons):
            try:
                check_job = start_check(paths, config_path(__file__), root, args, cid.strip())
                st.success(f"Started promotion check `{cid}`. It may take hours; it runs on its own.")
            except JobBusy as exc:
                st.error(str(exc))
        if est_job:
            st.markdown("**Estimate**")
            job_block(st, paths, est_job, key="check-estimate", tail_lines=12)
        else:
            st.caption("Estimate first: the check prints how long it expects to take before you commit to it.")
        if cid.strip() in checks:
            st.caption(f"A check `{cid.strip()}` exists: starting it again resumes it (it must have the same options).")
    unfinished = [c for c in checks if load_check(paths, c)["result"] is None]
    if unfinished:
        st.markdown("**Resume a check**")
        r1, r2, r3 = st.columns([2, 1, 1])
        rcid = r1.selectbox("Unfinished check", unfinished)
        rworkers = r2.number_input("Workers", 1, max(1, os.cpu_count() or 1), min(4, os.cpu_count() or 1),
                                   key="resume-workers")
        rengine = r3.selectbox("Engine", ["auto", "none"], key="resume-engine")
        if st.button("Resume check", disabled=bool(check_job and check_job["state"] in ACTIVE)):
            try:
                check_job = start_check(paths, config_path(__file__), repo_root(__file__),
                                        resume_check_args(paths, rcid, int(rworkers), rengine), rcid)
                st.success(f"Resuming `{rcid}`: finished folds are kept.")
            except (JobBusy, FileNotFoundError, KeyError) as exc:
                st.error(str(exc))
    if check_job:
        st.markdown("**Latest check job**")
        job_block(st, paths, check_job, key="check-loso")

if not checks:
    st.info("No promotion check yet. Start one above, or from a terminal: "
            f"`snowagent lab check-loso --genome {run_id}/{rounds_done[-1]}/1 --workers 4` (it prints an estimate "
            "first; it may take hours).")
else:
    cid = st.selectbox("Check", checks)
    chk = load_check(paths, cid)
    res, folds = chk["result"], chk["folds"]
    st.caption(f"Genome `{chk['check']['plan']['genome_ref']}` · seasons {', '.join(chk['check']['plan']['seasons'])}"
               f" · rule: {RULE}")
    if res is None:
        stt = chk.get("status") or {}
        gone = stt.get("state") == "running" and not pid_alive(stt.get("pid"))
        (st.warning if gone else st.info)(
            ("Interrupted (its process ended): resume it above. " if gone else "In progress: ")
            + f"{stt.get('phase', 'starting')} ({len(folds)} of {len(chk['check']['plan']['seasons'])} folds done).")
    else:
        verdict = "PASS" if res["passed"] else "FAIL"
        (st.success if res["passed"] else st.error)(
            f"{verdict}: pooled held-out composite, evolved {res['pooled_evolved_composite']} vs SNOWPACK "
            f"{res['pooled_incumbent_composite']} on {res['pooled_cases']} cases; wins {res['wins']}, losses "
            f"{res['losses']}, ties {res['ties']}."
            + (" A PASS makes the agent a candidate only: promotion to site output is the owner's decision "
               "(ADR-058)." if res["passed"] else " The evolved agent stays a research entry; SNOWPACK remains the "
               "site model."))
        if res.get("differs_from_training_run"):
            st.caption("Reduced configuration (" + ", ".join(
                f"{k} {v['check']} instead of {v['training_run']}" for k, v in res["differs_from_training_run"].items())
                + "): a weaker test of the training run.")
    if folds:
        st.dataframe(pd.DataFrame([{"held-out season": f["season"], "cases": f.get("holdout_cases"),
                                    "fold winner": f["winner"]["label"], "family": f["winner"]["family"],
                                    "evolved": f.get("winner_composite"), "SNOWPACK": f.get("incumbent_composite"),
                                    "difference": f.get("difference"), "outcome": f.get("outcome")}
                                   for f in folds]), width="stretch", hide_index=True)
