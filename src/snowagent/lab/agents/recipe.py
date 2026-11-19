"""Recipe agent: a snowpack built from the last pit by weather recipes, the way a forecaster reasons from the
weather record, with no heat physics at all (ADR-120; owner 2026-11-19: "look at a few others with differing
genome structures").

It reuses the column model's snowfall (ADR-118: snow depth sensor, then precipitation), its starting elements from
the latest pit, its settlement and its layer output, and nothing else of it: there is no energy balance, no heat
conduction and no water flow. Snow temperature, where settlement needs it, is a straight line from the last day's
mean air temperature at the surface (0 degC at most) to 0 degC at the ground. Every threshold of every recipe is a
gene. The recipes, each from the weather since snow last fell unless said otherwise:

- **Surface hoar**: ``rcp_sh_hours`` clear (sky emissivity at most 0.8), dark, humid (``rcp_sh_min_rh``) hours
  below 0 degC with wind at most ``rcp_sh_max_wind_ms``. An hour of wind above ``rcp_destroy_wind_ms``, a thaw hour
  or rain wipes the count. New snow buries the hoar as a 1 cm layer.
- **Near-surface facets** (Birkeland 1998): ``rcp_nsf_hours`` clear hours colder than ``rcp_nsf_temp_c`` turn the
  top ``rcp_nsf_depth_m`` of loose snow lighter than ``rcp_nsf_max_density`` to facets.
- **Wind slab** (Li and Pomeroy 1997): ``rcp_wind_hours`` hours of wind above ``rcp_wind_ms`` on new snow pack the
  top 20 cm of it to at least ``rcp_slab_density_kg_m3`` and round it.
- **Melt** by degree hours (``rcp_melt_mm_per_k_day``) from the top. **Crust**: ``rcp_crust_thaw_h`` thaw hours or
  ``rcp_crust_rain_mm`` of rain on snow, then a freeze (-1 degC), leave a crust ``rcp_crust_thick_m`` thick.
- **Facets on a crust** (Jamieson and Fierz 2004, dry snow on wet): snow that buries the crust within a day of
  the freeze by at most ``rcp_cf_max_burial_m`` and then sees ``rcp_cf_hours`` hours colder than
  ``rcp_cf_temp_c`` gets a facet layer ``rcp_cf_thick_m`` thick on the crust.
- **In the pack**: new snow is PP for ``rcp_pp_days``, DF for a further ``rcp_df_days``, then rounded. With the
  bulk gradient (ten-day mean air temperature over the snow depth) at ``rcp_facet_gradient_k_m`` or more, dry
  snow in the lowest ``rcp_facet_height_m`` facets after ``rcp_facet_hours`` and facets become depth hoar after
  ``rcp_dh_hours`` more; under half that gradient facets round after ``rcp_round_hours``.
"""

from __future__ import annotations

import math

import numpy as np

from snowagent.lab.agents import column as cm
from snowagent.lab.agents.column import Column, Hour
from snowagent.lab.agents.common import (
    block_defaults,
    build_prediction,
    insufficient,
    latest_season_pit,
    new_snow_density,
    snow_fraction,
)
from snowagent.lab.schemas.benchmark import ForecastSource, VisibleBenchmarkCase
from snowagent.lab.schemas.genome import AgentFamily, AgentGenome
from snowagent.lab.schemas.prediction import SnowpackPrediction

AGENT_VERSION = "recipe-1"
FIXED_BLOCKS = ("col_forcing", "new_snow", "col_water")  # rain/snow split, sensor and new-snow settings at defaults
SLAB_DEPTH_M = 0.20
SH_DZ_M, SH_KG_M2 = 0.01, 1.0
CRUST_KG_M3 = 400.0
FREEZE_C = -1.0
AIR_AVG_H = 240.0


def genes_of(genome: AgentGenome) -> dict:
    g = block_defaults(*FIXED_BLOCKS) | dict(genome.genes)
    for plot in ("bow", "goat", "simp"):
        g[f"col_precip_mult_{plot}"] = g[f"snow_mult_{plot}"]
    return g


def _split_top(col: Column, dz: float) -> None:
    """Split the top element so that its upper ``dz`` is an element of its own."""
    i = len(col) - 1
    if col.dz[i] <= dz + 1e-4:
        return
    frac = dz / col.dz[i]
    col.append(dz, col.ice[i] * frac, col.T[i], col.cls[i], col.code[i], col.hard0[i], col.rho0[i], col.age[i],
               col.fac[i], col.dhp[i], col.rnd[i], bool(col.new[i]))
    col.dz[i] -= dz
    col.ice[i] *= 1.0 - frac


def simulate(case: VisibleBenchmarkCase, g: dict) -> tuple[Column, dict]:
    pit = latest_season_pit(case)
    wx = cm.column_forcing(case, pit.t_rel_h if pit is not None else -math.inf)
    notes: dict = {"start": "pit" if pit is not None else "snow-free season start", "hours": 0,
                   "snowfall": "precipitation", "sensor_snow_m": 0.0, "precip_snow_mm": 0.0, "melt_mm": 0.0,
                   "recipes": ""}
    fired = {"hoar": 0, "near_surface": 0, "wind_slab": 0, "crust": 0, "crust_facets": 0}
    t = wx.t
    i0 = 0 if pit is None else int(np.searchsorted(t, pit.t_rel_h - 1e-6))
    col = Column.empty()
    if pit is not None:
        t_air = float(np.mean(wx.ta[max(0, i0 - 24): i0 + 1])) if i0 < len(t) else -5.0
        cm._start_from_pit(col, pit, t_air, 0.0)
    plot_mult = g[cm.PLOT_GENE.get(str(getattr(case.site_code, "value", case.site_code)), "col_precip_mult_bow")]
    hs_off = 0.0
    if wx.hs is not None and pit is not None and i0 < len(t) and np.isfinite(wx.hs[i0]):
        hs_off = (wx.hs[i0] - col.hs) * g["col_sensor_offset_weight"]
    if wx.hs is not None:
        notes["snowfall"] = "snow depth sensor, then precipitation"
    since_snow = 240.0
    ta_day = ta_ten = float(wx.ta[i0]) if i0 < len(t) else -5.0
    hoar_h = cold_h = wind_h = thaw_h = rain_mm = 0.0  # since snow last fell (thaw and rain: since the last freeze)
    crust_age = math.inf  # hours since a crust froze at the surface
    cf_watch: dict | None = None  # snow that buried a fresh crust: {"cold_h", "buried"}
    for i in range(i0, len(t)):
        if t[i] > case.horizon_hours + 1e-6:
            break
        notes["hours"] += 1
        ta, vw, rh = float(wx.ta[i]), float(wx.vw[i]), float(wx.rh[i])
        hr = Hour(ta=ta, vw=vw, rh=rh, sw=float(wx.sw[i]), lw=float(wx.lw[i]))
        ta_day += (ta - ta_day) / 24.0
        ta_ten += (ta - ta_ten) / AIR_AVG_H
        p = float(wx.psum[i]) * (plot_mult if wx.measured[i] else 1.0)
        sf = float(snow_fraction(ta, g["col_rain_snow_mid_c"], g["col_rain_snow_width_k"]))
        rain = p * (1.0 - sf)
        rho_new = float(new_snow_density(ta, vw, g))
        sensor = wx.hs[i] if wx.hs is not None else math.nan
        dz_new = 0.0
        if np.isfinite(sensor):
            gap = (sensor - hs_off) - col.hs
            if gap > g["col_sensor_add_m"] and sf > 0.0:
                dz_new = gap
                notes["sensor_snow_m"] += gap
        else:
            dz_new = p * sf / rho_new
            notes["precip_snow_mm"] += p * sf
        # ---- snowfall buries the surface: surface hoar, and the watch for facets on a fresh crust
        if dz_new > 0.0:
            if len(col) and hoar_h >= g["rcp_sh_hours"] and col.cls[-1] != "SH":
                col.append(SH_DZ_M, SH_KG_M2, 0.0, "SH", "SH", new=True)
                fired["hoar"] += 1
            if len(col) and col.cls[-1] == "MFcr" and crust_age <= 24.0:
                cf_watch = {"cold_h": 0.0, "buried": 0.0}
            if cf_watch is not None:
                cf_watch["buried"] += dz_new
            if (len(col) and col.cls[-1] == "PP" and col.new[-1] and since_snow <= g["storm_gap_h"]
                    and col.dz[-1] + dz_new <= cm.MAX_DZ_TOP):
                col.dz[-1] += dz_new
                col.ice[-1] += dz_new * rho_new
            else:
                left = dz_new
                while left > 1e-6:
                    d = min(left, cm.MAX_DZ_TOP)
                    col.append(d, d * rho_new, min(ta, 0.0), "PP", "PP", new=True)
                    left -= d
            since_snow = 0.0
            hoar_h = cold_h = wind_h = 0.0
        else:
            since_snow += 1.0
        if not len(col):
            continue
        # ---- melt and rain; a freeze afterwards leaves a crust
        if ta > 0.0:
            thaw_h += 1.0
            notes["melt_mm"] += cm._melt_top(col, ta * g["rcp_melt_mm_per_k_day"] / 24.0)
            hoar_h = cold_h = 0.0
        if rain > 0.05:
            rain_mm += rain
            hoar_h = 0.0
        cm._tidy(col)
        if not len(col):
            continue
        if (ta > 0.0 or rain > 0.05) and col.cls[-1] == "SH":
            col.keep(np.arange(len(col)) < len(col) - 1)
            if not len(col):
                continue
        crust_age += 1.0
        if ta <= FREEZE_C and (thaw_h or rain_mm):
            if thaw_h >= g["rcp_crust_thaw_h"] or rain_mm >= g["rcp_crust_rain_mm"]:
                _split_top(col, g["rcp_crust_thick_m"])
                k = len(col) - 1
                cm._set(col, k, "MFcr")
                col.dz[k] = min(col.dz[k], col.ice[k] / CRUST_KG_M3) if col.rho[k] < CRUST_KG_M3 else col.dz[k]
                crust_age = 0.0
                fired["crust"] += 1
            thaw_h = rain_mm = 0.0
        # ---- settlement, on a straight-line temperature
        z = np.cumsum(col.dz) - col.dz / 2
        col.T = min(ta_day, 0.0) * z / max(col.hs, 1e-3)
        cm._settle(g, col)
        # ---- surface recipes
        loose_top = col.cls[-1] in ("PP", "DF", "RG")
        if hr.clear and ta < 0.0 and p <= 0.01:
            if hr.sw < 5.0 and vw <= g["rcp_sh_max_wind_ms"] and rh >= g["rcp_sh_min_rh"]:
                hoar_h += 1.0
            if ta <= g["rcp_nsf_temp_c"]:
                cold_h += 1.0
        if vw > g["rcp_destroy_wind_ms"]:
            hoar_h = 0.0
        if vw >= g["rcp_wind_ms"] and ta < 0.0 and col.cls[-1] in ("PP", "DF"):
            wind_h += 1.0
        depth = 0.0
        if cold_h >= g["rcp_nsf_hours"] and loose_top:
            for k in range(len(col) - 1, -1, -1):
                if depth >= g["rcp_nsf_depth_m"] or col.cls[k] not in ("PP", "DF", "RG"):
                    break
                depth += col.dz[k]
                if col.rho[k] <= g["rcp_nsf_max_density"]:
                    cm._set(col, k, "FC")
                    col.fac[k], col.rnd[k] = 1.0, 0.0
            cold_h = 0.0
            fired["near_surface"] += 1
        if wind_h >= g["rcp_wind_hours"]:
            for k in range(len(col) - 1, -1, -1):
                if depth >= SLAB_DEPTH_M or col.cls[k] not in ("PP", "DF"):
                    break
                depth += col.dz[k]
                if col.rho[k] < g["rcp_slab_density_kg_m3"]:
                    col.dz[k] = col.ice[k] / g["rcp_slab_density_kg_m3"]
                cm._set(col, k, "RG")
            wind_h = 0.0
            fired["wind_slab"] += 1
        # ---- facets on a freshly buried crust
        if cf_watch is not None:
            if ta <= g["rcp_cf_temp_c"]:
                cf_watch["cold_h"] += 1.0
            if cf_watch["buried"] > g["rcp_cf_max_burial_m"] or ta > 0.0:
                cf_watch = None
            elif cf_watch["cold_h"] >= g["rcp_cf_hours"]:
                crusts = [k for k in range(len(col) - 1) if col.cls[k] == "MFcr" and col.cls[k + 1] != "MFcr"]
                if crusts:
                    k = crusts[-1] + 1
                    if col.dz[k] > g["rcp_cf_thick_m"] + 1e-4 and col.cls[k] in ("PP", "DF", "RG"):
                        # the lowest part of the snow on the crust becomes its own facet element
                        frac = g["rcp_cf_thick_m"] / col.dz[k]
                        keep_dz, keep_ice = col.dz[k] * (1 - frac), col.ice[k] * (1 - frac)
                        col.dz[k], col.ice[k] = col.dz[k] * frac, col.ice[k] * frac
                        _insert_above(col, k, keep_dz, keep_ice)
                    if col.cls[k] in ("PP", "DF", "RG"):
                        cm._set(col, k, "FC")
                        col.fac[k], col.rnd[k] = 1.0, 0.0
                        fired["crust_facets"] += 1
                cf_watch = None
        # ---- in the pack
        col.age += 1.0
        grad = abs(min(ta_ten, 0.0)) / max(col.hs, 0.05)
        low = np.cumsum(col.dz) - col.dz / 2 <= g["rcp_facet_height_m"]
        if grad >= g["rcp_facet_gradient_k_m"]:
            col.fac = np.where(low, col.fac + 1.0 / g["rcp_facet_hours"], col.fac)
            col.dhp = np.where(low & (col.fac >= 1.0), col.dhp + 1.0 / g["rcp_dh_hours"], col.dhp)
            col.rnd = np.where(low, 0.0, col.rnd)
        elif grad < 0.5 * g["rcp_facet_gradient_k_m"]:
            col.rnd = col.rnd + 1.0 / g["rcp_round_hours"]
        for k in range(len(col)):
            c = col.cls[k]
            if c in ("SH", "MFcr", "MF", "DH"):
                continue
            if c == "FC":
                if col.dhp[k] >= 1.0:
                    cm._set(col, k, "DH")
                elif col.rnd[k] >= 1.0:
                    cm._set(col, k, "RG")
                    col.fac[k] = 0.0
            elif col.fac[k] >= 1.0:
                cm._set(col, k, "FC")
                col.rnd[k] = 0.0
            elif c == "PP" and col.age[k] >= 24.0 * g["rcp_pp_days"]:
                cm._set(col, k, "DF")
            elif c == "DF" and col.age[k] >= 24.0 * (g["rcp_pp_days"] + g["rcp_df_days"]):
                cm._set(col, k, "RG")
        cm._tidy(col)
    if len(col) and hoar_h >= g["rcp_sh_hours"] and col.cls[-1] != "SH":
        col.append(SH_DZ_M, SH_KG_M2, 0.0, "SH", "SH", new=True)  # hoar standing on the surface at the valid time
        fired["hoar"] += 1
    notes["recipes"] = ",".join(f"{k}:{v}" for k, v in fired.items())
    notes = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in notes.items()}
    return col, notes


def _insert_above(col: Column, k: int, dz: float, ice: float) -> None:
    """A copy of element ``k`` with thickness ``dz`` and mass ``ice`` placed directly above it."""
    order = list(range(k + 1)) + [len(col)] + list(range(k + 1, len(col)))
    col.append(dz, ice, col.T[k], col.cls[k], col.code[k], col.hard0[k], col.rho0[k], col.age[k], col.fac[k],
               col.dhp[k], col.rnd[k], bool(col.new[k]))
    idx = np.array(order)
    for name in col._ARR:
        setattr(col, name, getattr(col, name)[idx])
    col.cls = [col.cls[j] for j in order]
    col.code = [col.code[j] for j in order]


class RecipeAgent:
    def __init__(self, genome: AgentGenome) -> None:
        if genome.family != AgentFamily.recipe:
            raise ValueError(f"RecipeAgent needs a recipe genome, got {genome.family}")
        self.genome = genome
        self.agent_id = genome.agent_id

    def predict(self, case: VisibleBenchmarkCase, seed: int) -> SnowpackPrediction:
        g = genes_of(self.genome)
        meta = {"agent_version": AGENT_VERSION}
        if case.site is None or not case.weather_observed:
            return insufficient(case, self.genome, "no visible weather", meta)
        try:
            col, notes = simulate(case, g)
        except ValueError as exc:
            return insufficient(case, self.genome, f"no forcing: {exc}"[:300], meta)
        layers = cm.column_layers(col, g, case)
        limits = ["weather recipes on the latest pit, no heat physics (no SNOWPACK); flat-plot column"]
        if case.forecast_source == ForecastSource.archived_gfs:
            limits.append("forecast hours are raw GFS at the plot (no correction)")
        return build_prediction(case, self.genome, col.hs, layers, g["presence_confidence"], limits, meta | notes)


__all__ = ["RecipeAgent", "genes_of", "simulate"]
