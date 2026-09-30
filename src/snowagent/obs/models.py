"""Contracts for field-observation metadata.

Layer data are deliberately absent here: the current uploads contain layers only as
rendered images. A profile's ``layers_status`` says whether machine-readable layers
exist; nothing downstream may treat an image-only profile as layered data.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from snowagent.contracts import UTCDateTime


class ProfileCategory(StrEnum):
    study_plot = "study_plot"
    test_profile = "test_profile"
    unknown = "unknown"


class LayersStatus(StrEnum):
    structured = "structured"  # CAAML/JSON layers available
    image_only = "image_only"  # layers exist only in a rendered chart
    none = "none"


class ProfileHeader(BaseModel):
    """One observed profile's metadata in SI units, with explicit provenance and QC flags."""

    model_config = ConfigDict(extra="forbid")

    record_id: str
    source_file: str
    sha256: str
    file_type: str
    header_source: str  # "pdf_text" | "filename"
    category: ProfileCategory
    site_key: str | None
    station_id: str | None
    profile_name: str | None = None
    observer: str | None = None
    obs_time_local: str | None = None  # as written in the source (no zone designator)
    time_zone: str | None = None
    time_zone_confirmed: bool = False
    obs_time_utc: UTCDateTime | None = None
    filename_date: str | None = None
    lat: float | None = Field(default=None, ge=-90, le=90)
    lon: float | None = Field(default=None, ge=-180, le=180)
    elevation_m: float | None = None
    elevation_source_unit: str | None = None
    aspect_deg: float | None = None
    aspect_text: str | None = None
    slope_deg: float | None = None
    hs_m: float | None = None
    air_temp_c: float | None = None
    sky_cover: str | None = None
    precipitation: str | None = None
    wind: str | None = None
    blowing_snow: str | None = None
    surface_grain: str | None = None
    foot_pen_m: float | None = None
    ski_pen_m: float | None = None
    notes: str | None = None
    layers_status: LayersStatus
    duplicate_of: str | None = None
    qc_flags: list[str] = Field(default_factory=list)
