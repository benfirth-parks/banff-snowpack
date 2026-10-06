"""Hybrid agent: SNOWPACK structure adjusted with the last pit and the weather rules (ADR-062).

1. Depth: the blend-weighted mean of the SNOWPACK depth (``snowpack_weight``), the carried-forward pit
   (``persistence_weight``) and the weather-rule column (``rule_weight``), over the members that answered.
2. Structure: the SNOWPACK profile (else the carried pit, else the rule column) scaled to that depth.
3. Pit adjustment: each layer of concern in the carried pit (persistent grains, crusts) that the base structure has
   no layer of the same class within ``boundary_match_m`` of is inserted, presence probability
   ``pit_trust`` x ``presence_confidence``, provided ``persistence_weight`` > 0.
4. Rule adjustment: near-surface layers of concern the rules make (surface hoar, near-surface facets, crusts in
   the top 30 cm) that are not matched are inserted with probability rule share x ``presence_confidence`` (rule
   share = ``rule_weight`` / the sum of the weights).

The members run with this genome's genes where it has them: the persistence member takes the pit, forcing and
new_snow blocks; the rule member takes forcing and new_snow; the SNOWPACK member runs the hybrid's own physics genes
(``snowpack_physics``, ADR-070) with the default output genes; the other genes are each family's defaults. Without
the engine the hybrid still predicts from the other members and says so in its limits and metadata.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np

from snowagent.lab.agents.common import (
    AgentUnavailable,
    Lyr,
    build_prediction,
    insert_layer,
    insufficient,
    scale_layers,
)
from snowagent.lab.agents.persistence import PersistenceAgent
from snowagent.lab.agents.snowpack import EngineBackend, SnowpackAgent
from snowagent.lab.agents.weather_rule import simulate
from snowagent.lab.ingest.mapping import CONCERN_CLASSES
from snowagent.lab.schemas.benchmark import VisibleBenchmarkCase
from snowagent.lab.schemas.genome import AgentFamily, AgentGenome
from snowagent.lab.schemas.prediction import SnowpackPrediction

AGENT_VERSION = "hybrid-1"
RULE_NEAR_SURFACE_M = 0.30


def _member_genes(family: AgentFamily, own: dict) -> dict:
    from snowagent.lab.genome import default_genome

    base = dict(default_genome(family).genes)
    base.update({k: v for k, v in own.items() if k in base})
    return base


def _matched(layers: list[Lyr], ly: Lyr, tol: float) -> bool:
    mid = (ly.top + ly.bottom) / 2
    return any(o.critical == ly.critical and o.top - tol <= mid <= o.bottom + tol for o in layers)


class HybridAgent:
    def __init__(self, genome: AgentGenome, backend: EngineBackend | None = None) -> None:
        if genome.family != AgentFamily.hybrid:
            raise ValueError(f"HybridAgent needs a hybrid genome, got {genome.family}")
        from snowagent.lab.genome import default_genome

        self.genome = genome
        self.agent_id = genome.agent_id
        g = genome.genes
        self.snowpack = SnowpackAgent(default_genome(AgentFamily.snowpack), backend, physics_genes=g) \
            if g["snowpack_weight"] > 0 else None  # its own physics genes (ADR-070), default output genes
        self.persistence_genes = _member_genes(AgentFamily.persistence, g)
        self.rule_genes = _member_genes(AgentFamily.weather_rule, g)
        self.persistence = PersistenceAgent(default_genome(AgentFamily.persistence))

    def predict(self, case: VisibleBenchmarkCase, seed: int) -> SnowpackPrediction:
        from snowagent.errors import EngineRunFailed

        g = self.genome.genes
        conf = g["presence_confidence"]
        meta: dict = {"agent_version": AGENT_VERSION}
        limits: list[str] = []
        members: dict[str, tuple[float, list[Lyr], float]] = {}  # name -> (depth, layers surface first, weight)
        if self.snowpack is not None:
            try:
                r = self.snowpack.engine_result(case)
                members["snowpack"] = (r.snow_depth_m, self.snowpack.layers_from(r, conf), g["snowpack_weight"])
                meta |= {"engine_source": r.source, "snowpack_version": r.snowpack_version,
                         "engine_config_hash": r.config_hash, "forcing_hash": r.forcing_hash,
                         "profile_lag_h": r.profile_lag_h, "steer_updates": r.steer_updates}
                if r.physics_key != "default":
                    meta["physics_key"] = r.physics_key
            except AgentUnavailable as exc:
                limits.append("SNOWPACK unavailable: blend of pit and weather rules only")
                meta["snowpack_unavailable"] = str(exc)[:200]
            except (EngineRunFailed, ValueError) as exc:
                limits.append("SNOWPACK run failed: blend of pit and weather rules only")
                meta["snowpack_failed"] = str(getattr(exc, "message", exc))[:200]
        hs_p, lay_p, _ = self.persistence.carry(case, self.persistence_genes)
        if hs_p is not None and g["persistence_weight"] > 0:
            members["persistence"] = (hs_p, lay_p, g["persistence_weight"])
        lay_r: list[Lyr] = []
        if g["rule_weight"] > 0:
            lay_r, _ = simulate(case, self.rule_genes)
            members["rule"] = (lay_r[-1].bottom if lay_r else 0.0, lay_r, g["rule_weight"])
        if not members:
            return insufficient(case, self.genome, "no member could predict (no engine, pit or weather)", meta)
        wsum = sum(w for _, _, w in members.values())
        hs = float(sum(d * w for d, _, w in members.values()) / wsum)
        meta["members"] = {k: {"depth_m": round(float(d), 3), "weight": round(w / wsum, 3)}
                           for k, (d, _, w) in members.items()}
        base_name = next(n for n in ("snowpack", "persistence", "rule") if n in members)
        base_hs, base_layers, _ = members[base_name]
        layers = scale_layers(base_layers, hs / base_hs) if base_hs > 0 else []
        layers = [replace(ly, prob=conf) for ly in layers]
        meta["structure_from"] = base_name
        tol = g["boundary_match_m"]
        added = {"pit": 0, "rule": 0}
        if "persistence" in members and base_name != "persistence" and hs_p and hs_p > 0:
            for ly in scale_layers(lay_p, hs / hs_p):
                if ly.critical in CONCERN_CLASSES and not _matched(layers, ly, tol):
                    layers = insert_layer(layers, replace(ly, prob=g["pit_trust"] * conf, source="pit"))
                    added["pit"] += 1
        if "rule" in members and base_name != "rule":
            share = g["rule_weight"] / wsum
            hs_r = members["rule"][0]
            for ly in scale_layers(lay_r, hs / hs_r) if hs_r > 0 else []:
                if ly.top < RULE_NEAR_SURFACE_M and ly.critical in CONCERN_CLASSES and not _matched(layers, ly, tol):
                    layers = insert_layer(layers, replace(ly, prob=float(np.clip(share * conf, 0, 1)), source="rule"))
                    added["rule"] += 1
        meta["inserted_layers"] = added
        limits.append(f"structure from {base_name}, adjusted with pit and rule layers of concern")
        return build_prediction(case, self.genome, hs, layers, conf, limits, meta)
