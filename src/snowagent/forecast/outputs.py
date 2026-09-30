"""Write machine-readable forecast products for one run directory.

run_dir/
  manifest.json                ForecastResultMeta (provenance, capability flags, label)
  domain.json                  terrain units used (self-contained point queries)
  profiles/<unit_id>.jsonl     one ProfileRecord per (member, lead)
  field/summary.csv            per unit x lead ensemble summary (map-ready attributes)
  field/lead_<h>h.geojson      WGS84 polygons with the same attributes (RFC 7946)
  diagnostics/mass_budget.csv  per unit x member column budget (slope-area kg m-2)
  diagnostics/forcing.csv      per unit x member terrain-forcing diagnostics
  plots/                       profile and map previews (inspection only)
  README.txt                   label + how to read the products
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
from pyproj import Transformer

from snowagent.contracts import (
    EXPERIMENTAL_LABEL,
    ForecastResultMeta,
    ProfileRecord,
    StateCheckpoint,
    TerrainDomain,
)

WL_MIN_THICKNESS_M = 0.005
WL_MAX_DEPTH_M = 1.5


def _shallowest(rec: ProfileRecord, attr: str) -> tuple[float | None, str | None]:
    cands = [ly for ly in rec.layers if getattr(ly, attr) and ly.thickness_vertical_m >= WL_MIN_THICKNESS_M]
    if not cands:
        return None, None
    top = max(cands, key=lambda ly: ly.top_vertical_m)
    return round(top.depth_top_vertical_m, 3), top.grain_form_primary


def _new_snow(rec: ProfileRecord, init: pd.Timestamp) -> float:
    return float(sum(ly.thickness_vertical_m for ly in rec.layers
                     if ly.deposition_time is not None and pd.Timestamp(ly.deposition_time) > init))


def unit_lead_summary(records: list[ProfileRecord], init: pd.Timestamp) -> dict:
    ctrl = next(r for r in records if r.member_id == 0)
    hs = np.array([r.diagnostics.hs_vertical_m for r in records])
    swe = np.array([r.diagnostics.swe_kg_m2_per_horizontal_area for r in records])
    ns = np.array([_new_snow(r, init) for r in records])
    crust_any = np.array([any(ly.is_crust for ly in r.layers) for r in records])
    wl_any = np.array([any(ly.is_candidate_weak_layer and ly.thickness_vertical_m >= WL_MIN_THICKNESS_M
                           and ly.depth_top_vertical_m <= WL_MAX_DEPTH_M for ly in r.layers) for r in records])
    surf_lwc = [max([ly.lwc_vol_frac for ly in r.layers if ly.depth_top_vertical_m <= 0.1], default=0.0)
                for r in records]
    c_depth, c_form = _shallowest(ctrl, "is_crust")
    w_depth, w_form = _shallowest(ctrl, "is_candidate_weak_layer")
    return {
        "n_members": len(records),
        "hs_vertical_m_control": round(ctrl.diagnostics.hs_vertical_m, 4),
        "hs_vertical_m_p10": round(float(np.percentile(hs, 10)), 4),
        "hs_vertical_m_p50": round(float(np.percentile(hs, 50)), 4),
        "hs_vertical_m_p90": round(float(np.percentile(hs, 90)), 4),
        "swe_kg_m2_horizontal_p50": round(float(np.percentile(swe, 50)), 2),
        "new_snow_since_init_vertical_m_control": round(_new_snow(ctrl, init), 4),
        "new_snow_since_init_vertical_m_p50": round(float(np.percentile(ns, 50)), 4),
        "crust_present_member_fraction": round(float(crust_any.mean()), 3),
        "crust_shallowest_depth_vertical_m_control": c_depth,
        "crust_shallowest_form_control": c_form,
        "candidate_weak_layer_member_fraction": round(float(wl_any.mean()), 3),
        "candidate_weak_layer_shallowest_depth_vertical_m_control": w_depth,
        "candidate_weak_layer_form_control": w_form,
        "surface_lwc_vol_frac_max_member_p50": round(float(np.percentile(surf_lwc, 50)), 4),
    }


def write_run(run_dir: Path, meta: ForecastResultMeta, domain: TerrainDomain, results: list[dict],
              cp: StateCheckpoint) -> None:
    for sub in ("profiles", "field", "diagnostics", "plots"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    (run_dir / "manifest.json").write_text(meta.model_dump_json(indent=1))
    (run_dir / "domain.json").write_text(domain.model_dump_json(indent=1))
    init = pd.Timestamp(meta.forecast_init_time)

    by_unit: dict[str, list[ProfileRecord]] = {}
    budgets, forcing = [], []
    for r in results:
        by_unit.setdefault(r["unit_id"], []).extend(r["records"])
        budgets.append({"unit_id": r["unit_id"], "member": r["member"], **r["budget"]})
        forcing.append({"unit_id": r["unit_id"], "member": r["member"], **r["forcing"]})
    for uid, recs in by_unit.items():
        recs.sort(key=lambda x: (x.member_id, x.lead_hours))
        with open(run_dir / "profiles" / f"{uid}.jsonl", "w") as fh:
            for rec in recs:
                fh.write(rec.model_dump_json() + "\n")
    pd.DataFrame(budgets).to_csv(run_dir / "diagnostics" / "mass_budget.csv", index=False)
    pd.DataFrame(forcing).to_csv(run_dir / "diagnostics" / "forcing.csv", index=False)

    rows = []
    for u in domain.units:
        base = {"unit_id": u.unit_id, "supported": u.supported, "elevation_m": round(u.elevation_m, 1),
                "slope_deg": round(u.slope_deg, 1), "aspect_deg": None if u.aspect_deg is None else round(u.aspect_deg, 1),
                "sky_view_factor": round(u.sky_view_factor, 3), "land_cover": u.land_cover.value,
                "unit_resolution_m": u.resolution_m, "transport_status": meta.capability.transport_status,
                "synthetic_inputs": meta.synthetic_inputs}
        for h in meta.request.output_lead_hours:
            row = {**base, "lead_hours": h, "valid_time": (init + pd.Timedelta(hours=h)).isoformat()}
            recs = [r for r in by_unit.get(u.unit_id, []) if r.lead_hours == h]
            if recs:
                row.update(unit_lead_summary(recs, init))
                row["status"] = "ok"
            elif not u.supported:
                row["status"] = "unsupported: " + "; ".join(u.unsupported_reasons)
            else:
                row["status"] = "failed: " + "; ".join(v for k, v in meta.unit_failures.items() if k.startswith(u.unit_id))
            rows.append(row)
    summary = pd.DataFrame(rows)
    summary.to_csv(run_dir / "field" / "summary.csv", index=False)

    to_ll = Transformer.from_crs(domain.crs, "EPSG:4326", always_xy=True)
    polys = {u.unit_id: [list(to_ll.transform(x, y)) for x, y in u.polygon_xy + [u.polygon_xy[0]]]
             for u in domain.units}
    for h in meta.request.output_lead_hours:
        feats = []
        for _, row in summary[summary.lead_hours == h].iterrows():
            props = {k: (None if isinstance(v, float) and np.isnan(v) else v) for k, v in row.items()}
            feats.append({"type": "Feature", "geometry": {"type": "Polygon", "coordinates": [polys[row.unit_id]]},
                          "properties": props})
        gj = {"type": "FeatureCollection", "name": f"{meta.run_id} lead {h} h",
              "label": EXPERIMENTAL_LABEL, "run_id": meta.run_id, "source_crs": domain.crs, "features": feats}
        (run_dir / "field" / f"lead_{h:03d}h.geojson").write_text(json.dumps(gj, default=str))

    (run_dir / "README.txt").write_text(f"""{EXPERIMENTAL_LABEL}

run_id: {meta.run_id}
synthetic inputs: {meta.synthetic_inputs}
transport_status: {meta.capability.transport_status} -- {meta.capability.transport_note}
initial state: {meta.initial_state_id} (analysis {cp.analysis_time.isoformat()}, not modified by this run)
engine: {meta.engine_version}, config hash {meta.engine_config_hash}
terrain: {meta.terrain_version}; unit resolution {domain.unit_resolution_m:g} m (DEM {domain.dem_resolution_m:g} m)

Representative profile = ensemble member 0 (unperturbed control), an actual member.
Layer indices are never averaged across members. Ensemble spread = scenario
uncertainty ({meta.request.ensemble.members} members, seed {meta.request.ensemble.seed}), not calibrated probability.
Heights: *_vertical (along gravity) and *_slope_normal are both given; SWE per horizontal
and per slope-parallel area are labelled. Candidate weak layers are grain-form flags only.
""")
    from snowagent.forecast import plots  # local import keeps matplotlib optional for core paths

    plots.write_run_plots(run_dir, meta, domain, by_unit, summary)
