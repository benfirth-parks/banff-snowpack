"""Canonical snow profile and layers (build guide "Snow profile records"; ADR-056).

Depths are metres from the snow surface, 0 at the surface and increasing downward; layers are ordered surface to
ground. Hard errors only where a record cannot be meant as written (a layer whose bottom is not below its top, a
negative depth, layers out of order). Everything else an old record may lack or get slightly wrong (no snow depth,
gaps, overlaps, unknown grain form) is a ``validation_warnings`` entry, never a rejection. The source record's
fields are kept unchanged in ``raw`` beside the normalized ones.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from snowagent.lab.schemas.common import AvailabilityAssumption, LabModel, SiteCode, UTCDateTime
from snowagent.obs.transcription import GRAIN_FORMS, MOISTURE

UNKNOWN_GRAIN = "UNKNOWN"
GRAIN_VOCABULARY = frozenset(GRAIN_FORMS) | {UNKNOWN_GRAIN}
_HARD = r"(F|4F|1F|P|K|I)[+-]?"
HARDNESS_CODE = re.compile(rf"^{_HARD}(-{_HARD})?$")  # OGRS hand hardness, or a range such as "P-K"
OVERLAP_TOLERANCE_M = 0.005  # half a centimetre of overlap or gap is rounding in the source, not a warning


class CriticalClass(StrEnum):
    """Broad class used for critical-layer scoring (``lab.ingest.mapping.critical_class``)."""

    surface_hoar = "surface_hoar"
    facets = "facets"
    depth_hoar = "depth_hoar"
    crust = "crust"  # melt-freeze or rain crust, ice layer
    other = "other"
    unknown = "unknown"  # no grain form recorded


class ProfileQuality(StrEnum):
    """How the profile was digitised: exact (structured file) or a transcription's confidence."""

    exact = "exact"
    high = "high"
    medium = "medium"
    low = "low"
    unknown = "unknown"


class SnowLayer(LabModel):
    layer_id: str
    profile_id: str
    top_depth_m: float = Field(ge=0)
    bottom_depth_m: float = Field(ge=0)
    grain_primary: str = UNKNOWN_GRAIN
    grain_secondary: str | None = None
    grain_size_mm: float | None = Field(default=None, ge=0)  # single value, or the midpoint of a range
    grain_size_max_mm: float | None = Field(default=None, ge=0)  # upper end of a range
    hardness: str | None = None  # OGRS code as recorded (layer top)
    hardness_index: float | None = Field(default=None, ge=0.5, le=6.5)  # F=1 .. I=6, +-1/3 steps
    wetness: str | None = None  # D M W V S
    density_kg_m3: float | None = Field(default=None, gt=0, le=1000)
    temperature_c: float | None = Field(default=None, le=0.5)  # pits record temperatures by depth, not per layer
    critical_class: CriticalClass = CriticalClass.unknown
    is_layer_of_concern: bool = False
    concern_basis: list[str] = Field(default_factory=list)
    confidence: ProfileQuality = ProfileQuality.unknown
    uncertain_fields: list[str] = Field(default_factory=list)
    date_tag: str | None = None  # observer's layer name, e.g. "Nov crust", "Jan 24"
    comment: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    @field_validator("grain_primary", "grain_secondary")
    @classmethod
    def _grain(cls, v: str | None) -> str | None:
        if v is not None and v not in GRAIN_VOCABULARY:
            raise ValueError(f"grain form {v!r} is not an IACS 2009 code (use {UNKNOWN_GRAIN!r} and keep the raw value)")
        return v

    @field_validator("hardness")
    @classmethod
    def _hardness(cls, v: str | None) -> str | None:
        if v is not None and not HARDNESS_CODE.match(v):
            raise ValueError(f"hardness {v!r} is not an OGRS hand-hardness code")
        return v

    @field_validator("wetness")
    @classmethod
    def _wetness(cls, v: str | None) -> str | None:
        if v is not None and v not in MOISTURE:
            raise ValueError(f"wetness {v!r} is not one of {sorted(MOISTURE)}")
        return v

    @model_validator(mode="after")
    def _thickness(self) -> SnowLayer:
        if self.bottom_depth_m <= self.top_depth_m:
            raise ValueError(f"layer {self.layer_id}: bottom depth {self.bottom_depth_m:.3f} m is not below top depth "
                             f"{self.top_depth_m:.3f} m (zero or negative thickness)")
        if self.is_layer_of_concern and not self.concern_basis:
            raise ValueError(f"layer {self.layer_id}: a layer of concern must say why (concern_basis)")
        return self

    @property
    def thickness_m(self) -> float:
        return self.bottom_depth_m - self.top_depth_m


class ProfileTemperature(LabModel):
    depth_m: float = Field(ge=0)
    temperature_c: float


class SnowProfile(LabModel):
    profile_id: str
    site_code: SiteCode
    plot_id: str
    observed_at: UTCDateTime
    source_recorded_at: UTCDateTime | None = None
    availability_assumption: AvailabilityAssumption = AvailabilityAssumption.observed_at
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    elevation_m: float | None = None
    aspect_deg: float | None = Field(default=None, ge=0, lt=360)
    slope_deg: float | None = Field(default=None, ge=0, lt=90)
    terrain_class: str = "unknown"
    observer_id: str | None = None
    source_id: str  # how it was digitised, e.g. structured:snowpro_3.0, transcription:vision_model
    profile_quality: ProfileQuality = ProfileQuality.unknown
    notes: str | None = None
    snow_depth_m: float | None = Field(default=None, ge=0)  # HS as recorded
    profile_depth_m: float | None = Field(default=None, ge=0)  # pit depth, when the pit did not reach the ground
    usable: bool = True  # False: the observed set marks it unusable (no layers, no time, parse error)
    duplicate_of: str | None = None  # same pit as another record (kept, flagged)
    review_reasons: list[str] = Field(default_factory=list)  # owner review list (ADR-050): excluded from scoring
    flags: list[str] = Field(default_factory=list)  # the source record's QC flags, unchanged
    validation_warnings: list[str] = Field(default_factory=list)
    temperatures: list[ProfileTemperature] = Field(default_factory=list)
    layers: list[SnowLayer] = Field(default_factory=list)
    raw: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _layers(self) -> SnowProfile:
        if self.source_recorded_at is None and self.availability_assumption != AvailabilityAssumption.observed_at:
            raise ValueError("without source_recorded_at the availability assumption must be observed_at")
        ids = [ly.layer_id for ly in self.layers]
        if len(set(ids)) != len(ids):
            raise ValueError(f"profile {self.profile_id}: duplicate layer ids")
        for ly in self.layers:
            if ly.profile_id != self.profile_id:
                raise ValueError(f"layer {ly.layer_id} belongs to profile {ly.profile_id}, not {self.profile_id}")
        for a, b in zip(self.layers, self.layers[1:], strict=False):
            if b.top_depth_m < a.top_depth_m or (b.top_depth_m == a.top_depth_m and b.bottom_depth_m < a.bottom_depth_m):
                raise ValueError(f"profile {self.profile_id}: layers out of order ({a.layer_id} at {a.top_depth_m:.3f} m "
                                 f"before {b.layer_id} at {b.top_depth_m:.3f} m); order surface to ground")
        return self

    @property
    def available_at(self):
        return self.source_recorded_at or self.observed_at


def structure_warnings(layers: list[SnowLayer], snow_depth_m: float | None) -> list[str]:
    """Warnings (not errors) about an ordered layer list: gaps, overlaps, layers below HS, unknown grain forms."""
    out: list[str] = []
    if not layers:
        return ["no_layers"]
    if layers[0].top_depth_m > OVERLAP_TOLERANCE_M:
        out.append(f"gap_at_surface_{layers[0].top_depth_m * 100:.1f}cm")
    for a, b in zip(layers, layers[1:], strict=False):
        d = b.top_depth_m - a.bottom_depth_m
        if d > OVERLAP_TOLERANCE_M:
            out.append(f"gap_{d * 100:.1f}cm_below_{a.layer_id}")
        elif d < -OVERLAP_TOLERANCE_M:
            out.append(f"overlap_{-d * 100:.1f}cm_{a.layer_id}_{b.layer_id}")
    if snow_depth_m is not None and layers[-1].bottom_depth_m > snow_depth_m + OVERLAP_TOLERANCE_M:
        out.append(f"layers_reach_{layers[-1].bottom_depth_m * 100:.1f}cm_below_snow_depth_{snow_depth_m * 100:.1f}cm")
    n_unknown = sum(ly.grain_primary == UNKNOWN_GRAIN for ly in layers)
    if n_unknown:
        out.append(f"grain_form_unknown_in_{n_unknown}_layers")
    return out
