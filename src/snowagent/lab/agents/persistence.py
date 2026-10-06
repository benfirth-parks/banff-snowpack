"""Persistence agent: the last available pit of the season carried forward (ADR-062).

1. The latest visible pit of the case's season with layers is the structure.
2. Depth at as-of: the pit's snow depth plus ``depth_change_weight`` x the measured snow-depth change between the pit
   and as-of (plot sensor, where both ends were measured); then ``pit_trust`` weighs that against the sensor's own
   depth at as-of (1 = the pit only). A rise is new snow on top of the pit's layers; a fall compresses them.
3. After as-of the forecast (or stand-in) weather adds snowfall (``forecast_snow_weight`` x precipitation x
   ``precipitation_factor``, solid share by the rain-snow genes, new-snow density by the new_snow genes, one layer
   per storm separated by ``storm_gap_h``), settles the column (settlement genes) and melts from the top
   (degree-day ``melt_factor_mm_per_k_day``).
4. Carried layers keep their grain and hardness; their presence probability halves every
   ``pit_age_half_life_days``.

Without a pit in the season the agent predicts snow depth only from the sensor (no layers); without that either it
returns ``insufficient_data``.
"""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from snowagent.lab.agents.common import (
    Lyr,
    build_prediction,
    density_from_hardness,
    hardness_from_density,
    insufficient,
    last_value,
    latest_season_pit,
    new_snow_density,
    pit_snow_depth,
    scale_layers,
    shift_layers,
    snow_fraction,
    value_near,
    weather_frame,
)
from snowagent.lab.schemas.benchmark import VisibleBenchmarkCase
from snowagent.lab.schemas.genome import AgentFamily, AgentGenome
from snowagent.lab.schemas.prediction import SnowpackPrediction

AGENT_VERSION = "persistence-1"


def storm_layers(wx: pd.DataFrame, t0: float, t1: float, genes: dict, weight: float = 1.0) -> list[dict]:
    """Snowfall between t0 and t1 (hours after as-of) as storm layers, oldest first: swe (mm), thickness (m) at
    deposition, age at t1 (h). Missing precipitation counts as none."""
    w = wx[(wx.index > t0) & (wx.index <= t1)]
    if w.empty:
        return []
    ta = w["ta_k"].interpolate(limit_direction="both").to_numpy() - 273.15 + genes["temperature_offset_k"]
    if np.isnan(ta).all():
        ta = np.full(len(w), -5.0)
    p = np.nan_to_num(w["precip_mm"].to_numpy(), nan=0.0).clip(min=0) * genes["precipitation_factor"] * weight
    swe = p * snow_fraction(ta, genes["rain_snow_threshold_c"], genes["rain_snow_range_k"])
    rho = new_snow_density(ta, w["ws"].to_numpy(), genes)
    storms: list[dict] = []
    last_t = -np.inf
    for t, s, r in zip(w.index.to_numpy(), swe, rho, strict=True):
        if s < 0.05:
            continue
        if not storms or t - last_t > genes["storm_gap_h"]:
            storms.append({"swe": 0.0, "thick": 0.0, "t_end": t})
        storms[-1]["swe"] += s
        storms[-1]["thick"] += s / r
        storms[-1]["t_end"] = t
        last_t = t
    for st in storms:
        st["age_h"] = t1 - st["t_end"]
    return storms


def settle_and_melt(layers: list[Lyr], wx: pd.DataFrame, t0: float, t1: float, genes: dict) -> list[Lyr]:
    """Settle every layer for the days t0..t1 (rate x exp(coeff x min(T, 0)) x (1 - rho / rho_max)) and remove the
    degree-day melt from the top. Layers ordered surface to ground."""
    if not layers or t1 <= t0:
        return layers
    w = wx[(wx.index > t0) & (wx.index <= t1)]
    ta = w["ta_k"].to_numpy() - 273.15 + genes["temperature_offset_k"] if len(w) else np.array([-5.0])
    tmean = float(np.nanmean(ta)) if np.isfinite(ta).any() else -5.0
    days = (t1 - t0) / 24.0
    out = []
    for ly in layers:
        rho = ly.density or density_from_hardness(ly.hardness_index)
        rate = genes["settlement_rate_per_day"] * np.exp(genes["settlement_temp_coeff"] * min(tmean, 0.0)) * max(
            0.0, 1.0 - rho / genes["max_density_kg_m3"])
        f = float(np.exp(-rate * days))
        out.append(replace(ly, top=ly.top, bottom=ly.top + ly.thickness * f, density=min(rho / f, 917.0)))
    melt_mm = float(np.nansum(np.clip(ta, 0.0, None))) / 24.0 * genes["melt_factor_mm_per_k_day"]
    kept = []
    for ly in out:  # surface first: melt removes water equivalent from the top down
        rho = ly.density or 250.0
        swe = ly.thickness * rho
        if melt_mm >= swe:
            melt_mm -= swe
            continue
        if melt_mm > 0:
            ly = replace(ly, bottom=ly.bottom - melt_mm / rho)
            melt_mm = 0.0
        kept.append(ly)
    return restack(kept)


def restack(layers: list[Lyr]) -> list[Lyr]:
    """Layers (surface first) stacked from depth 0 by their thicknesses."""
    z, out = 0.0, []
    for ly in layers:
        out.append(replace(ly, top=z, bottom=z + ly.thickness))
        z += ly.thickness
    return out


def add_storms(layers: list[Lyr], storms: list[dict], prob: float) -> list[Lyr]:
    """Storm layers on top (newest at the surface): PP within 24 h of its last snowfall, DF after."""
    for st in storms:
        if st["thick"] < 0.003:
            continue
        rho = st["swe"] / st["thick"]
        layers = shift_layers(layers, st["thick"])
        layers.insert(0, Lyr(top=0.0, bottom=st["thick"], grain="PP" if st["age_h"] < 24 else "DF",
                             hardness_index=hardness_from_density(rho), density=rho, prob=prob, source="new_snow"))
    return layers


class PersistenceAgent:
    """The last available pit carried forward, adjusted for depth change where measured weather allows."""

    def __init__(self, genome: AgentGenome) -> None:
        if genome.family != AgentFamily.persistence:
            raise ValueError(f"PersistenceAgent needs a persistence genome, got {genome.family}")
        self.genome = genome
        self.agent_id = genome.agent_id

    def carry(self, case: VisibleBenchmarkCase, genes: dict | None = None) -> tuple[float | None, list[Lyr], dict]:
        """(snow depth at the valid time, layers, notes). Also used by the hybrid agent."""
        g = genes or self.genome.genes
        wx = weather_frame(case)
        hs_sensor = last_value(wx["hs_m"], 0.0, 24.0) if len(wx) else None
        pit = latest_season_pit(case, with_layers=True)
        notes: dict = {"pit_age_h": None, "sensor_depth_asof_m": hs_sensor}
        if pit is None:
            if hs_sensor is None:
                return None, [], notes | {"reason": "no pit with layers in the season and no measured snow depth"}
            layers: list[Lyr] = []
            hs = hs_sensor
            t_from = 0.0
        else:
            hs_pit = pit_snow_depth(pit) or 0.0
            notes["pit_age_h"] = -pit.t_rel_h
            age_d = (case.horizon_hours - pit.t_rel_h) / 24.0
            prob = g["presence_confidence"] * 0.5 ** (age_d / g["pit_age_half_life_days"])
            layers = [Lyr(top=ly.top_depth_m, bottom=ly.bottom_depth_m, grain=ly.grain_primary,
                          hardness_index=ly.hardness_index, density=ly.density_kg_m3, prob=prob, source="pit")
                      for ly in pit.layers]
            hs0 = hs_pit
            hs_at_pit = value_near(wx["hs_m"], pit.t_rel_h, 3.0) if len(wx) else None
            if hs_at_pit is not None and hs_sensor is not None:
                hs0 = hs_pit + g["depth_change_weight"] * (hs_sensor - hs_at_pit)
                notes["sensor_change_m"] = round(float(hs_sensor - hs_at_pit), 3)
            hs = g["pit_trust"] * hs0 + (1 - g["pit_trust"]) * hs_sensor if hs_sensor is not None else hs0
            hs = max(hs, 0.0)
            if hs > hs_pit + 0.01:  # new snow since the pit
                layers = shift_layers(layers, hs - hs_pit)
                rho = 120.0
                layers.insert(0, Lyr(top=0.0, bottom=hs - hs_pit, grain="DF", hardness_index=hardness_from_density(
                    rho), density=rho, prob=g["presence_confidence"], source="new_snow_measured"))
            elif hs_pit > 0 and hs < hs_pit:
                layers = scale_layers(layers, hs / hs_pit)
            t_from = 0.0
        layers = settle_and_melt(layers, wx, t_from, case.horizon_hours, g)
        storms = storm_layers(wx, t_from, case.horizon_hours, g, weight=g["forecast_snow_weight"])
        layers = add_storms(layers, storms, g["presence_confidence"])
        hs_t = max((ly.bottom for ly in layers), default=0.0) if layers else hs + sum(s["thick"] for s in storms)
        notes["forecast_snow_m"] = round(float(sum(s["thick"] for s in storms)), 3)
        return hs_t, layers, notes

    def predict(self, case: VisibleBenchmarkCase, seed: int) -> SnowpackPrediction:
        hs, layers, notes = self.carry(case)
        meta = {"agent_version": AGENT_VERSION, **{k: v for k, v in notes.items() if k != "reason"}}
        if hs is None:
            return insufficient(case, self.genome, notes["reason"], meta)
        limits = [] if layers else ["no pit with layers in the season: snow depth only"]
        conf = self.genome.genes["presence_confidence"] if layers else 0.3
        return build_prediction(case, self.genome, hs, layers, conf, limits, meta)
