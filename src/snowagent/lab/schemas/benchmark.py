"""Benchmark-case contracts (build guide "Benchmark-case design"; the case builder is a later milestone).

A case is split in two types that never meet in an agent call:
- ``VisibleBenchmarkCase``: the only thing an agent receives. It has no field that can hold the target profile or
  any hidden truth (unknown fields are rejected), and it refuses any record that was not available at the case's
  as-of time (weather by availability time, forecasts by issue time, profiles and observations by availability).
- ``HiddenTruth``: the withheld pit and verification data, read by the evaluator only.
``CaseManifest`` describes a case for the harness and the evaluator (it names the target profile, so it is never
passed to an agent either).
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import Field, field_validator, model_validator

from snowagent.lab.schemas.common import AvailabilityAssumption, LabModel, SiteCode, UTCDateTime
from snowagent.lab.schemas.observation import Observation
from snowagent.lab.schemas.profile import SnowProfile
from snowagent.lab.schemas.site import ReferenceScenario
from snowagent.lab.schemas.weather import WeatherRecord

SHA256 = re.compile(r"^[0-9a-f]{64}$")


class CaseType(StrEnum):
    forecast_h72 = "forecast_h72"  # as-of 72 h before a pit; archived forecast weather issued at or before as-of
    next_pit = "next_pit"  # as-of at a pit, target the next pit at the plot; measured weather between them


class Split(StrEnum):
    development = "development"
    validation = "validation"
    sealed_test = "sealed_test"


class CaseManifest(LabModel):
    case_id: str
    case_type: CaseType
    site_code: SiteCode
    season: str
    split: Split
    as_of_time: UTCDateTime
    valid_time: UTCDateTime
    horizon_hours: float = Field(gt=0)
    scenario: ReferenceScenario
    target_profile_id: str  # evaluator side only
    visible_hashes: dict[str, str]  # visible file -> sha256
    hidden_hashes: dict[str, str]
    availability_assumption: AvailabilityAssumption
    warnings: list[str] = Field(default_factory=list)
    created_at: UTCDateTime
    builder_version: str

    @model_validator(mode="after")
    def _check(self) -> CaseManifest:
        if self.valid_time <= self.as_of_time:
            raise ValueError("valid_time must be after as_of_time")
        hours = (self.valid_time - self.as_of_time).total_seconds() / 3600
        if abs(hours - self.horizon_hours) > 0.5:
            raise ValueError(f"horizon_hours {self.horizon_hours} does not match valid - as_of = {hours:.1f} h")
        for name, hashes in (("visible_hashes", self.visible_hashes), ("hidden_hashes", self.hidden_hashes)):
            if not hashes:
                raise ValueError(f"{name} missing: every case file must be hashed")
            bad = [k for k, v in hashes.items() if not SHA256.match(v)]
            if bad:
                raise ValueError(f"{name}: not a sha256 for {bad}")
        return self


class VisibleBenchmarkCase(LabModel):
    case_id: str
    case_type: CaseType
    site_code: SiteCode
    as_of_time: UTCDateTime
    valid_time: UTCDateTime
    horizon_hours: float = Field(gt=0)
    scenario: ReferenceScenario
    weather_observed: list[WeatherRecord] = Field(default_factory=list)
    weather_forecasts: list[WeatherRecord] = Field(default_factory=list)
    permitted_profiles: list[SnowProfile] = Field(default_factory=list)
    permitted_observations: list[Observation] = Field(default_factory=list)
    availability_warnings: list[str] = Field(default_factory=list)

    @field_validator("weather_forecasts")
    @classmethod
    def _forecasts(cls, v: list[WeatherRecord]) -> list[WeatherRecord]:
        if any(r.kind != "forecast" for r in v):
            raise ValueError("weather_forecasts holds forecast records only")
        return v

    @model_validator(mode="after")
    def _no_future_data(self) -> VisibleBenchmarkCase:
        t = self.as_of_time
        if self.valid_time <= t:
            raise ValueError("valid_time must be after as_of_time")
        late = [f"weather {r.source_id} {r.observed_at.isoformat()}" for r in self.weather_observed
                if r.kind == "forecast" or r.available_at > t]
        late += [f"forecast issued {r.issued_at.isoformat()}" for r in self.weather_forecasts
                 if r.issued_at > t or (r.source_recorded_at is not None and r.source_recorded_at > t)]
        late += [f"profile {p.profile_id}" for p in self.permitted_profiles if p.observed_at > t or p.available_at > t]
        late += [f"observation {o.observation_id}" for o in self.permitted_observations
                 if o.observed_at > t or o.available_at > t]
        if late:
            raise ValueError(f"future data leakage: {len(late)} visible records not available at as-of "
                             f"{t.isoformat()} (first: {late[:3]})")
        other = {r.site_code for r in self.weather_observed + self.weather_forecasts} - {self.site_code}
        if other:
            raise ValueError(f"weather of other sites {sorted(other)} in a {self.site_code} case")
        return self


class HiddenTruth(LabModel):
    """Withheld data of one case: read by the evaluator, never passed to an agent."""

    case_id: str
    truth_profile: SnowProfile
    verification: dict[str, Any] = Field(default_factory=dict)
