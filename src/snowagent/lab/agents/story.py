"""Story agent: every layer keeps a short history of what happened to it, and a weighted sum of that history
decides its grain form (ADR-119; owner 2026-11-19: "look at a few others with differing genome structures").

The column model (ADR-118) still does the heat, water, settlement and snowfall, with all its own settings fixed at
their defaults. Its grain rules and its surface hoar growth are replaced. The genes are the weights, so the evidence
for each weight is every layer of every case that had that history, not one plot.

History kept per element, in hours, each fading with an e-folding time of ``sty_memory_days`` (what happened
recently counts more):

- ``grad``: the day-averaged temperature gradient above 10 K m-1, in units of that threshold
- ``big``: hours with the gradient at 20 K m-1 or more
- ``near``: hours within 15 cm of the surface with the hour's own gradient at 100 K m-1 or more (Birkeland 1998)
- ``crust``: dry hours next to a melt-freeze crust (Hammonds et al. 2015)
- ``onwet``: dry hours lying on wet or refreezing snow (Jamieson and Fierz 2004)
- ``thin``: hours with less than 60 cm of snow on the ground
- ``weak``: dry hours with the gradient under 5 K m-1
- ``warm``: dry hours warmer than -2 degC
- ``dense``: hours denser than 350 kg m-3 (Marbouty 1980: no faceting in dense snow)
- ``prior``: 1 where the start pit reported facets

Dry snow that is not a crust is facets while

    -1 + w_grad grad/72 + w_near near/36 + w_crust crust/240 + w_wet onwet/24 + w_thin thin/720
       - w_weak weak/720 - w_warm warm/720 - w_dense dense/720 + w_prior prior  >=  0

(each history is divided by the hours that, alone and at weight 1, make facets), rounds again once the sum falls
below ``-sty_hold``, and facets become depth hoar, for good, when

    -1 + w_big big/400 + w_dh_thin thin/720 - w_dh_dense dense/720  >=  0.

New snow decomposes by age as in the column model (``sty_pp_hours``, ``sty_df_hours``). Surface hoar is a layer
1 cm thick laid down when new snow buries a surface carrying at least ``sty_sh_hours`` of hoar weather: an hour with
the surface colder than the air's dew point, wind at most ``sty_sh_max_wind_ms``, humidity at least
``sty_sh_min_rh`` and no precipitation adds one; an hour with the surface warmer than the dew point takes one away,
a windy hour ``sty_sh_wind_loss_h`` and a melting or rainy hour ``sty_sh_melt_loss_h``. The same test puts surface
hoar on the surface at the valid time.
"""

from __future__ import annotations

import math

import numpy as np

from snowagent.lab.agents import column as cm
from snowagent.lab.agents.column import Column, ColumnExt, Hour
from snowagent.lab.agents.common import block_defaults, build_prediction, insufficient
from snowagent.lab.schemas.benchmark import ForecastSource, VisibleBenchmarkCase
from snowagent.lab.schemas.genome import AgentFamily, AgentGenome
from snowagent.lab.schemas.prediction import SnowpackPrediction

AGENT_VERSION = "story-1"
FIXED_BLOCKS = ("col_forcing", "new_snow", "col_energy", "col_settlement", "col_water", "col_metamorphism",
                "col_surface_hoar")  # the column's own settings, at their defaults
GRAD, BIG, NEAR, CRUST, ONWET, THIN, WEAK, WARM, DENSE, PRIOR = range(10)
N_HIST = 10
G0, G_BIG, G_NEAR, G_WEAK = 10.0, 20.0, 100.0, 5.0  # K m-1
NEAR_DEPTH_M, THIN_PACK_M, DENSE_KG_M3, WARM_C = 0.15, 0.6, 350.0, -2.0
SH_DZ_M, SH_KG_M2 = 0.01, 1.0


def genes_of(genome: AgentGenome) -> dict:
    """The genome's genes over the fixed column settings (plot snowfall genes map onto the column's)."""
    g = block_defaults(*FIXED_BLOCKS) | dict(genome.genes)
    for plot in ("bow", "goat", "simp"):
        g[f"col_precip_mult_{plot}"] = g[f"snow_mult_{plot}"]
    return g


def facet_score(g: dict, h: np.ndarray) -> np.ndarray:
    return (-1.0 + g["sty_w_grad"] * h[:, GRAD] / 72.0 + g["sty_w_near"] * h[:, NEAR] / 36.0
            + g["sty_w_crust"] * h[:, CRUST] / 240.0 + g["sty_w_wet"] * h[:, ONWET] / 24.0
            + g["sty_w_thin"] * h[:, THIN] / 720.0 - g["sty_w_weak"] * h[:, WEAK] / 720.0
            - g["sty_w_warm"] * h[:, WARM] / 720.0 - g["sty_w_dense"] * h[:, DENSE] / 720.0
            + g["sty_w_prior"] * h[:, PRIOR])


def depth_hoar_score(g: dict, h: np.ndarray) -> np.ndarray:
    return (-1.0 + g["sty_w_big"] * h[:, BIG] / 400.0 + g["sty_w_dh_thin"] * h[:, THIN] / 720.0
            - g["sty_w_dh_dense"] * h[:, DENSE] / 720.0)


class StoryExt(ColumnExt):
    n_hist = N_HIST

    def __init__(self) -> None:
        self.hoar_h = 0.0  # hours of hoar weather standing on the surface

    def start(self, g: dict, col: Column, pit) -> None:
        if col.hist is not None and len(col):
            col.hist[:, PRIOR] = [1.0 if c == "FC" else 0.0 for c in col.cls]

    # ---- surface hoar: weather since the last snowfall
    def hoar_growth(self, g: dict, hr: Hour, dew_c: float, rate: float) -> float:
        if hr.ts < dew_c and hr.vw <= g["sty_sh_max_wind_ms"] and hr.rh >= g["sty_sh_min_rh"]:
            self.hoar_h += 1.0
        elif hr.ts > dew_c:
            self.hoar_h = max(self.hoar_h - 1.0, 0.0)  # the hoar sublimates in the day
        return 0.0  # no physical hoar growth: the layer is laid down at burial

    def hoar_ready(self, g: dict) -> bool:
        return self.hoar_h >= g["sty_sh_hours"]

    def before_snowfall(self, g: dict, col: Column, hr: Hour) -> None:
        if self.hoar_ready(g) and col.cls[-1] != "SH":
            col.append(SH_DZ_M, SH_KG_M2, min(hr.ts, 0.0), "SH", "SH", new=True)
        self.hoar_h = 0.0

    def finish(self, g: dict, col: Column) -> None:
        """Surface hoar standing on the surface at the valid time."""
        if len(col) and self.hoar_ready(g) and col.cls[-1] != "SH":
            col.append(SH_DZ_M, SH_KG_M2, float(min(col.T[-1], 0.0)), "SH", "SH", new=True)

    # ---- grain forms from each element's history
    def metamorphism(self, g: dict, col: Column, ts: float, tg: float, hr: Hour) -> bool:
        if hr.vw > g["col_sh_destroy_wind_ms"]:
            self.hoar_h = max(self.hoar_h - g["sty_sh_wind_loss_h"], 0.0)
        if hr.q_melt > 0 or hr.rain > 0.05:
            self.hoar_h = max(self.hoar_h - g["sty_sh_melt_loss_h"], 0.0)
        n = len(col)
        z = np.cumsum(col.dz) - col.dz / 2
        zz = np.concatenate([[0.0], z, [col.hs]])
        tt = np.concatenate([[tg], col.T, [ts]])
        g_now = (tt[:-2] - tt[2:]) / np.maximum(zz[2:] - zz[:-2], 1e-3)
        col.gbar = col.gbar + (g_now - col.gbar) / cm.GRAD_AVG_H
        grad = np.abs(col.gbar)
        dry = col.water <= 1e-6
        crust = np.array([c == "MFcr" for c in col.cls], dtype=bool)
        by_crust = np.zeros(n, dtype=bool)
        on_wet = np.zeros(n, dtype=bool)
        if n > 1:
            by_crust[:-1] |= crust[1:]
            by_crust[1:] |= crust[:-1]
            on_wet[1:] = (col.water[:-1] > 1e-6) | (col.frz[:-1] > 0.0)
        h = col.hist
        h *= math.exp(-1.0 / (24.0 * g["sty_memory_days"]))
        h[:, GRAD] += np.where(dry, np.maximum(grad - G0, 0.0) / G0, 0.0)
        h[:, BIG] += dry & (grad >= G_BIG)
        h[:, NEAR] += dry & (col.hs - z <= NEAR_DEPTH_M) & (np.abs(g_now) >= G_NEAR)
        h[:, CRUST] += dry & by_crust & ~crust
        h[:, ONWET] += dry & on_wet
        h[:, THIN] += col.hs < THIN_PACK_M
        h[:, WEAK] += dry & (grad < G_WEAK)
        h[:, WARM] += dry & (col.T > WARM_C)
        h[:, DENSE] += col.rho > DENSE_KG_M3
        col.age += 1.0
        col.wet_h = np.where(dry, 0.0, col.wet_h + 1.0)
        s_fc, s_dh = facet_score(g, h), depth_hoar_score(g, h)
        tf = np.exp(g["col_temp_coeff"] * np.minimum(col.T, 0.0))
        for i in range(n):
            c = col.cls[i]
            if c == "SH":
                continue
            if not dry[i]:
                if col.wet_h[i] >= cm.WET_MF_H and c != "MF":
                    cm._set(col, i, "MF")
                    h[i, PRIOR] = 0.0
                continue
            if col.frz[i] >= g["col_crust_water_frac"] * col.ice[i] and col.T[i] < -0.1:
                if c != "MFcr":
                    cm._set(col, i, "MFcr")
                col.frz[i] = 0.0
                continue
            if col.T[i] < -0.1:
                col.frz[i] = 0.0
            if c in ("MFcr", "DH"):
                continue
            if c == "FC":
                if s_dh[i] >= 0.0:
                    cm._set(col, i, "DH")
                elif s_fc[i] < -g["sty_hold"]:
                    cm._set(col, i, "RG")
            elif s_fc[i] >= 0.0:
                cm._set(col, i, "FC")
            elif c == "PP" and col.age[i] * tf[i] >= g["sty_pp_hours"]:
                cm._set(col, i, "DF")
            elif c == "DF" and col.age[i] * tf[i] >= g["sty_pp_hours"] + g["sty_df_hours"]:
                cm._set(col, i, "RG")
        return True


class StoryAgent:
    def __init__(self, genome: AgentGenome) -> None:
        if genome.family != AgentFamily.story:
            raise ValueError(f"StoryAgent needs a story genome, got {genome.family}")
        self.genome = genome
        self.agent_id = genome.agent_id

    def predict(self, case: VisibleBenchmarkCase, seed: int) -> SnowpackPrediction:
        g = genes_of(self.genome)
        meta = {"agent_version": AGENT_VERSION}
        if case.site is None or not case.weather_observed:
            return insufficient(case, self.genome, "no visible weather", meta)
        ext = StoryExt()
        try:
            col, notes = cm.simulate(case, g, ext)
        except ValueError as exc:
            return insufficient(case, self.genome, f"no forcing: {exc}"[:300], meta)
        ext.finish(g, col)
        layers = cm.column_layers(col, g, case)
        limits = ["the lab's own column model with grain forms from each layer's history (no SNOWPACK); "
                  "flat-plot column"]
        if case.forecast_source == ForecastSource.archived_gfs:
            limits.append("forecast hours are raw GFS at the plot (no correction)")
        return build_prediction(case, self.genome, col.hs, layers, g["presence_confidence"], limits, meta | notes)


__all__ = ["StoryAgent", "StoryExt", "depth_hoar_score", "facet_score", "genes_of"]
