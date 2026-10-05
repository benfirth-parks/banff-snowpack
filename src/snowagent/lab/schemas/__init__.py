"""The lab's data contracts (pydantic v2). New and additive: nothing outside ``snowagent.lab`` reads them (ADR-056)."""

from snowagent.lab.schemas.benchmark import (
    CaseManifest,
    CaseType,
    ForecastRun,
    ForecastSource,
    HiddenTruth,
    Split,
    SplitMode,
    TargetScope,
    VisibleBenchmarkCase,
    VisibleForecastRun,
    VisibleLayer,
    VisibleObservation,
    VisiblePit,
    VisibleWeatherHour,
)
from snowagent.lab.schemas.common import AvailabilityAssumption, QualityFlag, SiteCode
from snowagent.lab.schemas.genome import AgentGenome, gene_bounds, normalize_ensemble_weights
from snowagent.lab.schemas.observation import Observation
from snowagent.lab.schemas.prediction import (
    BulkState,
    Confidence,
    PredictedLayer,
    Quantiles,
    SnowpackPrediction,
)
from snowagent.lab.schemas.profile import CriticalClass, ProfileQuality, SnowLayer, SnowProfile
from snowagent.lab.schemas.run import RunKind, RunManifest, ScoringWeights
from snowagent.lab.schemas.site import ReferenceScenario, Site
from snowagent.lab.schemas.weather import WeatherRecord

__all__ = [
    "AgentGenome", "AvailabilityAssumption", "BulkState", "CaseManifest", "CaseType", "Confidence", "CriticalClass",
    "ForecastRun", "ForecastSource", "HiddenTruth", "Observation", "PredictedLayer", "ProfileQuality", "Quantiles", "QualityFlag", "ReferenceScenario",
    "RunKind", "RunManifest", "ScoringWeights", "Site", "SiteCode", "SnowLayer", "SnowProfile", "SnowpackPrediction",
    "Split", "SplitMode", "TargetScope", "VisibleBenchmarkCase", "VisibleForecastRun", "VisibleLayer", "VisibleObservation", "VisiblePit",
    "VisibleWeatherHour", "WeatherRecord", "gene_bounds", "normalize_ensemble_weights",
]
