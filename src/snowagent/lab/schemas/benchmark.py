"""Benchmark-case contracts (build guide "Benchmark-case design"; the case builder is ``lab.benchmark``, ADR-059).

A case is split in two types that never meet in an agent call:
- ``VisibleBenchmarkCase``: the only thing an agent receives. It is anonymous (owner, 2026-10-05: agents must not
  memorize snowpacks): an opaque ``case_key``, times in hours relative to the as-of time plus the UTC day of year
  (for solar geometry) and never a calendar date or year, pits as ``pit_01``.. without profile, observer or pit
  identifiers, free text or source coordinates. It has no field for the target pit or any hidden truth (unknown
  fields are rejected), and it refuses any record not available at as-of (relative availability time > 0), any
  forecast issued after as-of, and measured stand-in weather unless the case says so (``forecast_source``).
- ``HiddenTruth``: the withheld pit and verification data, read by the evaluator only.
``CaseManifest`` describes a case for the harness and the evaluator: the real ids (case id, target pit, the pit
behind each visible ``pit_key``), the season and split, hashes and assumptions. It is never passed to an agent.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from snowagent.lab.schemas.common import AvailabilityAssumption, LabModel, QualityFlag, SiteCode, UTCDateTime
from snowagent.lab.schemas.profile import CriticalClass, ProfileQuality, ProfileTemperature, SnowProfile
from snowagent.lab.schemas.site import ReferenceScenario, Site
from snowagent.lab.schemas.weather import check_values

SHA256 = re.compile(r"^[0-9a-f]{64}$")
CASE_KEY = re.compile(r"^[0-9a-f]{16}$")
PIT_KEY = re.compile(r"^pit_\d{2,4}$")
TOLERANCE_H = 1e-6


class CaseType(StrEnum):
    forecast_h72 = "forecast_h72"  # as-of when the archived run reaching the pit is available (ADR-060), or 72 h
    # before the pit with a measured stand-in
    next_pit = "next_pit"  # as-of when the previous pit became available; measured stand-in weather to the pit


class SplitMode(StrEnum):
    """How seasons are assigned (config/lab.yaml ``splits.mode``; ADR-059)."""

    all = "all"  # every configured season is training data, nothing sealed (owner's default, 2026-10-05)
    split = "split"  # development / validation / sealed test
    loso = "loso"  # leave one season out: one named holdout season, the others training (CLAUDE.md principle 3)


class Split(StrEnum):
    training = "training"  # modes all and loso
    holdout = "holdout"  # mode loso: the held-out season
    development = "development"
    validation = "validation"
    sealed_test = "sealed_test"


class TargetScope(StrEnum):
    """What the withheld pit can verify: the full profile, or only snow depth (a pit without placed layers)."""

    full_profile = "full_profile"
    depth_only = "depth_only"


class ForecastSource(StrEnum):
    """Where a case's weather after as-of comes from."""

    archived_gfs = "archived_gfs"  # an archived GFS run issued (and available) at or before as-of
    measured_standin = "measured_standin"  # measured weather after as-of, given as a forecast issued at as-of


# --------------------------------------------------------------------------------------------- visible (agent side)


class VisibleWeatherHour(LabModel):
    """One hour of weather as a case shows it: times relative to as-of (hours), no calendar date."""

    t_rel_h: float  # end of the hourly interval minus as-of, in hours (negative: before as-of)
    day_of_year: float = Field(ge=1, lt=367)  # UTC day of year of the hour, with fraction (1.0 = 1 Jan 00 UTC)
    kind: Literal["observed", "reanalysis", "forecast", "perfect_forecast"]
    source_id: str  # plot_stations, gfs025:<point>, measured_standin
    issued_rel_h: float | None = None  # forecasts and stand-ins: issue time minus as-of (stand-in: 0)
    available_rel_h: float  # when the value became available minus as-of (assumed delay; must be <= 0)
    availability_assumption: AvailabilityAssumption
    air_temperature_k: float | None = Field(default=None, gt=0)
    relative_humidity_frac: float | None = Field(default=None, ge=0)
    precipitation_mm: float | None = None
    wind_speed_ms: float | None = Field(default=None, ge=0)
    wind_direction_deg: float | None = Field(default=None, ge=0, le=360)
    snow_depth_m: float | None = None
    swe_mm: float | None = None
    shortwave_radiation_wm2: float | None = None
    longwave_radiation_wm2: float | None = None
    station_pressure_pa: float | None = Field(default=None, gt=0)
    sources: dict[str, str] = Field(default_factory=dict)
    qc: dict[str, QualityFlag] = Field(default_factory=dict)
    quality_flag: QualityFlag = QualityFlag.missing

    @model_validator(mode="after")
    def _check(self) -> VisibleWeatherHour:
        check_values(self)
        if self.kind in ("forecast", "perfect_forecast") and self.issued_rel_h is None:
            raise ValueError(f"{self.kind} hours need issued_rel_h")
        return self


class VisibleLayer(LabModel):
    """A pit layer without ids, observer tags or free text."""

    top_depth_m: float = Field(ge=0)
    bottom_depth_m: float = Field(ge=0)
    grain_primary: str
    grain_secondary: str | None = None
    grain_size_mm: float | None = Field(default=None, ge=0)
    grain_size_max_mm: float | None = Field(default=None, ge=0)
    hardness: str | None = None
    hardness_index: float | None = Field(default=None, ge=0.5, le=6.5)
    wetness: str | None = None
    density_kg_m3: float | None = Field(default=None, gt=0, le=1000)
    temperature_c: float | None = Field(default=None, le=0.5)
    critical_class: CriticalClass = CriticalClass.unknown
    is_layer_of_concern: bool = False
    concern_basis: list[str] = Field(default_factory=list)  # grain_class:<class>:<form>, or "observer_tag" (no text)
    confidence: ProfileQuality = ProfileQuality.unknown
    uncertain_fields: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _thickness(self) -> VisibleLayer:
        if self.bottom_depth_m <= self.top_depth_m:
            raise ValueError("layer bottom is not below its top")
        return self


class VisiblePit(LabModel):
    """An earlier pit available at as-of, anonymous: ``pit_key`` (pit_01 = oldest) instead of its profile id."""

    pit_key: str
    t_rel_h: float  # observation time minus as-of (hours, negative)
    day_of_year: float = Field(ge=1, lt=367)
    season_offset: int = Field(le=0)  # 0: the case's season, -1: the season before, ...
    available_rel_h: float
    availability_assumption: AvailabilityAssumption
    aspect_deg: float | None = Field(default=None, ge=0, lt=360)
    slope_deg: float | None = Field(default=None, ge=0, lt=90)
    terrain_class: str = "unknown"
    profile_quality: ProfileQuality = ProfileQuality.unknown
    snow_depth_m: float | None = Field(default=None, ge=0)
    profile_depth_m: float | None = Field(default=None, ge=0)
    temperatures: list[ProfileTemperature] = Field(default_factory=list)
    layers: list[VisibleLayer] = Field(default_factory=list)

    @model_validator(mode="after")
    def _key(self) -> VisiblePit:
        if not PIT_KEY.match(self.pit_key):
            raise ValueError(f"pit_key {self.pit_key!r} is not an anonymous key like pit_01")
        return self


class VisibleObservation(LabModel):
    """A test recorded in a visible pit: type and result, no raw text, comment or layer tag."""

    pit_key: str
    observation_type: str
    t_rel_h: float
    available_rel_h: float
    payload: dict[str, Any] = Field(default_factory=dict)


class VisibleForecastRun(LabModel):
    source_id: str  # gfs025:<point>
    issued_rel_h: float
    available_rel_h: float
    surface_elevation_m: float | None = None  # values are at the model's surface height at the point
    max_lead_h: float = Field(ge=0)


class VisibleBenchmarkCase(LabModel):
    case_key: str  # opaque (random) key; the dated case id is in the manifest only
    case_type: CaseType
    site_code: SiteCode
    as_of_day_of_year: float = Field(ge=1, lt=367)
    horizon_hours: float = Field(gt=0)  # valid time minus as-of
    scenario: ReferenceScenario
    site: Site | None = None
    forecast_source: ForecastSource
    weather_observed: list[VisibleWeatherHour] = Field(default_factory=list)
    weather_forecasts: list[VisibleWeatherHour] = Field(default_factory=list)  # archived run, or measured stand-in
    forecast_runs: list[VisibleForecastRun] = Field(default_factory=list)
    permitted_pits: list[VisiblePit] = Field(default_factory=list)
    permitted_observations: list[VisibleObservation] = Field(default_factory=list)
    availability_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_future_data(self) -> VisibleBenchmarkCase:
        if not CASE_KEY.match(self.case_key):
            raise ValueError("case_key must be an opaque 16-hex key, not a dated case id")
        tol = TOLERANCE_H
        late = [f"weather {r.source_id} t{r.t_rel_h:+.1f}h" for r in self.weather_observed
                if r.kind not in ("observed", "reanalysis") or r.available_rel_h > tol or r.t_rel_h > tol]
        standin = self.forecast_source == ForecastSource.measured_standin
        for r in self.weather_forecasts:
            if r.issued_rel_h is None or r.issued_rel_h > tol or r.available_rel_h > tol:
                late.append(f"forecast hour issued {r.issued_rel_h}h after as-of")
            elif r.t_rel_h > self.horizon_hours + tol:
                late.append(f"forecast hour t{r.t_rel_h:+.1f}h beyond the valid time")
            elif (r.kind == "perfect_forecast") != standin or (standin and abs(r.issued_rel_h) > tol):
                late.append(f"{r.kind} hour in a case whose forecast source is {self.forecast_source}")
        late += [f"forecast run {r.source_id} issued {r.issued_rel_h:+.1f}h" for r in self.forecast_runs
                 if r.issued_rel_h > tol or r.available_rel_h > tol]
        if standin and self.forecast_runs:
            late.append("a measured stand-in case lists forecast runs")
        late += [f"pit {p.pit_key} t{p.t_rel_h:+.1f}h" for p in self.permitted_pits
                 if p.t_rel_h > tol or p.available_rel_h > tol]
        late += [f"observation in {o.pit_key}" for o in self.permitted_observations
                 if o.t_rel_h > tol or o.available_rel_h > tol]
        if late:
            raise ValueError(f"future data leakage: {len(late)} visible records not available at as-of "
                             f"(first: {late[:3]})")
        keys = [p.pit_key for p in self.permitted_pits]
        if len(set(keys)) != len(keys):
            raise ValueError("pit keys repeat")
        unknown = {o.pit_key for o in self.permitted_observations} - set(keys)
        if unknown:
            raise ValueError(f"observations of pits not in the case: {sorted(unknown)}")
        if self.site is not None and self.site.code != self.site_code:
            raise ValueError(f"site {self.site.code} in a {self.site_code} case")
        return self


# --------------------------------------------------------------------------------------------- evaluator side


class ForecastRun(LabModel):
    """An archived forecast run used by a case (manifest: real times and the file)."""

    source_id: str  # e.g. gfs025:bow_summit_plot
    point: str  # the archived point extract
    issued_at: UTCDateTime  # initial time (from the archive file name, checked against the file's run_utc)
    available_at: UTCDateTime  # issued_at + configured latency
    surface_elevation_m: float | None = None
    max_lead_h: float = Field(ge=0)
    file: str  # archive file (relative to the source checkout)
    sha256: str

    @model_validator(mode="after")
    def _check(self) -> ForecastRun:
        if self.available_at < self.issued_at:
            raise ValueError("a forecast run cannot be available before it is issued")
        if not SHA256.match(self.sha256):
            raise ValueError("forecast run sha256 must be a sha256 hex digest")
        return self


class CaseManifest(LabModel):
    case_id: str
    case_type: CaseType
    site_code: SiteCode
    season: str  # the target's season key: M3 analogue agents are barred from drawing on it
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
    # added with the case builder (ADR-059); defaults keep milestone-1 manifests valid
    case_key: str | None = None  # the opaque key the visible package carries
    case_set: str | None = None  # all, split, loso_<season>: the directory the case lives in
    split_mode: SplitMode | None = None
    holdout_season: str | None = None  # mode loso
    target_scope: TargetScope = TargetScope.full_profile
    target_copies: list[str] = Field(default_factory=list)  # duplicate records of the target pit (never visible)
    anchor_profile_id: str | None = None  # next_pit: the previous pit whose availability sets as_of
    forecast_source: ForecastSource | None = None
    forecast_runs: list[ForecastRun] = Field(default_factory=list)
    forecast_standin: dict[str, Any] | None = None  # measured stand-in: hours, coverage, withheld variables
    availability_rules: dict[str, str] = Field(default_factory=dict)  # visible table -> how availability was set
    availability_provisional: bool = False  # an assumed delay awaits the owner's confirmation
    split_provisional: bool = False  # the season split awaits the owner's confirmation
    pit_keys: dict[str, str] = Field(default_factory=dict)  # visible pit_key -> profile_id (lineage, scoring)
    visible_profile_ids: list[str] = Field(default_factory=list)  # profile_ids used (CLAUDE.md principle 2)
    visible_counts: dict[str, int] = Field(default_factory=dict)  # visible table -> rows
    excluded_counts: dict[str, int] = Field(default_factory=dict)  # "<table>:<reason>" -> records not shown
    build_run_id: str | None = None
    config_hash: str | None = None
    data_hash: str | None = None

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
        for name in ("config_hash", "data_hash"):
            v = getattr(self, name)
            if v is not None and not SHA256.match(v):
                raise ValueError(f"{name} must be a sha256 hex digest")
        if self.case_key is not None and not CASE_KEY.match(self.case_key):
            raise ValueError("case_key must be an opaque 16-hex key")
        used = set(self.visible_profile_ids) | set(self.pit_keys.values())
        if self.target_profile_id in used or set(self.target_copies) & used:
            raise ValueError("the target profile (or a copy of it) is listed among the visible profiles")
        if self.case_type == CaseType.next_pit and self.anchor_profile_id is None:
            raise ValueError("a next_pit case names its anchor (previous) pit")
        late = [r.source_id for r in self.forecast_runs if r.issued_at > self.as_of_time or r.available_at > self.as_of_time]
        if late:
            raise ValueError(f"forecast runs issued or available after as_of: {late}")
        if self.forecast_source == ForecastSource.measured_standin and self.forecast_runs:
            raise ValueError("a measured stand-in case uses no forecast run")
        if self.forecast_source == ForecastSource.archived_gfs and not self.forecast_runs:
            raise ValueError("an archived_gfs case names its forecast run")
        if self.split_mode == SplitMode.loso and not self.holdout_season:
            raise ValueError("a loso case names its holdout season")
        return self


class HiddenTruth(LabModel):
    """Withheld data of one case: read by the evaluator, never passed to an agent."""

    case_id: str
    truth_profile: SnowProfile
    verification: dict[str, Any] = Field(default_factory=dict)
