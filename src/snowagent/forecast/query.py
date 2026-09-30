"""Point/profile query against a stored forecast run.

A lat/lon is resolved to exactly one terrain unit; the response reports that
unit's geometry and resolution. Points outside the domain boundary, or inside it
but not covered by a unit, are rejected (no extrapolation). Unsupported units
return an explicit error instead of a profile.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from pyproj import Transformer

from snowagent.contracts import EXPERIMENTAL_LABEL, ForecastResultMeta, ProfileRecord, TerrainDomain
from snowagent.errors import InvalidInput, OutOfDomain, UnsupportedTerrain
from snowagent.forecast.outputs import unit_lead_summary
from snowagent.terrain.dem import points_in_polygon


def resolve_run(run: str, runs_root: Path | None) -> Path:
    p = Path(run)
    if (p / "manifest.json").exists():
        return p
    if runs_root is not None and (Path(runs_root) / run / "manifest.json").exists():
        return Path(runs_root) / run
    raise InvalidInput(f"forecast run {run!r} not found (looked in {p} and {runs_root})")


def locate_unit(domain: TerrainDomain, lat: float, lon: float):
    if not (-90 <= lat <= 90 and -180 <= lon <= 180):
        raise InvalidInput("lat/lon out of range")
    x, y = Transformer.from_crs("EPSG:4326", domain.crs, always_xy=True).transform(lon, lat)
    if not bool(points_in_polygon(np.array([x]), np.array([y]), domain.boundary_xy)[0]):
        raise OutOfDomain(f"point ({lat}, {lon}) is outside domain {domain.domain_id}; not extrapolating",
                          x=x, y=y)
    for u in domain.units:
        if bool(points_in_polygon(np.array([x]), np.array([y]), u.polygon_xy)[0]):
            return u, (x, y)
    raise OutOfDomain(f"point ({lat}, {lon}) is inside the boundary but not covered by a terrain unit "
                      "(insufficient DEM coverage)", x=x, y=y)


def query_profile(run_dir: Path, lat: float, lon: float, lead_hours: float, member: int | None = None) -> dict:
    meta = ForecastResultMeta.model_validate_json((run_dir / "manifest.json").read_text())
    domain = TerrainDomain.model_validate_json((run_dir / "domain.json").read_text())
    unit, (x, y) = locate_unit(domain, lat, lon)
    if not unit.supported:
        raise UnsupportedTerrain(f"terrain unit {unit.unit_id} is not simulated: {'; '.join(unit.unsupported_reasons)}",
                                 unit_id=unit.unit_id, reasons=unit.unsupported_reasons)
    if unit.unit_id in {k.split('/')[0] for k in meta.unit_failures}:
        raise UnsupportedTerrain(f"engine failed for unit {unit.unit_id} in this run", failures=meta.unit_failures)
    if lead_hours not in meta.request.output_lead_hours:
        raise InvalidInput(f"lead {lead_hours} h not in this run's outputs {meta.request.output_lead_hours}")
    recs = [ProfileRecord.model_validate_json(line)
            for line in (run_dir / "profiles" / f"{unit.unit_id}.jsonl").read_text().splitlines()]
    recs = [r for r in recs if r.lead_hours == lead_hours]
    member_id = 0 if member is None else member
    chosen = [r for r in recs if r.member_id == member_id]
    if not chosen:
        raise InvalidInput(f"member {member_id} not available (members 0..{meta.request.ensemble.members - 1})")
    import pandas as pd

    return {
        "status": "ok",
        "label": EXPERIMENTAL_LABEL,
        "synthetic_inputs": meta.synthetic_inputs,
        "run_id": meta.run_id,
        "query": {"lat": lat, "lon": lon, "x": x, "y": y, "crs": domain.crs, "lead_hours": lead_hours},
        "terrain_unit": {
            "unit_id": unit.unit_id, "polygon_xy": unit.polygon_xy, "centroid_lonlat": unit.centroid_lonlat,
            "unit_resolution_m": unit.resolution_m, "dem_resolution_m": domain.dem_resolution_m,
            "elevation_m": unit.elevation_m, "elevation_range_m": [unit.elevation_min_m, unit.elevation_max_m],
            "slope_deg": unit.slope_deg, "aspect_deg": unit.aspect_deg, "sky_view_factor": unit.sky_view_factor,
            "land_cover": unit.land_cover.value, "terrain_version": unit.terrain_version,
            "note": "profile represents the unit-mean terrain column, not the exact point",
        },
        "profile_member": member_id,
        "profile_is_actual_member": True,
        "profile": chosen[0].model_dump(mode="json"),
        "ensemble_summary": unit_lead_summary(recs, pd.Timestamp(meta.forecast_init_time)),
        "capability": meta.capability.model_dump(),
        "provenance": {
            "initial_state_id": meta.initial_state_id, "engine_version": meta.engine_version,
            "engine_config_hash": meta.engine_config_hash, "forcing_hash": meta.forcing_hash,
            "terrain_version": meta.terrain_version, "forecast_series_id": meta.request.forecast_series_id,
            "issue_time": meta.request.issue_time.isoformat(), "forecast_init_time": meta.forecast_init_time.isoformat(),
        },
    }


def dumps(obj: dict) -> str:
    return json.dumps(obj, indent=1, default=str)
