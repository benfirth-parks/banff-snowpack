"""Snowpack Agent Lab: home page (purpose, disclaimer, data coverage per site, latest runs)."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from snowagent.lab.services.data import coverage, data_status, latest_runs, load_profiles
from snowagent.lab.ui.app import empty_state, lab_context, page_header

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
    st.warning("Availability times are unknown for every record: the observation time stands in for the time a pit "
               "or value became available (retrospective prototyping only).", icon="🕒")

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
