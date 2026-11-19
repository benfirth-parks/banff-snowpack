"""Blind test and site (ADR-119; before it the lower part of the Training page): what can be done with an agent
you believe in. Freeze it (or a standard model, ADR-105) for this winter's blind test (ADR-086: permanent, a few a
winter, scored daily on pits dug after the freeze), or show it on the public site as an experimental extra
(ADR-084). Both use this computer's GitHub sign-in. The agent is the one chosen on Run results, or chosen here."""

from __future__ import annotations

import os
from datetime import UTC, datetime
from pathlib import Path

import streamlit as st

from snowagent.lab.genome import NAMED_MODELS, named_genome
from snowagent.lab.schemas.genome import AgentFamily
from snowagent.lab.services import labels
from snowagent.lab.services.blind_test import (
    MAX_ENTRIES,
    entry_record,
    freeze,
    frozen,
    is_frozen,
    outside_line,
    result_line,
    standard_entry_record,
)
from snowagent.lab.services.blind_test import results as blind_results
from snowagent.lab.services.names import STANDARD_NAMES, display
from snowagent.lab.services.site_send import SendError, agent_record, on_site, remove, send
from snowagent.lab.services.training import round_table, run_overview
from snowagent.lab.settings import season_key
from snowagent.lab.ui.app import lab_context, page_header, repo_root
from snowagent.lab.ui.training import AGENT_KEY, pick_run, run_day
from snowagent.ops.site_agents import MAX_AGENTS

page_header(st, "Blind test and site",
            f"Two things you can do with an agent you believe in: freeze it for {labels.COMPETITIONS['llyk'][1]} "
            f"({labels.name('llyk')}), or show it on the public site as an experimental extra. Standard SNOWPACK "
            "stays the site's main forecast.",
            page="blind")
cfg, paths = lab_context(__file__)
site_repo = Path(os.environ.get("SNOWAGENT_SITE_REPO") or repo_root(__file__))  # the checkout that pushes


def _local(utc_text: str) -> str:
    """'25 Oct 2026 13:14' in this computer's time, from a stored UTC time."""
    try:
        t = datetime.fromisoformat(utc_text).astimezone()
    except (TypeError, ValueError):
        return str(utc_text)[:16].replace("T", " ") + " UTC"
    return f"{t.day} {t:%b %Y %H:%M}"


# ------------------------------------------------------------------------------------------- the agent
st.subheader("1. Choose the agent")
best, run_id, r = None, pick_run(st, paths), None
if run_id is None:
    st.caption("No training run yet, so there is no evolved agent to choose. A standard model can still be frozen "
               "for the blind test, below.")
else:
    trace = run_overview(paths, run_id)["rounds"]
    rounds_done = trace["round"].tolist() if len(trace) else []
    if not rounds_done:
        st.caption("This run has no finished round yet: its agents can be chosen when round 1 is done.")
    else:
        kept = st.session_state.get(AGENT_KEY) or (None, None, None)
        kept = kept if kept[0] == run_id and kept[1] in rounds_done else (run_id, rounds_done[-1], None)
        c1, c2 = st.columns([1, 3])
        r = c1.selectbox("Round", rounds_done, index=rounds_done.index(kept[1]),
                         help="the last finished round by default")
        table = round_table(paths, run_id, r)
        agents = table["agent"].tolist()
        pick = c2.selectbox("Agent", agents, index=agents.index(kept[2]) if kept[2] in agents else 0,
                            format_func=lambda a: display(table.set_index("agent").at[a, "genome_hash"], a),
                            help="best first: the first is the round's best agent")
        best = table[table["agent"] == pick].iloc[0]
        st.caption(f"Chosen: **{best['name']}** ({best['family']}), rank {int(best['rank'])} in round {r} of "
                   f"{run_day(run_id)}.")

# ------------------------------------------------------------------------------------------- blind test
winter = season_key(datetime.now(UTC), cfg.season_start)
st.subheader(f"2. {labels.titled('llyk')}, winter {winter[:5]}{winter[7:]}")
st.caption("Freezes this agent now, before this winter's pits are dug, and scores it on them as they arrive: a test "
           "nothing can leak into, because the answers do not exist yet. Only pits dug after the freeze count. The "
           f"freeze is recorded on GitHub and can never be changed or undone; at most {MAX_ENTRIES} agents a "
           "winter, so freeze the ones you believe in. Standard SNOWPACK is the comparison.")
if msg := st.session_state.pop("blind-flash", None):
    st.success(msg)


@st.cache_data(ttl=120, show_spinner="Checking the blind test…")
def _frozen(season: str) -> list[dict]:
    return frozen(site_repo, season)


@st.cache_data(ttl=600, show_spinner=False)
def _blind_results(season: str) -> dict:
    try:
        return {x.get("id"): x for x in (blind_results(site_repo, season) or {}).get("entries", []) if isinstance(x, dict)}
    except (SendError, OSError):
        return {}


@st.cache_data(ttl=600, show_spinner=False)
def _hrdps_result(season: str) -> dict | None:
    try:
        x = (blind_results(site_repo, season) or {}).get("hrdps")
    except (SendError, OSError):
        return None
    return x if isinstance(x, dict) else None


@st.cache_data(ttl=600, show_spinner=False)
def _outside_results(season: str) -> list[dict]:
    try:
        return [x for x in (blind_results(site_repo, season) or {}).get("outside", []) if isinstance(x, dict)]
    except (SendError, OSError):
        return []


try:
    entries = _frozen(winter)
except (SendError, OSError) as exc:
    entries = None
    st.warning(f"Could not check the blind test: {exc}")
if entries is not None:
    scores = _blind_results(winter) if entries else {}
    st.markdown(f"**Frozen so far: {len(entries)} of {MAX_ENTRIES}**")
    for e in entries:
        src = (f"from {run_day(e['run_id'])}, round {e.get('round')}, rank {e.get('rank')}"
               if e.get("run_id") else "standard model")
        st.markdown(f"**{e['name']}** {src} · frozen {_local(e['frozen_utc'])} · "
                    f"{result_line(scores.get(e['id']) or {})}")
    if entries:
        st.caption("The daily update scores the frozen agents on each new pit (next-pit and 72-hour forecast cases) "
                   "with the scoring of the day they were frozen; these results update once a day.")
    outside = _outside_results(winter) if entries else []
    for e in outside:  # an outside SNOWPACK forecast saved daily (ADR-112): not ours, not frozen, not on the site
        st.markdown(f"**{e.get('name')}** (outside forecast, saved daily from {str(e.get('from_utc'))[:10]}) · "
                    f"{outside_line(e)}")
    hr = _hrdps_result(winter) if entries else None
    if hr:  # ADR-116: standard SNOWPACK with HRDPS forecast weather, 72-hour cases only; the frozen agents keep GFS
        st.markdown(f"**{hr.get('name')}** (72-hour forecast cases, HRDPS for the first 2 days, then GFS) · "
                    f"{outside_line(hr)}")
    if outside:
        st.caption("SnowpackBC (snowpackbc.com) runs SNOWPACK for Banff with the AWSOME framework. Its forecasts "
                   "are saved each day and scored as issued: only a forecast saved before the case's start counts. "
                   "It does not use one of the five freezes and is never shown on the public site.")
    if not entries:
        st.caption("No agent frozen for this winter yet.")
    if best is None:
        pass
    elif is_frozen(best["agent_id"], entries):
        st.caption(f"{best['name']} is in this winter's blind test.")
    elif len(entries) >= MAX_ENTRIES:
        st.info(f"This winter's blind test is full ({MAX_ENTRIES} agents).")
    else:
        sure = st.checkbox(f"I want to freeze {best['name']}. I know a freeze is permanent and uses one of this "
                           f"winter's {MAX_ENTRIES} places.", key=f"sure-{best['agent_id']}")
        if st.button(f"Freeze {best['name']} for the blind test", disabled=not sure):
            try:
                with st.spinner(f"Freezing {best['name']}…"):
                    freeze(site_repo, entry_record(paths, run_id, r, best["agent_id"], site_repo, cfg.season_start))
                _frozen.clear()
                st.session_state["blind-flash"] = (f"{best['name']} is frozen for the {winter} blind test. It will "
                                                   "be scored on every pit dug from now on.")
                st.rerun()
            except (SendError, OSError, ValueError) as exc:
                st.error(str(exc))
    # a standard model (ADR-105): its clean test when its rule was found on the lab's own cases
    std_names = {m: STANDARD_NAMES.get(m, m) for m in NAMED_MODELS}
    with st.expander("Freeze a standard model instead"):
        std = st.selectbox("Or a standard model", list(NAMED_MODELS), index=len(NAMED_MODELS) - 1,
                           format_func=std_names.get,
                           help="Standard SNOWPACK's variants (Leaderboard › Run a competition). Standard SNOWPACK "
                                "itself is the comparison, so it is not offered.")
        std_id = named_genome(std, cfg.genome).agent_id
        if is_frozen(std_id, entries):
            st.caption(f"{std_names[std]} is in this winter's blind test.")
        elif len(entries) < MAX_ENTRIES:
            sure_std = st.checkbox(f"I want to freeze {std_names[std]}. I know a freeze is permanent and uses one "
                                   f"of this winter's {MAX_ENTRIES} places.", key=f"sure-std-{std}")
            if st.button(f"Freeze {std_names[std]} for the blind test", key="freeze-std", disabled=not sure_std):
                try:
                    with st.spinner(f"Freezing {std_names[std]}…"):
                        freeze(site_repo, standard_entry_record(std, site_repo, cfg.season_start, spec=cfg.genome))
                    _frozen.clear()
                    st.session_state["blind-flash"] = (f"{std_names[std]} is frozen for the {winter} blind test. It "
                                                       "will be scored on every pit dug from now on.")
                    st.rerun()
                except (SendError, OSError, ValueError) as exc:
                    st.error(str(exc))

# ------------------------------------------------------------------------------------------- send to site
st.subheader("3. Show it on the public site (experimental)")
st.caption(f"Sends this agent to the public site as an extra choice beside standard SNOWPACK, labelled experimental "
           f"and not validated. The next daily update runs it for this winter at all three plots. At most "
           f"{MAX_AGENTS} agents at a time; standard SNOWPACK stays the site's default. Unlike a freeze, this can be "
           "undone: Remove takes it off again.")
if msg := st.session_state.pop("site-flash", None):
    st.success(msg)


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
            c1.markdown(f"**{a['name']}** from {run_day(a.get('run_id'))}, round {a.get('round')}, rank "
                        f"{a.get('rank')}"
                        + (f"; locked test winters {lk:.3f}" if lk is not None else "")
                        + f" · sent {_local(a['added_utc'])}")
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
    if best is None:
        pass
    elif best["family"] != AgentFamily.snowpack.value:
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
