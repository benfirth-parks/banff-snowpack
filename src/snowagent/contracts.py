"""Typed, validated data contracts (pydantic v2).

Conventions (see docs/decisions.md):
- All times are timezone-aware UTC. Naive datetimes are rejected.
- Internal units are SI. Heights/thicknesses are metres and always carry an
  explicit geometry: ``vertical`` (measured along gravity) or ``slope_normal``.
- Mass fluxes are kg m-2 per *horizontal* area unless the field name says
  ``per_slope_area``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, field_validator, model_validator

EXPERIMENTAL_LABEL = (
    "EXPERIMENTAL snowpack-structure prediction for expert decision support. "
    "Not an avalanche forecast, danger rating or operational guidance."
)


def _require_utc(v: datetime) -> datetime:
    if v.tzinfo is None or v.utcoffset() is None:
        raise ValueError("naive datetime rejected: times must be timezone-aware UTC")
    return v.astimezone(UTC)


UTCDateTime = Annotated[datetime, AfterValidator(_require_utc)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --------------------------------------------------------------------------- provenance


class Provenance(Strict):
    source: str
    description: str = ""
    sha256: str | None = None
    created_utc: UTCDateTime | None = None
    synthetic: bool
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- terrain


class LandCover(StrEnum):
    open = "open"  # alpine / meadow / avalanche path: supported
    forest = "forest"  # canopy scheme not enabled: unsupported
    rock = "rock"  # supported (bare-rock substrate assumption)
    glacier = "glacier"  # unsupported until ice substrate is configured
    water = "water"  # unsupported
    unknown = "unknown"


SUPPORTED_LAND_COVER = {LandCover.open, LandCover.rock}


class TerrainUnit(Strict):
    unit_id: str
    domain_id: str
    terrain_version: str
    crs: str
    # polygon ring in CRS coordinates (closed ring not required)
    polygon_xy: list[tuple[float, float]]
    centroid_xy: tuple[float, float]
    centroid_lonlat: tuple[float, float]
    resolution_m: float = Field(gt=0)
    n_dem_cells: int = Field(gt=0)
    area_planimetric_m2: float = Field(gt=0)
    area_surface_m2: float = Field(gt=0)
    elevation_m: float
    elevation_min_m: float
    elevation_max_m: float
    slope_deg: float = Field(ge=0, lt=90)
    # 0 = north, clockwise. None only when slope is below the flat threshold.
    aspect_deg: float | None = Field(default=None, ge=0, lt=360)
    horizon_azimuths_deg: list[float]
    horizon_elevation_deg: list[float]
    sky_view_factor: float = Field(ge=0, le=1)
    land_cover: LandCover
    # directional exposure is a PROXY (Winstral-type Sx), never a deposition model
    exposure_sx_deg: dict[str, float] = Field(default_factory=dict)
    neighbour_ids: list[str] = Field(default_factory=list)
    supported: bool
    unsupported_reasons: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> TerrainUnit:
        if len(self.horizon_azimuths_deg) != len(self.horizon_elevation_deg):
            raise ValueError("horizon azimuth/elevation lengths differ")
        if self.supported and self.unsupported_reasons:
            raise ValueError("supported unit cannot carry unsupported_reasons")
        if not self.supported and not self.unsupported_reasons:
            raise ValueError("unsupported unit must state why")
        return self


class TerrainDomain(Strict):
    domain_id: str
    terrain_version: str
    crs: str
    dem_resolution_m: float
    unit_resolution_m: float
    boundary_xy: list[tuple[float, float]]
    units: list[TerrainUnit]
    provenance: Provenance
    assumptions: list[str] = Field(default_factory=list)

    def unit(self, unit_id: str) -> TerrainUnit:
        for u in self.units:
            if u.unit_id == unit_id:
                return u
        raise KeyError(unit_id)


# --------------------------------------------------------------------------- weather


class WeatherKind(StrEnum):
    actuals = "actuals"  # observed station data
    historical_forcing = "historical_forcing"  # reanalysis / labelled historical forcing
    forecast = "forecast"  # NWP forecast run


class WeatherMeta(Strict):
    """Metadata for one weather series. Values live in a validated DataFrame."""

    series_id: str
    kind: WeatherKind
    source: str
    model: str | None = None
    model_version: str | None = None
    station_id: str | None = None
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    source_elevation_m: float
    elevation_adjusted_by_provider: bool = False
    timestamp_convention: Literal["end_of_interval"]
    accumulation_interval_s: int = Field(gt=0)
    # forecasts: model initialisation and when the run became available to us
    issue_time: UTCDateTime | None = None
    available_time: UTCDateTime | None = None
    # actuals/historical: each record becomes available at valid_time + latency
    availability_latency_s: int | None = Field(default=None, ge=0)
    wind_height_m: float = Field(gt=0)
    met_height_m: float = Field(gt=0)
    provenance: Provenance

    @model_validator(mode="after")
    def _check_times(self) -> WeatherMeta:
        if self.kind == WeatherKind.forecast:
            if self.issue_time is None or self.available_time is None:
                raise ValueError("forecast series require issue_time and available_time")
            if self.available_time < self.issue_time:
                raise ValueError("available_time precedes issue_time")
        elif self.availability_latency_s is None:
            raise ValueError("actuals/historical series require availability_latency_s")
        return self


# --------------------------------------------------------------------------- profiles


class Geometry(StrEnum):
    vertical = "vertical"
    slope_normal = "slope_normal"


class NullReason(Strict):
    field: str
    reason: str


class Layer(Strict):
    """One engine element (or a documented aggregate) in a predicted profile.

    Depth/height fields are metres. ``*_vertical`` is along gravity, ``*_slope_normal``
    is perpendicular to the slope surface; the engine stores slope-normal geometry.
    """

    index_from_bottom: int = Field(ge=0)
    top_vertical_m: float = Field(ge=0)
    bottom_vertical_m: float = Field(ge=0)
    thickness_vertical_m: float = Field(gt=0)
    thickness_slope_normal_m: float = Field(gt=0)
    depth_top_vertical_m: float = Field(ge=0)
    density_kg_m3: float = Field(gt=0, le=1000)
    temperature_c: float = Field(le=0.5)
    lwc_vol_frac: float = Field(ge=0, le=1)
    ice_vol_frac: float | None = Field(default=None, ge=0, le=1)
    grain_code_swiss: int | None = None
    grain_form_primary: str | None = None
    grain_form_secondary: str | None = None
    grain_size_mm: float | None = Field(default=None, ge=0)
    bond_size_mm: float | None = Field(default=None, ge=0)
    dendricity: float | None = None
    sphericity: float | None = None
    hand_hardness_index: float | None = None
    temperature_gradient_k_m: float | None = None
    deposition_time: UTCDateTime | None = None
    lineage_id: str | None = None
    is_crust: bool = False
    melt_freeze_marker: bool = False  # engine F3 == 2: layer has melt-freeze history
    is_candidate_weak_layer: bool = False
    candidate_weak_layer_basis: str | None = None
    sk38: float | None = None


class ProfileDiagnostics(Strict):
    hs_vertical_m: float = Field(ge=0)
    hs_slope_normal_m: float = Field(ge=0)
    swe_kg_m2_per_slope_area: float = Field(ge=0)
    swe_kg_m2_per_horizontal_area: float = Field(ge=0)
    n_layers: int = Field(ge=0)
    surface_hoar_size_mm: float | None = None


class CapabilityFlags(Strict):
    transport_status: Literal["unresolved", "resolved"] = "unresolved"
    transport_note: str = (
        "Independent terrain-conditioned columns. No wind erosion/deposition between units; "
        "lee-slope loading is NOT represented and must not be inferred from exposure."
    )
    virtual_slope_redistribution: Literal["disabled"] = "disabled"
    column_wind_erosion: Literal["disabled"] = "disabled"
    canopy: Literal["unsupported"] = "unsupported"
    gravitational_redistribution: Literal["unsupported"] = "unsupported"
    wind_downscaling: Literal["none"] = "none"
    longwave_terrain_treatment: Literal["simplified_sky_view"] = "simplified_sky_view"
    instability_model: Literal["none"] = "none"
    instability_note: str = (
        "No calibrated instability classifier is available. Candidate weak layers are "
        "grain-form based structure flags, not instability or danger."
    )
    hardness_source: str = "SNOWPACK hand-hardness parameterization (uncalibrated for this region)"
    uncertainty_kind: Literal["scenario_spread"] = "scenario_spread"
    uncertainty_note: str = (
        "Ensemble spread is scenario uncertainty from configured perturbations; it is not "
        "a calibrated probability or a confidence guarantee."
    )


class ProfileRecord(Strict):
    run_id: str
    unit_id: str
    member_id: int
    valid_time: UTCDateTime
    lead_hours: float
    slope_deg: float
    aspect_deg: float | None
    layers: list[Layer]
    diagnostics: ProfileDiagnostics
    nulls: list[NullReason] = Field(default_factory=list)
    lineage_basis: str
    identity_uncertainty: str | None = None


# --------------------------------------------------------------------------- state / runs


class UnitState(Strict):
    unit_id: str
    sno_file: str  # relative to checkpoint dir
    sha256: str
    hs_vertical_m: float
    swe_kg_m2_per_slope_area: float


class StateCheckpoint(Strict):
    state_id: str
    domain_id: str
    analysis_time: UTCDateTime
    analysis_version: int = Field(ge=1)
    parent_state_id: str | None
    supersedes_state_id: str | None = None
    terrain_version: str
    engine_version: str
    engine_config_hash: str
    parameter_version: str
    forcing_lineage: list[str]
    forcing_hash: str
    # latest availability time of any datum (weather or observation) used
    assimilation_cutoff: UTCDateTime
    observations_used: list[str] = Field(default_factory=list)
    initialization: str
    member_weights: dict[str, float] = Field(default_factory=lambda: {"0": 1.0})
    units: list[UnitState]
    synthetic: bool
    created_utc: UTCDateTime
    manifest_sha256: str | None = None


class InitializationPolicy(StrEnum):
    latest_valid = "latest_valid"


class EnsembleConfig(Strict):
    members: int = Field(ge=1, le=100)
    seed: int
    ta_sigma_k: float = Field(ge=0)
    psum_log_sigma: float = Field(ge=0)
    iswr_rel_sigma: float = Field(ge=0)
    ilwr_sigma_wm2: float = Field(ge=0)
    ar1_hourly: float = Field(ge=0, lt=1)


class WhatIf(Strict):
    ta_offset_k: float = 0.0
    psum_factor: float = Field(default=1.0, ge=0)
    iswr_factor: float = Field(default=1.0, ge=0)

    @property
    def is_identity(self) -> bool:
        return self.ta_offset_k == 0 and self.psum_factor == 1 and self.iswr_factor == 1


class ForecastRequest(Strict):
    domain_id: str
    issue_time: UTCDateTime
    forecast_series_id: str
    horizon_hours: int = Field(gt=0)
    output_lead_hours: list[int]
    initialization_policy: InitializationPolicy = InitializationPolicy.latest_valid
    ensemble: EnsembleConfig
    what_if: WhatIf = Field(default_factory=WhatIf)
    model_version: str

    @field_validator("output_lead_hours")
    @classmethod
    def _leads(cls, v: list[int]) -> list[int]:
        if not v or any(x < 0 for x in v) or sorted(set(v)) != v:
            raise ValueError("output_lead_hours must be sorted, unique and non-negative")
        return v


class ForecastResultMeta(Strict):
    """Manifest for one forecast run (one per run directory)."""

    run_id: str
    status: Literal["ok", "partial", "failed"]
    label: str = EXPERIMENTAL_LABEL
    synthetic_inputs: bool
    request: ForecastRequest
    forecast_init_time: UTCDateTime
    initial_state_id: str
    initial_state_manifest_sha256: str
    terrain_version: str
    engine_version: str
    engine_config_hash: str
    forcing_hash: str
    input_provenance: list[Provenance]
    capability: CapabilityFlags
    units_simulated: list[str]
    units_unsupported: dict[str, list[str]]
    unit_failures: dict[str, str] = Field(default_factory=dict)
    member_perturbations: dict[str, dict[str, Any]]
    forcing_assumptions: list[str]
    mass_budget_tolerance_kg_m2: float
    mass_budget_max_residual_kg_m2: float | None
    created_utc: UTCDateTime
