"""Snowpack Agent Lab: overview (the app's first page): what is ready, what is running, the best agent so far, set up
data, and the data coverage per site. The "Set up data" panel runs `lab prepare`, `lab init` and `lab import` as a
background job (ADR-077)."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from snowagent.lab.competition.scoring import SCORING_VERSION
from snowagent.lab.services.data import coverage, data_status, latest_runs, load_profiles
from snowagent.lab.services.jobs import ACTIVE, KINDS, JobBusy, job_info, latest_job, list_jobs
from snowagent.lab.services.training import best_so_far, run_overview, running_training, time_left
from snowagent.lab.services.workflow import setup_estimate, setup_readiness, start_setup
from snowagent.lab.ui.app import (
    config_path,
    empty_state,
    finish_text,
    lab_context,
    page_header,
    repo_root,
)
from snowagent.lab.ui.jobs import job_block

FEW_PITS = 100  # fewer unique usable pits than this: scores at the site will be noisy

page_header(st, "Snowpack Agent Lab")
cfg, paths = lab_context(__file__)

st.markdown(
    "Agents evolve to predict the layered snowpack later observed in the study-plot pits, from the weather before "
    "each pit. SNOWPACK, as the site runs it, is the agent to beat; layers made by other agents are research "
    f"entries, never site output. Times are shown in {cfg.display_timezone} time.")

status = data_status(paths)
if not (status["profiles"] or status["weather"]):
    empty_state(st, paths)


@st.cache_data(ttl=300, show_spinner=False)
def engine_found() -> str | None:
    from snowagent.engine.snowpack import find_engine
    from snowagent.errors import SnowAgentError

    try:
        return find_engine().binary
    except SnowAgentError:
        return None


# ------------------------------------------------------------------------------------------- at a glance
built = (paths.benchmark / "all").is_dir() and any((paths.benchmark / "all").glob("*/*/manifest.json"))
engine = engine_found()
ready_items = [("Pits imported", status["profiles"], "Set up data, below"),
               ("Weather imported", status["weather"], "Set up data, below"),
               ("SNOWPACK engine", engine is not None, "bash scripts/setup_env.sh builds it"),
               ("Benchmark cases", built, "Data › Benchmark cases, Build")]
for col, (label, ok, how) in zip(st.columns(len(ready_items)), ready_items, strict=True):
    col.markdown(f"{'✅' if ok else '⬜'} **{label}**")
    col.caption("ready" if ok else f"missing: {how}")

live = running_training(paths)
if live:
    ov = run_overview(paths, live)
    stt = ov["status"]
    st.info(f"**Training `{live}` is running**: round {stt.get('round', 0)} of {ov['plan'].get('rounds', '?')}, "
            f"{finish_text(time_left(ov))}. Follow it under Evolve agents › Training.", icon="🧬")
for job_id in list_jobs(paths)[:20]:
    info = job_info(paths, job_id)
    if info["state"] in ACTIVE and info.get("kind") != "train":
        st.info(f"**{KINDS.get(info.get('kind'), 'Job')} is running**: {info.get('title', job_id)}. "
                "Follow it under Background › Jobs.", icon="⏳")
best = best_so_far(paths, SCORING_VERSION)
if best:
    gain = best["composite"] - best["round1"]
    st.success(f"**Best agent so far**: {best['label']} ({best['family']}), score {best['composite']:.4f} in "
               f"round {best['round']} of `{best['run_id']}`"
               + (f", {gain:+.4f} on that run's round 1." if best["round"] > 1 else "."), icon="🏆")
elif built:
    st.caption("No training round finished yet: start one under Evolve agents › Training.")


def _min(m: float) -> str:
    return f"{m / 60:.1f} h" if m >= 90 else f"{max(1, round(m))} min"


# ------------------------------------------------------------------------------------------- set up data
root = repo_root(__file__)
setup_job = latest_job(paths, "setup")
with st.expander("Set up data", expanded=not (status["profiles"] and status["weather"])
                 or bool(setup_job and setup_job["state"] != "finished")):
    st.caption("Runs, in order, `snowagent lab prepare` (station files, observed profiles and the ERA5 months, from "
               "the project's own sources), `snowagent lab init` and `snowagent lab import`, as a background job. "
               "Nothing that exists is overwritten; run it again to resume or to add new ERA5 months.")
    ready = setup_readiness(paths, cfg, root)
    tick = {True: "ready", False: "missing"}
    st.dataframe(pd.DataFrame([
        {"input": "station files (data/raw/fts360)", "state": tick[ready["station_files"]], "made by": "prepare"},
        {"input": "observed profiles", "state": tick[ready["observed_profiles"]], "made by": "prepare"},
        {"input": "ERA5 months", "state": f"{ready['era5_cached']} of {ready['era5_months']}"
         if ready["era5_needed"] else "not used", "made by": "prepare"},
        {"input": f"lab tables in {paths.root}", "state": tick[ready["lab_initialised"]], "made by": "init"},
        {"input": "profiles and weather imported", "state": tick[ready["imported"]], "made by": "import"}]),
        hide_index=True)
    c1, c2 = st.columns(2)
    era5 = c1.checkbox("Fetch ERA5 months", True, help="fills station gaps (wind, radiation, pressure, "
                       "precipitation) as the published runs do; off: minutes instead of hours, but the scores differ")
    era5_workers = c2.number_input("ERA5 downloads at once", 1, 16, 4)
    est = setup_estimate(ready, era5, int(era5_workers))
    st.caption(f"Estimate: prepare about {_min(est['prepare_min'][0])}-{_min(est['prepare_min'][1])} "
               f"({est['era5_months_todo']} ERA5 months to fetch, network-bound), init seconds, import about 1 min; "
               f"total about {_min(est['total_min'][0])}-{_min(est['total_min'][1])}. That is the worst case: "
               "prepare first copies the ready-made ERA5 months from the repository's bundle branch (ADR-079, one "
               "download of about 0.2 GB, minutes), and only the months it lacks come from the slow mirror.")
    running = bool(setup_job and setup_job["state"] in ACTIVE)
    if st.button("Run set-up (prepare, init, import)", disabled=running, type="primary"):
        try:
            setup_job = start_setup(paths, config_path(__file__), root, era5=era5, workers=int(era5_workers))
            st.success(f"Started job `{setup_job['job_id']}`. Press Refresh to follow it.")
        except JobBusy as exc:
            st.error(str(exc))
    if setup_job:
        job_block(st, paths, setup_job, key="setup")

with st.expander("Data coverage, configuration and run history", expanded=not built):
    cov = coverage(paths, cfg)
    prof = load_profiles(paths)
    st.markdown("#### By site")
    cols = st.columns(len(cfg.sites))
    for col, (code, site) in zip(cols, cfg.sites.items(), strict=True):
        with col:
            st.markdown(f"**{site.display_name}** ({code.value})")
            st.caption(f"{site.latitude:.4f}, {site.longitude:.4f} · {site.elevation_m:.0f} m · plot `{site.plot_id}`")
            p = prof[prof["site_code"] == code.value] if len(prof) else prof
            w = cov["weather"][cov["weather"]["site_code"] == code.value]
            n_unique = int(p["unique_usable"].sum()) if len(p) else 0
            st.metric("unique usable pits", n_unique)
            st.metric("weather hours", f"{int(w['hours'].sum()):,}" if len(w) else "0")
            if len(p):
                st.caption(f"pits {p['observed_at'].min():%Y-%m-%d} to {p['observed_at'].max():%Y-%m-%d}; "
                           f"{int((p['review_reasons_json'] != '[]').sum())} on the owner's review list (ADR-050)")
            if status["profiles"] and not len(p):
                st.warning("No profiles imported for this site.")
            elif 0 < n_unique < FEW_PITS:
                st.warning(f"Only {n_unique} unique usable pits: scores at this site will be noisy; case counts are "
                           "shown beside every score.")
            if status["weather"] and not len(w):
                st.warning("No station weather imported for this site.")

    if status["profiles"] or status["weather"]:
        a = cfg.benchmark.availability
        st.warning("No source records when a pit or value was published, so benchmark cases assume a delay after it was "
                   f"observed: pits {a.profile_delay_h:g} h"
                   + (" (provisional, owner to confirm)" if a.profile_delay_provisional else "")
                   + f", station weather {a.weather_latency_h:g} h, ERA5 fills {a.era5_latency_h:g} h, archived GFS "
                   f"{a.gfs_latency_h:g} h after issue. Details on the Benchmark Cases page.", icon="🕒")

    st.markdown("#### Profiles per season")
    if len(cov["profiles"]):
        pv = cov["profiles"].pivot_table(index="season", columns="site_code", values="unique_usable", aggfunc="sum",
                                         fill_value=0)
        st.caption("Unique usable pits (duplicates and unusable records excluded) per season, season starting 15 Sep.")
        st.dataframe(pv.sort_index(ascending=False), width="stretch")
    else:
        st.caption("No profiles imported.")

    st.markdown("#### Weather coverage per season")
    if len(cov["weather"]):
        st.caption("Hours in the table and the share (%) with a QC-ok value. Wind and radiation are not measured at "
                   "the plots (wind from the configured nearby station; radiation absent).")
        st.dataframe(cov["weather"].sort_values(["site_code", "season"], ascending=[True, False]),
                     width="stretch", hide_index=True)
    else:
        st.caption("No weather imported.")

    st.markdown("#### Configuration")
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Scoring weights** (frozen into every scored run)")
        st.dataframe(pd.DataFrame([cfg.scoring_weights.model_dump()]).T.rename(columns={0: "weight"}),
                     width="stretch")
    with c2:
        st.markdown(f"**Season splits** (mode `{cfg.splits.mode.value}`)")
        if cfg.splits.is_empty():
            st.info("No seasons assigned for this mode yet (config/lab.yaml `splits`).")
        elif cfg.splits.warn_provisional():
            st.warning("Provisional split, owner to confirm: the recommended seasons in config/lab.yaml are used until "
                       "the owner confirms them.")
        if cfg.splits.mode.value == "loso" and not cfg.splits.loso_holdout:
            st.json({"all_seasons": cfg.splits.all_seasons, "holdout": "named at build time (--holdout)"})
        else:
            st.json(cfg.splits.mode_seasons())

    st.markdown("#### Latest runs")
    runs = latest_runs(paths, 10)
    if runs:
        st.dataframe(pd.DataFrame([{"run_id": r.run_id, "kind": r.kind.value, "status": r.status,
                                    "created (UTC)": r.created_at.isoformat(timespec="seconds"),
                                    "data hash": r.data_hash[:12], "config hash": r.config_hash[:12],
                                    "software": r.software_version, "counts": json.dumps(r.counts)[:120]}
                                   for r in runs]), width="stretch", hide_index=True)
    else:
        st.caption("No runs recorded.")
