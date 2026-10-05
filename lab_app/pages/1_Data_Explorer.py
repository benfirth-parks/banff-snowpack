"""Data Explorer: profiles (vertical plot, metadata, raw vs normalized) and station weather, by site and date range."""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from snowagent.lab.services.data import data_status, load_layers, load_profiles, load_weather
from snowagent.lab.settings import season_bounds, season_key
from snowagent.lab.ui.app import empty_state, lab_context, page_header
from snowagent.lab.ui.plots import profile_figure, weather_figure


def _plain(v):
    """A value st.json can show: numpy scalars as Python values, NaN/NaT as None."""
    if v is None or (not isinstance(v, str | list | dict) and pd.isna(v)):
        return None
    return v.item() if hasattr(v, "item") else v


page_header(st, "Data Explorer")
cfg, paths = lab_context(__file__)
tz = ZoneInfo(cfg.display_timezone)
status = data_status(paths)
if not (status["profiles"] or status["weather"]):
    empty_state(st, paths)
    st.stop()

# ------------------------------------------------------------------------------------------- filters
codes = [c.value for c in cfg.sites]
site = st.sidebar.selectbox("Site", codes, format_func=lambda c: f"{cfg.sites[c].display_name} ({c})")
all_prof = load_profiles(paths, site)
latest = all_prof["observed_at"].max() if len(all_prof) else pd.Timestamp.now(tz="UTC")
s0, s1 = season_bounds(season_key(latest, cfg.season_start), cfg.season_start)
picked = st.sidebar.date_input("Date range (local)", value=(s0.tz_convert(tz).date(),
                                                           (s1 - pd.Timedelta(days=1)).tz_convert(tz).date()))
start_d, end_d = (picked if isinstance(picked, tuple | list) and len(picked) == 2 else (picked, picked))
if isinstance(start_d, date) and isinstance(end_d, date):
    start = pd.Timestamp(datetime.combine(start_d, time.min, tz)).tz_convert("UTC")
    end = pd.Timestamp(datetime.combine(end_d + timedelta(days=1), time.min, tz)).tz_convert("UTC")
else:  # pragma: no cover - date_input always returns dates
    start, end = s0, s1
st.sidebar.caption(f"Times shown in {cfg.display_timezone}; stored in UTC.")

tab_p, tab_w = st.tabs(["Profiles", "Weather"])

# ------------------------------------------------------------------------------------------- profiles
with tab_p:
    prof = all_prof[(all_prof["observed_at"] >= start) & (all_prof["observed_at"] < end)] if len(all_prof) else all_prof
    if not status["profiles"]:
        st.info("No profiles imported (`snowagent lab import --only profiles`).")
    elif prof.empty:
        st.info(f"No profiles at {cfg.sites[site].display_name} in this date range "
                f"({len(all_prof)} at the site in all).")
    else:
        only_unique = st.checkbox("Unique usable pits only (hide duplicates and unusable records)", value=True)
        if only_unique:
            prof = prof[prof["unique_usable"]]
        table = pd.DataFrame({
            "profile_id": prof["profile_id"],
            "observed (local)": prof["observed_at"].dt.tz_convert(tz).dt.strftime("%Y-%m-%d %H:%M"),
            "HS (cm)": (prof["snow_depth_m"] * 100).round(0),
            "layers": prof["n_layers"], "of concern": prof["n_layers_of_concern"],
            "quality": prof["profile_quality"], "source": prof["source_id"],
            "review list": prof["review_reasons_json"] != "[]",
            "warnings": prof["validation_warnings_json"].map(lambda s: len(json.loads(s))),
        })
        st.dataframe(table, width="stretch", hide_index=True)
        if table.empty:
            st.info("No unique usable pits in this range.")
        else:
            pid = st.selectbox("Profile", table["profile_id"].tolist(), index=len(table) - 1)
            row = prof[prof["profile_id"] == pid].iloc[0]
            layers = load_layers(paths, pid)
            hs = None if pd.isna(row["snow_depth_m"]) else float(row["snow_depth_m"])
            left, right = st.columns([3, 2])
            with left:
                when = row["observed_at"].tz_convert(tz).strftime("%Y-%m-%d %H:%M %Z")
                st.plotly_chart(profile_figure(layers, hs, f"{pid} · {when}"), width="stretch", theme="streamlit")
            with right:
                st.markdown("**Metadata**")
                meta = {k: _plain(row[k]) for k in ("site_code", "plot_id", "season", "latitude", "longitude", "elevation_m",
                                  "aspect_deg", "slope_deg", "terrain_class", "source_id", "profile_quality",
                                  "usable", "duplicate_of", "availability_assumption")}
                meta["observed (UTC)"] = row["observed_at"].isoformat()
                meta["snow depth (cm)"] = None if hs is None else round(hs * 100, 1)
                st.json(meta)
                for name, label in (("review_reasons_json", "Review reasons (excluded from scoring, ADR-050)"),
                                    ("validation_warnings_json", "Validation warnings"),
                                    ("flags_json", "Source flags")):
                    items = json.loads(row[name])
                    if items:
                        (st.warning if name == "review_reasons_json" else st.caption)(f"{label}: {', '.join(items)}")
                if isinstance(row.get("notes"), str) and row["notes"]:
                    st.caption(f"Notes: {row['notes']}")
            st.markdown("**Layers (normalized: depth from surface)**")
            if layers.empty:
                st.info("This profile has no placed layers.")
            else:
                lt = pd.DataFrame({
                    "top (cm)": (layers["top_depth_m"] * 100).round(1),
                    "bottom (cm)": (layers["bottom_depth_m"] * 100).round(1),
                    "grain": layers["grain_primary"], "grain 2": layers["grain_secondary"],
                    "size (mm)": layers["grain_size_mm"], "hardness": layers["hardness"],
                    "wetness": layers["wetness"], "density (kg/m³)": layers["density_kg_m3"],
                    "class": layers["critical_class"], "of concern": layers["is_layer_of_concern"],
                    "basis": layers["concern_basis_json"].map(lambda s: ", ".join(json.loads(s))),
                    "date tag": layers["date_tag"]})
                st.dataframe(lt, width="stretch", hide_index=True)
                with st.expander("Raw source fields beside the normalized ones"):
                    raw = pd.DataFrame([json.loads(s) for s in layers["raw_json"]])
                    keep = [c for c in ("raw_index", "top_cm", "bottom_cm", "grain_form", "grain_form_2",
                                        "grain_size_mm", "hardness", "moisture", "date_tag", "uncertain_fields")
                            if c in raw]
                    st.caption(f"Source heights are '{json.loads(row['raw_json']).get('height_reference')}' in cm "
                               f"(HS {json.loads(row['raw_json']).get('hs_cm')} cm).")
                    both = pd.concat([raw[keep].add_prefix("raw "), lt[["top (cm)", "bottom (cm)", "grain"]]],
                                     axis=1)
                    st.dataframe(both.astype(str), width="stretch", hide_index=True)
                    st.json(json.loads(row["raw_json"]), expanded=False)

# ------------------------------------------------------------------------------------------- weather
with tab_w:
    if not status["weather"]:
        st.info("No weather imported (`snowagent lab import --only weather`).")
    else:
        w = load_weather(paths, site, start, end)
        if w.empty:
            st.info(f"No station weather at {cfg.sites[site].display_name} in this date range.")
        else:
            st.plotly_chart(weather_figure(w, cfg.display_timezone), width="stretch", theme="streamlit")
            st.caption("Values as measured at the named station (no elevation transfer, no gap filling); values that "
                       "failed QC are blank. Precipitation is daily sums for ranges over 30 days.")
            rows = []
            for var in ("air_temperature_k", "relative_humidity_frac", "precipitation_mm", "snow_depth_m",
                        "wind_speed_ms", "swe_mm"):
                counts = w[f"{var}_qc"].value_counts().to_dict()
                srcs = w[f"{var}_source"].dropna().value_counts().to_dict()
                rows.append({"variable": var, **{k: counts.get(k, 0) for k in ("ok", "suspect", "bad", "missing")},
                             "stations": ", ".join(f"{k} ({v})" for k, v in srcs.items())})
            st.markdown("**QC flags and sources in this range (hours)**")
            st.dataframe(pd.DataFrame(rows), width="stretch", hide_index=True)
