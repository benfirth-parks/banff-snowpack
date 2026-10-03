"""Baseline verification: modelled vs measured snow depth, and modelled vs observed profiles.

HS: hourly model HS against the plot station's snow-depth sensor (QC "ok" values only), daily means.
Profiles: for each observed pit at the plot (unique, usable observations), the model profile nearest in time,
compared with obs.agreement.compare_profiles (observed = reference). Results are reported per plot and
season, uncorrected (the baseline every learned component must beat on held-out seasons).
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

import numpy as np
import pandas as pd

from snowagent.baseline.run import model_profile_as_observed, profile_at
from snowagent.obs.agreement import compare_profiles, summarise
from snowagent.obs.observed import exclude_flagged_pits, review_reasons


def hs_scores(model_hs_m: pd.Series, obs_hs_m: pd.Series) -> dict:
    m = model_hs_m.resample("D").mean()
    o = obs_hs_m.resample("D").mean()
    j = pd.concat([m.rename("m"), o.rename("o")], axis=1).dropna()
    j = j[(j.o > 0.05) | (j.m > 0.05)]
    if len(j) < 10:
        return {"days": len(j)}
    err = j.m - j.o
    peak_o, peak_m = j.o.max(), j.m.max()
    return {"days": len(j), "bias_m": round(float(err.mean()), 3), "mae_m": round(float(err.abs().mean()), 3),
            "rmse_m": round(float(np.sqrt((err ** 2).mean())), 3), "r": round(float(j.m.corr(j.o)), 3),
            "peak_obs_m": round(float(peak_o), 2), "peak_model_m": round(float(peak_m), 2)}


def profile_scores(profiles, observed: list[dict]) -> tuple[list[dict], dict]:
    rows = []
    for o in observed:
        when = pd.Timestamp(o["obs_time_utc"])
        t, layers, _diag = profile_at(profiles, when)
        if abs((t - when).total_seconds()) > 12 * 3600:
            continue
        model = model_profile_as_observed(layers)
        rows.append({"profile_id": o["profile_id"], "obs_time_utc": str(when), "model_time_utc": str(t),
                     "provenance": o["provenance"].get("method")} | compare_profiles(o, model))
    return rows, summarise(rows)


def pits_at_plot(records: Iterable[dict], site_key: str, start: pd.Timestamp, end: pd.Timestamp,
                 exclude_flagged: bool = False, excluded: list[dict] | None = None) -> list[dict]:
    """Unique, usable pits at a study plot in [start, end] with heights above ground: the pits that steer the site
    runs and are scored. With ``exclude_flagged`` a pit with review reasons (``obs.observed.review_reasons``:
    location_qc entries, printed date/site flags) is left out and appended to ``excluded`` (ADR-050)."""
    out = []
    for o in records:
        if o.get("site_key") != site_key or o.get("duplicate_of") or o.get("unusable") or not o.get("obs_time_utc"):
            continue
        if o.get("height_reference") != "height_above_ground":
            continue
        t = pd.Timestamp(o["obs_time_utc"])
        if start <= t <= end:
            why = review_reasons(o) if exclude_flagged else []
            if why:
                if excluded is not None:
                    excluded.append({"profile_id": o["profile_id"], "reasons": why})
                continue
            out.append(o)
    return out


def observed_at_plot(observed_jsonl: Path, site_key: str, start: pd.Timestamp, end: pd.Timestamp,
                     exclude_flagged: bool | None = None, excluded: list[dict] | None = None) -> list[dict]:
    """``pits_at_plot`` over an observed-profiles file. ``exclude_flagged`` None: config/observations.yaml
    ``exclude_flagged_pits_from_steering_and_scoring`` (default false: every pit, as before ADR-050)."""
    if exclude_flagged is None:
        exclude_flagged = exclude_flagged_pits()
    records = (json.loads(line) for line in Path(observed_jsonl).read_text().splitlines())
    return pits_at_plot(records, site_key, start, end, exclude_flagged, excluded)


def plot_pits(observed_jsonl: Path, site_key: str, start: pd.Timestamp, end: pd.Timestamp,
              exclude_flagged: bool | None = None) -> tuple[list[dict], dict]:
    """``observed_at_plot`` plus what an output records about it: ``{"pits_excluded": [{profile_id, reasons}]}`` when
    ``exclude_flagged`` (None: the config switch) left pits out, else ``{}`` (ADR-050). The site build and
    `snowagent baseline` add it to their outputs."""
    excluded: list[dict] = []
    pits = observed_at_plot(observed_jsonl, site_key, start, end, exclude_flagged, excluded)
    return pits, ({"pits_excluded": excluded} if excluded else {})


def ghcnd_snwd(path: Path) -> pd.Series:
    """GHCN-Daily snow depth (m) from a raw by_station CSV (gz). Values with a quality flag are excluded.

    GHCN days are local observation days (morning readings at these stations); each value is placed at
    15 UTC (08 MST) of its day so the daily means in ``hs_scores`` fall on the same day.
    """
    d = pd.read_csv(path, dtype={"Q_FLAG": str}, usecols=["DATE", "ELEMENT", "DATA_VALUE", "Q_FLAG"])
    d = d[(d["ELEMENT"] == "SNWD") & d["Q_FLAG"].isna()]
    t = pd.to_datetime(d["DATE"].astype(str), format="%Y%m%d").dt.tz_localize("UTC") + pd.Timedelta(hours=15)
    return pd.Series(d["DATA_VALUE"].to_numpy() / 1000.0, index=pd.DatetimeIndex(t)).sort_index()
