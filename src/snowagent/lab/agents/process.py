"""Process agent: the column model (ADR-118) with eight named processes, each with an on/off switch and its own
strengths as genes (ADR-120; owner 2026-11-19: "look at a few others with differing genome structures").

The column model's heat, water, settlement and snowfall are unchanged and its energy, water, new-snow and
settlement settings are fixed at their defaults (not in this genome). What evolves is which processes run and how
strongly. Every default is a published or textbook value written down before any scoring:

- ``proc_sw_kinetics``: faceting depends on the snow as well as the gradient (Marbouty 1980). The rate peaks at
  ``proc_facet_peak_c`` (Lorentzian, half width ``proc_facet_peak_width_k``) and falls to zero as density rises to
  ``proc_facet_density_max``: dense snow has no room to grow facets or depth hoar.
- ``proc_sw_crust``: faceting next to a crust (Hammonds et al. 2015: the gradient at an ice layer is many times the
  bulk gradient). The gradient that drives faceting is multiplied by ``proc_crust_boost_below`` in the element under
  a crust and ``proc_crust_boost_above`` in the one on it.
- ``proc_sw_wet``: dry snow on wet snow (Jamieson and Fierz 2004: facets within hours while the wet layer holds
  0 degC). An element on a wet or refreezing one facets from the gradient of the hour, not the day's average, at
  ``proc_wet_facet_mult`` times the rate.
- ``proc_sw_surface``: near-surface faceting in clear cold spells (Birkeland 1998). Dry snow lighter than
  ``proc_nsf_max_density`` within ``proc_nsf_depth_m`` of the surface facets from the hour's own gradient above
  ``proc_nsf_gradient_k_m`` (``proc_nsf_hours`` at that gradient).
- ``proc_sw_hoar``: surface hoar grows fastest in a light breeze and with more vapour (Hachikubo and Akitaya 1997;
  Stoessel et al. 2010): growth x a wind factor (``proc_sh_calm_frac`` in calm air, 1 at ``proc_sh_best_wind_ms``,
  0 at ``col_sh_max_wind_ms``) x the vapour pressure excess of the air over the surface /
  ``proc_sh_vapour_ref_hpa`` (at most 3).
- ``proc_sw_wind``: wind packs loose surface snow only while it drifts (Sommer et al. 2018; Li and Pomeroy 1997):
  above ``proc_drift_wind_ms`` dry new snow in the top 10 cm densifies toward ``proc_slab_density_kg_m3`` and
  becomes small rounded grains.
- ``proc_sw_ground``: the ground under thin snow is colder than 0 degC: ground temperature + ``proc_ground_cool_frac``
  x the air temperature of about the last ten days x exp(-depth / 0.3 m).
- ``proc_sw_round``: rounding is faster the weaker the gradient: rate x 2 x (1 - gradient / the faceting gradient).

Weather switches (ADR-152, fixed, off unless a run or a check turns them on), published numbers only:

- ``proc_sw_phase``: rain or snow from air temperature and humidity (Jennings and Molotch 2019, their Eq. 1, the
  bivariate logistic of Jennings et al. 2018): snow share = 1 / (1 + exp(-10.04 + 1.41 (Ta - ``proc_phase_shift_k``)
  + 0.09 RH%)): ``on`` uses it as the hour's solid share (a smooth band), ``cut`` as the paper does (all snow at
  50 % or more, else all rain); without humidity the column's ramp.
- ``proc_sw_catch``: snow gauges catch less in wind (Kochendorfer et al. 2017, Eq. 3 and Table 2, single-Alter
  shield, wind at gauge height capped at 7.2 m/s): catch = exp(-0.0348 U (1 - atan(1.366 Ta) + 0.779)). On gauge
  hours ``on`` divides the raw gauge by the catch in place of the plot's fixed gauge factor; ``norm`` divides by the
  catch and rescales so the visible gauge total stays what the fixed factor gives (only the storm-to-storm split
  changes).
- ``proc_sw_newsnow`` (ADR-153): density of falling snow from a published formula in place of the column model's
  line: ``hedstrom`` (Hedstrom and Pomeroy 1998, 67.92 + 51.25 exp(Ta / 2.59), Ta capped at +1 degC) or ``pahaut``
  (Pahaut 1976 as Crocus uses it, 109 + 6 Ta + 26 sqrt(wind)).
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

AGENT_VERSION = "process-1"
FIXED_BLOCKS = ("col_forcing", "new_snow", "col_energy", "col_settlement", "col_water")  # at their defaults
PACK_DEPTH_M = 0.10  # wind packs the top 10 cm
GROUND_E_FOLD_M = 0.3
GROUND_AVG_H = 240.0
SPELL_END_H = 24.0  # hours below 0 degC that end a warm spell (ADR-141)
# ADR-152 published coefficients
JM19_BI = (-10.04, 1.41, 0.09)  # Jennings and Molotch 2019 Eq. 1: p_snow = 1 / (1 + exp(a + b Ta + c RH%))
K17_SA_GH = (0.0348, 1.366, 0.779, 7.2)  # Kochendorfer et al. 2017 Table 2: single-Alter, gauge-height wind
CATCH_MIN = 0.25  # no hour's catch below this (the fit's range)


def jennings_snow_share(ta_c: float, rh: float, shift_k: float = 0.0) -> float:
    """Solid share from air temperature (degC) and relative humidity (0-1), ADR-152."""
    a, b, c = JM19_BI
    x = a + b * (ta_c - shift_k) + c * 100.0 * min(max(rh, 0.0), 1.0)
    return float(1.0 / (1.0 + math.exp(min(max(x, -50.0), 50.0))))


def gauge_catch(vw: float, ta_c: float) -> float:
    """Share of the true precipitation a single-Alter gauge catches (Kochendorfer et al. 2017), ADR-152."""
    a, b, c, cap = K17_SA_GH
    u = min(max(vw, 0.0) if math.isfinite(vw) else 0.0, cap)
    return float(max(math.exp(-a * u * (1.0 - math.atan(b * ta_c) + c)), CATCH_MIN))


def genes_of(genome: AgentGenome) -> dict:
    """The genome's genes over the fixed column settings (plot snowfall genes map onto the column's). A
    ``process_melt`` genome (ADR-136) carries the ``col_energy`` and ``col_water`` genes itself, so they override
    the fixed defaults; at those defaults it is exactly the process agent."""
    g = block_defaults(*FIXED_BLOCKS, "proc_switches", "proc_strengths", "proc_weather") | dict(genome.genes)
    for plot in ("bow", "goat", "simp"):
        if f"snow_mult_{plot}" in g:
            g[f"col_precip_mult_{plot}"] = g[f"snow_mult_{plot}"]
    return g


class Spell:
    """The latest warm spell (ADR-141): it starts with an hour above 0 degC and ends after ``SPELL_END_H`` hours
    below; its rain, hours above 0 degC and warmest hour stay until the next spell starts. Strong-sun days (day
    mean sunlight of at least ``proc_cg_sun_wm2``, counted in 24-hour blocks) are counted in a row, ending at a
    day below."""

    def __init__(self) -> None:
        self.active = False
        self.cold_h = 0.0
        self.rain = 0.0
        self.warm_h = 0.0
        self.tmax = -99.0
        self.fresh = False  # a spell that may still add its crust
        self.day_sw: list[float] = []
        self.sun_days = 0

    def update(self, g: dict, hr: Hour) -> None:
        if hr.ta > 0.0:
            if not self.active:
                self.active, self.rain, self.warm_h, self.tmax, self.fresh = True, 0.0, 0.0, -99.0, True
            self.cold_h = 0.0
            self.warm_h += 1.0
        elif self.active:
            self.cold_h += 1.0
            if self.cold_h >= SPELL_END_H:
                self.active = False
        if self.active:
            self.rain += hr.rain
            self.tmax = max(self.tmax, hr.ta)
        self.day_sw.append(hr.sw)
        if len(self.day_sw) == 24:
            strong = sum(self.day_sw) / 24.0 >= g["proc_cg_sun_wm2"]
            self.sun_days = self.sun_days + 1 if strong else 0
            if strong and self.sun_days >= g["proc_cg_sun_days"]:
                self.fresh = True
            self.day_sw = []

    def opens(self, g: dict) -> bool:
        if self.sun_days >= g["proc_cg_sun_days"]:
            return True
        if self.warm_h <= 0.0:
            return False
        return self.rain >= g["proc_cg_rain_mm"] or self.warm_h >= g["proc_cg_warm_h"]


class ProcessExt(ColumnExt):
    def __init__(self, g: dict) -> None:
        self.on = {k[len("proc_sw_"):]: v == "on" for k, v in g.items() if k.startswith("proc_sw_")}
        self.on.setdefault("crustgate", False)  # ADR-141 switch: genomes without it keep it off
        self.on.setdefault("duration", False)  # ADR-143 switch, likewise
        self.phase = str(g.get("proc_sw_phase", "off"))  # ADR-152 switches, likewise
        self.catch = str(g.get("proc_sw_catch", "off"))
        self.catch_k = 1.0
        self.ta_bar: float | None = None
        self.spell = Spell()

    def prepare(self, g: dict, wx) -> None:
        if self.catch != "norm" or wx.gauge is None:
            return
        m = wx.gauge & (wx.psum > 0)
        if not m.any():
            return
        ce = np.array([gauge_catch(v, t) for v, t in zip(wx.vw[m], wx.ta[m], strict=True)])
        self.catch_k = float(wx.psum[m].sum() / (wx.psum[m] / ce).sum())

    def precip(self, g: dict, i: int, wx, p: float) -> float:
        if self.catch == "off" or p <= 0.0 or wx.gauge is None or not wx.gauge[i]:
            return p
        ce = gauge_catch(float(wx.vw[i]), float(wx.ta[i]))
        if self.catch == "norm":
            return p / ce * self.catch_k
        return p / ce / max(wx.gauge_factor, 1e-6)

    def snow_share(self, g: dict, ta: float, rh: float) -> float:
        if self.phase != "off" and math.isfinite(rh):
            p = jennings_snow_share(ta, rh, g.get("proc_phase_shift_k", 0.0))
            return p if self.phase == "on" else float(p >= 0.5)  # cut: all snow or all rain, the paper's own use
        return super().snow_share(g, ta, rh)

    def ground_temp(self, g: dict, col: Column, tg: float, hr: Hour) -> float:
        self.ta_bar = hr.ta if self.ta_bar is None else self.ta_bar + (hr.ta - self.ta_bar) / GROUND_AVG_H
        if not self.on["ground"]:
            return tg
        return tg + g["proc_ground_cool_frac"] * min(self.ta_bar, 0.0) * math.exp(-col.hs / GROUND_E_FOLD_M)

    def metamorphism(self, g: dict, col: Column, ts: float, tg: float, hr: Hour) -> bool:
        n = len(col)
        z = np.cumsum(col.dz) - col.dz / 2
        zz = np.concatenate([[0.0], z, [col.hs]])
        tt = np.concatenate([[tg], col.T, [ts]])
        g_now = (tt[:-2] - tt[2:]) / np.maximum(zz[2:] - zz[:-2], 1e-3)
        col.gbar = col.gbar + (g_now - col.gbar) / cm.GRAD_AVG_H
        grad = np.abs(col.gbar)
        dry = col.water <= 1e-6
        rho = col.rho
        crust = np.array([c == "MFcr" for c in col.cls], dtype=bool)
        rate = np.ones(n)  # multiplies faceting progress
        if self.on["crust"] and n > 1:
            grad = grad.copy()
            grad[:-1] = np.where(crust[1:] & ~crust[:-1], grad[:-1] * g["proc_crust_boost_below"], grad[:-1])
            grad[1:] = np.where(crust[:-1] & ~crust[1:], grad[1:] * g["proc_crust_boost_above"], grad[1:])
        if self.on["wet"] and n > 1:
            on_wet = np.zeros(n, dtype=bool)
            on_wet[1:] = (col.water[:-1] > 1e-6) | (col.frz[:-1] > 0.0)
            on_wet &= dry
            grad = np.where(on_wet, np.maximum(grad, np.abs(g_now)), grad)
            rate = np.where(on_wet, rate * g["proc_wet_facet_mult"], rate)
        t_snow = np.minimum(col.T, 0.0)
        tf_round = np.exp(g["col_temp_coeff"] * t_snow)
        if self.on["kinetics"]:
            tf = 1.0 / (1.0 + ((t_snow - g["proc_facet_peak_c"]) / g["proc_facet_peak_width_k"]) ** 2)
            room = np.clip((g["proc_facet_density_max"] - rho) / max(g["proc_facet_density_max"] - 150.0, 1.0),
                           0.0, 1.0)
        else:
            tf, room = tf_round, np.ones(n)
        g0, gdh = g["col_facet_gradient_k_m"], g["col_dh_gradient_k_m"]
        col.age += 1.0
        strong = dry & (grad >= g0)
        excess = (grad - g0) / g0
        if self.on["duration"]:  # ADR-143: hours above the threshold count, not how far above it
            excess = np.minimum(excess, g["proc_facet_excess_cap"])
        col.fac = np.where(strong, col.fac + excess * tf * room * rate / g["col_facet_hours"], col.fac)
        if self.on["surface"]:
            depth = col.hs - z  # mid depth below the surface
            loose = np.array([c in ("PP", "DF", "RG") for c in col.cls], dtype=bool)
            near = (dry & loose & (depth <= g["proc_nsf_depth_m"]) & (rho <= g["proc_nsf_max_density"])
                    & (np.abs(g_now) >= g["proc_nsf_gradient_k_m"]))
            col.fac = np.where(near, col.fac + np.abs(g_now) / g["proc_nsf_gradient_k_m"] / g["proc_nsf_hours"],
                               col.fac)
        weak = 2.0 * np.clip(1.0 - grad / g0, 0.0, 1.0) if self.on["round"] else np.ones(n)
        col.rnd = np.where(strong, np.maximum(col.rnd - 1.0 / g["col_facet_hours"], 0.0),
                           np.where(dry, col.rnd + tf_round * weak / g["col_round_hours"], col.rnd))
        col.dhp = np.where(dry & (grad >= gdh) & (col.fac >= 1.0), col.dhp + tf * room / g["col_dh_hours"], col.dhp)
        col.wet_h = np.where(dry, 0.0, col.wet_h + 1.0)
        if self.on["crustgate"]:
            self.spell.update(g, hr)
            self.gate(g, col, dry)
        cm.grain_changes(g, col, dry, tf_round)
        return True

    def gate(self, g: dict, col: Column, dry: np.ndarray) -> None:
        """ADR-141 crust gate: a refreeze becomes a crust only after a warm spell of the kind that left crusts in
        the training winters' pits (enough rain, enough hours above 0 degC, or a run of strong-sun days); after
        such a spell the topmost refrozen element becomes a crust even if it refroze less than the column's
        crust threshold (once per spell)."""
        ready = dry & (col.T < -0.1) & np.array([c not in ("MFcr", "SH") for c in col.cls], dtype=bool)
        made = ready & (col.frz >= g["col_crust_water_frac"] * col.ice)
        if not self.spell.opens(g):
            col.frz[made] = 0.0  # this refreeze leaves no crust
            return
        if made.any() or not self.spell.fresh:
            return
        top = np.flatnonzero(ready & (col.frz > 0.0))
        if len(top):
            i = int(top[-1])
            col.frz[i] = max(col.frz[i], g["col_crust_water_frac"] * col.ice[i])
            self.spell.fresh = False

    def after_step(self, g: dict, col: Column, hr: Hour) -> None:
        if not self.on["wind"] or hr.vw < g["proc_drift_wind_ms"] or hr.q_melt > 0 or not len(col):
            return
        target = g["proc_slab_density_kg_m3"]
        share = min(1.0, 0.1 * (hr.vw - g["proc_drift_wind_ms"] + 1.0))  # of the way to the slab density, per hour
        depth = 0.0
        for i in range(len(col) - 1, -1, -1):
            if depth >= PACK_DEPTH_M or col.cls[i] not in ("PP", "DF") or col.water[i] > 1e-6:
                break
            depth += col.dz[i]
            mass = col.ice[i]
            rho = mass / max(col.dz[i], 1e-6)
            if rho < target:
                rho += share * (target - rho)
                col.dz[i] = mass / rho
            if col.cls[i] == "PP":
                cm._set(col, i, "DF")
            elif rho >= 0.85 * target:
                cm._set(col, i, "RG")
                col.rnd[i] = max(col.rnd[i], g["col_df_hours"] / g["col_round_hours"])

    def hoar_growth(self, g: dict, hr: Hour, dew_c: float, rate: float) -> float:
        if not self.on["hoar"]:
            return super().hoar_growth(g, hr, dew_c, rate)
        if hr.ts >= dew_c:
            return 0.0
        best, vmax = g["proc_sh_best_wind_ms"], g["col_sh_max_wind_ms"]
        if hr.vw <= best:
            wind = g["proc_sh_calm_frac"] + (1.0 - g["proc_sh_calm_frac"]) * hr.vw / best
        elif hr.vw < vmax:
            wind = (vmax - hr.vw) / (vmax - best)
        else:
            return 0.0
        excess = float(cm.sat_vapour_hpa(dew_c) - cm.sat_vapour_hpa(hr.ts))
        return rate * wind * float(np.clip(excess / g["proc_sh_vapour_ref_hpa"], 0.0, 3.0))


class ProcessAgent:
    def __init__(self, genome: AgentGenome) -> None:
        if genome.family not in (AgentFamily.process, AgentFamily.process_melt):
            raise ValueError(f"ProcessAgent needs a process genome, got {genome.family}")
        self.genome = genome
        self.agent_id = genome.agent_id

    def predict(self, case: VisibleBenchmarkCase, seed: int) -> SnowpackPrediction:
        g = genes_of(self.genome)
        meta = {"agent_version": AGENT_VERSION}
        if case.site is None or not case.weather_observed:
            return insufficient(case, self.genome, "no visible weather", meta)
        ext = ProcessExt(g)
        try:
            col, notes = cm.simulate(case, g, ext)
        except ValueError as exc:
            return insufficient(case, self.genome, f"no forcing: {exc}"[:300], meta)
        layers = cm.column_layers(col, g, case)
        limits = ["the lab's own column model with switchable processes (no SNOWPACK); flat-plot column"]
        if case.forecast_source == ForecastSource.archived_gfs:
            limits.append("forecast hours are raw GFS at the plot (no correction)")
        notes["processes_on"] = ",".join(k for k, v in sorted(ext.on.items()) if v)
        return build_prediction(case, self.genome, col.hs, layers, g["presence_confidence"], limits, meta | notes)


__all__ = ["ProcessAgent", "ProcessExt", "genes_of"]
