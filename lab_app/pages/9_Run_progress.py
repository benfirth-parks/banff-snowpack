"""Watch the run (ADR-119; before it the top of the Training page): the training run that is going now, or the one
chosen, round by round: state, round, time left, Stop and Resume, and after every round its best agent against
standard SNOWPACK on the locked test winters (ADR-083) and at Parker Ridge (ADR-117). The training score, the
train-vs-held-out warning signal and the log are folded away below."""

from __future__ import annotations

import os

import plotly.graph_objects as go
import streamlit as st

from snowagent.lab.services import labels
from snowagent.lab.services.jobs import ACTIVE, JobBusy, job_for, latest_job
from snowagent.lab.services.training import (
    resume_training,
    run_label,
    run_overview,
    running_training,
    stop_training,
    time_left,
)
from snowagent.lab.ui.app import (
    config_path,
    default_workers,
    finish_text,
    fmt_duration,
    lab_context,
    page_header,
    repo_root,
)
from snowagent.lab.ui.jobs import job_block
from snowagent.lab.ui.pages import link
from snowagent.lab.ui.plots import CONCERN, NEUTRAL, SERIES
from snowagent.lab.ui.training import locked_verdict, no_runs, pick_run, run_when

GAP_WARNING = ("The per-round gap (composite on the other seasons minus composite on the monitor season) is a "
               "**warning signal only**: in split mode `all` the monitor season is also training data, so a small "
               "gap proves nothing. The evidence that an evolved agent generalises is the leave-one-season-out "
               "promotion check, on the Promotion check page.")
LIVE_EVERY_S = 5  # seconds between the run panel's own updates while a run is running

page_header(st, "Watch the run",
            "How a training run is going: the round it is on, the time left and, after every round, whether its "
            "best agent beats standard SNOWPACK on winters it never trained on.",
            page="watch")
cfg, paths = lab_context(__file__)
flash = st.session_state.pop("train-flash", None)  # a message from the click that resumed a run
if flash:
    st.success(flash)

run_id = pick_run(st, paths)
if run_id is None:
    no_runs(st)
    st.stop()


def train_job_active() -> bool:
    job = latest_job(paths, "train")
    return bool(job and job["state"] in ACTIVE)


ov = run_overview(paths, run_id)
plan = ov["plan"]
page_state, page_rounds = ov["status"].get("state", "unknown"), len(ov["rounds"])
live_at_draw = running_training(paths)
live_updates = page_state == "running" or train_job_active()


@st.fragment(run_every=LIVE_EVERY_S if live_updates else None)
def run_panel() -> None:
    """State, round, best score, time left, Stop or Resume; every few seconds while it runs."""
    o = run_overview(paths, run_id)
    stt, trace = o["status"], o["rounds"]
    state = stt.get("state", "unknown")
    newer = running_training(paths)
    if state != page_state or len(trace) != page_rounds or (newer and newer != live_at_draw):
        st.rerun()  # a round committed, the run ended, or a new run began: redraw the whole page
    if state == "running":
        st.info("**This run is going now.** It keeps going when you close the browser; keep the computer awake "
                "and plugged in.", icon="🟢")
    elif state == "finished":
        st.success(f"**This run is over.** {run_when(o)} Nothing is running here now.", icon="🏁")
    if o["fold_of_check"]:
        st.caption("This run is one part of a promotion check (one winter held out).")
    if plan:
        st.caption(f"{len(plan['case_ids'])} pit cases from winters {plan['seasons'][0]} to {plan['seasons'][-1]} · "
                   f"{plan['population']} agents a round, the best {plan['survivors']} carry on")
    m1, m2, m3, m4 = st.columns(4)
    m1.metric("State", {"interrupted": "cut short"}.get(state, state))
    m2.metric("Round", f"{stt.get('round', 0)} / {plan.get('rounds', '?')}")
    if len(trace):
        best, first = trace["best_composite"].iloc[-1], trace["best_composite"].iloc[0]
        m3.metric("Best score so far", f"{best:.4f}",
                  delta=f"{best - first:+.4f} since round 1" if len(trace) > 1 else None,
                  help="the score (0 to 1, on the training winters) of the best agent in the latest finished round")
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
        if not stopping and st.button("Stop the run", key="train-stop"):
            stop_training(paths, run_id)
            stopping = True
        if stopping:
            st.info("Stop requested: the run stops once the cases already started finish (a minute or two). "
                    "Resume continues it later.")
    elif state in ("interrupted", "stopped", "failed"):
        st.warning(f"**This run is not going now.** {run_when(o)}"
                   + (f" ({stt.get('message')})" if stt.get("message") else "")
                   + " Resume continues it at the first unfinished round; finished work is kept.", icon="⏸️")
        if not o["fold_of_check"]:
            r1, r2, _ = st.columns([1, 1, 2])
            rw = r1.number_input("Workers", 1, max(1, os.cpu_count() or 1), default_workers(),
                                 key="resume-train-workers",
                                 help="how many cases run at once: the Mac's performance cores by default")
            if r2.button("Resume the run", type="primary", key="train-resume", disabled=train_job_active()):
                try:
                    resume_training(paths, config_path(__file__), run_id, workers=int(rw), cwd=repo_root(__file__))
                    st.session_state["train-flash"] = "The run is going again, from where it stopped."
                    st.rerun()
                except JobBusy as exc:
                    st.error(str(exc))


run_panel()

trace = ov["rounds"]
if trace.empty:
    st.info("No round is finished yet. The first round is the longest; the charts appear here when it is done.")

locked = ov.get("locked")
if locked and not trace.empty:
    st.subheader("Locked test winters")
    lk = trace.dropna(subset=["locked_composite"])
    inc = (locked.get("incumbent") or {}).get("composite")
    st.caption(f"{', '.join(locked['seasons'])} ({locked['cases']} cases) never train or choose agents. After each "
               "round's selection, its leaders are scored on them: a true unseen-winter score, and the number to "
               "watch.")
    if plan.get("seeded_saw_locked"):
        st.warning(f"Not a clean test: this run started with agents from "
                   f"{', '.join(sorted({x['run_id'] for x in plan.get('seeded_from', [])}))}, which had already "
                   f"trained on {', '.join(plan['seeded_saw_locked'])}. Their descendants can look better on these "
                   "winters than they really are; the promotion check is the fair test.", icon="⚠️")
    if len(lk):
        locked_verdict(st, ov)
        fig = go.Figure(go.Scatter(x=lk["round"], y=lk["locked_composite"], mode="lines+markers",
                                   line={"color": SERIES, "width": 2}, marker={"size": 8}, name="best agent",
                                   customdata=lk[["best_name"]],
                                   hovertemplate="round %{x}<br>%{customdata[0]}<br>locked %{y:.4f}<extra></extra>"))
        if inc is not None:
            fig.add_hline(y=inc, line={"color": NEUTRAL, "width": 1, "dash": "dash"},
                          annotation_text="standard SNOWPACK", annotation_position="bottom right")
        fig.update_layout(title="Round's best agent on the locked winters", height=300,
                          margin={"l": 10, "r": 10, "t": 40, "b": 10}, xaxis_title="round",
                          yaxis_title="composite (0-1)", showlegend=False)
        fig.update_xaxes(dtick=1)
        st.plotly_chart(fig, width="stretch", theme="streamlit")

area = ov.get("area")
if area and not trace.empty:  # ADR-117
    st.subheader(labels.titled("jasper") if (area.get("name") or "Parker Ridge") == "Parker Ridge"
                 else f"{area['name']} check")
    ak = trace.dropna(subset=["area_composite"])
    ainc = (area.get("incumbent") or {}).get("composite")
    st.caption(f"{area['cases']} cases from {len(area.get('seasons') or [])} winters at a plot outside Banff. The "
               "agents never train on them and they never choose an agent: after each round the same agents as on "
               "the locked winters are scored here too. A change that only helps Banff will not show up here.")
    if len(ak):
        last = ak.iloc[-1]
        if ainc is not None:
            d = last["area_composite"] - ainc
            (st.success if d > 0 else st.warning)(
                f"Round {int(last['round'])}'s best agent ({last['best_name']}) scores {last['area_composite']:.4f} at "
                f"Parker Ridge, against {ainc:.4f} for standard SNOWPACK ({d:+.4f}).")
        fig = go.Figure(go.Scatter(x=ak["round"], y=ak["area_composite"], mode="lines+markers",
                                   line={"color": SERIES, "width": 2}, marker={"size": 8}, name="best agent",
                                   customdata=ak[["best_name"]],
                                   hovertemplate="round %{x}<br>%{customdata[0]}<br>Parker Ridge %{y:.4f}"
                                   "<extra></extra>"))
        if ainc is not None:
            fig.add_hline(y=ainc, line={"color": NEUTRAL, "width": 1, "dash": "dash"},
                          annotation_text="standard SNOWPACK", annotation_position="bottom right")
        fig.update_layout(title="Round's best agent at Parker Ridge (never trained on)", height=300,
                          margin={"l": 10, "r": 10, "t": 40, "b": 10}, xaxis_title="round",
                          yaxis_title="composite (0-1)", showlegend=False)
        fig.update_xaxes(dtick=1)
        st.plotly_chart(fig, width="stretch", theme="streamlit")

if not trace.empty:
    flags = trace[trace["gap_flag"]]["round"].tolist()
    if flags:
        st.error(f"Gap flagged in round(s) {', '.join(map(str, flags))}: it widened {plan.get('gap_flag_rounds')} "
                 "rounds in a row. Treat the latest gains with suspicion until a promotion check confirms them.")
    st.markdown("**Next**")
    n1, n2, n3, _ = st.columns(4)
    with n1:
        link(st, "results", "See the agents")
    with n2:
        link(st, "reports", "Read the run's report")
    with n3:
        link(st, "start", "Start another run")

    with st.expander("More charts: the score on the training winters and the warning signal"):
        left, right = st.columns(2)
        with left:
            fig = go.Figure(go.Scatter(x=trace["round"], y=trace["best_composite"], mode="lines+markers",
                                       line={"color": SERIES, "width": 2}, marker={"size": 8},
                                       name="best composite", customdata=trace[["best_name"]],
                                       hovertemplate="round %{x}<br>%{customdata[0]}<br>composite %{y:.4f}"
                                       "<extra></extra>"))
            fig.update_layout(title="Best composite per round", height=320,
                              margin={"l": 10, "r": 10, "t": 40, "b": 10}, xaxis_title="round",
                              yaxis_title="composite (0-1)", showlegend=False)
            fig.update_xaxes(dtick=1)
            st.plotly_chart(fig, width="stretch", theme="streamlit")
        with right:
            g = trace.dropna(subset=["gap"])
            fig = go.Figure()
            fig.add_hline(y=0, line={"color": NEUTRAL, "width": 1})
            if len(g):
                fig.add_trace(go.Scatter(x=g["round"], y=g["gap"], mode="lines+markers",
                                         line={"color": SERIES, "width": 2}, marker={"size": 8}, name="gap",
                                         hovertemplate="round %{x}<br>gap %{y:+.4f}<extra></extra>"))
                fl = g[g["gap_flag"]]
                if len(fl):
                    fig.add_trace(go.Scatter(x=fl["round"], y=fl["gap"], mode="markers+text",
                                             text=["flag"] * len(fl), textposition="top center",
                                             marker={"size": 12, "color": CONCERN, "symbol": "triangle-up"},
                                             name="widened K rounds in a row",
                                             hovertemplate="round %{x}: gap widened K rounds in a row"
                                             "<extra></extra>"))
            fig.update_layout(title=f"Train vs held-out gap, top two (monitor {plan.get('monitor_season')})",
                              height=320, margin={"l": 10, "r": 10, "t": 40, "b": 10}, xaxis_title="round",
                              yaxis_title="composite gap", showlegend=False)
            fig.update_xaxes(dtick=1)
            st.plotly_chart(fig, width="stretch", theme="streamlit")
        st.warning(GAP_WARNING, icon="🔍")
        st.markdown("**Every round in numbers**")
        st.dataframe(trace[["round", "best_name", *[c for c in trace.columns if c not in ("round", "best_name")]]],
                     width="stretch", hide_index=True)

with st.expander("Technical details (the run's settings and its log)"):
    st.caption(f"Run `{run_id}` ({run_label(paths, run_id)})"
               + (f" · seed {plan['seed']} · scoring {plan.get('scoring_version', '?')}" if plan else "")
               + f" · files in `{ov['dir']}`")
    train_job = job_for(paths, "train", "run_id", run_id)
    if train_job:
        job_block(st, paths, train_job, key="train", controls=False, show_log=True)
    elif (ov["dir"] / "train.log").is_file():
        st.code("\n".join((ov["dir"] / "train.log").read_text().splitlines()[-40:]), language=None)
    else:
        st.caption("No log yet.")
    st.caption(f"From Terminal, `snowagent lab train --resume --run-id {run_id}` resumes it.")
