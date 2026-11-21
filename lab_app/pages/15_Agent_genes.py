"""How agents change (ADR-133): the settings ("genes") of a training run's agents, round by round. What the run may
change, the score weights it ranked agents with, how far each round's best agent has moved from the standard
settings, one setting across every agent of every round, and one agent's full settings with its family tree. Works
while a run is going (the round being tested now comes from the run's event log) and for finished runs, including a
run copied from another computer (a cloud run's .tar.gz). Display only: it reads the run's files and changes
nothing."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from snowagent.lab.services import labels
from snowagent.lab.services.genomes import (
    HOW_MADE,
    ArchiveError,
    agent_index,
    best_agents,
    drift_table,
    family_tree,
    gene_info,
    import_run_archive,
    run_families,
    run_rounds,
    run_spec,
    shift,
    step_text,
    total_drift,
    value_history,
    weights_in_force,
)
from snowagent.lab.services.training import run_overview
from snowagent.lab.ui.app import lab_context, page_header
from snowagent.lab.ui.genes import UNITS, change_text
from snowagent.lab.ui.pages import link
from snowagent.lab.ui.plots import NEUTRAL, SERIES
from snowagent.lab.ui.training import no_runs, pick_run

LIVE_EVERY_S = 10
LOWER, MID, HIGHER = "#e34948", "#f0efec", "#2a78d6"  # diverging: lowered, standard, raised
HOW_COLOURS = {"start": "#9a9890", "small random change": "#2a78d6", "mix of two parents": "#eb6834",
               "mix of two parents, then a small change": "#1baf7a",
               "brought in from another run": "#6250d6"}

page_header(st, "How agents change",
            "What a training run changes in its agents' settings, round by round: how far the best agent has "
            "moved from the standard settings, which settings moved, and where each agent came from.",
            page="genes")
cfg, paths = lab_context(__file__)

with st.expander("Add a run trained on another computer (a cloud run's .tar.gz)"):
    st.caption("A run trained in the cloud is saved as one file, for example cloud1-process.tar.gz. Add it here "
               "to look at its agents on this computer. Only the run file is needed, not the one ending in "
               "-cache.tar.gz. Nothing else changes.")
    up = st.file_uploader("Run file", type=["gz", "tgz"], key="genes-archive")
    if up is not None and st.button("Add this run", key="genes-import"):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "run.tar.gz"
            f.write_bytes(up.getbuffer())
            try:
                new = import_run_archive(paths, f)
                st.session_state["lab-run"] = new
                st.success(f"Added the run {new}.")
            except ArchiveError as exc:
                st.error(str(exc))

run_id = pick_run(st, paths)
if run_id is None:
    no_runs(st)
    st.stop()
ov = run_overview(paths, run_id)
plan = ov["plan"] or {}
spec = run_spec(cfg.genome, plan)
running = ov["status"].get("state") == "running"
rounds = run_rounds(paths, run_id, spec, live=running)


@st.fragment(run_every=LIVE_EVERY_S if running else None)
def _watch() -> None:
    """While the run is going: redraw the page when a round starts or finishes."""
    if not running:
        return
    now = run_rounds(paths, run_id, spec, live=True)
    if [(r["round"], r["scored"]) for r in now] != [(r["round"], r["scored"]) for r in rounds]:
        st.rerun()
    st.caption(f"This run is going now: this page updates itself when a round starts or finishes (checked every "
               f"{LIVE_EVERY_S} seconds).")


_watch()
if not rounds:
    st.info("This run has no agents yet: they appear here when its first round starts.")
    st.stop()
families = run_families(rounds)


def _kind(f: str) -> str:
    key = labels.OWN_FAMILIES.get(f) or ("tuned" if f == "snowpack" else None)
    return labels.name(key, paths) if key else f


family = families[0] if len(families) == 1 else st.selectbox(
    "Kind of model", families, format_func=_kind, help="this run holds agents of more than one kind")
fam_rounds = [r | {"agents": [a for a in r["agents"] if a["family"] == family]} for r in rounds]
fam_rounds = [r for r in fam_rounds if r["agents"]]
info = gene_info(spec, family)
meta = info.set_index("gene").to_dict("index")
scored = [r for r in fam_rounds if r["scored"]]
live_round = next((r for r in fam_rounds if not r["scored"]), None)
evolution = labels.OWN_FAMILIES.get(family) or ("tuned" if family == "snowpack" else None)

# ------------------------------------------------------------------------------------------- what it evolves
st.subheader("What this run changes")
n_free = int(info["evolves"].sum())
st.markdown(f"Its agents are **{_kind(family)}** models"
            + (f" ({labels.EVOLUTIONS[evolution][1]})" if evolution in labels.EVOLUTIONS else "")
            + f". Each agent is a list of **{len(info)} settings** (its genome). This run may change "
            + (f"**{n_free} of them**, the short list that mattered most in a test before any training; the rest "
               "stay as they started." if plan.get("trimmed_genes") else f"**{n_free} of them**.")
            + " Every round keeps its best agents unchanged and makes new ones from them: a small random change to "
              "a few settings, or a mix of two good agents.")
if live_round:
    st.info(f"Round {live_round['round']} is being tested now: {len(live_round['agents'])} agents. Their scores "
            "come when the round finishes; their settings are shown below already.", icon="🟢")

st.markdown("**How agents are scored in this run**")
w = weights_in_force(plan, cfg.scoring_weights.model_dump())
c1, c2 = st.columns([2, 3])
with c1:
    st.dataframe(w.rename(columns={"this run": "this run, %", "today": "today, %"}), hide_index=True,
                 width="stretch")
with c2:
    st.caption("An agent's score (0 to 1) adds up how well it matched the pits on each part, with these weights. "
               "Training never changes them: they are fixed when a run starts. They are not settings of the agents.")
    if w["this run"].notna().any() and (w["this run"] != w["today"]).any():
        st.warning("This run was scored with older weights than the lab uses today (the weights changed on "
                   "19 Nov 2026 toward layering and weak layers). Compare it with newer runs with care.", icon="⚖️")

# ------------------------------------------------------------------------------------------- drift
if scored:
    st.subheader("How far the best agent has moved from the standard settings")
    td = total_drift(fam_rounds, info)
    fig = go.Figure(go.Scatter(x=td["round"], y=td["drift"], mode="lines+markers", line={"color": SERIES, "width": 2},
                               marker={"size": 8}, customdata=td[["name", "settings moved"]],
                               hovertemplate="round %{x}<br>%{customdata[0]}<br>moved %{y:.2f} in all, "
                               "%{customdata[1]} settings changed<extra></extra>"))
    fig.update_layout(title="Round's best agent: total distance from the standard settings", height=280,
                      margin={"l": 10, "r": 10, "t": 40, "b": 10}, xaxis_title="round",
                      yaxis_title="distance", showlegend=False)
    fig.update_xaxes(dtick=1)
    st.plotly_chart(fig, width="stretch", theme="streamlit")
    st.caption("Distance adds up how far each setting has moved, counting a setting moved across its whole allowed "
               "range as 1 (and a changed choice or switch as 1). Standard settings are 0. The run takes a small "
               "penalty for distance when it chooses agents, so a change has to earn its place."
               if plan.get("drift_penalty") else
               "Distance adds up how far each setting has moved, counting a setting moved across its whole allowed "
               "range as 1 (and a changed choice or switch as 1). Standard settings are 0.")

    dt = drift_table(fam_rounds, info)
    if dt.empty:
        st.info("The best agent of every round so far has the standard settings.")
    else:
        dt = dt.loc[dt.abs().max(axis=1).sort_values(ascending=False).index]
        last = best_agents(fam_rounds)[-1]
        by_name = info.set_index("setting")
        hover = [[f"{s}<br>round {c}: {v:+.0%} of its range" for c, v in zip(dt.columns, row, strict=True)]
                 for s, row in zip(dt.index, dt.values, strict=True)]
        nums = [meta[g]["kind"] != "choice" for g in info.set_index("setting").loc[dt.index, "gene"]]
        num_vals = dt.values[nums] if any(nums) else dt.values
        top = float(min(1.0, max(0.2, pd.Series(abs(num_vals).ravel()).quantile(0.95))))
        fig = go.Figure(go.Heatmap(z=dt.values, x=[str(c) for c in dt.columns], y=list(dt.index), zmid=0, zmin=-top,
                                   zmax=top, colorscale=[[0, LOWER], [0.5, MID], [1, HIGHER]], xgap=2, ygap=2,
                                   text=hover, hoverinfo="text",
                                   colorbar={"title": "moved", "tickvals": [-top, 0, top],
                                             "ticktext": [f"lowered {top:.0%}+ of its range", "standard",
                                                          f"raised {top:.0%}+ of its range"]}))
        fig.update_layout(title="Which settings the round's best agent has moved", height=120 + 26 * len(dt),
                          margin={"l": 10, "r": 10, "t": 40, "b": 10}, xaxis_title="round")
        fig.update_yaxes(autorange="reversed")
        fig.update_xaxes(type="category")
        st.plotly_chart(fig, width="stretch", theme="streamlit")
        st.caption("Each row is a setting the best agent has moved in at least one round; blue is raised, red is "
                   "lowered, grey is the standard value; the colours reach full strength at the share of the range on the "
                   "key. A switch or choice that changed shows as full blue. Hover for the amount.")
        latest = pd.DataFrame({"setting": dt.index, "change now": [
            change_text(g, meta[g]["standard"], last["genes"].get(g, meta[g]["standard"]), meta[g]["unit"],
                        meta[g]["what it does"]) for g in by_name.loc[dt.index, "gene"]],
                               "what it does": by_name.loc[dt.index, "what it does"].tolist()})
        with st.expander(f"Round {last['round']}'s best agent ({last['name']}): its changes in words"):
            st.dataframe(latest, hide_index=True, width="stretch")

# ------------------------------------------------------------------------------------------- one setting
st.subheader("One setting, round by round")
moving = info[info["evolves"]]
order = moving["gene"].tolist()
if scored:
    last = best_agents(fam_rounds)[-1]
    order.sort(key=lambda g: -abs(shift(last["genes"].get(g, meta[g]["standard"]), meta[g])))
if not order:
    st.info("This run changes none of its settings.")
else:
    gene = st.selectbox("Setting", order, format_func=lambda g: f"{meta[g]['setting']} ({meta[g]['topic']})",
                        key=f"genes-gene-{run_id}-{family}", help="the settings the best agent has moved most come first")
    m = meta[gene]
    vh = value_history(fam_rounds, gene)
    rng = (f"allowed {m['min']:.3g} to {m['max']:.3g}" if m["kind"] != "choice" else
           f"choices: {', '.join(m['choices'] or [])}")
    st.caption(f"**What it does:** {m['what it does']}. Standard value {m['standard']}"
               + UNITS.get(m["unit"], f" {m['unit']}") + f"; {rng}.")
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=vh["round"], y=vh["value"], mode="markers", name="every agent",
                             marker={"size": 8, "color": NEUTRAL, "opacity": 0.6,
                                     "symbol": ["circle" if s else "circle-open" for s in vh["scored"]]},
                             customdata=vh[["name", "role", "operator"]].fillna(""),
                             hovertemplate="round %{x}<br>%{customdata[0]}<br>%{y}<extra></extra>"))
    b = vh[vh["best"]]
    if len(b):
        fig.add_trace(go.Scatter(x=b["round"], y=b["value"], mode="lines+markers", name="round's best agent",
                                 line={"color": SERIES, "width": 2}, marker={"size": 9},
                                 customdata=b[["name"]],
                                 hovertemplate="round %{x}<br>best: %{customdata[0]}<br>%{y}<extra></extra>"))
    if m["kind"] != "choice":
        fig.add_hline(y=m["standard"], line={"color": NEUTRAL, "width": 1, "dash": "dash"},
                      annotation_text="standard", annotation_position="bottom right")
        fig.update_yaxes(range=[m["min"] - 0.02 * (m["max"] - m["min"]), m["max"] + 0.02 * (m["max"] - m["min"])])
    fig.update_layout(title=f"{m['setting']}: every agent of every round", height=340,
                      margin={"l": 10, "r": 10, "t": 40, "b": 10}, xaxis_title="round", yaxis_title=m["setting"],
                      legend={"orientation": "h", "y": -0.25})
    fig.update_xaxes(dtick=1)
    st.plotly_chart(fig, width="stretch", theme="streamlit")
    st.caption("Grey dots are all the agents of a round (open dots: a round still being tested); the blue line is "
               "each round's best agent. The y-axis is the setting's whole allowed range. Dots that bunch together "
               "mean the run has settled on a value.")

# ------------------------------------------------------------------------------------------- one agent
st.subheader("One agent: its settings and its family tree")
c1, c2 = st.columns([1, 3])
rnums = [r["round"] for r in fam_rounds]
with c1:
    rsel = st.selectbox("Round", rnums, index=len(scored) - 1 if scored else len(rnums) - 1, key=f"genes-round-{run_id}-{family}")
rd = next(r for r in fam_rounds if r["round"] == rsel)
with c2:
    pick = st.selectbox("Agent", [a["hash"] for a in rd["agents"]], key=f"genes-agent-{run_id}-{rsel}",
                        format_func=lambda h: next(f"{a['name']} ({a['label']})"
                                                   + (f", rank {a['rank']}" if a["rank"] else "")
                                                   for a in rd["agents"] if a["hash"] == h),
                        help="in the round's leaderboard order: the first is the round's best agent")
agent = next(a for a in rd["agents"] if a["hash"] == pick)
only = st.toggle("Only the settings it has changed", value=True, key="genes-only")
rows = []
for g, mm in meta.items():
    v = agent["genes"].get(g, mm["standard"])
    if only and abs(shift(v, mm)) < 1e-12:
        continue
    rows.append({"setting": mm["setting"], "topic": mm["topic"], "standard": mm["standard"], "this agent": v,
                 "change": "same" if v == mm["standard"] else change_text(g, mm["standard"], v, mm["unit"],
                                                                         mm["what it does"]),
                 "allowed": (f"{mm['min']:.3g} to {mm['max']:.3g}" if mm["kind"] != "choice" else
                             " / ".join(mm["choices"] or [])),
                 "can change": "yes" if mm["evolves"] else "no",
                 "what it does": mm["what it does"]})
if rows:
    tbl = pd.DataFrame(rows)
    tbl[["standard", "this agent"]] = tbl[["standard", "this agent"]].map(
        lambda v: f"{v:.3g}" if isinstance(v, float) else str(v))
    st.dataframe(tbl, hide_index=True, width="stretch")
else:
    st.caption("All its settings are at the standard values.")

idx = agent_index(fam_rounds)
tree = family_tree(fam_rounds, pick)
inside = [t for t in tree if not t["outside"]]
if len(inside) > 1:
    lanes: dict[int, int] = {}
    pos = {}
    for t in sorted(inside, key=lambda t: t["round"]):
        lanes[t["round"]] = lanes.get(t["round"], 0) + 1
        pos[t["hash"]] = (t["round"], lanes[t["round"]])
    fig = go.Figure()
    for t in inside:
        for p in t.get("parents") or []:
            if p in pos:
                fig.add_trace(go.Scatter(x=[pos[p][0], pos[t["hash"]][0]], y=[pos[p][1], pos[t["hash"]][1]],
                                         mode="lines", line={"color": NEUTRAL, "width": 1}, hoverinfo="skip",
                                         showlegend=False))
    for how, colour in HOW_COLOURS.items():
        nodes = [t for t in inside if HOW_MADE.get(t.get("operator") or "initial", "start") == how]
        if not nodes:
            continue
        fig.add_trace(go.Scatter(x=[pos[t["hash"]][0] for t in nodes], y=[pos[t["hash"]][1] for t in nodes],
                                 mode="markers", name=how,
                                 marker={"size": 13, "color": colour, "line": {"color": "#fcfcfb", "width": 2}},
                                 text=[f"<b>{t['name']}</b><br>round {t['round']}<br>{step_text(t, info, idx)}"
                                       for t in nodes], hoverinfo="text"))
    fig.update_layout(title=f"Where {agent['name']} came from", height=300,
                      margin={"l": 10, "r": 10, "t": 40, "b": 10}, xaxis_title="round it was made in",
                      legend={"orientation": "h", "y": -0.3})
    fig.update_xaxes(dtick=1)
    fig.update_yaxes(visible=False)
    st.plotly_chart(fig, width="stretch", theme="streamlit")
    st.caption("Each dot is an ancestor, placed in the round it was made in; lines join a parent to its child. "
               "Hover a dot for how it was made and what that step changed.")
with st.expander("Its family tree in words"):
    for t in tree:
        if t["outside"]:
            st.markdown("- an agent from outside this run (brought in from another run)")
        else:
            st.markdown(f"- **{t['name']}**, round {t['round']}: {step_text(t, info, idx)}")

st.markdown("**Next**")
n1, n2, _ = st.columns(3)
with n1:
    link(st, "results", "See the agents' scores")
with n2:
    link(st, "watch", "Watch the run")
