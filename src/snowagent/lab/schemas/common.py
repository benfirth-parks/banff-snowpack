"""Shared types of the lab's data contracts (ADR-056).

Times are timezone-aware UTC (naive times are rejected, ``contracts.UTCDateTime``); units are SI with the unit in
the field name (lengths in metres, air temperature in K, precipitation and SWE in mm = kg m-2), except snow
temperature (deg C), grain size (mm) and density (kg m-3), as in ``snowagent.contracts.Layer``. The UI converts.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

from snowagent.contracts import UTCDateTime

__all__ = ["AvailabilityAssumption", "LabModel", "QualityFlag", "SiteCode", "UTCDateTime"]


class LabModel(BaseModel):
    """Frozen, no unknown fields: a misspelt field is an error, not a silently dropped value."""

    model_config = ConfigDict(extra="forbid", frozen=True)


class SiteCode(StrEnum):
    BOW = "BOW"
    GOAT = "GOAT"
    SIMP = "SIMP"


class QualityFlag(StrEnum):
    """QC flag of a value or record (CLAUDE.md: flag, don't delete). ``missing``: no value; ``filled``: a value
    supplied by another source than the record's own (never done silently; the source is named)."""

    ok = "ok"
    suspect = "suspect"
    bad = "bad"
    filled = "filled"
    missing = "missing"


class AvailabilityAssumption(StrEnum):
    """How a record's availability time is known. ``observed_at``: no publication time is known and the observation
    time stands in for it (retrospective prototyping only; shown as a warning in the UI and manifests)."""

    source_recorded_at = "source_recorded_at"
    observed_at = "observed_at"
