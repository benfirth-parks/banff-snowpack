"""The prediction contract every agent's output must validate against (build guide "Prediction contract").

Depths are metres from the surface as quantiles p10 <= p50 <= p90. An agent that cannot predict returns status
``insufficient_data`` with a reason (scored honestly as a miss, never a crash). Explanations are optional and never
scored.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from snowagent.lab import LAB_DISCLAIMER
from snowagent.lab.schemas.common import LabModel, SiteCode, UTCDateTime
from snowagent.lab.schemas.profile import GRAIN_VOCABULARY, CriticalClass
from snowagent.lab.schemas.site import ReferenceScenario

Probability = float


class Quantiles(LabModel):
    """A non-negative quantity as p10 <= p50 <= p90 (metres for depths)."""

    p10: float = Field(ge=0)
    p50: float = Field(ge=0)
    p90: float = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> Quantiles:
        if not self.p10 <= self.p50 <= self.p90:
            raise ValueError(f"quantiles must be ordered p10 <= p50 <= p90, got {self.p10}, {self.p50}, {self.p90}")
        return self


class PredictedLayer(LabModel):
    name: str
    top_depth_m: Quantiles
    bottom_depth_m: Quantiles
    grain_form: list[str] = Field(default_factory=list)  # IACS codes, most likely first
    hardness: str | None = None
    wetness: str | None = None
    probability_present: Probability = Field(ge=0, le=1)
    is_layer_of_concern: bool = False
    critical_class: CriticalClass = CriticalClass.unknown
    confidence: Probability = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _check(self) -> PredictedLayer:
        for q in ("p10", "p50", "p90"):
            top, bottom = getattr(self.top_depth_m, q), getattr(self.bottom_depth_m, q)
            if bottom <= top:
                raise ValueError(f"layer {self.name!r}: bottom depth {q} {bottom} m is not below top depth {q} {top} m")
        bad = [g for g in self.grain_form if g not in GRAIN_VOCABULARY]
        if bad:
            raise ValueError(f"layer {self.name!r}: grain forms {bad} are not IACS 2009 codes")
        return self


class BulkState(LabModel):
    snow_depth_m: Quantiles


class Confidence(LabModel):
    overall: Probability = Field(ge=0, le=1)
    main_limits: list[str] = Field(default_factory=list)


class SnowpackPrediction(LabModel):
    case_id: str
    agent_id: str
    issued_at: UTCDateTime  # the case's as-of time
    valid_at: UTCDateTime
    site_code: SiteCode
    scenario: ReferenceScenario
    status: Literal["ok", "insufficient_data"] = "ok"
    insufficient_data_reason: str | None = None
    bulk_state: BulkState | None = None
    layers: list[PredictedLayer] = Field(default_factory=list)  # surface to ground by p50 top depth
    confidence: Confidence
    explanation: str | None = None  # optional; never affects a score
    model_metadata: dict[str, Any] = Field(default_factory=dict)  # agent version, genome id, snowpack version ...
    label: str = LAB_DISCLAIMER

    @property
    def insufficient_data(self) -> bool:
        return self.status == "insufficient_data"

    @model_validator(mode="after")
    def _check(self) -> SnowpackPrediction:
        if self.valid_at < self.issued_at:
            raise ValueError("valid_at precedes issued_at")
        if self.status == "insufficient_data":
            if not self.insufficient_data_reason:
                raise ValueError("an insufficient_data prediction must give its reason")
            if self.layers or self.bulk_state is not None:
                raise ValueError("an insufficient_data prediction carries no layers or bulk state")
            return self
        if self.insufficient_data_reason:
            raise ValueError("insufficient_data_reason given but status is ok")
        if self.bulk_state is None:
            raise ValueError("a prediction with status ok needs bulk_state.snow_depth_m")
        tops = [ly.top_depth_m.p50 for ly in self.layers]
        if tops != sorted(tops):
            raise ValueError("predicted layers must be ordered surface to ground (increasing p50 top depth)")
        return self
