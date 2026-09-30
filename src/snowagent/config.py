"""Load config/pipeline.yaml into typed settings objects."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml

from snowagent.contracts import EnsembleConfig
from snowagent.engine.snowpack import REPO_ROOT, EngineSettings
from snowagent.spatial_forcing.builder import ForcingConfig
from snowagent.state.replay import InitPolicy
from snowagent.terrain.units import UnitConfig

DEFAULT_CONFIG = REPO_ROOT / "config" / "pipeline.yaml"


@dataclass(frozen=True)
class PipelineConfig:
    units: UnitConfig
    forcing: ForcingConfig
    init: InitPolicy
    ensemble: EnsembleConfig
    lead_hours: list[int]
    engine: EngineSettings


def load_config(path: Path | None = None) -> PipelineConfig:
    raw = yaml.safe_load(Path(path or DEFAULT_CONFIG).read_text())
    eng = raw.get("engine", {})
    return PipelineConfig(
        units=UnitConfig(**raw.get("terrain", {})),
        forcing=ForcingConfig(**raw.get("forcing", {})),
        init=InitPolicy(snow_free_months=tuple(raw["initialization"]["snow_free_months"]),
                        min_spinup_hours=raw["initialization"]["min_spinup_hours"]),
        ensemble=EnsembleConfig(**raw["ensemble"]),
        lead_hours=list(raw["output"]["lead_hours"]),
        engine=EngineSettings(utm_zone=eng.get("utm_zone", "11U"),
                              prof_days_between=eng.get("history_profile_days_between", 0.25)),
    )
