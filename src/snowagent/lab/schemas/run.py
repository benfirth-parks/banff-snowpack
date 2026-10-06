"""Run manifest: the provenance every lab run records in the run registry (CLAUDE.md principle 2; build guide
"Reproducibility"). Same data, code, configuration, genome and seed reproduce a run."""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field, model_validator

from snowagent.lab import LAB_DISCLAIMER
from snowagent.lab.schemas.benchmark import SHA256
from snowagent.lab.schemas.common import LabModel, UTCDateTime

WEIGHT_SUM_TOLERANCE = 1e-6


class ScoringWeights(LabModel):
    snow_depth: float = Field(ge=0, le=1)
    layer_structure: float = Field(ge=0, le=1)
    critical_layers: float = Field(ge=0, le=1)
    uncertainty: float = Field(ge=0, le=1)
    robustness: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _sum(self) -> ScoringWeights:
        total = sum(self.model_dump().values())
        if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
            raise ValueError(f"scoring weights sum to {total}, not 1")
        return self


class RunKind(StrEnum):
    data_import = "data_import"
    case_build = "case_build"
    competition = "competition"
    evolution = "evolution"
    sealed_test = "sealed_test"


SCORED_KINDS = {RunKind.competition, RunKind.evolution, RunKind.sealed_test}


class InputFile(LabModel):
    path: str
    sha256: str
    bytes: int = Field(ge=0)


class RunManifest(LabModel):
    run_id: str
    kind: RunKind
    status: str = Field(pattern=r"^(ok|partial|failed)$")
    created_at: UTCDateTime
    finished_at: UTCDateTime | None = None
    config_hash: str  # sha256 of the lab configuration as loaded (plot coordinates included)
    data_hash: str  # sha256 over the input files' hashes
    software_version: str  # snowagent version
    git_commit: str | None = None
    snowpack_version: str | None = None  # when a SNOWPACK run is part of it
    seed: int | None = None
    scoring_weights: ScoringWeights | None = None  # frozen at run time for scored runs
    splits: dict[str, list[str]] = Field(default_factory=dict)
    case_ids: list[str] = Field(default_factory=list)
    agent_ids: list[str] = Field(default_factory=list)
    genome_hashes: dict[str, str] = Field(default_factory=dict)  # agent_id -> genome sha256 (competition, evolution)
    case_set_hash: str | None = None  # sha256 over the scored cases' ids and manifests
    profile_ids_used: list[str] = Field(default_factory=list)
    inputs: list[InputFile] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    counts: dict[str, int] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    runtime_s: float | None = Field(default=None, ge=0)
    label: str = LAB_DISCLAIMER

    @model_validator(mode="after")
    def _check(self) -> RunManifest:
        for name in ("config_hash", "data_hash"):
            if not SHA256.match(getattr(self, name)):
                raise ValueError(f"{name} must be a sha256 hex digest")
        if self.finished_at is not None and self.finished_at < self.created_at:
            raise ValueError("finished_at precedes created_at")
        if self.kind in SCORED_KINDS and (self.seed is None or self.scoring_weights is None):
            raise ValueError(f"a {self.kind} run must record its seed and the frozen scoring weights")
        return self
