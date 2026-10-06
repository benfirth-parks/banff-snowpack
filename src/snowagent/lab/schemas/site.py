"""Lab sites: the three study plots, located by config/plot_forcing.yaml (not repeated in config/lab.yaml)."""

from __future__ import annotations

from pydantic import Field

from snowagent.lab.schemas.common import LabModel, SiteCode


class ReferenceScenario(LabModel):
    """The terrain a site's predictions are for. Study plots are flat, so aspect is undefined (None)."""

    name: str
    slope_deg: float = Field(ge=0, lt=90)
    aspect_deg: float | None = Field(default=None, ge=0, lt=360)
    terrain_class: str = "unknown"


class Site(LabModel):
    code: SiteCode
    plot_id: str  # key in config/plot_forcing.yaml and the observed set's site_key
    display_name: str
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    elevation_m: float
    timezone: str  # display only; data are UTC
    reference_scenario: ReferenceScenario
    wind_stations: list[str] = Field(default_factory=list)
