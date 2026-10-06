"""Training: start, stop and resume a local training run (`snowagent lab train`, a background job, never inside this
app), follow it round by round (leaderboard, best composite, the train-vs-held-out gap with its flags, the log), see
the lineage of any agent, and start, follow and resume the leave-one-season-out promotion check (`snowagent lab
check-loso`, estimate first) (ADR-066 to ADR-069, ADR-077)."""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from snowagent.lab.competition.scoring import SCORING_VERSION
from snowagent.lab.schemas.genome import AgentFamily
from snowagent.lab.services.data import data_status
from snowagent.lab.services.jobs import ACTIVE, JobBusy, job_for, latest_job, pid_alive
from snowagent.lab.services.names import display
from snowagent.lab.services.site_send import SendError, agent_record, on_site, remove, send
from snowagent.lab.services.training import (
    PRESETS,
    list_training_runs,
    resume_training,
    round_table,
    run_overview,
    running_training,
    seen_locked,
    start_training,
    stop_training,
    time_left,
)
from snowagent.lab.services.workflow import (
    check_args,
    estimate_for,
    resume_check_args,
    start_check,
    start_check_estimate,
)
from snowagent.lab.training.lineage import format_ancestry, lineage_for
from snowagent.lab.training.loop import LOCKED_SEASONS, committed_rounds, training_root
from snowagent.lab.training.loso import RULE, list_checks, load_check
from snowagent.lab.ui.app import (
    config_path,
    default_run_index,
    default_workers,
    empty_state,
    finish_text,
    fmt_duration,
    lab_context,
    page_header,
    repo_root,
)
from snowagent.lab.ui.genes import gene_rows
from snowagent.lab.ui.jobs import job_block
from snowagent.lab.ui.plots import CONCERN, NEUTRAL, SERIES
from snowagent.ops.site_agents import MAX_AGENTS

GAP_WARNING = ("The per-round gap (composite on the other seasons minus composite on the monitor season) is a "
               "**warning signal only**: in split mode `all` the monitor season is also training data, so a small "
               "gap proves nothing. The evidence that an evolved agent generalises is the leave-one-season-out "
               "promotion check below (`snowagent lab check-loso`).")

LIVE_EVERY_S = 5  # seconds between the run panel's own updates while a run is running

page_header(st, "Training")
cfg, paths = lab_context(__file__)
st.caption("Each round every agent predicts every training case and is scored; the top two survive unchanged and "
           "are mutated and crossed to make the next population. Evolved agents are research entries: the site's model "
           "stays SNOWPACK unless an agent passes the promotion check, though you can show up to three on the site "
           "as experimental extras (below the agent card).")

built = (paths.benchmark / "all").is_dir() and any((paths.benchmark / "all").glob("*/*/manifest.json"))
runs = list_training_runs(paths)
flash = st.session_state.pop("train-flash", None)  # a message from the click that started or resumed a run
if flash:
    st.success(flash)


def train_job_active() -> bool:
    job = latest_job(paths, "train")
    return bool(job and job["state"] in ACTIVE)


# ------------------------------------------------------------------------------------------- the selected run
if runs:
    run_id = st.sidebar.selectbox("Training run", runs,
                                  index=default_run_index(paths.outputs / "training", runs, SCORING_VERSION),
                                  help="a run that is running now is chosen first")
    ov = run_overview(paths, run_id)
    plan = ov["plan"]
    page_state, page_rounds = ov["status"].get("state", "unknown"), len(ov["rounds"])
    live_updates = page_state == "running" or train_job_active()

    @st.fragment(run_every=LIVE_EVERY_S if live_updates else None)
    def run_panel() -> None:
        """State, round, best score, time left, Stop or Resume, and the log; every few seconds while it runs."""
        o = run_overview(paths, run_id)
        stt, trace = o["status"], o["rounds"]
        state = stt.get("state", "unknown")
        newer = running_training(paths)
        if state != page_state or len(trace) != page_rounds or (newer and newer not in runs):
            st.rerun()  # a round committed, the run ended, or a new run began: redraw the whole page
        st.subheader(f"Run `{run_id}`")
        if o["fold_of_check"]:
            st.caption("This run is one fold of a leave-one-season-out check (one season held out).")
        if plan:
            st.caption(f"{len(plan['case_ids'])} pit cases · seasons {plan['seasons'][0]} to {plan['seasons'][-1]} · "
                       f"population {plan['population']} · survivors {plan['survivors']} · seed {plan['seed']} · "
                       f"scoring {plan.get('scoring_version', '?')}")
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("State", state)
        m2.metric("Round", f"{stt.get('round', 0)} / {plan.get('rounds', '?')}")
        if len(trace):
            best, first = trace["best_composite"].iloc[-1], trace["best_composite"].iloc[0]
            m3.metric("Best score so far", f"{best:.4f}",
                      delta=f"{best - first:+.4f} since round 1" if len(trace) > 1 else None,
                      help="the composite score (0-1) of the best agent in the latest finished round")
        else:
            m3.metric("Best score so far", "after round 1")
        if state == "running":
            left = time_left(o)
            m4.metric("Time left", fmt_duration(left) if left is not None else "not known yet",
                      help="from this run's own finished rounds; a round whose children carry new SNOWPACK "
                      "physics takes longer than one that does not")
            st.caption(f"Finish: {finish_text(left)}. This panel updates itself every {LIVE_EVERY_S} seconds.")
            if stt.get("total"):
                st.progress(min(1.0, stt.get("done", 0) / stt["total"]),
                            text=f"round {stt.get('round')}: {stt.get('phase', 'evaluating')}, "
                                 f"{stt.get('done', 0)} of {stt['total']} cases with work")
            stopping = (o["dir"] / "stop").exists()
            if not stopping and st.button("Stop", key="train-stop"):
                stop_training(paths, run_id)
                stopping = True
            if stopping:
                st.info("Stop requested: the run stops once the cases already started finish (a minute or two). "
                        "Resume continues it later.")
        elif state in ("interrupted", "stopped", "failed"):
            st.warning(f"The run is {state}" + (f" ({stt.get('message')})" if stt.get("message") else "")
                       + ". Resume continues it at the first unfinished round; finished work comes from the cache.")
            if not o["fold_of_check"]:
                r1, r2, _ = st.columns([1, 1, 2])
                rw = r1.number_input("Workers", 1, max(1, os.cpu_count() or 1), default_workers(),
                                     key="resume-train-workers")
                if r2.button("Resume", type="primary", key="train-resume", disabled=train_job_active()):
                    try:
                        info = resume_training(paths, config_path(__file__), run_id, workers=int(rw),
                                               cwd=repo_root(__file__))
                        st.session_state["train-flash"] = f"Resumed `{run_id}` (job `{info['job_id']}`)."
                        st.rerun()
                    except JobBusy as exc:
                        st.error(str(exc))
        elif state == "finished":
            st.success("Finished. The charts, leaderboard and lineage below are final.")
        train_job = job_for(paths, "train", "run_id", run_id)
        with st.expander("Output (log)", expanded=state == "running"):
            if train_job:
                job_block(st, paths, train_job, key="train", controls=False)
            elif (o["dir"] / "train.log").is_file():
                st.code("\n".join((o["dir"] / "train.log").read_text().splitlines()[-40:]), language=None)
            else:
                st.caption("No log yet.")
            st.caption(f"From a terminal: `snowagent lab train --resume --run-id {run_id}` resumes it.")

    run_panel()

# ------------------------------------------------------------------------------------------- start a run
with st.expander("Start a new training run", expanded=not runs):
    if not built:
        if not any(data_status(paths).values()):
            empty_state(st, paths)
        st.info("No benchmark cases yet: run `snowagent lab build-cases` first (Benchmark Cases page).")
    busy = running_training(paths) or (train_job_active() and "a training job")
    if busy:
        b1, b2 = st.columns([3, 1])
        b1.info(f"`{busy}` is running. One run at a time: stop it first (Resume continues it later).")
        if b2.button("Stop it", key="train-stop-busy") and running_training(paths):
            stop_training(paths, running_training(paths))
            st.session_state["train-flash"] = "Stop requested: the run stops once its started cases finish."
            st.rerun()
    t = cfg.training
    preset_name = st.selectbox("Preset", list(PRESETS), help="fills in the options below; Custom keeps the "
                               "configuration's defaults")
    pre, k = PRESETS[preset_name], list(PRESETS).index(preset_name)
    with st.form("train"):
        c1, c2, c3, c4 = st.columns(4)
        rounds = c1.number_input("Rounds", 1, 1000, pre.get("rounds", t.rounds), key=f"rounds-{k}")
        population = c2.number_input("Population", 2, 500, pre.get("population", t.population), key=f"pop-{k}")
        survivors = c3.number_input("Survivors", 1, 100, pre.get("survivors", t.survivors), key=f"surv-{k}")
        workers = c4.number_input("Workers", 1, max(1, os.cpu_count() or 1), default_workers(),
                                  help="the Mac's performance cores by default")
        with st.expander("Advanced"):
            a1, a2, a3, a4 = st.columns(4)
            strength = a1.number_input("Mutation strength", 0.01, 1.0, t.mutation_strength, 0.05,
                                       help="the chance that each gene changes in a child, and how far")
            cross = a2.number_input("Crossover share", 0.0, 1.0, t.crossover_share, 0.05,
                                    help="the share of children made by crossing the two survivors")
            seed = a3.number_input("Seed", 0, 2**31 - 1, 0, help="another seed gives an independent run")
            engine = a4.selectbox("Engine", ["auto", "none"],
                                  help="auto: the SNOWPACK binary (SNOWPACK_BIN or PATH); none: SNOWPACK skipped, "
                                  "the hybrid predicts from its other members")
            all_plots = [c.value for c in cfg.sites]
            plots = st.multiselect("Plots", all_plots, default=pre.get("plots") or all_plots, key=f"plots-{k}")
            case_types = st.multiselect("Case types", ["forecast_h72", "next_pit"],
                                        default=pre.get("case_types") or ["forecast_h72", "next_pit"],
                                        key=f"types-{k}")
            initial = st.multiselect("Initial population", [f.value for f in AgentFamily],
                                     default=[f.value for f in AgentFamily],
                                     help="the default genome of each chosen family (genome files: use the CLI)")
            a5, a6 = st.columns(2)
            screen = a5.number_input("Screen cases (0 = off)", 0, 10000, pre.get("screen_cases", 30),
                                     key=f"screen-{k}",
                                     help="score a child with new SNOWPACK physics on this many cases first; only "
                                     "one beating the worst survivor there runs on every case")
            family_slots = a6.checkbox("Family slots", False,
                                       help="each round, one mutant of every family's best agent")
            locked_n = st.number_input("Locked test winters (most recent)", 0, 20, LOCKED_SEASONS,
                                       key=f"locked-{k}",
                                       help="these winters are never used to train or choose agents; each round's "
                                       "leaders are tested on them, a true unseen-winter score (0 = off)")
            sources = [x for x in runs if committed_rounds(training_root(paths) / x)]
            s1, s2 = st.columns([3, 1])
            seed_from = s1.selectbox("Start from an earlier run's agents", ["(none)"] + sources, key=f"seedfrom-{k}",
                                     help="adds the best evolved agents of that run's last round to the starting "
                                     "agents. If they trained on the winters this run locks, its locked-winter "
                                     "results are no longer a clean test (the page will say so)")
            seed_top = s2.number_input("How many", 1, 10, 2, key=f"seedtop-{k}",
                                       help="that many of its best agents (family defaults skipped)")
        start = st.form_submit_button("Start training", type="primary", disabled=not built or bool(busy))
    if start:
        if survivors >= population or len(initial) < survivors:
            st.error("Survivors must be fewer than the population and no more than the initial agents.")
        elif family_slots and population - survivors < len(AgentFamily):
            st.error(f"Family slots need at least {len(AgentFamily)} children per round (population - survivors).")
        elif not plots or not case_types:
            st.error("Choose at least one plot and one case type (under Advanced).")
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
                    screen_cases=int(screen) or None, family_slots=bool(family_slots),
                    locked_seasons=int(locked_n),
                    seed_from=None if seed_from == "(none)" else seed_from, seed_top=int(seed_top))
                seen = seen_locked(paths, None if seed_from == "(none)" else seed_from, int(locked_n))
                st.session_state["train-flash"] = (
                    f"Started training run `{info['run_id']}` (process {info['pid']}). It runs on its own: closing "
                    "this page does not stop it. It appears above once it has loaded its cases."
                    + (f" Note: the agents from `{seed_from}` already trained on {', '.join(seen)}, which this run "
                       "locks, so its locked-winter results will not be a clean test." if seen else ""))
                st.rerun()
            except JobBusy as exc:
                st.error(str(exc))
    st.caption(f"The same from a terminal: `snowagent lab train --rounds {int(rounds)} --population {int(population)} "
               f"--survivors {int(survivors)} --seed {int(seed)} --workers {int(workers)}"
               f"{f' --screen-cases {int(screen)}' if screen else ''}{' --engine none' if engine == 'none' else ''}"
               f" --locked-seasons {int(locked_n)}` "
               "(see docs/lab/training.md for times; the first round runs SNOWPACK once per case, and so does every "
               "child with new SNOWPACK physics genes).")

if not runs:
    st.info("No training run yet. Start one above, or from the repository root run `snowagent lab train`.")
    st.stop()

trace = ov["rounds"]
if trace.empty:
    st.info("No round committed yet.")
    st.stop()

locked = ov.get("locked")
if locked:
    st.subheader("Locked test winters")
    lk = trace.dropna(subset=["locked_composite"])
    inc = (locked.get("incumbent") or {}).get("composite")
    st.caption(f"{', '.join(locked['seasons'])} ({locked['cases']} cases) never train or choose agents. After each "
               "round's selection, its leaders are scored on them: a true unseen-winter score, unlike the gap below.")
    if plan.get("seeded_saw_locked"):
        st.warning(f"Not a clean test: this run started with agents from "
                   f"{', '.join(sorted({x['run_id'] for x in plan.get('seeded_from', [])}))}, which had already "
                   f"trained on {', '.join(plan['seeded_saw_locked'])}. Their descendants can look better on these "
                   "winters than they really are; the promotion check is the fair test.", icon="⚠️")
    if len(lk):
        last = lk.iloc[-1]
        if inc is not None:
            d = last["locked_composite"] - inc
            (st.success if d > 0 else st.warning)(
                f"Round {int(last['round'])}'s best agent ({last['best']}) scores {last['locked_composite']:.4f} on the "
                f"locked winters, against {inc:.4f} for standard SNOWPACK ({d:+.4f}).")
        fig = go.Figure(go.Scatter(x=lk["round"], y=lk["locked_composite"], mode="lines+markers",
                                   line={"color": SERIES, "width": 2}, marker={"size": 8}, name="best agent",
                                   customdata=lk[["best"]],
                                   hovertemplate="round %{x}<br>%{customdata[0]}<br>locked %{y:.4f}<extra></extra>"))
        if inc is not None:
            fig.add_hline(y=inc, line={"color": NEUTRAL, "width": 1, "dash": "dash"},
                          annotation_text="standard SNOWPACK", annotation_position="bottom right")
        fig.update_layout(title="Round's best agent on the locked winters", height=300,
                          margin={"l": 10, "r": 10, "t": 40, "b": 10}, xaxis_title="round",
                          yaxis_title="composite (0-1)", showlegend=False)
        fig.update_xaxes(dtick=1)
        st.plotly_chart(fig, width="stretch", theme="streamlit")

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
           "only; the p10-p90 range is scored in uncertainty, and its coverage is a diagnostic.")

# ------------------------------------------------------------------------------------------- lineage
st.subheader("Agent card")
pick = st.selectbox("Agent", table["agent"].tolist(), index=0,
                    format_func=lambda a: display(table.set_index("agent").at[a, "genome_hash"], a),
                    help="the round's leaderboard order: the first is the round's best agent")
best = table[table["agent"] == pick].iloc[0]
try:
    rec, chain = lineage_for(paths, best["genome_hash"], run_id)
    st.markdown(f"**{best['name']}**: {best['agent']} (`{best['agent_id']}`, {best['family']})")
    changed = rec.get("changed_vs_default") or {}
    if changed:
        st.caption("How this agent differs from its family's default settings:")
        st.dataframe(pd.DataFrame(gene_rows(changed, cfg.genome)), width="stretch", hide_index=True)
    else:
        st.caption("All settings at the family default.")
    st.code("\n".join(format_ancestry(chain)), language=None)
except KeyError as exc:
    st.info(f"No lineage record: {exc}")

# ------------------------------------------------------------------------------------------- send to site
st.subheader("Put on the public site (experimental)")
st.caption(f"Sends this agent to the public site as an extra choice beside standard SNOWPACK, labelled experimental "
           f"and not validated (ADR-084). The next daily update runs it for this winter at all three plots. At most "
           f"{MAX_AGENTS} agents at a time; standard SNOWPACK stays the site's default. Uses this computer's GitHub "
           "sign-in.")
if msg := st.session_state.pop("site-flash", None):
    st.success(msg)


site_repo = Path(os.environ.get("SNOWAGENT_SITE_REPO") or repo_root(__file__))  # the checkout that pushes


@st.cache_data(ttl=120, show_spinner="Checking which agents are on the site…")
def _on_site() -> list[dict]:
    return on_site(site_repo)


try:
    current = _on_site()
except (SendError, OSError) as exc:
    current = None
    st.warning(f"Could not check the site's agents: {exc}")
if current is not None:
    if current:
        for a in current:
            c1, c2 = st.columns([5, 1])
            lk = a.get("locked_composite")
            c1.markdown(f"**{a['name']}** from run `{a.get('run_id')}`, round {a.get('round')}, rank {a.get('rank')}"
                        + (f"; locked test winters {lk:.3f}" if lk is not None else "")
                        + f" · sent {a['added_utc'][:16].replace('T', ' ')} UTC")
            if c2.button("Remove", key=f"rm-{a['id']}"):
                try:
                    with st.spinner(f"Removing {a['name']}…"):
                        remove(site_repo, a["id"])
                    _on_site.clear()
                    st.session_state["site-flash"] = f"{a['name']} removed. The site drops it at the next daily update."
                    st.rerun()
                except (SendError, OSError) as exc:
                    st.error(str(exc))
    else:
        st.caption("No agents on the site yet.")
    here = {a["id"] for a in current}
    if best["family"] != AgentFamily.snowpack.value:
        st.info("Only SNOWPACK-family agents can run on the site.")
    elif best["agent_id"] in here:
        st.caption(f"{best['name']} is on the site.")
    elif len(current) >= MAX_AGENTS:
        st.info(f"The site has {MAX_AGENTS} agents already. Remove one to send {best['name']}.")
    elif st.button(f"Send {best['name']} to the site", type="primary"):
        try:
            with st.spinner(f"Sending {best['name']}…"):
                send(site_repo, agent_record(paths, run_id, r, best["agent_id"]))
            _on_site.clear()
            st.session_state["site-flash"] = (f"{best['name']} sent. It appears on the site after the next daily "
                                              "update, under Weather input as an experimental agent.")
            st.rerun()
        except (SendError, OSError, ValueError) as exc:
            st.error(str(exc))

# ------------------------------------------------------------------------------------------- promotion check
st.subheader("Promotion check (leave one season out)")
st.caption("An evolved agent can reach site output only if it beats SNOWPACK on seasons it never trained on "
           "(the project's rule), and even then only by the owner's decision: nothing is promoted "
           f"automatically. The check re-runs this training once per season with that season held out. Rule: {RULE}")
checks = list_checks(paths)
check_job = latest_job(paths, "check-loso")
with st.expander("Start or resume a promotion check",
                 expanded=not checks or bool(check_job and check_job["state"] != "finished")):
    if plan.get("case_set") != "all":
        st.info("Choose a training run of the case set `all` in the sidebar: this run is itself a fold of a check.")
    else:
        root = repo_root(__file__)
        k1, k2, k3, k4 = st.columns(4)
        c_round = k1.number_input("Round", 1, max(rounds_done), max(rounds_done))
        c_rank = k2.number_input("Rank", 1, int(plan.get("population") or 2), 1,
                                 help="1 = the round's best agent")
        c_workers = k3.number_input("Workers", 1, max(1, os.cpu_count() or 1), default_workers(),
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
        rworkers = r2.number_input("Workers", 1, max(1, os.cpu_count() or 1), default_workers(),
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
            + (" A PASS makes the agent a candidate only: promotion to site output is the owner's decision."
               if res["passed"] else " The evolved agent stays a research entry; SNOWPACK remains the site model."))
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
