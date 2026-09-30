"""Explicit, machine-readable failure types.

Every failure the forecast pipeline can hit maps to one of these codes so that
callers never receive a plausible-looking profile in place of an error.
"""

from __future__ import annotations


class SnowAgentError(Exception):
    """Base class. ``code`` is stable and machine-readable."""

    code = "snowagent_error"

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, object]:
        return {"status": "error", "code": self.code, "message": self.message, "details": self.details}


class InitializationRequired(SnowAgentError):
    """No valid checkpoint and no adequate history / defensible starting state."""

    code = "initialization_required"


class InvalidUnits(SnowAgentError):
    code = "invalid_units"


class InvalidTime(SnowAgentError):
    code = "invalid_time"


class InvalidInput(SnowAgentError):
    code = "invalid_input"


class DataLeakage(SnowAgentError):
    """Data that became available after the forecast issue time was requested."""

    code = "future_data_leakage"


class EngineUnavailable(SnowAgentError):
    code = "engine_unavailable"


class EngineRunFailed(SnowAgentError):
    code = "engine_run_failed"


class UnsupportedTerrain(SnowAgentError):
    code = "unsupported_terrain"


class OutOfDomain(SnowAgentError):
    code = "out_of_domain"


class CheckpointIntegrityError(SnowAgentError):
    code = "checkpoint_integrity_error"


class StateGap(SnowAgentError):
    """The checkpoint cannot be advanced to issue time with data available by then."""

    code = "state_gap"


class ImmutableRecord(SnowAgentError):
    """Attempt to overwrite an issued forecast or stored checkpoint."""

    code = "immutable_record"
