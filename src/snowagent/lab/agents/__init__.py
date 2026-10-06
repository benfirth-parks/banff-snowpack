"""Lab agents (ADR-062): persistence, weather rules, analogues, the SNOWPACK incumbent and a hybrid, each built
from a genome. Agents see only a ``VisibleBenchmarkCase``; the harness builds whatever else they may use (the
analogue library of other seasons, the engine backend)."""

from __future__ import annotations

from snowagent.lab.agents.analogue import AnalogueAgent, AnalogueLibrary
from snowagent.lab.agents.base import SnowpackAgent
from snowagent.lab.agents.common import AgentUnavailable
from snowagent.lab.agents.hybrid import HybridAgent
from snowagent.lab.agents.persistence import PersistenceAgent
from snowagent.lab.agents.snowpack import EngineBackend
from snowagent.lab.agents.snowpack import SnowpackAgent as SnowpackEngineAgent
from snowagent.lab.agents.weather_rule import WeatherRuleAgent
from snowagent.lab.schemas.genome import AgentFamily, AgentGenome


def make_agent(genome: AgentGenome, library: AnalogueLibrary | None = None,
               backend: EngineBackend | None = None) -> SnowpackAgent:
    """The agent of a genome's family. ``library`` is used by analogue agents only, ``backend`` by SNOWPACK and
    hybrid agents."""
    match genome.family:
        case AgentFamily.persistence:
            return PersistenceAgent(genome)
        case AgentFamily.weather_rule:
            return WeatherRuleAgent(genome)
        case AgentFamily.analogue:
            return AnalogueAgent(genome, library)
        case AgentFamily.snowpack:
            return SnowpackEngineAgent(genome, backend)
        case AgentFamily.hybrid:
            return HybridAgent(genome, backend)
    raise ValueError(f"no agent for family {genome.family}")


__all__ = ["AgentUnavailable", "AnalogueAgent", "AnalogueLibrary", "HybridAgent", "PersistenceAgent",
           "SnowpackAgent", "SnowpackEngineAgent", "WeatherRuleAgent", "make_agent"]
