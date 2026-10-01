"""Baseline verification: modelled vs measured snow depth, and modelled vs observed profiles.

HS: hourly model HS against the plot station's snow-depth sensor (QC "ok" values only), daily means.
Profiles: for each observed pit at the plot (unique, usable observations), the model profile nearest in time,
compared with obs.agreement.compare_profiles (observed = reference). Results are reported per plot and
season, uncorrected (the baseline every learned component must beat on held-out seasons).
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from snowagent.baseline.run import model_profile_as_observed, profile_at
from snowagent.obs.agreement import compare_profiles, summarise


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


def observed_at_plot(observed_jsonl: Path, site_key: str, start: pd.Timestamp, end: pd.Timestamp) -> list[dict]:
    out = []
    for line in Path(observed_jsonl).read_text().splitlines():
        o = json.loads(line)
        if o.get("site_key") != site_key or o.get("duplicate_of") or o.get("unusable") or not o.get("obs_time_utc"):
            continue
        if o.get("height_reference") != "height_above_ground":
            continue
        t = pd.Timestamp(o["obs_time_utc"])
        if start <= t <= end:
            out.append(o)
    return out
