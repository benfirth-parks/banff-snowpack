"""Benchmark Cases: built case packages by case set, site, split and type; their visible (anonymous) inputs, excluded
records and leakage checks; build cases. Hidden truth is shown only for training/development cases and never for a
sealed-test case (ADR-059)."""

from __future__ import annotations

import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from snowagent.lab.benchmark import package as pkg
from snowagent.lab.benchmark.loader import read_checks, read_manifest, read_visible_json, read_visible_table
from snowagent.lab.services.benchmark import (
    build_cases,
    build_report,
    case_index,
    case_sets,
    check_cases,
    hidden_truth,
)
from snowagent.lab.services.data import data_status
from snowagent.lab.ui.app import empty_state, lab_context, page_header, repo_root
from snowagent.lab.ui.plots import profile_figure, weather_figure

TRUTH_SPLITS = {"training", "development"}  # validation, holdout and sealed-test truth stay out of the browser

page_header(st, "Benchmark Cases")
cfg, paths = lab_context(__file__)
tz = ZoneInfo(cfg.display_timezone)
a = cfg.benchmark.availability


def _local(t) -> str:
    return pd.Timestamp(t).tz_convert(tz).strftime("%Y-%m-%d %H:%M")


# ------------------------------------------------------------------------------------------- assumptions
st.caption(f"Split mode `{cfg.splits.mode.value}`: " + "; ".join(
    f"{k}: {', '.join(v) if v else '(none)'}" for k, v in (
        cfg.splits.mode_seasons() if cfg.splits.mode.value != "loso" or cfg.splits.loso_holdout
        else {"all seasons": cfg.splits.all_seasons, "holdout": ["named at build time"]}).items()))
if cfg.splits.warn_provisional():
    st.warning("Provisional split, owner to confirm: the development / validation / sealed-test seasons in "
               "config/lab.yaml are a recommendation.", icon="🗓️")
if a.profile_delay_provisional:
    st.warning(f"Pit availability is assumed {a.profile_delay_h:g} h after the pit was dug (provisional, owner to "
               "confirm): the pits record no publication time.", icon="🕒")
with st.expander("Availability assumptions (every visible record satisfies available time ≤ as-of)"):
    st.markdown(f"- pits and their tests: observed + {a.profile_delay_h:g} h\n"
                f"- station weather: observed + {a.weather_latency_h:g} h; ERA5-filled values: observed + "
                f"{a.era5_latency_h:g} h\n"
                f"- archived GFS runs: issued + {a.gfs_latency_h:g} h (the latest run issued within "
                f"{cfg.benchmark.forecast_h72.max_run_age_h:g} h before as-of)\n"
                "- measured stand-in (`measured_standin`): measured weather after as-of, given as a forecast issued "
                "at as-of by convention, snow depth and SWE withheld")
    st.caption("Agents see no profile, case, observer or pit identifier and no calendar date: times relative to "
               "as-of plus day of year.")

status = data_status(paths)
sets = case_sets(paths)

# ------------------------------------------------------------------------------------------- build
with st.sidebar.expander("Build cases", expanded=not sets):
    st.caption(f"Builds every usable pit under split mode `{cfg.splits.mode.value}`; each case passes the leakage "
               "checks before it is written. Same as `snowagent lab build-cases`.")
    holdout = None
    if cfg.splits.mode.value == "loso":
        holdout = st.selectbox("Held-out season", cfg.splits.all_seasons,
                               index=cfg.splits.all_seasons.index(cfg.splits.loso_holdout)
                               if cfg.splits.loso_holdout in cfg.splits.all_seasons else 0)
    btypes = st.multiselect("Case types", ["forecast_h72", "next_pit"], default=["forecast_h72", "next_pit"])
    if st.button("Build", disabled=not status["profiles"] or not btypes):
        with st.spinner("Building cases (a few minutes on the full set)…"):
            try:
                rep = build_cases(paths, cfg, repo_root(__file__), case_types=btypes, holdout=holdout)
                st.success(f"{rep['cases']} cases built, {len(rep['exclusions'])} pits excluded, leakage checks "
                           f"passed {rep['leakage']['pass']}/{rep['cases']}.")
                sets = case_sets(paths)
            except Exception as exc:  # shown, never hidden: a leak or a missing input stops the build
                st.error(f"Build failed: {exc}")

if not (status["profiles"] or status["weather"]):
    empty_state(st, paths)
    st.stop()
if not sets:
    st.info("No cases built yet. From the repository root run `snowagent lab build-cases` (reads "
            "`archive/forecasts/gfs` for the archived forecasts), or use **Build cases** in the sidebar.")
    st.stop()

# ------------------------------------------------------------------------------------------- filters
default_set = cfg.splits.case_set() if cfg.splits.mode.value != "loso" or cfg.splits.loso_holdout else sets[0]
case_set = st.sidebar.selectbox("Case set", sets, index=sets.index(default_set) if default_set in sets else 0)
idx = case_index(paths, case_set)
codes = [c.value for c in cfg.sites]
site = st.sidebar.selectbox("Site", codes, format_func=lambda c: f"{cfg.sites[c].display_name} ({c})")
splits = sorted(idx["split"].unique()) if len(idx) else []
split = st.sidebar.selectbox("Split", splits) if splits else None
ctype = st.sidebar.selectbox("Case type", ["forecast_h72", "next_pit"])

report = build_report(paths, case_set)
if report:
    with st.expander(f"Build report of `{case_set}` ({report['created_at']} UTC, run {report['run_id']})"):
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**Cases per plot and season** (forecast source)")
            per = report.get("cases_per_plot_season", {}).get(ctype, {})
            if per:
                st.dataframe(pd.DataFrame(per).T.rename_axis("plot season"), width="stretch")
        with c2:
            st.markdown("**Exclusions by reason**")
            ex = pd.DataFrame(report["exclusions"])
            if len(ex):
                ex = ex[(ex["site_code"] == site) & (ex["case_type"] == ctype)]
                st.dataframe(ex.groupby("reason").size().rename("pits").reset_index(), width="stretch",
                             hide_index=True)
                with st.popover("Every excluded pit"):
                    st.dataframe(ex[["profile_id", "observed_at", "season", "reason", "detail"]], width="stretch",
                                 hide_index=True)
            else:
                st.caption("No exclusions.")

sel = idx[(idx["site_code"] == site) & (idx["split"] == split) & (idx["case_type"] == ctype)] if len(idx) else idx
st.subheader(f"{cfg.sites[site].display_name} · {split or '-'} · {ctype}")
if sel.empty:
    st.info("No cases for this selection.")
    st.stop()
n_gfs = int((sel["forecast_source"] == "archived_gfs").sum())
m1, m2, m3, m4 = st.columns(4)
m1.metric("cases", len(sel))
m2.metric("archived forecast", n_gfs)
m3.metric("measured stand-in", len(sel) - n_gfs)
m4.metric("leakage checks passed", f"{int((sel['leakage_check'] == 'pass').sum())}/{len(sel)}")
if split == "sealed_test":
    st.warning("Sealed-test cases: their hidden truth is never shown here and is read only with an explicit unseal "
               "(`snowagent lab case-truth --unseal`).", icon="🔒")
table = pd.DataFrame({
    "case_id": sel["case_id"], "as-of (local)": sel["as_of_time"].map(_local),
    "valid (local)": sel["valid_time"].map(_local), "horizon (h)": sel["horizon_hours"].round(1),
    "forecast": sel["forecast_source"], "target": sel["target_scope"], "visible pits": sel["visible_pits"],
    "weather hours": sel["visible_weather_hours"], "excluded records": sel["excluded_records"],
    "leakage check": sel["leakage_check"]})
st.dataframe(table, width="stretch", hide_index=True)

# ------------------------------------------------------------------------------------------- one case
case_id = st.selectbox("Case", sel["case_id"].tolist(), index=len(sel) - 1)
row = sel[sel["case_id"] == case_id].iloc[0]
d = Path(row["path"])
m = read_manifest(d)
as_of = pd.Timestamp(m.as_of_time)
head = read_visible_json(d, pkg.CASE)
for w in m.warnings:
    st.caption(f"⚠️ {w}")

tab_v, tab_x, tab_c, tab_t = st.tabs(["Visible inputs", "Eligible vs excluded", "Leakage checks", "Hidden truth"])
with tab_v:
    st.caption(f"What an agent receives (anonymous): case key `{head['case_key']}`, as-of day of year "
               f"{head['as_of_day_of_year']:.2f}, horizon {head['horizon_hours']:.1f} h, forecast source "
               f"`{head['forecast_source']}`. Dates below are reconstructed for you from the manifest; the agent sees "
               "hours relative to as-of.")
    wo = read_visible_table(d, pkg.WEATHER_OBSERVED)
    wf = read_visible_table(d, pkg.WEATHER_FORECASTS)
    show = pd.concat([wo[wo["t_rel_h"] > -24 * 14], wf], ignore_index=True)
    if len(show):
        show["observed_at"] = as_of + pd.to_timedelta(show["t_rel_h"], unit="h")
        st.plotly_chart(weather_figure(show, cfg.display_timezone), width="stretch", theme="streamlit")
        st.caption(f"Last 14 days of measured weather before as-of ({_local(as_of)}), then the "
                   f"{'archived GFS run' if head['forecast_source'] == 'archived_gfs' else 'measured stand-in'} "
                   "to the valid time.")
    runs = read_visible_json(d, pkg.FORECAST_RUNS)
    if runs:
        st.dataframe(pd.DataFrame(runs), width="stretch", hide_index=True)
    pits = read_visible_table(d, pkg.PITS)
    layers = read_visible_table(d, pkg.LAYERS)
    if len(pits):
        last = pits.sort_values("t_rel_h").iloc[-1]
        ly = layers[layers["pit_key"] == last["pit_key"]]
        hs = None if pd.isna(last["snow_depth_m"]) else float(last["snow_depth_m"])
        st.plotly_chart(profile_figure(ly, hs, f"latest visible pit {last['pit_key']} · {-last['t_rel_h']:.0f} h "
                                               "before as-of"), width="stretch", theme="streamlit")
        st.dataframe(pits.drop(columns=["temperatures_json"]).sort_values("t_rel_h", ascending=False),
                     width="stretch", hide_index=True)
    else:
        st.info("No earlier pit was available at as-of.")

with tab_x:
    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Visible records**")
        st.dataframe(pd.Series(m.visible_counts, name="rows").rename_axis("table").reset_index(), width="stretch",
                     hide_index=True)
    with c2:
        st.markdown("**Not shown, by reason**")
        if m.excluded_counts:
            st.dataframe(pd.Series(m.excluded_counts, name="records").rename_axis("table:reason").reset_index(),
                         width="stretch", hide_index=True)
        else:
            st.caption("Nothing excluded.")
    st.json({"availability_rules": m.availability_rules, "forecast_standin": m.forecast_standin,
             "split_mode": m.split_mode, "holdout_season": m.holdout_season, "config_hash": m.config_hash,
             "data_hash": m.data_hash, "build_run_id": m.build_run_id}, expanded=False)

with tab_c:
    checks = read_checks(d)
    st.markdown(f"Result when built: **{checks['status']}** ({checks.get('checked_at', '-')})")
    st.dataframe(pd.DataFrame(checks["checks"]), width="stretch", hide_index=True)
    if st.button("Re-run the leakage checks"):
        rep = check_cases(paths, cfg, case_id, case_set)[0]
        (st.success if rep.status == "pass" else st.error)(f"{rep.status}: "
                                                           + "; ".join(c["name"] for c in rep.failed))

with tab_t:
    if m.split.value not in TRUTH_SPLITS:
        st.info(f"Hidden truth of {m.split.value} cases is not shown in the browser"
                + (" (sealed: read only with an explicit unseal)." if m.split.value == "sealed_test" else "."))
    elif st.toggle("Show the withheld pit (training data)", value=False):
        truth = hidden_truth(paths, case_id, case_set)
        tp = truth.truth_profile
        tl = pd.DataFrame([ly.model_dump(mode="json") for ly in tp.layers])
        if len(tl):
            tl["concern_basis_json"] = tl["concern_basis"].map(json.dumps)
        st.plotly_chart(profile_figure(tl, tp.snow_depth_m, f"{tp.profile_id} · {_local(tp.observed_at)}"),
                        width="stretch", theme="streamlit")
        st.json(truth.verification, expanded=False)
