"""The interface every lab agent implements (build guide "Prediction contract"; ADR-062).

``predict`` receives only a ``VisibleBenchmarkCase``, which cannot hold the target pit or any record not available
at the case's as-of time; it returns a ``SnowpackPrediction`` (or one with status ``insufficient_data``). An agent
that cannot run at all here (the SNOWPACK binary is missing) raises ``AgentUnavailable``: the harness skips the case
for that agent with the reason instead of scoring a miss. Every agent is built from a genome (``make_agent``); its
``agent_id`` is ``<family>-<genome hash>``. Layers an agent makes are benchmark entries: they are never published
as site output (ADR-055).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from snowagent.lab.schemas.benchmark import VisibleBenchmarkCase
from snowagent.lab.schemas.genome import AgentGenome
from snowagent.lab.schemas.prediction import SnowpackPrediction


@runtime_checkable
class SnowpackAgent(Protocol):
    agent_id: str
    genome: AgentGenome

    def predict(self, case: VisibleBenchmarkCase, seed: int) -> SnowpackPrediction: ...
