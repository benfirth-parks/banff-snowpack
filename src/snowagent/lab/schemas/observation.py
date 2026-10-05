"""Generic observation record (build guide "Observation records"): stability tests, snow depths, avalanche or
public reports. The type-specific content stays in ``payload`` as recorded."""

from __future__ import annotations

from typing import Any

from pydantic import Field, model_validator

from snowagent.lab.schemas.common import AvailabilityAssumption, LabModel, SiteCode, UTCDateTime


class Observation(LabModel):
    observation_id: str
    site_code: SiteCode
    observed_at: UTCDateTime
    source_recorded_at: UTCDateTime | None = None
    availability_assumption: AvailabilityAssumption = AvailabilityAssumption.observed_at
    observation_type: str  # stability_test, snow_depth, avalanche, public_report, ...
    profile_id: str | None = None  # the pit it was recorded in, if any
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    elevation_m: float | None = None
    aspect_deg: float | None = Field(default=None, ge=0, lt=360)
    slope_deg: float | None = Field(default=None, ge=0, lt=90)
    terrain_class: str = "unknown"
    payload: dict[str, Any] = Field(default_factory=dict)
    quality_score: float | None = Field(default=None, ge=0, le=1)
    source_id: str
    provenance_id: str

    @model_validator(mode="after")
    def _availability(self) -> Observation:
        if self.source_recorded_at is None and self.availability_assumption != AvailabilityAssumption.observed_at:
            raise ValueError("without source_recorded_at the availability assumption must be observed_at")
        return self

    @property
    def available_at(self):
        return self.source_recorded_at or self.observed_at
