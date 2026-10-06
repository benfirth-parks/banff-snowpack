"""WeatherRule agent: a layered snowpack built from the visible weather by simple, physically motivated rules,
every threshold and rate a gene (ADR-062). It starts snow-free at the first visible hour (the season start) and
steps through the observed and then the forecast (or stand-in) weather in 6-hour steps:

- precipitation x ``precipitation_factor``; solid share by ``rain_snow_threshold_c`` +/- ``rain_snow_range_k`` / 2;
  snowfall density from the new_snow genes; a new layer after a dry spell of ``storm_gap_h``;
- settlement of every layer: rate x exp(``settlement_temp_coeff`` x T_layer) x (1 - rho / ``max_density``) x
  (1 + load / 100 mm), with the layer temperature linear between the surface (min(air, 0)) and 0 degC at the ground;
- melt (degree-day ``melt_factor_mm_per_k_day``) from the top; ``melt_hours_for_crust`` hours above
  ``melt_temp_threshold_c``, or rain of ``rain_crust_min_mm`` on snow, refrozen below ``refreeze_temp_c``, make a
  melt-freeze crust (MFcr);
- temperature gradient: bulk (0 degC ground to the surface) plus, within ``near_surface_depth_m``, half the daily
  air temperature range over that depth, halved (it points upward about half the day) and fading to 0 at that
  depth; ``facet_hours`` above ``facet_gradient_k_per_m`` turn a layer to facets (FC), weak gradients round it
  (DF -> RG); basal facets in a pack thinner than ``depth_hoar_hs_max_m`` become depth hoar (DH) after
  ``depth_hoar_hours``;
- surface hoar (SH) grows on ``sh_min_hours`` clear (longwave / sigma T^4 <= ``sh_max_emissivity``), calm
  (<= ``sh_max_wind_ms``), cold (<= ``sh_max_air_temp_c``) and humid (>= ``sh_min_rh``) night hours; wind, rain or
  melt destroy it at the surface, new snow buries it;
- ``pit_depth_nudge``: at the latest pit of the season (visible at as-of) the layer thicknesses are scaled toward its
  snow depth with this weight (0 = weather only).

Missing precipitation counts as none, missing temperature carries the last value (recorded in the metadata).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from snowagent.lab.agents.common import (
    SIGMA,
    Lyr,
    build_prediction,
    hardness_from_density,
    latest_season_pit,
    new_snow_density,
    pit_snow_depth,
    snow_fraction,
    weather_frame,
)
from snowagent.lab.schemas.benchmark import VisibleBenchmarkCase
from snowagent.lab.schemas.genome import AgentFamily, AgentGenome
from snowagent.lab.schemas.prediction import SnowpackPrediction

AGENT_VERSION = "weather_rule-1"
STEP_H = 6
MAX_LAYERS = 60


@dataclass
class _L:
    swe: float  # mm
    thick: float  # m
    grain: str
    age_h: float = 0.0
    since_snow_h: float = 0.0  # hours since snow last fell on it (top layer)
    facet_h: float = 0.0
    round_h: float = 0.0
    melt_h: float = 0.0
    rain_mm: float = 0.0
    wet: bool = False

    @property
    def rho(self) -> float:
        return self.swe / self.thick if self.thick > 0 else 917.0


def _solar_night(doy: np.ndarray, lon: float) -> np.ndarray:
    """Night hours from the UTC day-of-year fraction and longitude (local solar hour outside 06-18)."""
    hour = ((doy % 1.0) * 24.0 + lon / 15.0) % 24.0
    return (hour < 6.0) | (hour >= 18.0)


def simulate(case: VisibleBenchmarkCase, genes: dict, nudge: bool = True) -> tuple[list[Lyr], dict]:
    """Run the rule snowpack to the valid time; returns layers (surface first) and notes."""
    g = genes
    wx = weather_frame(case)
    notes: dict = {"steps": 0, "hours_no_temperature": 0, "hours_no_precipitation": 0, "nudged": False}
    if wx.empty:
        return [], notes | {"reason": "no visible weather"}
    t = wx.index.to_numpy()
    full = np.arange(np.floor(t.min()), case.horizon_hours + 1e-6, 1.0)  # hourly grid to the valid time
    w = wx.reindex(wx.index.union(pd.Index(full))).sort_index()
    notes["hours_no_temperature"] = int(w["ta_k"].isna().sum())
    notes["hours_no_precipitation"] = int(w["precip_mm"].isna().sum())
    ta = w["ta_k"].ffill().bfill().fillna(268.15).to_numpy() - 273.15 + g["temperature_offset_k"]
    p = np.nan_to_num(w["precip_mm"].to_numpy(), nan=0.0).clip(min=0) * g["precipitation_factor"]
    ws = w["ws"].ffill().fillna(2.0).to_numpy()
    rh = w["rh"].ffill().fillna(0.7).to_numpy()
    lw = w["lw"].to_numpy()
    doy = w["doy"].interpolate(limit_direction="both").to_numpy()
    tt = w.index.to_numpy()
    lon = case.site.longitude if case.site else -116.0
    snowf = snow_fraction(ta, g["rain_snow_threshold_c"], g["rain_snow_range_k"])
    rho_new = new_snow_density(ta, ws, g)
    emis = lw / (SIGMA * (ta + 273.15) ** 4)
    sh_hour = (_solar_night(doy, lon) & (ta <= g["sh_max_air_temp_c"]) & (ws <= g["sh_max_wind_ms"])
               & (rh >= g["sh_min_rh"]) & np.isfinite(lw) & (emis <= g["sh_max_emissivity"]))
    pit = latest_season_pit(case, with_layers=False) if nudge else None
    pit_t = pit.t_rel_h if pit is not None else None
    stack: list[_L] = []  # bottom first
    edges = np.arange(tt.min(), tt.max() + STEP_H, STEP_H)
    tas = pd.Series(ta, index=tt)
    daily_range = tas.rolling(24, min_periods=1).max() - tas.rolling(24, min_periods=1).min()
    for a, b in zip(edges[:-1], edges[1:], strict=True):
        m = (tt > a) & (tt <= b) if a > edges[0] else (tt >= a) & (tt <= b)
        if not m.any():
            continue
        notes["steps"] += 1
        ta_s, swe_new, rain = ta[m], float((p[m] * snowf[m]).sum()), float((p[m] * (1 - snowf[m])).sum())
        t_mean, t_min = float(ta_s.mean()), float(ta_s.min())
        # 1 snowfall
        if swe_new >= 0.1:
            rho = float(np.average(rho_new[m], weights=np.maximum(p[m] * snowf[m], 1e-9)))
            top = stack[-1] if stack else None
            if top is not None and top.grain == "PP" and top.since_snow_h <= g["storm_gap_h"]:
                top.swe += swe_new
                top.thick += swe_new / rho
                top.since_snow_h = 0.0
            else:
                stack.append(_L(swe=swe_new, thick=swe_new / rho, grain="PP"))
        elif stack:
            stack[-1].since_snow_h += STEP_H
        if not stack:
            continue
        top = stack[-1]
        # 2 rain on snow
        if rain >= 0.1:
            top.swe += rain
            top.rain_mm += rain
            top.wet = True
            if top.grain.startswith("SH"):
                stack.pop()
                if not stack:
                    continue
                top = stack[-1]
        # 3 melt from the top
        melt_hours = int((ta_s > g["melt_temp_threshold_c"]).sum())
        melt = float(np.clip(ta_s - g["melt_temp_threshold_c"], 0, None).sum()) / 24.0 * g["melt_factor_mm_per_k_day"]
        if melt_hours:
            if top.grain.startswith("SH"):
                stack.pop()
            while melt > 0 and stack:
                top = stack[-1]
                if top.swe <= melt:
                    melt -= top.swe
                    stack.pop()
                else:
                    top.thick *= (top.swe - melt) / top.swe
                    top.swe -= melt
                    melt = 0.0
            if not stack:
                continue
            stack[-1].melt_h += melt_hours
            stack[-1].wet = True
        top = stack[-1]
        # 4 refreeze -> crust
        if top.wet and t_min < g["refreeze_temp_c"]:
            if top.melt_h >= g["melt_hours_for_crust"] or top.rain_mm >= g["rain_crust_min_mm"]:
                crust_mm = min(top.swe, 0.03 * 400.0)
                if top.swe - crust_mm > 1.0:  # the crust is the top 3 cm; the rest stays below
                    rest = _L(swe=top.swe - crust_mm, thick=max(top.thick - crust_mm / 400.0, 0.002), grain=top.grain,
                              age_h=top.age_h, facet_h=top.facet_h, round_h=top.round_h)
                    stack[-1] = rest
                    stack.append(_L(swe=crust_mm, thick=crust_mm / 400.0, grain="MFcr", age_h=top.age_h))
                else:
                    top.grain, top.thick = "MFcr", top.swe / 400.0
            top = stack[-1]
            top.wet, top.melt_h, top.rain_mm = False, 0.0, 0.0
        # 5 settlement and 6 temperature gradient
        hs = sum(x.thick for x in stack)
        ts = min(t_mean, 0.0)
        g_bulk = abs(ts) / max(hs, 0.05)
        amp = float(daily_range.loc[tt[m]].iloc[-1]) / 2.0
        load, z_top = 0.0, 0.0
        for x in reversed(stack):  # surface down
            z_mid = z_top + x.thick / 2
            t_layer = ts * (1 - z_mid / max(hs, 1e-3))
            rate = g["settlement_rate_per_day"] * np.exp(g["settlement_temp_coeff"] * min(t_layer, 0.0)) * max(
                0.0, 1.0 - x.rho / g["max_density_kg_m3"]) * (1.0 + load / 100.0)
            x.thick *= float(np.exp(-rate * STEP_H / 24.0))
            # the diurnal near-surface gradient points upward about half the time and fades with depth
            nsd = g["near_surface_depth_m"]
            grad = g_bulk + 0.5 * amp / nsd * max(0.0, 1.0 - z_mid / nsd)
            x.age_h += STEP_H
            if x.grain in ("MFcr",) or x.grain.startswith("SH"):
                pass
            elif x.wet:
                x.grain = "MFcl" if x.melt_h > 2 * g["melt_hours_for_crust"] else x.grain
            elif grad >= g["facet_gradient_k_per_m"]:
                x.facet_h += STEP_H
            elif grad < 0.5 * g["facet_gradient_k_per_m"]:
                x.round_h += STEP_H
            if x.grain == "PP" and x.age_h >= 24:
                x.grain = "DF"
            if x.grain in ("PP", "DF", "RG") and x.facet_h >= g["facet_hours"]:
                x.grain, x.round_h = "FC", 0.0
            elif x.grain == "DF" and x.round_h >= 96:
                x.grain = "RG"
            if (x.grain == "FC" and hs < g["depth_hoar_hs_max_m"] and z_mid > 2 * hs / 3
                    and x.facet_h >= g["depth_hoar_hours"]):
                x.grain = "DH"
            load += x.swe
            z_top += x.thick
        # 7 surface hoar
        hours_sh = int(sh_hour[m].sum())
        top = stack[-1]
        if top.grain.startswith("SH") and (ws[m].max() > 2 * g["sh_max_wind_ms"] or t_mean > 0):
            stack.pop()
        elif hours_sh >= g["sh_min_hours"]:
            if top.grain.startswith("SH"):
                top.thick = min(top.thick + 0.002, 0.03)
            else:
                stack.append(_L(swe=0.5, thick=0.005, grain="SH"))
        # 8 the latest season pit's snow depth
        if pit_t is not None and a < pit_t <= b:
            hs = sum(x.thick for x in stack)
            hp = pit_snow_depth(pit)
            if hp and hs > 0.1:
                f = 1.0 + g["pit_depth_nudge"] * (hp / hs - 1.0)
                for x in stack:
                    x.thick *= f
                notes["nudged"] = True
        if len(stack) > MAX_LAYERS:
            stack = _merge(stack)
    return _to_layers(stack), notes


def _merge(stack: list[_L]) -> list[_L]:
    """Merge the most similar adjacent pair (same grain) until under the cap; crusts and surface hoar stay."""
    while len(stack) > MAX_LAYERS:
        best, k = None, None
        for i in range(len(stack) - 1):
            a, b = stack[i], stack[i + 1]
            if a.grain != b.grain or a.grain in ("MFcr", "SH"):
                continue
            d = abs(a.rho - b.rho)
            if best is None or d < best:
                best, k = d, i
        if k is None:
            k = int(np.argmin([stack[i].thick + stack[i + 1].thick for i in range(len(stack) - 1)]))
        a, b = stack[k], stack[k + 1]
        stack[k] = _L(swe=a.swe + b.swe, thick=a.thick + b.thick, grain=a.grain if a.thick >= b.thick else b.grain,
                      age_h=max(a.age_h, b.age_h), facet_h=max(a.facet_h, b.facet_h), round_h=max(a.round_h, b.round_h))
        del stack[k + 1]
    return stack


def _to_layers(stack: list[_L]) -> list[Lyr]:
    out, z = [], 0.0
    for x in reversed(stack):
        if x.thick <= 0:
            continue
        out.append(Lyr(top=z, bottom=z + x.thick, grain=x.grain, hardness_index=hardness_from_density(x.rho),
                       density=x.rho, source="rule", extra={"age_h": x.age_h}))
        z += x.thick
    return out


class WeatherRuleAgent:
    def __init__(self, genome: AgentGenome) -> None:
        if genome.family != AgentFamily.weather_rule:
            raise ValueError(f"WeatherRuleAgent needs a weather_rule genome, got {genome.family}")
        self.genome = genome
        self.agent_id = genome.agent_id

    def predict(self, case: VisibleBenchmarkCase, seed: int) -> SnowpackPrediction:
        g = self.genome.genes
        layers, notes = simulate(case, g)
        conf = g["presence_confidence"]
        for ly in layers:  # thin rule-made weak layers are less certain than the bulk layering
            ly.prob = conf * (0.8 if ly.grain in ("SH", "MFcr") else 1.0)
        hs = layers[-1].bottom if layers else 0.0
        limits = ["layers from weather rules (no engine physics)"]
        if notes["hours_no_temperature"]:
            limits.append(f"{notes['hours_no_temperature']} hours without air temperature (last value carried)")
        return build_prediction(case, self.genome, hs, layers, conf, limits,
                                {"agent_version": AGENT_VERSION, **notes})
