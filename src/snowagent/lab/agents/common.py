"""Pieces every lab agent shares: the visible weather as one hourly table, the latest pit of the season, layer and
quantile construction, and the prediction envelope (ADR-062).

Agents see only the anonymous ``VisibleBenchmarkCase`` (times relative to as-of). A prediction therefore carries the
case key and times on a fixed anonymous epoch (``ANON_EPOCH``); the harness, which holds the manifest, re-stamps it
with the case id and the real as-of and valid times (``stamp_prediction``) before it is stored or scored.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd

from snowagent.lab.ingest.mapping import CONCERN_CLASSES, critical_class
from snowagent.lab.schemas.benchmark import CaseManifest, VisibleBenchmarkCase, VisiblePit
from snowagent.lab.schemas.genome import AgentGenome
from snowagent.lab.schemas.prediction import (
    BulkState,
    Confidence,
    PredictedLayer,
    Quantiles,
    SnowpackPrediction,
)
from snowagent.lab.schemas.profile import UNKNOWN_GRAIN, CriticalClass

ANON_EPOCH = datetime(2000, 1, 1, tzinfo=UTC)  # agents never know the real date; the harness re-stamps
SIGMA = 5.670374419e-8
MIN_THICKNESS_M = 0.005
# hand-hardness index -> density (kg m-3): the pits' own medians (learn.steer.HARD_RHO_PITS, ADR-039)
HARD_RHO = {1: 130.0, 2: 220.0, 3: 260.0, 4: 308.0, 5: 340.0, 6: 550.0}
HARD_CODES = {1: "F", 2: "4F", 3: "1F", 4: "P", 5: "K", 6: "I"}


class AgentUnavailable(RuntimeError):
    """The agent cannot run here at all (e.g. no SNOWPACK binary): the case is skipped with this reason, not scored
    as a miss. A case the agent can run but cannot predict is an ``insufficient_data`` prediction instead."""


# --------------------------------------------------------------------------------------------- weather


_WX_CACHE: dict[int, tuple[VisibleBenchmarkCase, pd.DataFrame]] = {}
WEATHER_COLUMNS = {"air_temperature_k": "ta_k", "relative_humidity_frac": "rh", "precipitation_mm": "precip_mm",
                   "wind_speed_ms": "ws", "shortwave_radiation_wm2": "sw", "longwave_radiation_wm2": "lw",
                   "snow_depth_m": "hs_m", "wind_direction_deg": "wd"}


def weather_frame(case: VisibleBenchmarkCase) -> pd.DataFrame:
    """Observed hours up to as-of, then the forecast (or stand-in) hours after the last observed hour, as one table
    indexed by ``t_rel_h`` (hours after as-of, end of each hour). Columns: ta_k, rh, precip_mm, ws, wd, sw, lw,
    hs_m (observed hours only; the stand-in withholds it), ``fc`` (True after the last observed hour) and
    ``doy`` (UTC day of year). Missing values stay NaN."""
    hit = _WX_CACHE.get(id(case))
    if hit is not None and hit[0] is case:
        return hit[1]

    def rows(hours, fc: bool) -> pd.DataFrame:
        if not hours:
            return pd.DataFrame(columns=["t_rel_h", "doy", "fc", *WEATHER_COLUMNS.values()])
        d = pd.DataFrame([{"t_rel_h": h.t_rel_h, "doy": h.day_of_year, "fc": fc,
                           **{c: getattr(h, v) for v, c in WEATHER_COLUMNS.items()}} for h in hours])
        return d

    obs = rows(case.weather_observed, False)
    last = float(obs["t_rel_h"].max()) if len(obs) else -math.inf
    fc = rows([h for h in case.weather_forecasts if h.t_rel_h > last + 1e-6], True)
    if len(fc):
        fc["hs_m"] = np.nan
    types = {c: float for c in WEATHER_COLUMNS.values()} | {"t_rel_h": float, "doy": float}
    parts = [x.astype(types) for x in (obs, fc) if len(x)]
    d = pd.concat(parts, ignore_index=True) if len(parts) > 1 else parts[0] if parts else obs.astype(types)
    d = d.sort_values("t_rel_h").drop_duplicates("t_rel_h", keep="first").set_index("t_rel_h")
    if len(_WX_CACHE) >= 4:  # agents of one case share the table; keep only the last few cases
        _WX_CACHE.pop(next(iter(_WX_CACHE)))
    _WX_CACHE[id(case)] = (case, d)
    return d


def snow_fraction(ta_c: np.ndarray | float, threshold_c: float, range_k: float) -> np.ndarray:
    """Solid share of precipitation: 1 below threshold - range/2, 0 above threshold + range/2, linear between."""
    return np.clip((threshold_c + range_k / 2 - np.asarray(ta_c, dtype=float)) / range_k, 0.0, 1.0)


def new_snow_density(ta_c: np.ndarray | float, ws: np.ndarray | float, genes: dict) -> np.ndarray:
    rho = (genes["new_snow_density_kg_m3"] + genes["new_snow_density_temp_coeff"] * (np.asarray(ta_c) + 10.0)
           + genes["new_snow_density_wind_coeff"] * np.nan_to_num(np.asarray(ws, dtype=float), nan=2.0))
    return np.clip(rho, 30.0, 350.0)


def last_value(s: pd.Series, before: float = 0.0, within_h: float = 24.0) -> float | None:
    """The latest non-null value at t <= ``before`` and within ``within_h`` of it."""
    v = s[(s.index <= before + 1e-6) & (s.index >= before - within_h)].dropna()
    return float(v.iloc[-1]) if len(v) else None


def value_near(s: pd.Series, t: float, within_h: float = 3.0) -> float | None:
    v = s[(s.index >= t - within_h) & (s.index <= t + within_h)].dropna()
    if not len(v):
        return None
    return float(v.iloc[int(np.argmin(np.abs(v.index.to_numpy() - t)))])


# --------------------------------------------------------------------------------------------- pits


def season_pits(case: VisibleBenchmarkCase) -> list[VisiblePit]:
    """Pits of the case's own season (``season_offset`` 0), oldest first."""
    return sorted((p for p in case.permitted_pits if p.season_offset == 0), key=lambda p: p.t_rel_h)


def pit_snow_depth(p: VisiblePit) -> float | None:
    if p.snow_depth_m is not None and p.snow_depth_m > 0:
        return float(p.snow_depth_m)
    if p.layers:
        return float(max(ly.bottom_depth_m for ly in p.layers))
    return None


def latest_season_pit(case: VisibleBenchmarkCase, with_layers: bool = True) -> VisiblePit | None:
    pits = [p for p in season_pits(case) if (p.layers if with_layers else pit_snow_depth(p))]
    return pits[-1] if pits else None


# --------------------------------------------------------------------------------------------- layers


@dataclass
class Lyr:
    """An agent's working layer: depth from the surface (m), IACS grain, hand-hardness index, presence."""

    top: float
    bottom: float
    grain: str = UNKNOWN_GRAIN
    hardness_index: float | None = None
    density: float | None = None
    prob: float = 0.7
    source: str = ""
    extra: dict = field(default_factory=dict)

    @property
    def thickness(self) -> float:
        return self.bottom - self.top

    @property
    def critical(self) -> CriticalClass:
        return critical_class(self.grain)[0]


def hardness_from_density(rho: float | None) -> float | None:
    """Hand-hardness index from density (inverse of the pits' medians, ADR-039), 1..6."""
    if rho is None or not np.isfinite(rho):
        return None
    xs, ys = list(HARD_RHO.values()), list(HARD_RHO)
    return float(np.interp(rho, xs, ys))


def density_from_hardness(h: float | None, default: float = 250.0) -> float:
    if h is None or not np.isfinite(h):
        return default
    return float(np.interp(h, list(HARD_RHO), list(HARD_RHO.values())))


def hardness_code(index: float | None) -> str | None:
    """OGRS code of a hand-hardness index (F=1 .. I=6, thirds as +/-)."""
    if index is None or not np.isfinite(index):
        return None
    x = float(np.clip(index, 1.0, 6.0))
    base = int(round(x))
    d = x - base
    suffix = "+" if d > 1 / 6 else "-" if d < -1 / 6 else ""
    if base == 6 and suffix == "+":
        suffix = ""
    if base == 1 and suffix == "-":
        suffix = ""
    return HARD_CODES[base] + suffix


def scale_layers(layers: list[Lyr], factor: float) -> list[Lyr]:
    return [replace(ly, top=ly.top * factor, bottom=ly.bottom * factor) for ly in layers]


def shift_layers(layers: list[Lyr], dz: float) -> list[Lyr]:
    return [replace(ly, top=ly.top + dz, bottom=ly.bottom + dz) for ly in layers]


def clean_layers(layers: list[Lyr], hs: float) -> list[Lyr]:
    """Surface to ground, inside [0, hs], no layer thinner than half a centimetre."""
    out = []
    for ly in sorted(layers, key=lambda x: (x.top, x.bottom)):
        top, bottom = max(0.0, ly.top), min(hs, ly.bottom)
        if bottom - top >= MIN_THICKNESS_M:
            out.append(replace(ly, top=top, bottom=bottom))
    return out


def insert_layer(layers: list[Lyr], new: Lyr) -> list[Lyr]:
    """``new`` placed into a non-overlapping column: the layers it overlaps are cut around it."""
    out = []
    for ly in layers:
        if ly.bottom <= new.top or ly.top >= new.bottom:
            out.append(ly)
            continue
        if ly.top < new.top:
            out.append(replace(ly, bottom=new.top))
        if ly.bottom > new.bottom:
            out.append(replace(ly, top=new.bottom))
    out.append(new)
    return sorted((ly for ly in out if ly.thickness >= MIN_THICKNESS_M / 5), key=lambda x: x.top)


def quantiles(p50: float, frac: float, floor: float) -> Quantiles:
    half = max(abs(p50) * frac, floor)
    p50 = max(p50, 0.0)
    return Quantiles(p10=max(0.0, p50 - half), p50=p50, p90=p50 + half)


def predicted_layer(i: int, ly: Lyr, spread: float, confidence: float) -> PredictedLayer:
    eps = 1e-4
    t50, b50 = ly.top, max(ly.bottom, ly.top + 2 * eps)
    t10, t90 = max(0.0, t50 - spread), t50 + spread
    b10 = max(t10 + eps, b50 - spread)
    b10 = min(b10, b50)
    b90 = max(b50 + spread, t90 + eps)
    cls = ly.critical
    return PredictedLayer(
        name=f"L{i + 1:02d}", top_depth_m=Quantiles(p10=t10, p50=t50, p90=t90),
        bottom_depth_m=Quantiles(p10=b10, p50=b50, p90=b90),
        grain_form=[ly.grain] if ly.grain and ly.grain != UNKNOWN_GRAIN else [],
        hardness=hardness_code(ly.hardness_index), probability_present=float(np.clip(ly.prob, 0.0, 1.0)),
        is_layer_of_concern=cls in CONCERN_CLASSES, critical_class=cls,
        confidence=float(np.clip(confidence, 0.0, 1.0)))


def build_prediction(case: VisibleBenchmarkCase, genome: AgentGenome, hs_p50: float, layers: list[Lyr],
                     confidence: float, limits: list[str] | None = None, metadata: dict | None = None,
                     explanation: str | None = None) -> SnowpackPrediction:
    """The prediction contract from an agent's layers; depths from the genome's uncertainty genes."""
    g = genome.genes
    hs_p50 = max(0.0, float(hs_p50))
    lyrs = clean_layers(layers, hs_p50) if hs_p50 > 0 else []
    return SnowpackPrediction(
        case_id=case.case_key, agent_id=genome.agent_id, issued_at=ANON_EPOCH,
        valid_at=ANON_EPOCH + timedelta(hours=case.horizon_hours), site_code=case.site_code, scenario=case.scenario,
        bulk_state=BulkState(snow_depth_m=quantiles(hs_p50, g["depth_spread_frac"], g["depth_spread_floor_m"])),
        layers=[predicted_layer(i, ly, g["boundary_spread_m"], confidence) for i, ly in enumerate(lyrs)],
        confidence=Confidence(overall=float(np.clip(confidence, 0, 1)), main_limits=limits or []),
        explanation=explanation,
        model_metadata={"family": genome.family.value, "genome_hash": genome.genome_hash,
                        "genome_label": genome.label, **(metadata or {})})


def insufficient(case: VisibleBenchmarkCase, genome: AgentGenome, reason: str, metadata: dict | None = None
                 ) -> SnowpackPrediction:
    return SnowpackPrediction(
        case_id=case.case_key, agent_id=genome.agent_id, issued_at=ANON_EPOCH,
        valid_at=ANON_EPOCH + timedelta(hours=case.horizon_hours), site_code=case.site_code, scenario=case.scenario,
        status="insufficient_data", insufficient_data_reason=reason, confidence=Confidence(overall=0.0),
        model_metadata={"family": genome.family.value, "genome_hash": genome.genome_hash,
                        "genome_label": genome.label, **(metadata or {})})


def stamp_prediction(pred: SnowpackPrediction, manifest: CaseManifest) -> SnowpackPrediction:
    """Harness side: the prediction with the case id and the real as-of and valid times (validated again). The
    agent's answer must be for this case (its case key) and horizon."""
    if pred.case_id != manifest.case_key:
        raise ValueError(f"prediction for case key {pred.case_id}, expected {manifest.case_key}")
    d = pred.model_dump()
    d.update(case_id=manifest.case_id, issued_at=manifest.as_of_time, valid_at=manifest.valid_time)
    return SnowpackPrediction.model_validate(d)
