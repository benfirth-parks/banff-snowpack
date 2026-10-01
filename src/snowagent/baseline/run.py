"""Uncorrected baseline: SNOWPACK at each study plot (flat, open) driven by assembled station/ERA5 forcing.

One run per plot and season from a snow-free start. Outputs the model HS series and model profiles at the
dates of observed pits, for verification against station snow depth and the observed profiles. This is the
reference every later correction must beat (CLAUDE.md principle 3); nothing here is tuned.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from snowagent.baseline.assemble import PlotForcing
from snowagent.contracts import TerrainUnit
from snowagent.engine import snowpack as sp
from snowagent.engine.column import prepare_and_run
from snowagent.engine.profiles import convert_profile
from snowagent.spatial_forcing.builder import ForcingConfig, build_unit_forcing


def plot_unit(plot_id: str, lat: float, lon: float, elevation_m: float) -> TerrainUnit:
    """A flat, open, unshaded point unit (study plots are level clearings; horizon not yet applied)."""
    from snowagent.terrain.units import site_unit

    return site_unit(plot_id, "study_plots", "flat-open-v1", lat, lon, elevation_m)


def model_profile_as_observed(layers, aggregate: bool = True, hardness_tol: float = 0.5) -> dict:
    """Engine layers -> the observed-profile layout used by obs.agreement (cm above ground, IACS class).

    ``aggregate`` merges adjacent elements with the same grain class and hand hardness within
    ``hardness_tol`` index units, the way an observer draws one layer over many engine elements
    (thickness-weighted hardness/density). Without it, 1-2 cm elements swamp boundary scores.
    """
    out = []
    for ly in sorted(layers, key=lambda x: -x.top_vertical_m):
        g = ly.grain_form_primary
        out.append({"top_cm": round(ly.top_vertical_m * 100, 1), "bottom_cm": round(ly.bottom_vertical_m * 100, 1),
                    "grain_form": g, "grain_class": g[:2] if g else None, "hardness_index": ly.hand_hardness_index,
                    "density_kg_m3": ly.density_kg_m3})
    if aggregate and out:
        merged = [dict(out[0])]
        for ly in out[1:]:
            m = merged[-1]
            same = ly["grain_class"] == m["grain_class"] and (
                ly["hardness_index"] is None or m["hardness_index"] is None
                or abs(ly["hardness_index"] - m["hardness_index"]) <= hardness_tol)
            if same:
                tm, tl = m["top_cm"] - m["bottom_cm"], ly["top_cm"] - ly["bottom_cm"]
                for k in ("hardness_index", "density_kg_m3"):
                    if m[k] is not None and ly[k] is not None and tm + tl > 0:
                        m[k] = (m[k] * tm + ly[k] * tl) / (tm + tl)
                m["bottom_cm"] = ly["bottom_cm"]
            else:
                merged.append(dict(ly))
        out = merged
    return {"layers": out, "hs_cm": round(max((ly.top_vertical_m for ly in layers), default=0.0) * 100, 1),
            "height_reference": "height_above_ground", "temperatures": []}


def run_season(pf: PlotForcing, unit: TerrainUnit, start: pd.Timestamp, end: pd.Timestamp, work: Path,
               engine: sp.EngineInfo | None = None, settings: sp.EngineSettings | None = None) -> dict:
    engine = engine or sp.find_engine()
    settings = settings or sp.EngineSettings()
    fcfg = ForcingConfig()
    src = pf.data.copy()
    missing = src.isna().any(axis=1)
    if missing.any():
        raise ValueError(f"{int(missing.sum())} forcing hours still missing for {pf.plot_id}; ERA5 not complete?")
    uf = build_unit_forcing(src, unit.centroid_lonlat[1], unit.centroid_lonlat[0], unit.elevation_m, unit, fcfg)
    run_dir = work / f"{pf.plot_id}_{start:%Y}"
    out = prepare_and_run(engine, settings, run_dir, unit, uf.smet, end.to_pydatetime(),
                          snowfree_start=start.to_pydatetime())
    met = sp.parse_met(out.met)
    hdr, profiles = sp.parse_pro(out.pro)
    return {"run_dir": str(run_dir), "met": met, "profiles": profiles, "outputs": out}


def profile_at(profiles, when: pd.Timestamp, slope_deg: float = 0.0):
    """The model profile nearest in time to ``when`` (profiles are written every 6 h)."""
    times = [p.time for p in profiles]
    k = int(np.argmin([abs((t - when).total_seconds()) for t in times]))
    layers, diag, _nulls, _ = convert_profile(profiles[k], slope_deg, "plot")
    return times[k], layers, diag


def save_summary(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=1, default=str))
