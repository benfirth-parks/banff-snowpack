"""Snowpack Agent Lab: home page (purpose, disclaimer, set up data, data coverage per site, latest runs). The
"Set up data" panel runs `lab prepare`, `lab init` and `lab import` as a background job (ADR-077)."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from snowagent.lab.services.data import coverage, data_status, latest_runs, load_profiles
from snowagent.lab.services.jobs import ACTIVE, JobBusy, latest_job
from snowagent.lab.services.workflow import setup_estimate, setup_readiness, start_setup
from snowagent.lab.ui.app import config_path, empty_state, lab_context, page_header, repo_root
from snowagent.lab.ui.jobs import job_block

FEW_PITS = 100  # fewer unique usable pits than this: scores at the site will be noisy

page_header(st, "Snowpack Agent Lab")
cfg, paths = lab_context(__file__)

st.markdown(
    "A local research benchmark: agents predict the observed snow pit at a study plot from the data available at a "
    "cut-off, and are scored against pits they were not shown. SNOWPACK, as the site runs it, is the incumbent; "
    "layers made by other agents are benchmark entries, never site output. Data are stored in UTC and shown in "
    f"{cfg.display_timezone} time.")

status = data_status(paths)
if not (status["profiles"] or status["weather"]):
    empty_state(st, paths)


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
               f"total about {_min(est['total_min'][0])}-{_min(est['total_min'][1])}.")
    running = bool(setup_job and setup_job["state"] in ACTIVE)
    if st.button("Run set-up (prepare, init, import)", disabled=running, type="primary"):
        try:
            setup_job = start_setup(paths, config_path(__file__), root, era5=era5, workers=int(era5_workers))
            st.success(f"Started job `{setup_job['job_id']}`. Press Refresh to follow it.")
        except JobBusy as exc:
            st.error(str(exc))
    if setup_job:
        job_block(st, paths, setup_job, key="setup")

cov = coverage(paths, cfg)
prof = load_profiles(paths)
st.subheader("Data coverage by site")
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

st.subheader("Profiles per season")
if len(cov["profiles"]):
    pv = cov["profiles"].pivot_table(index="season", columns="site_code", values="unique_usable", aggfunc="sum",
                                     fill_value=0)
    st.caption("Unique usable pits (duplicates and unusable records excluded) per season, season starting 15 Sep.")
    st.dataframe(pv.sort_index(ascending=False), width="stretch")
else:
    st.caption("No profiles imported.")

st.subheader("Weather coverage per season")
if len(cov["weather"]):
    st.caption("Hours in the table and the share (%) with a QC-ok value. Wind and radiation are not measured at "
               "the plots (wind from the configured nearby station; radiation absent).")
    st.dataframe(cov["weather"].sort_values(["site_code", "season"], ascending=[True, False]),
                 width="stretch", hide_index=True)
else:
    st.caption("No weather imported.")

st.subheader("Configuration")
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

st.subheader("Latest runs")
runs = latest_runs(paths, 10)
if runs:
    st.dataframe(pd.DataFrame([{"run_id": r.run_id, "kind": r.kind.value, "status": r.status,
                                "created (UTC)": r.created_at.isoformat(timespec="seconds"),
                                "data hash": r.data_hash[:12], "config hash": r.config_hash[:12],
                                "software": r.software_version, "counts": json.dumps(r.counts)[:120]}
                               for r in runs]), width="stretch", hide_index=True)
else:
    st.caption("No runs recorded.")
