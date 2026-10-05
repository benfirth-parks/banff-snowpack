"""Canonical hourly weather record (build guide "Weather records", in SI; ADR-056).

One record per site, hour and ``source_id`` (a source set such as ``plot_stations``; later e.g. a reanalysis or a
forecast run). Each variable names the station that supplied it (``sources``) and its QC flag (``qc``), so values
from different stations in one record stay traceable. Values are as measured at that station (no elevation
transfer); missing values stay null with flag ``missing`` and are never filled; a value that failed QC is null with
flag ``bad`` (the raw file keeps it).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from snowagent.lab.schemas.common import AvailabilityAssumption, LabModel, QualityFlag, SiteCode, UTCDateTime

WEATHER_VARIABLES: tuple[str, ...] = (
    "air_temperature_k", "relative_humidity_frac", "precipitation_mm", "wind_speed_ms", "wind_direction_deg",
    "snow_depth_m", "swe_mm", "shortwave_radiation_wm2", "longwave_radiation_wm2", "station_pressure_pa",
)
SEVERITY = [QualityFlag.ok, QualityFlag.filled, QualityFlag.suspect, QualityFlag.bad]
NULL_FLAGS = {QualityFlag.missing, QualityFlag.bad}  # bad: a value failed QC and is not used (the raw file keeps it)


class WeatherRecord(LabModel):
    site_code: SiteCode
    observed_at: UTCDateTime  # end of the hourly interval
    source_id: str
    kind: Literal["observed", "reanalysis", "forecast"] = "observed"
    issued_at: UTCDateTime | None = None  # forecasts only: the run's issue time
    source_recorded_at: UTCDateTime | None = None  # when the value became available; None: unknown
    availability_assumption: AvailabilityAssumption = AvailabilityAssumption.observed_at
    air_temperature_k: float | None = Field(default=None, gt=0)
    relative_humidity_frac: float | None = Field(default=None, ge=0)
    precipitation_mm: float | None = None  # hourly sum, kg m-2; a gauge's negative increment is kept and flagged
    wind_speed_ms: float | None = Field(default=None, ge=0)
    wind_direction_deg: float | None = Field(default=None, ge=0, le=360)
    snow_depth_m: float | None = None
    swe_mm: float | None = None
    shortwave_radiation_wm2: float | None = None
    longwave_radiation_wm2: float | None = None
    station_pressure_pa: float | None = Field(default=None, gt=0)
    sources: dict[str, str] = Field(default_factory=dict)  # variable -> station/source that supplied it
    qc: dict[str, QualityFlag] = Field(default_factory=dict)  # variable -> flag; absent variable: missing
    quality_flag: QualityFlag = QualityFlag.missing  # worst flag over the variables (missing: none has a value)
    provenance_id: str  # the import run that wrote the record

    @model_validator(mode="after")
    def _check(self) -> WeatherRecord:
        unknown = (set(self.sources) | set(self.qc)) - set(WEATHER_VARIABLES)
        if unknown:
            raise ValueError(f"sources/qc name unknown variables {sorted(unknown)}")
        for var in WEATHER_VARIABLES:
            if getattr(self, var) is None and self.qc.get(var, QualityFlag.missing) not in NULL_FLAGS:
                raise ValueError(f"{var} is null but flagged {self.qc[var]}; a null value is flagged missing or bad")
            if getattr(self, var) is not None and var not in self.qc:
                raise ValueError(f"{var} has a value but no QC flag")
        if self.kind == "forecast" and self.issued_at is None:
            raise ValueError("forecast records need issued_at")
        if self.source_recorded_at is None and self.availability_assumption != AvailabilityAssumption.observed_at:
            raise ValueError("without source_recorded_at the availability assumption must be observed_at")
        if self.quality_flag != record_quality(self.qc):
            raise ValueError(f"quality_flag {self.quality_flag} differs from the worst variable flag "
                             f"{record_quality(self.qc)}")
        return self

    @property
    def available_at(self):
        """When the record could have been known: the source time, else (assumption) the observation time."""
        return self.source_recorded_at or self.observed_at


def record_quality(qc: dict[str, QualityFlag | str]) -> QualityFlag:
    """Worst flag over the variables that have a value (ok < filled < suspect < bad); ``missing`` if none has."""
    flags = [QualityFlag(f) for f in qc.values() if QualityFlag(f) != QualityFlag.missing]
    if not flags:
        return QualityFlag.missing
    return max(flags, key=SEVERITY.index)
