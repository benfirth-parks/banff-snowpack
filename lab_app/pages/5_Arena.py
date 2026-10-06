"""Arena: competitions and training runs as they happen, and replays of finished ones (ADR-078). Race (running mean
case composite per agent, the SNOWPACK incumbent as the bar to beat), heat strip (agents x cases), duel (the latest
case's observed pit beside the leader's and the incumbent's predictions; training/development truth only, as on the
Leaderboard) and, for training, the family tree with the best-per-round and gap lines. Every number is read from the
run's own files (its event feed, or its records for runs from before the feed)."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from snowagent.lab.benchmark.loader import find_case, read_manifest
from snowagent.lab.competition.truth import scoring_truth
from snowagent.lab.genome import default_genome
from snowagent.lab.schemas.genome import AgentFamily
from snowagent.lab.services.arena import (
    arena_runs,
    finished,
    is_live,
    load_feed,
    race_table,
    rounds_of,
    scored_frame,
)
from snowagent.lab.ui.app import lab_context, page_header
from snowagent.lab.ui.arena_plots import evolution_figure, heat_figure, race_figure, trend_figure
from snowagent.lab.ui.plots import prediction_frame, profile_figure

TRUTH_SPLITS = {"training", "development"}  # as on the Leaderboard: no validation/holdout/sealed truth in the browser
PLAY_FRAMES = 60  # a replay plays in about this many steps
POLL_S = 2.0

page_header(st, "Arena")
cfg, paths = lab_context(__file__)
mode = getattr(getattr(st.context, "theme", None), "type", None) or "light"
st.caption("Competitions and training runs as they happen, and replays of finished ones. The race shows each "
           "agent's mean case composite over the cases scored so far; the leaderboard composite also weighs "
           "robustness, so the final ranking is on the Leaderboard and Training pages. Every number comes from the "
           "run's own files.")

runs = arena_runs(paths)
if not runs:
    st.info("No competition or training run yet. Start one on the Leaderboard page (Run a competition) or the "
            "Training page (Start a training run); it appears here live.")
    st.stop()


def _name(i: int) -> str:
    r = runs[i]
    return ("● live · " if r["live"] else "") + f"{r['kind']} · {r['run_id']}"


i = st.sidebar.selectbox("Run", range(len(runs)), format_func=_name)
run = runs[i]
kind, run_id = run["kind"], run["run_id"]
ss = st.session_state
if ss.get("arena_run") != (kind, run_id):  # another run: start from its end, not playing
    ss["arena_run"] = (kind, run_id)
    ss["arena_feed"] = None
    ss["arena_play"] = False
    ss.pop("arena_pos", None)
live = run["live"]
if not live:
    c1, c2 = st.sidebar.columns(2)
    if c1.button("▶ Play", disabled=bool(ss.get("arena_play")), help="replay the run from the start"):
        ss["arena_play"] = True
        ss["arena_pos"] = 0
    if c2.button("⏸ Pause", disabled=not ss.get("arena_play")):
        ss["arena_play"] = False
inc_hash = default_genome(AgentFamily.snowpack, cfg.genome).genome_hash
every = POLL_S if live else (0.5 if ss.get("arena_play") else None)


def _prediction(feed, ev: dict) -> dict | None:
    if feed.kind == "competition":
        f = feed.dir / "cases" / f"{ev['case_id']}.json"
        try:
            return json.loads(f.read_text())["predictions"].get(ev["agent_id"])
        except (OSError, ValueError, KeyError):
            return None
    if not ev.get("key"):
        return None
    from snowagent.lab.training.cache import TrainingCache

    e = TrainingCache(paths.outputs / "cache").get(ev["key"])
    return e.get("prediction") if e else None


def _observed(case_id: str, case_set: str):
    dirs = find_case(paths, case_id, case_set)
    m = read_manifest(dirs[0]) if dirs else None
    if m is None or m.split.value not in TRUTH_SPLITS:
        return None, None
    tp = scoring_truth(dirs[0], m).truth_profile
    tl = pd.DataFrame([ly.model_dump(mode="json") for ly in tp.layers])
    if len(tl):
        tl["concern_basis_json"] = tl["concern_basis"].map(json.dumps)
    return tl, tp.snow_depth_m


def _profile(col, feed, ev: dict | None, title: str) -> None:
    pred = _prediction(feed, ev) if ev is not None else None
    if pred is None or pred.get("status") != "ok":
        col.info(f"{title}: no profile" + (f" ({ev.get('status')})" if ev else ""))
        return
    hs = pred["bulk_state"]["snow_depth_m"]
    col.plotly_chart(profile_figure(prediction_frame(pred), hs["p50"], f"{title} · HS {hs['p50'] * 100:.0f} cm · "
                                    f"composite {ev['composite']:.2f}"), width="stretch", theme="streamlit",
                     key=f"duel-{title}")


def _incumbent(df: pd.DataFrame, cur: pd.DataFrame, board: pd.DataFrame) -> dict | None:
    row = board[board["genome_hash"] == inc_hash] if "genome_hash" in board else board.iloc[0:0]
    if len(row) and pd.notna(row["mean"].iloc[0]):
        return {"value": float(row["mean"].iloc[0]), "label": row["label"].iloc[0]}
    if len(row) and int(row["skipped"].iloc[0]):
        return {"value": None, "label": row["label"].iloc[0], "note": "skipped: no SNOWPACK engine"}
    first = df[(df["round"] == 1) & (df.get("genome_hash") == inc_hash) & df["case_id"].isin(cur["case_id"])] \
        if "genome_hash" in df else df.iloc[0:0]
    if len(first) and first["composite"].notna().any():
        return {"value": float(first["composite"].mean()), "label": first["label"].iloc[0],
                "note": "round 1, same cases"}
    return None


def _evolution(feed, df: pd.DataFrame, cur_round: int) -> None:
    rounds = {r: v for r, v in rounds_of(feed).items() if r <= cur_round}
    if not rounds:
        st.info("No round has started yet.")
        return
    nodes, edges, where = [], [], {}
    for r in sorted(rounds):
        rec = rounds[r]
        com = rec["committed"]
        ranked = {x["genome_hash"]: x for x in (com or {}).get("ranked", [])}
        live_board = race_table(df[df["round"] == r]) if not com else None
        means = {} if live_board is None else dict(zip(live_board["genome_hash"], live_board["mean"], strict=True))
        screen = {a["agent_id"]: a for a in ((rec["screen"] or {}).get("agents") or [])}
        surv_next = set((com or {}).get("survivors_next") or [])
        rows = []
        for a in rec["agents"]:
            out = a.get("role") == "screened_out"
            comp = (ranked.get(a["genome_hash"], {}).get("composite") if com else means.get(a["genome_hash"]))
            if out:
                comp = screen.get(a["agent_id"], {}).get("sample_composite")
            changed = a.get("changed_genes") or {}
            genes = "; ".join(f"{k} {v[0]} → {v[1]}" for k, v in list(changed.items())[:6]) or "none"
            hover = (f"<b>{a['label']}</b> ({a['family']})<br>round {r} · {a.get('role')} · {a.get('operator')}"
                     f"<br>{'sample composite' if out else 'composite' if com else 'running mean'} "
                     + (f"{comp:.4f}" if isinstance(comp, int | float) and comp == comp else "-")
                     + f"<br>changed genes vs parent: {genes}")
            rows.append({"round": r, "genome_hash": a["genome_hash"], "family": a["family"], "composite": comp,
                         "faded": out, "survives": a["agent_id"] in surv_next, "hover": hover, "out": out,
                         "rank": ranked.get(a["genome_hash"], {}).get("rank")})
            for p in a.get("parents") or []:
                kind_e = "survivor" if a.get("role") == "survivor" else (a.get("operator") or "mutation")
                if p in where:
                    edges.append({"from": (where[p], p), "to": (r, a["genome_hash"]), "kind": kind_e})
            if a.get("role") == "survivor" and a["genome_hash"] in where:
                edges.append({"from": (where[a["genome_hash"]], a["genome_hash"]), "to": (r, a["genome_hash"]),
                              "kind": "survivor"})
        rows.sort(key=lambda x: (x["out"], x["rank"] if x["rank"] else 999,
                                 -(x["composite"] if isinstance(x["composite"], int | float) and
                                   x["composite"] == x["composite"] else -1)))
        for y, x in enumerate(rows, 1):
            x["y"] = y
        nodes += rows
        for x in rows:
            where[x["genome_hash"]] = r
    st.plotly_chart(evolution_figure(pd.DataFrame(nodes), edges, mode), width="stretch", theme="streamlit",
                    key="evo")
    st.caption("Edges: solid = mutation, dashed = crossover, dotted = kept unchanged as a survivor. A child's "
               "parents are the previous round's survivors (family-slot children: the family's best so far).")
    com = [rounds[r]["committed"] for r in sorted(rounds) if rounds[r]["committed"]]
    if com:
        c1, c2 = st.columns(2)
        c1.plotly_chart(trend_figure([c["round"] for c in com], [c["best"]["composite"] for c in com],
                                     "Best composite per round", "composite", mode), width="stretch",
                        theme="streamlit", key="best")
        g = [c for c in com if (c.get("gap") or {}).get("gap") is not None]
        if g:
            c2.plotly_chart(trend_figure([c["round"] for c in g], [c["gap"]["gap"] for c in g],
                                         "Memorising gap (other seasons minus monitor season)", "composite gap",
                                         mode, zero=True, flags=[bool(c["gap"].get("flag")) for c in g]),
                            width="stretch", theme="streamlit", key="gap")
        else:
            c2.caption("No gap yet (no monitor season cases in these rounds).")
        st.caption("The gap is a warning signal only; the promotion check (Training page) is the evidence.")


@st.fragment(run_every=every)
def arena() -> None:
    feed = load_feed(paths, kind, run_id, ss.get("arena_feed"))
    ss["arena_feed"] = feed
    if live and not is_live(kind, feed.dir):
        st.rerun(scope="app")  # it just finished: stop polling, offer the replay
    df = scored_frame(feed)
    n = len(df)
    plan = feed.plan
    if live:
        pos = n
    else:
        pos = ss.get("arena_pos", n)
        if ss.get("arena_play"):
            pos = min(n, pos + max(1, n // PLAY_FRAMES))
        ss["arena_pos"] = pos
        if ss.get("arena_play") and pos >= n:
            ss["arena_play"] = False
            st.rerun(scope="app")  # the replay reached the end: stop the timer
        if n:
            pos = st.slider("Replay position (agent-case results shown)", 0, n, key="arena_pos",
                            help="drag to any point of the run, or press Play in the sidebar")
    upto = df.iloc[:pos]
    cur_round = int(upto["round"].iloc[-1]) if len(upto) else 1
    cur = upto[upto["round"] == cur_round] if kind == "training" else upto
    board = race_table(cur)

    state = "live" if live else ("finished" if finished(feed) else "stopped")
    st.markdown(f"**{kind.capitalize()} `{run_id}`**" + (" · updating every few seconds" if live else "")
                + ("" if state != "stopped" else " · not finished (stopped or interrupted): resume it from its page"))
    m = st.columns(4)
    m[0].metric("State", state)
    m[1].metric("Results shown", f"{pos} of {n}")
    m[2].metric("Cases scored", cur["case_id"].nunique() if len(cur) else 0)
    if kind == "training":
        m[3].metric("Round", f"{cur_round} of {plan.get('rounds', '?')}")
    else:
        m[3].metric("Agents", board["agent_id"].nunique())
    if feed.rebuilt:
        st.caption("This run has no live feed (it ran before the Arena existed): its results are replayed from its "
                   "files" + (", case order within each round" if kind == "training" else
                              ", in the order the case records were written") + ".")
    if not n:
        st.info("No case scored yet." + (" It updates every few seconds." if live else ""))
        return

    tabs = st.tabs(["Race", "Heat strip", "Duel"] + (["Evolution"] if kind == "training" else []))
    with tabs[0]:
        inc = _incumbent(df, cur, board)
        title = f"Round {cur_round}: " if kind == "training" else ""
        st.plotly_chart(race_figure(board, inc, mode, title + f"{cur['case_id'].nunique()} cases scored"),
                        width="stretch", theme="streamlit", key="race")
        if inc is None:
            st.caption("No SNOWPACK incumbent result to compare with in this run.")
        elif inc.get("value") is None:
            st.caption(f"The incumbent {inc['label']} was skipped: no SNOWPACK engine on this machine.")
    with tabs[1]:
        st.plotly_chart(heat_figure(cur, board["agent_id"].tolist(), mode), width="stretch", theme="streamlit",
                        key="heat")
        st.caption("Darker = higher case composite (0-1). Hover a cell for the plot, case type, season, pit time and "
                   "component scores; blank = not scored yet or skipped.")
    with tabs[2]:
        viewable = cur[cur["split"].isin(TRUTH_SPLITS)] if "split" in cur else cur.iloc[0:0]
        if viewable.empty:
            st.info("The duel shows training and development cases only (their observed pits may be shown).")
        else:
            case_id = viewable["case_id"].iloc[-1]
            on_case = cur[cur["case_id"] == case_id]
            leader = board["agent_id"].iloc[0]
            ev = {r["agent_id"]: r for r in on_case.to_dict("records")}
            inc_ev = next((r for r in on_case.to_dict("records") if r.get("genome_hash") == inc_hash), None)
            if inc_ev is None and kind == "training" and "genome_hash" in df:
                first = df[(df["round"] == 1) & (df["genome_hash"] == inc_hash) & (df["case_id"] == case_id)]
                inc_ev = first.to_dict("records")[0] if len(first) else None
            st.markdown(f"Latest case **{case_id}** ({on_case['site_code'].iloc[0]}, {on_case['case_type'].iloc[0]}):"
                        f" the observed pit, the current leader and the SNOWPACK incumbent.")
            c1, c2, c3 = st.columns(3)
            tl, hs = _observed(case_id, plan.get("case_set", "all"))
            if tl is None:
                c1.info("The observed pit of this case is not shown in the browser.")
            else:
                c1.plotly_chart(profile_figure(tl, hs, f"observed pit · HS {(hs or 0) * 100:.0f} cm"),
                                width="stretch", theme="streamlit", key="duel-obs")
            leader_ev = ev.get(leader)
            _profile(c2, feed, leader_ev, f"leader {leader_ev['label']}" if leader_ev else "leader")
            _profile(c3, feed, inc_ev, "incumbent " + (inc_ev["label"] if inc_ev else "SNOWPACK"))
    if kind == "training":
        with tabs[3]:
            _evolution(feed, df.iloc[:pos], cur_round)


arena()
if live:
    st.caption(f"Live: updates every {POLL_S:.0f} s while the run is active.")
