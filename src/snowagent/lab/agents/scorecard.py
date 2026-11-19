"""Scorecard agent: fixed traits of each plot, weighted, set a few dials on the column model (ADR-122; the owner's
own idea of a weighted scorecard of site factors, 2026-11-18).

There is no per-plot gene. What differs between plots comes only from ``config/site_traits.yaml``: the owner's
dated site sheet and terrain numbers from a 30 m elevation map, both fixed before any agent of this family was
trained. Three indices (0 to 1) summarise a plot:

- ``ground``: damp ground, deadfall and cold air pooling (mean of the three)
- ``exposure``: wind exposure from the sheet and how far the plot stands above the ground within 500 m
- ``canopy``: how closely trees close in (less open sky, nearer trees, taller trees)

Six dials each take a weight per index, shared by all plots: ``exp(sum of weight x (2 index - 1))``, so an index of
0 or 1 at weight w is a factor of e^-w or e^w and a plot in the middle of every index gets 1.

- ``snow`` multiplies precipitation; ``wind`` multiplies the wind speed the plot gets
- ``facet`` multiplies the faceting and depth hoar rates; ``hoar`` the surface hoar growth
- ``crust`` divides the refrozen water a crust needs
- ``ground`` is additive: 2 K x the weighted sum is added to the ground temperature (0.5 degC at most)

Two more genes use the map's shelter from the hour's own wind direction (Winstral Sx: positive = sheltered),
blended between the 300 m and 2 km values by ``sc_shelter_scale``: ``sc_shelter_wind`` slows the wind by
exp(-k Sx / 10 deg) and ``sc_lee_snow`` adds k x Sx / 10 deg of snowfall at 8 m/s or more (less in lighter wind).

Every weight starts at 0, so the default is exactly the column model at its defaults: nothing about a plot is
assumed until training finds it. With three training plots the three indices can do no more than tell the plots
apart, so the weights are not evidence on their own; the test of this structure is Parker Ridge, which is never
trained on. A plot without traits (not in the file) gets every dial at 1.
"""

from __future__ import annotations

import math
from functools import lru_cache

import numpy as np
import yaml

from snowagent.lab.agents import column as cm
from snowagent.lab.agents.column import ColumnExt
from snowagent.lab.agents.common import block_defaults, build_prediction, insufficient
from snowagent.lab.schemas.benchmark import ForecastSource, VisibleBenchmarkCase
from snowagent.lab.schemas.genome import DEFAULT_LAB_CONFIG, AgentFamily, AgentGenome
from snowagent.lab.schemas.prediction import SnowpackPrediction

AGENT_VERSION = "scorecard-1"
SITE_TRAITS = DEFAULT_LAB_CONFIG.parent / "site_traits.yaml"
FIXED_BLOCKS = ("col_forcing", "new_snow", "col_energy", "col_settlement", "col_water")
INDICES = ("ground", "exposure", "canopy")
DIALS = ("snow", "wind", "facet", "hoar", "crust", "ground")
GROUND_K = 2.0
SX_UNIT_DEG, SX_CLIP_DEG, LEE_WIND_MS = 10.0, 20.0, 8.0


@lru_cache(maxsize=1)
def _traits() -> dict:
    return yaml.safe_load(SITE_TRAITS.read_text())["plots"]


def site_indices(site_code: str) -> dict[str, float] | None:
    """The plot's three indices from its sheet answers and map numbers; None for a plot without traits."""
    t = _traits().get(site_code)
    if t is None:
        return None
    s, m = t["sheet"], t["maps"]
    perched = min(max((m["height_vs_500m_m"] + 50.0) / 100.0, 0.0), 1.0)
    return {"ground": (s["moist_ground"] + min(2.0 * s["cold_pool"], 1.0) + s["deadfall"]) / 3.0,
            "exposure": (s["wind_exposed"] + perched) / 2.0,
            "canopy": ((1.0 - s["sky_open"]) + (1.0 - min(s["tree_dist_m"] / 40.0, 1.0))
                       + min(s["tree_height_m"] / 15.0, 1.0)) / 3.0}


def dials(g: dict, site_code: str) -> dict[str, float]:
    """Each dial's weighted sum for the plot (0 for a plot without traits)."""
    idx = site_indices(site_code)
    if idx is None:
        return dict.fromkeys(DIALS, 0.0)
    return {d: sum(g[f"sc_{d}_{k}"] * (2.0 * idx[k] - 1.0) for k in INDICES) for d in DIALS}


class ScorecardExt(ColumnExt):
    def __init__(self, g: dict, site_code: str, sums: dict[str, float]) -> None:
        t = _traits().get(site_code)
        self.snow, self.wind = math.exp(sums["snow"]), math.exp(sums["wind"])
        a = g["sc_shelter_scale"]
        self.sx = None if t is None else ((1.0 - a) * np.array(t["maps"]["shelter_300m_deg"], dtype=float)
                                          + a * np.array(t["maps"]["shelter_2000m_deg"], dtype=float))

    def shelter(self, wd: float) -> float:
        """Shelter (degrees) from wind blowing from ``wd``, between the eight mapped directions."""
        if self.sx is None:
            return 0.0
        x = (wd % 360.0) / 45.0
        i = int(x) % 8
        sx = self.sx[i] + (x - int(x)) * (self.sx[(i + 1) % 8] - self.sx[i])
        return float(min(max(sx, -SX_CLIP_DEG), SX_CLIP_DEG))

    def weather(self, g: dict, vw: float, p: float, wd: float) -> tuple[float, float]:
        sx = self.shelter(wd) / SX_UNIT_DEG
        lee = max(1.0 + g["sc_lee_snow"] * sx * min(vw / LEE_WIND_MS, 1.0), 0.2)
        return vw * self.wind * math.exp(-g["sc_shelter_wind"] * sx), p * self.snow * lee


def genes_for(genome: AgentGenome, site_code: str) -> tuple[dict, dict[str, float]]:
    """The column model's gene values for this plot: its defaults, the genome's shared settings, and the dials."""
    g = block_defaults(*FIXED_BLOCKS) | dict(genome.genes)
    sums = dials(g, site_code)
    rate = math.exp(sums["facet"])
    g["col_facet_hours"] /= rate
    g["col_dh_hours"] /= rate
    g["col_sh_growth_m_h"] *= math.exp(sums["hoar"])
    g["col_crust_water_frac"] /= math.exp(sums["crust"])
    g["col_ground_temp_c"] = min(g["col_ground_temp_c"] + GROUND_K * sums["ground"], 0.5)
    return g, sums


class ScorecardAgent:
    def __init__(self, genome: AgentGenome) -> None:
        if genome.family != AgentFamily.scorecard:
            raise ValueError(f"ScorecardAgent needs a scorecard genome, got {genome.family}")
        self.genome = genome
        self.agent_id = genome.agent_id

    def predict(self, case: VisibleBenchmarkCase, seed: int) -> SnowpackPrediction:
        meta = {"agent_version": AGENT_VERSION}
        if case.site is None or not case.weather_observed:
            return insufficient(case, self.genome, "no visible weather", meta)
        site = str(getattr(case.site_code, "value", case.site_code))
        g, sums = genes_for(self.genome, site)
        try:
            col, notes = cm.simulate(case, g, ScorecardExt(g, site, sums))
        except ValueError as exc:
            return insufficient(case, self.genome, f"no forcing: {exc}"[:300], meta)
        layers = cm.column_layers(col, g, case)
        limits = ["the lab's own column model steered by fixed site traits (no SNOWPACK); flat-plot column"]
        if site_indices(site) is None:
            limits.append("no site traits for this plot: every dial at 1")
        if case.forecast_source == ForecastSource.archived_gfs:
            limits.append("forecast hours are raw GFS at the plot (no correction)")
        notes["dials"] = ",".join(f"{d}:{v:+.2f}" for d, v in sums.items())
        return build_prediction(case, self.genome, col.hs, layers, g["presence_confidence"], limits, meta | notes)


__all__ = ["ScorecardAgent", "ScorecardExt", "dials", "genes_for", "site_indices"]
