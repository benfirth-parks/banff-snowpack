"""Agent genome: the typed, allow-listed parameters the evolution loop may change (build guide "AgentGenome").

Every gene is bounded (``Field(ge=, le=)``); ``gene_bounds`` lists them so mutation can stay inside the bounds.
Ensemble weights must sum to 1, be non-zero for every enabled module and zero for every disabled one. Raw data,
splits, case cut-offs, scoring weights and evaluator code are not genes and cannot be reached from here.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from snowagent.lab.schemas.common import LabModel, SiteCode

WEIGHT_SUM_TOLERANCE = 1e-6


# per-site genes: (lower, upper) bounds of each site's value
PER_SITE_BOUNDS: dict[str, tuple[float, float]] = {
    "temperature_bias_k": (-3.0, 3.0), "precipitation_multiplier": (0.5, 2.0), "wind_loading_weight": (0.0, 1.0)}


def _per_site(default: float, doc: str) -> Any:
    return Field(default_factory=lambda: dict.fromkeys(SiteCode, default), description=doc)


class WeatherGenes(LabModel):
    temperature_bias_k: dict[SiteCode, float] = _per_site(0.0, "added to air temperature (K), per site")
    precipitation_multiplier: dict[SiteCode, float] = _per_site(1.0, "precipitation factor, per site")
    rain_snow_threshold_c: float = Field(default=1.0, ge=-1.0, le=3.0)
    wind_loading_weight: dict[SiteCode, float] = _per_site(0.0, "wind-loading proxy weight, per site")

    @model_validator(mode="after")
    def _sites(self) -> WeatherGenes:
        for name, (lo, hi) in PER_SITE_BOUNDS.items():
            vals = getattr(self, name)
            if set(vals) != set(SiteCode):
                raise ValueError(f"weather.{name} needs a value for each site {sorted(SiteCode)}")
            for site, v in vals.items():
                if not lo <= v <= hi:
                    raise ValueError(f"weather.{name}[{site}] = {v} is outside [{lo}, {hi}]")
        return self


class ObservationGenes(LabModel):
    profile_age_half_life_days: float = Field(default=14.0, ge=1.0, le=90.0)
    elevation_match_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    aspect_match_weight: float = Field(default=0.5, ge=0.0, le=1.0)
    terrain_match_weight: float = Field(default=0.5, ge=0.0, le=1.0)


class ModuleGenes(LabModel):
    use_persistence: bool = True
    use_weather_rules: bool = True
    use_analogue_search: bool = True
    use_physics_adapter: bool = False


class EnsembleGenes(LabModel):
    persistence_weight: float = Field(default=1 / 3, ge=0.0, le=1.0)
    weather_rule_weight: float = Field(default=1 / 3, ge=0.0, le=1.0)
    analogue_weight: float = Field(default=1 / 3, ge=0.0, le=1.0)
    physics_weight: float = Field(default=0.0, ge=0.0, le=1.0)


class UncertaintyGenes(LabModel):
    interval_multiplier: float = Field(default=1.0, ge=0.5, le=3.0)


MODULE_WEIGHT = {"use_persistence": "persistence_weight", "use_weather_rules": "weather_rule_weight",
                 "use_analogue_search": "analogue_weight", "use_physics_adapter": "physics_weight"}


class AgentGenome(LabModel):
    schema_version: str = "lab-genome-1"
    weather: WeatherGenes = Field(default_factory=WeatherGenes)
    observations: ObservationGenes = Field(default_factory=ObservationGenes)
    modules: ModuleGenes = Field(default_factory=ModuleGenes)
    ensemble: EnsembleGenes = Field(default_factory=EnsembleGenes)
    uncertainty: UncertaintyGenes = Field(default_factory=UncertaintyGenes)

    @model_validator(mode="after")
    def _ensemble(self) -> AgentGenome:
        enabled = [m for m in MODULE_WEIGHT if getattr(self.modules, m)]
        if not enabled:
            raise ValueError("at least one module must be enabled")
        for m, w in MODULE_WEIGHT.items():
            v = getattr(self.ensemble, w)
            if m in enabled and v <= 0:
                raise ValueError(f"module {m} is enabled but ensemble.{w} is 0")
            if m not in enabled and v != 0:
                raise ValueError(f"module {m} is disabled but ensemble.{w} is {v}")
        total = sum(getattr(self.ensemble, w) for w in MODULE_WEIGHT.values())
        if abs(total - 1.0) > WEIGHT_SUM_TOLERANCE:
            raise ValueError(f"ensemble weights sum to {total:.6f}, not 1 (use normalize_ensemble_weights)")
        return self


def normalize_ensemble_weights(weights: dict[str, float], modules: dict[str, bool]) -> dict[str, float]:
    """Weights of the enabled modules rescaled to sum to 1; disabled modules get 0. An enabled module whose weight is
    0 or negative is an error (nothing to rescale from)."""
    out = {}
    for m, w in MODULE_WEIGHT.items():
        v = float(weights.get(w, 0.0))
        if modules.get(m, False) and v <= 0:
            raise ValueError(f"module {m} is enabled but its weight {w} is {v}")
        out[w] = v if modules.get(m, False) else 0.0
    total = sum(out.values())
    if total <= 0:
        raise ValueError("no enabled module")
    return {k: v / total for k, v in out.items()}


def gene_bounds(model: type[BaseModel] = AgentGenome, prefix: str = "") -> dict[str, tuple[float, float] | type]:
    """Allow-list of genes: dotted path -> (lower, upper) for numbers (per-site genes as ``path.SITE``), ``bool``
    for module switches. Mutation may touch only these paths."""
    out: dict[str, tuple[float, float] | type] = {}
    for name, f in model.model_fields.items():
        path = f"{prefix}{name}"
        ann = f.annotation
        if isinstance(ann, type) and issubclass(ann, BaseModel):
            out.update(gene_bounds(ann, path + "."))
        elif ann is bool:
            out[path] = bool
        elif model is WeatherGenes and name in PER_SITE_BOUNDS:
            for site in SiteCode:
                out[f"{path}.{site}"] = PER_SITE_BOUNDS[name]
        else:
            lo = next((m.ge for m in f.metadata if getattr(m, "ge", None) is not None), None)
            hi = next((m.le for m in f.metadata if getattr(m, "le", None) is not None), None)
            if lo is not None and hi is not None:
                out[path] = (lo, hi)
    return out
