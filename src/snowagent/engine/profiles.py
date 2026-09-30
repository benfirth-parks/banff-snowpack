"""Convert raw SNOWPACK .pro records into typed layered profiles.

Geometry (verified in AsciiIO::writeProfilePro): engine node heights are
slope-normal internally and written to .pro divided by cos(slope), i.e. the
.pro heights are VERTICAL. We keep both conventions explicitly.

Layer identity: element IDs are runtime counters that are not persisted in .sno
restart files, so lineage uses the engine deposition date (persisted) plus the
ordinal among elements sharing it. See docs/decisions.md ADR-007.
"""

from __future__ import annotations

import math
from collections import Counter

import numpy as np
import pandas as pd

from snowagent.contracts import Layer, NullReason, ProfileDiagnostics
from snowagent.engine import grains
from snowagent.engine.snowpack import NODATA, ProProfile

LINEAGE_BASIS = "engine_deposition_time+ordinal (SNOWPACK .pro 0505 DATE; persisted across restarts)"


def _floats(values: list[str] | None, n: int) -> np.ndarray | None:
    if values is None:
        return None
    arr = np.array([float(v) for v in values[:n]], dtype=float)
    if len(arr) != n:
        return None
    arr[arr == NODATA] = np.nan
    return arr


def _opt(x: float) -> float | None:
    return None if x is None or not np.isfinite(x) else float(x)


def convert_profile(p: ProProfile, slope_deg: float, unit_id: str
                    ) -> tuple[list[Layer], ProfileDiagnostics, list[NullReason], str | None]:
    heights = p.data.get("height_cm")
    nulls: list[NullReason] = []
    cos_sl = math.cos(math.radians(slope_deg))
    if heights is None or (len(heights) == 1 and float(heights[0]) == 0.0):
        diag = ProfileDiagnostics(hs_vertical_m=0.0, hs_slope_normal_m=0.0, swe_kg_m2_per_slope_area=0.0,
                                  swe_kg_m2_per_horizontal_area=0.0, n_layers=0)
        return [], diag, nulls, None
    n = len(heights)
    top_v = np.array([float(h) for h in heights]) / 100.0
    bottom_v = np.concatenate([[0.0], top_v[:-1]])
    rho = _floats(p.data.get("density"), n)
    temp = _floats(p.data.get("temperature_c"), n)
    lwc = _floats(p.data.get("lwc_pct"), n)
    ice = _floats(p.data.get("ice_pct"), n)
    dd = _floats(p.data.get("dendricity"), n)
    spher = _floats(p.data.get("sphericity"), n)
    gs = _floats(p.data.get("grain_size_mm"), n)
    bs = _floats(p.data.get("bond_size_mm"), n)
    gt = _floats(p.data.get("grain_type"), n)  # may have n+1 entries (surface SH); first n are elements
    hard = _floats(p.data.get("hardness"), n)
    tgrad = _floats(p.data.get("temp_gradient"), n)
    sk38 = _floats(p.data.get("sk38"), n)
    dep_raw = p.data.get("deposition")
    if rho is None or temp is None or lwc is None:
        raise ValueError(f"profile at {p.time} lacks density/temperature/LWC for {unit_id}")
    deposition = None
    if dep_raw is not None and len(dep_raw) >= n:
        deposition = [pd.Timestamp(d).tz_localize("UTC") if d not in ("-999", "") else None for d in dep_raw[:n]]
    else:
        nulls.append(NullReason(field="deposition_time", reason="engine did not report deposition dates"))
    if hard is None:
        nulls.append(NullReason(field="hand_hardness_index", reason="engine output 0534 absent"))
    if sk38 is None:
        nulls.append(NullReason(field="sk38", reason="engine output 0533 absent"))
    nulls.append(NullReason(field="p_unstable", reason="no validated instability model is configured"))

    layers: list[Layer] = []
    hs_v = float(top_v[-1])
    ordinal: Counter[str] = Counter()
    for i in range(n):
        th_v = float(top_v[i] - bottom_v[i])
        if th_v <= 0:
            continue
        code = None if gt is None or not np.isfinite(gt[i]) else int(gt[i])
        primary, secondary, _ = grains.decode(code)
        crust = grains.is_crust(code)
        basis = grains.candidate_weak_layer_basis(code)
        dep = deposition[i] if deposition else None
        lineage = None
        if dep is not None:
            key = dep.isoformat()
            lineage = f"{unit_id}:{key}#{ordinal[key]}"
            ordinal[key] += 1
        layers.append(Layer(
            index_from_bottom=len(layers),
            top_vertical_m=float(top_v[i]),
            bottom_vertical_m=float(bottom_v[i]),
            thickness_vertical_m=th_v,
            thickness_slope_normal_m=th_v * cos_sl,
            depth_top_vertical_m=max(0.0, hs_v - float(top_v[i])),
            density_kg_m3=float(rho[i]),
            temperature_c=float(temp[i]),
            lwc_vol_frac=float(lwc[i]) / 100.0,
            ice_vol_frac=None if ice is None else _opt(ice[i] / 100.0),
            grain_code_swiss=code,
            grain_form_primary=primary,
            grain_form_secondary=secondary,
            grain_size_mm=None if gs is None else _opt(gs[i]),
            bond_size_mm=None if bs is None else _opt(bs[i]),
            dendricity=None if dd is None else _opt(dd[i]),
            sphericity=None if spher is None else _opt(spher[i]),
            hand_hardness_index=None if hard is None else _opt(abs(hard[i])),
            temperature_gradient_k_m=None if tgrad is None else _opt(tgrad[i]),
            deposition_time=dep,
            lineage_id=lineage,
            is_crust=crust,
            melt_freeze_marker=grains.melt_freeze_marker(code),
            is_candidate_weak_layer=basis is not None,
            candidate_weak_layer_basis=basis,
            sk38=None if sk38 is None else _opt(sk38[i]),
        ))
    swe_slope = float(sum(ly.density_kg_m3 * ly.thickness_slope_normal_m for ly in layers))
    sh = p.data.get("surface_hoar")
    sh_size = None
    if sh and len(sh) >= 2 and float(sh[1]) != NODATA:
        sh_size = float(sh[1])
    diag = ProfileDiagnostics(
        hs_vertical_m=hs_v,
        hs_slope_normal_m=hs_v * cos_sl,
        swe_kg_m2_per_slope_area=swe_slope,
        swe_kg_m2_per_horizontal_area=swe_slope / cos_sl,
        n_layers=len(layers),
        surface_hoar_size_mm=sh_size,
    )
    identity_uncertainty = None
    if deposition is None:
        identity_uncertainty = "no deposition dates; layer identity across time is unknown"
    return layers, diag, nulls, identity_uncertainty
