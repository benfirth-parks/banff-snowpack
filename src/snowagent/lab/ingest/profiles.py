"""Observed profiles (``data/interim/obs/observed_profiles.jsonl``, built by ``snowagent obs profiles``) -> canonical
``SnowProfile`` records and stability-test ``Observation`` records for the lab's three sites.

The observed set gives layers as height above ground in cm (``top_cm`` > ``bottom_cm``, HS in ``hs_cm``), except
depth charts without HS, which stay depths from the surface (``height_reference`` = depth_from_surface). Converted
here to metres below the surface, 0 at the surface:
- heights: depth = (surface - height) / 100 with surface = HS; without HS, or with a top layer above HS, the
  surface is the top of the highest layer (warning);
- depths: depth = value / 100.
Nothing is filled. A layer without both boundaries, or with zero thickness after conversion, is left out of the
normalized layers with a warning and stays in ``raw``. Every source field is kept unchanged in ``raw``.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Any

from snowagent.lab.ingest.mapping import layer_of_concern
from snowagent.lab.schemas.common import SiteCode
from snowagent.lab.schemas.observation import Observation
from snowagent.lab.schemas.profile import (
    GRAIN_VOCABULARY,
    HARDNESS_CODE,
    UNKNOWN_GRAIN,
    ProfileQuality,
    ProfileTemperature,
    SnowLayer,
    SnowProfile,
    structure_warnings,
)
from snowagent.lab.settings import LabConfig
from snowagent.obs.observed import review_reasons
from snowagent.obs.transcription import MOISTURE

HS_TOLERANCE_CM = 0.5
ASPECT_DEG = {"N": 0.0, "NNE": 22.5, "NE": 45.0, "ENE": 67.5, "E": 90.0, "ESE": 112.5, "SE": 135.0, "SSE": 157.5,
              "S": 180.0, "SSW": 202.5, "SW": 225.0, "WSW": 247.5, "W": 270.0, "WNW": 292.5, "NW": 315.0,
              "NNW": 337.5}
_WORDS = (("north", "N"), ("south", "S"), ("east", "E"), ("west", "W"))


def parse_aspect(text: str | None) -> float | None:
    """'NE' -> 45, '135° SE' -> 135, 'South West' -> 225; 'inapplicable', 'N/A', None -> None."""
    if text is None:
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*°", str(text))
    if m:
        return float(m[1]) % 360
    t = str(text).lower()
    for word, letter in _WORDS:
        t = t.replace(word, letter)
    t = re.sub(r"[\s\-]", "", t).upper()
    return ASPECT_DEG.get(t)


def depth_converter(rec: dict) -> tuple[Callable[[float | None], float | None], float | None, list[str]]:
    """(cm value -> depth below the surface in m, surface height used in cm, warnings) for one observed record."""
    hs = rec.get("hs_cm")
    if rec.get("height_reference") == "depth_from_surface":
        warn = [] if hs is not None else ["snow_depth_unknown"]
        return (lambda v: None if v is None else v / 100.0), None, warn
    tops = [ly["top_cm"] for ly in rec.get("layers") or [] if ly.get("top_cm") is not None]
    top = max(tops) if tops else None
    warnings: list[str] = []
    if hs is None:
        surface = top
        if top is not None:
            warnings.append("snow_depth_unknown_surface_taken_at_top_of_highest_layer")
    elif top is not None and top > hs + HS_TOLERANCE_CM:
        surface = top
        warnings.append(f"highest_layer_top_{top:g}cm_above_recorded_hs_{hs:g}cm_surface_taken_at_layer_top")
    else:
        surface = hs
    if surface is None:
        return (lambda v: None), None, warnings
    return (lambda v: None if v is None else (surface - v) / 100.0), surface, warnings


def _grain_size(size: Any) -> tuple[float | None, float | None]:
    if not size:
        return None, None
    vals = [float(x) for x in (size if isinstance(size, list | tuple) else [size])]
    lo, hi = min(vals), max(vals)
    return (lo, None) if lo == hi else ((lo + hi) / 2, hi)


def _quality(rec: dict) -> ProfileQuality:
    c = (rec.get("provenance") or {}).get("confidence")
    return ProfileQuality(c) if c in ProfileQuality.__members__ else ProfileQuality.unknown


def to_layers(rec: dict, profile_id: str, conv: Callable[[float | None], float | None], quality: ProfileQuality
              ) -> tuple[list[SnowLayer], list[str]]:
    """Normalized layers (surface to ground) and warnings; layers that cannot be placed are left out (in raw)."""
    rows, warnings = [], []
    for i, ly in enumerate(rec.get("layers") or []):
        top, bottom = conv(ly.get("top_cm")), conv(ly.get("bottom_cm"))
        if top is None or bottom is None:
            warnings.append(f"raw_layer_{i}_missing_boundary_left_out")
            continue
        if bottom <= top:
            warnings.append(f"raw_layer_{i}_zero_or_negative_thickness_left_out")
            continue
        if top < 0:
            warnings.append(f"raw_layer_{i}_above_surface_left_out")
            continue
        g1, g2 = ly.get("grain_form"), ly.get("grain_form_2")
        if g1 is not None and g1 not in GRAIN_VOCABULARY:
            warnings.append(f"raw_layer_{i}_grain_form_{g1}_not_iacs")
            g1 = None
        if g2 is not None and g2 not in GRAIN_VOCABULARY:
            warnings.append(f"raw_layer_{i}_grain_form_2_{g2}_not_iacs")
            g2 = None
        hard = ly.get("hardness")
        if hard is not None and not HARDNESS_CODE.match(hard):
            warnings.append(f"raw_layer_{i}_hardness_{hard}_not_ogrs")
            hard = None
        wet = ly.get("moisture")
        if wet is not None and wet not in MOISTURE:
            warnings.append(f"raw_layer_{i}_wetness_{wet}_unknown")
            wet = None
        rho = ly.get("density_kg_m3")
        if rho is not None and not 0 < rho <= 1000:
            warnings.append(f"raw_layer_{i}_density_{rho}_implausible")
            rho = None
        size, size_max = _grain_size(ly.get("grain_size_mm"))
        hi = ly.get("hardness_index")
        cls, concern, basis = layer_of_concern(g1, g2, ly.get("date_tag"))
        rows.append(dict(top_depth_m=top, bottom_depth_m=bottom, grain_primary=g1 or UNKNOWN_GRAIN,
                         grain_secondary=g2, grain_size_mm=size, grain_size_max_mm=size_max, hardness=hard,
                         hardness_index=hi if hi is not None and 0.5 <= hi <= 6.5 else None, wetness=wet,
                         density_kg_m3=rho, critical_class=cls, is_layer_of_concern=concern, concern_basis=basis,
                         confidence=quality, uncertain_fields=list(ly.get("uncertain_fields") or []),
                         date_tag=ly.get("date_tag"), comment=ly.get("comment"), raw=ly | {"raw_index": i}))
    rows.sort(key=lambda r: (r["top_depth_m"], r["bottom_depth_m"]))
    return [SnowLayer(layer_id=f"{profile_id}_L{k:02d}", profile_id=profile_id, **r) for k, r in enumerate(rows)], warnings


def profile_from_observed(rec: dict, site: SiteCode) -> SnowProfile:
    """One observed-set record -> ``SnowProfile`` (raises ValueError when it has no observation time)."""
    if not rec.get("obs_time_utc"):
        raise ValueError(f"{rec.get('profile_id')}: no observation time")
    pid = rec["profile_id"]
    conv, _surface, warnings = depth_converter(rec)
    quality = _quality(rec)
    layers, lw = to_layers(rec, pid, conv, quality)
    hs = rec.get("hs_cm")
    snow_depth = hs / 100.0 if hs is not None else None
    temps = []
    for t in rec.get("temperatures") or []:
        d = conv(t.get("height_cm"))
        if d is None or t.get("t_c") is None or d < 0:
            warnings.append("temperature_reading_without_valid_depth_left_out")
            continue
        temps.append(ProfileTemperature(depth_m=d, temperature_c=t["t_c"]))
    temps.sort(key=lambda t: t.depth_m)
    slope = rec.get("slope_deg")
    prov = rec.get("provenance") or {}
    return SnowProfile(
        profile_id=pid, site_code=site, plot_id=rec["site_key"], observed_at=rec["obs_time_utc"],
        latitude=rec.get("lat"), longitude=rec.get("lon"), elevation_m=rec.get("elevation_m"),
        aspect_deg=parse_aspect(rec.get("aspect")), slope_deg=slope if slope is not None and 0 <= slope < 90 else None,
        terrain_class=rec.get("category") or "unknown", source_id=prov.get("method") or "unknown",
        profile_quality=quality, notes=rec.get("comments"), snow_depth_m=snow_depth,
        profile_depth_m=rec["profile_depth_cm"] / 100.0 if rec.get("profile_depth_cm") is not None else None,
        usable=not rec.get("unusable") and bool(layers), duplicate_of=rec.get("duplicate_of"),
        review_reasons=review_reasons(rec), flags=list(rec.get("flags") or []),
        validation_warnings=sorted(set(warnings)) + lw + structure_warnings(layers, snow_depth),
        temperatures=temps, layers=layers, raw={k: v for k, v in rec.items() if k != "layers"})


def tests_from_observed(rec: dict, profile: SnowProfile, provenance_id: str) -> list[Observation]:
    """The pit's stability tests as ``stability_test`` observations (payload: the test as recorded plus depth_m)."""
    conv, _s, _w = depth_converter(rec)
    out = []
    for i, t in enumerate(rec.get("tests") or []):
        out.append(Observation(
            observation_id=f"{profile.profile_id}_T{i:02d}", site_code=profile.site_code,
            observed_at=profile.observed_at, observation_type="stability_test", profile_id=profile.profile_id,
            latitude=profile.latitude, longitude=profile.longitude, elevation_m=profile.elevation_m,
            terrain_class=profile.terrain_class, payload=t | {"depth_m": conv(t.get("height_cm"))},
            source_id=profile.source_id, provenance_id=provenance_id))
    return out


def import_profiles(records: Iterable[dict], config: LabConfig, provenance_id: str
                    ) -> tuple[list[SnowProfile], list[Observation], dict]:
    """Records of the lab's sites -> profiles and test observations; others are counted, not converted."""
    profiles: list[SnowProfile] = []
    observations: list[Observation] = []
    skipped: dict[str, int] = {}
    skipped_ids: dict[str, list[str]] = {}
    for rec in records:
        site = config.site_by_plot(rec.get("site_key") or "")
        if site is None:
            reason = f"not_a_lab_site:{rec.get('site_key') or rec.get('category') or 'unassigned'}"
            skipped[reason] = skipped.get(reason, 0) + 1
            continue
        try:
            p = profile_from_observed(rec, site.code)
        except ValueError as exc:
            reason = "no_observation_time" if "no observation time" in str(exc) else "invalid_record"
            skipped[reason] = skipped.get(reason, 0) + 1
            skipped_ids.setdefault(reason, []).append(f"{rec.get('profile_id')}: {str(exc)[:200]}")
            continue
        profiles.append(p)
        observations += tests_from_observed(rec, p, provenance_id)
    return profiles, observations, {"skipped": skipped, "skipped_ids": skipped_ids}
