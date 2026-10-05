"""The interface every lab agent implements (build guide "Prediction contract").

``predict`` receives only a ``VisibleBenchmarkCase``, which cannot hold the target pit or any record not available
at the case's as-of time; it returns a ``SnowpackPrediction`` (or one with status ``insufficient_data``). Layers
an agent makes are benchmark entries: they are never published as site output (ADR-055).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from snowagent.lab.schemas.benchmark import VisibleBenchmarkCase
from snowagent.lab.schemas.genome import AgentGenome
from snowagent.lab.schemas.prediction import SnowpackPrediction


@runtime_checkable
class SnowpackAgent(Protocol):
    agent_id: str
    genome: AgentGenome | None  # None for fixed agents (persistence, the SNOWPACK incumbent)

    def predict(self, case: VisibleBenchmarkCase, seed: int) -> SnowpackPrediction: ...
