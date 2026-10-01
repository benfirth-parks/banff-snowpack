"""Public field reports near the study plots (ADR-037): a contract for point observations written by the public
and guides on open platforms (first source: Avalanche Canada's Mountain Information Network, MIN).

They are not plot profiles: each report is at its own place, elevation and aspect (distance to every plot is
recorded) and is unverified. They are shown beside the simulations as context and kept for later evaluation;
nothing here feeds the engine. Usernames and account ids stay in the archived raw file only.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "public-obs-1"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PublicTest(_Strict):
    """The single stability-test summary a MIN snowpack report carries (initiation, fracture, failure depth)."""

    initiation: str | None = None  # e.g. easy | moderate | hard (MIN vocabulary)
    fracture: str | None = None  # e.g. sudden | resistant
    depth_cm: float | None = None
    crystal_types: list[str] = Field(default_factory=list)


class PublicAvalanche(_Strict):
    time_utc: datetime | None = None
    number: str | None = None
    size: str | None = None
    character: list[str] = Field(default_factory=list)
    trigger: str | None = None
    aspects: list[str] = Field(default_factory=list)
    elevation_bands: list[str] = Field(default_factory=list)
    incline_deg: float | None = None
    weak_layer: list[str] = Field(default_factory=list)
    crust_near_weak_layer: bool | None = None


class PublicObservation(_Strict):
    schema_version: Literal["public-obs-1"] = SCHEMA_VERSION
    source: Literal["min"]
    source_id: str
    url: str  # the report's public page
    obs_time_utc: datetime
    submitted_utc: datetime | None = None
    updated_utc: datetime | None = None
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    region: str | None = None
    title: str = ""
    types: list[str] = Field(default_factory=list)  # snowpack | avalanche | weather | quick | incident
    snowpack_obs_type: str | None = None  # MIN: point | summary
    elevation_m: float | None = None
    elevation_bands: list[str] = Field(default_factory=list)  # alp | tln | btl
    aspects: list[str] = Field(default_factory=list)
    hs_cm: float | None = Field(default=None, ge=0, le=2000)
    foot_pen_cm: float | None = None
    ski_pen_cm: float | None = None
    test: PublicTest | None = None
    surface: list[str] = Field(default_factory=list)
    whumpfing: bool | None = None
    cracking: bool | None = None
    new_snow_24h_cm: float | None = None
    avalanches: list[PublicAvalanche] = Field(default_factory=list)
    incident: bool = False
    comments: dict[str, str] = Field(default_factory=dict)
    image_urls: list[str] = Field(default_factory=list)
    distance_km: dict[str, float] = Field(default_factory=dict)  # study plot -> km
    flags: list[str] = Field(default_factory=list)  # values set to null because implausible (raw keeps them)
    raw_path: str
    raw_sha256: str
