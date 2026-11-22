"""Column agent: the lab's own layered snowpack model, written for the study plots, with no SNOWPACK in it
(ADR-118; owner 2026-11-18: "run something where SNOWPACK is not involved to see if we can evolve something better
for these specific locations").

One flat column of snow elements (bottom first) stepped hour by hour from the latest visible pit of the season
(its layers, grain forms and hardness as observed; snow-free from the first visible hour when the season has no
pit yet) to the valid time. Every rate and threshold is a gene (``config/lab.yaml``, blocks ``col_*``); the
defaults are textbook values chosen before any scoring, not fitted. Per hour:

1. **Snowfall.** Until the last observed hour the plot's snow depth sensor (QC ok, despiked and gap-bridged as the
   SNOWPACK agent's ADR-100 forcing) adds new snow whenever it reads more than ``col_sensor_add_m`` above the model
   (the sensor's offset from the pit's depth at the start is removed by ``col_sensor_offset_weight``); without a
   usable sensor and after it, precipitation x the plot's adopted factor (gauge, or ERA5 for reanalysis hours,
   ADR-024/038/106) x ``col_precip_mult_<plot>``, raw on GFS hours as the site. Rain-snow split by a linear ramp;
   new-snow density from temperature and wind (block ``new_snow``). Rain enters the top as liquid water.
2. **Surface energy balance.** Absorbed shortwave (albedo decaying from fresh to old snow with the days since
   snowfall), incoming longwave (+ ``col_lw_offset_wm2``), emitted longwave, a bulk turbulent exchange
   ``(col_turb_base + col_turb_wind x wind) x (Ta - Ts)`` and conduction from the top element give the surface
   temperature (at most 0 degC); the surplus at 0 degC melts snow.
3. **Heat conduction** through the elements (implicit, one hour), conductivity from density (Sturm et al. 1997) x
   ``col_conductivity_mult``, ground at ``col_ground_temp_c``. Phase change: water in an element below 0 degC
   refreezes; an element warmed above 0 degC melts.
4. **Water.** Melt and rain percolate downward; each element holds ``col_water_hold_frac`` of its ice mass; water
   meeting cold snow refreezes there. An element that refroze at least ``col_crust_water_frac`` of its mass and is
   dry again becomes a melt-freeze crust (MFcr); one wet for 12 h is a melt form (MF).
5. **Settlement** under the weight above (viscosity of Anderson 1976 / Jordan 1991: 3.6e6 N s m-2 x exp(0.08 K-1 x
   cold) x exp(0.021 x density), times ``col_viscosity_mult``) and destructive metamorphism of new snow (x
   ``col_destructive_mult``), faster when wet.
6. **Grain metamorphism** by the temperature gradient across each dry element: above ``col_facet_gradient_k_m``
   faceting progresses (``col_facet_hours`` at twice the threshold near 0 degC), slower in the cold
   (``col_temp_coeff``); facets under at least ``col_dh_gradient_k_m`` for ``col_dh_hours`` become depth hoar;
   under a weak gradient new snow decomposes (PP -> DF after ``col_pp_hours``, DF -> RG after ``col_df_hours``) and
   facets round (``col_round_hours``).
7. **Surface hoar** grows by ``col_sh_growth_m_h`` in hours with the surface below the air's dew point, wind at most
   ``col_sh_max_wind_ms`` and no precipitation; wind above ``col_sh_destroy_wind_ms``, rain or melt removes it from
   the surface; new snow buries it.

Switch ``col_old_facets`` (ADR-128, off unless a run starts with it on): the last pit's facets and depth hoar keep
the observer's hardness and do not round back to rounded grains in dry snow.

Output: adjacent elements of one grain form within ``hardness_merge_tol`` hand hardness are one layer. Hardness:
pit layers keep the observer's hardness, shifted by their density change; model-made layers take the pits'
density-hardness relation (ADR-039) plus ``col_hard_facet_offset`` for facets and depth hoar; crusts at least
Pencil. Presence ``presence_confidence``; facets, depth hoar and surface hoar in snow that fell since the last pit x
``col_new_weak_factor`` (as SNOWPACK's ADR-103 gene).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd
import yaml
from scipy.linalg import solve_banded

from snowagent.lab.agents.common import (
    SIGMA,
    Lyr,
    build_prediction,
    density_from_hardness,
    hardness_from_density,
    insufficient,
    latest_season_pit,
    new_snow_density,
    pit_snow_depth,
    snow_fraction,
)
from snowagent.lab.schemas.benchmark import ForecastSource, VisibleBenchmarkCase
from snowagent.lab.schemas.genome import AgentFamily, AgentGenome
from snowagent.lab.schemas.prediction import SnowpackPrediction
from snowagent.lab.schemas.profile import CriticalClass

AGENT_VERSION = "column-1"
LF = 334000.0  # latent heat of fusion, J kg-1
C_ICE = 2100.0  # heat capacity of ice, J kg-1 K-1
RHO_ICE = 917.0
G = 9.81
EPS_SNOW = 0.98
DT = 3600.0
MAX_DZ_TOP, MAX_DZ = 0.03, 0.06  # element size: new snow and the top 30 cm; deeper pit layers
MAX_ELEMENTS = 90
MIN_DZ = 2e-4
WET_MF_H = 12.0  # hours wet that make a melt form
OLD_SNOW_F2 = 4.0  # Crocus (Vionnet et al. 2012) viscosity factor for coarse old grains
GRAD_AVG_H = 24.0  # the gradient that drives metamorphism is averaged over about this many hours
SH_MIN_BURIED_M = 0.002  # buried surface hoar thinner than this joins the snow below (SNOWPACK: 2 mm)
CRUST_MIN_HARD = 4.0  # Pencil
NEW_WEAK = (CriticalClass.facets, CriticalClass.depth_hoar, CriticalClass.surface_hoar)
PLOT_GENE = {"BOW": "col_precip_mult_bow", "GOAT": "col_precip_mult_goat", "SIMP": "col_precip_mult_simp"}

# model grain state codes (internal) -> IACS code reported
_BASE = {"PP": "PP", "DF": "DF", "RG": "RG", "FC": "FC", "DH": "DH", "SH": "SH", "MF": "MF", "MFcr": "MFcr",
         "IF": "IF"}


def _class_of(code: str) -> str:
    """A pit's IACS code -> the model's state class."""
    if not code or code == "UNKNOWN":
        return "RG"
    if code.startswith("MFcr") or code.startswith("IF"):
        return "MFcr"
    for k in ("PP", "DF", "RG", "FC", "DH", "SH", "MF"):
        if code.startswith(k):
            return k
    if code.startswith("MM"):
        return "RG"
    return "RG"


@dataclass
class Column:
    """Elements bottom first. ``code``: the grain form reported (a pit's own code until the model changes it)."""

    dz: np.ndarray
    ice: np.ndarray  # ice mass, kg m-2
    water: np.ndarray  # liquid water, kg m-2 (= mm)
    T: np.ndarray  # degC
    cls: list[str]
    code: list[str]
    hard0: np.ndarray  # observer hardness (NaN for model-made snow)
    rho0: np.ndarray  # density when the hardness was observed
    age: np.ndarray  # hours
    fac: np.ndarray  # faceting progress (1 = facets)
    dhp: np.ndarray  # depth hoar progress
    rnd: np.ndarray  # rounding progress
    wet_h: np.ndarray
    frz: np.ndarray  # refrozen water since the element was last dry and cold, kg m-2
    new: np.ndarray  # fell during the run (after the start pit)
    gbar: np.ndarray  # temperature gradient (K m-1, positive = warmer below) averaged over about a day
    hist: np.ndarray | None = None  # per-element history features (n x k), for models that keep them (ADR-120)

    @classmethod
    def empty(cls, n_hist: int = 0) -> Column:
        z = np.zeros(0)
        return cls(z.copy(), z.copy(), z.copy(), z.copy(), [], [], z.copy(), z.copy(), z.copy(), z.copy(), z.copy(),
                   z.copy(), z.copy(), z.copy(), np.zeros(0, dtype=bool), z.copy(),
                   np.zeros((0, n_hist)) if n_hist else None)

    def __len__(self) -> int:
        return len(self.dz)

    @property
    def hs(self) -> float:
        return float(self.dz.sum())

    @property
    def rho(self) -> np.ndarray:
        return (self.ice + self.water) / np.maximum(self.dz, 1e-6)

    _ARR = ("dz", "ice", "water", "T", "hard0", "rho0", "age", "fac", "dhp", "rnd", "wet_h", "frz", "new", "gbar")

    def append(self, dz: float, ice: float, T: float, cls_: str, code: str, hard0: float = math.nan,
               rho0: float = math.nan, age: float = 0.0, fac: float = 0.0, dhp: float = 0.0, rnd: float = 0.0,
               new: bool = True) -> None:
        vals = {"dz": dz, "ice": ice, "water": 0.0, "T": T, "hard0": hard0, "rho0": rho0, "age": age, "fac": fac,
                "dhp": dhp, "rnd": rnd, "wet_h": 0.0, "frz": 0.0, "new": new, "gbar": 0.0}
        for k in self._ARR:
            setattr(self, k, np.append(getattr(self, k), vals[k]))
        if self.hist is not None:
            self.hist = np.vstack([self.hist, np.zeros((1, self.hist.shape[1]))])
        self.cls.append(cls_)
        self.code.append(code)

    def keep(self, mask: np.ndarray) -> None:
        for k in self._ARR:
            setattr(self, k, getattr(self, k)[mask])
        if self.hist is not None:
            self.hist = self.hist[mask]
        self.cls = [c for c, m in zip(self.cls, mask, strict=True) if m]
        self.code = [c for c, m in zip(self.code, mask, strict=True) if m]

    def merge(self, i: int) -> None:
        """Element i+1 into i (mass-weighted state; the thicker one's grain form)."""
        j = i + 1
        wi, wj = self.ice[i] + 1e-9, self.ice[j] + 1e-9
        w = wi + wj
        for k in ("T", "age", "fac", "dhp", "rnd", "wet_h", "gbar"):
            a = getattr(self, k)
            a[i] = (a[i] * wi + a[j] * wj) / w
        if self.hist is not None:
            self.hist[i] = (self.hist[i] * wi + self.hist[j] * wj) / w
        for k in ("hard0", "rho0"):
            a = getattr(self, k)
            a[i] = a[i] if self.dz[i] >= self.dz[j] else a[j]
        if self.dz[j] > self.dz[i]:
            self.cls[i], self.code[i] = self.cls[j], self.code[j]
        self.new[i] = self.new[i] or self.new[j]
        for k in ("dz", "ice", "water", "frz"):
            a = getattr(self, k)
            a[i] += a[j]
        mask = np.ones(len(self), dtype=bool)
        mask[j] = False
        self.keep(mask)


# --------------------------------------------------------------------------------------------- forcing


@dataclass
class Forcing:
    t: np.ndarray  # hours after as-of (end of hour)
    ta: np.ndarray  # degC at the plot
    rh: np.ndarray
    vw: np.ndarray
    sw: np.ndarray
    lw: np.ndarray
    psum: np.ndarray  # mm, the plot's adopted factor applied on measured hours (raw GFS)
    measured: np.ndarray  # True: the precipitation gene applies (not GFS)
    hs: np.ndarray | None  # sensor snow depth (m), NaN after the switch; None without a usable sensor
    dw: np.ndarray | None = None  # wind direction, degrees the wind blows from (0 where the record has none)


_FORCING_CACHE: dict[tuple, tuple[VisibleBenchmarkCase, Forcing]] = {}


def _plot_factors(plot_id: str) -> tuple[float, float]:
    from snowagent.lab.agents.snowpack import PLOT_FORCING

    p = yaml.safe_load(PLOT_FORCING.read_text())["plots"].get(plot_id, {})
    f = float(p.get("psum_factor", 1.0))
    return f, float(p.get("era5_psum_factor", f))


def column_forcing(case: VisibleBenchmarkCase, start_rel_h: float) -> Forcing:
    """The visible weather as the SNOWPACK agent's forcing builder makes it (plot elevation, gaps filled), from the
    first visible hour; the sensor snow depth from ``start_rel_h`` (the run start) to the switch (ADR-100)."""
    key = (id(case), round(start_rel_h, 3))
    hit = _FORCING_CACHE.get(key)
    if hit is not None and hit[0] is case:
        return hit[1]
    from snowagent.lab.agents.snowpack import (
        _hours_frame,
        engine_forcing,
        measured_snow_depth,
        reference_time,
    )

    gauge, era5 = _plot_factors(case.site.plot_id)
    f, _ = engine_forcing(case, 1.0)  # factors applied below, per hour
    ref = pd.Timestamp(reference_time(case.as_of_day_of_year))
    h = _hours_frame(case)
    h.index = pd.DatetimeIndex([ref + pd.Timedelta(hours=float(t)) for t in h.index]).round("h")
    h = h[~h.index.duplicated(keep="first")].reindex(f.index)
    gfs = h["gfs"].eq(True).to_numpy()
    era5_h = h["psum_src"].astype(str).str.startswith("era5").to_numpy()
    fac = np.where(gfs, 1.0, np.where(era5_h, era5, gauge))
    t = ((f.index - ref) / pd.Timedelta(hours=1)).to_numpy(dtype=float)
    start = ref + pd.Timedelta(hours=float(start_rel_h)) if math.isfinite(start_rel_h) else f.index[0]
    hs = None
    if start < f.index[-1]:
        hs_s, switch = measured_snow_depth(case, f.index, ref, max(start.floor("h"), f.index[0]), f.index[-1])
        if hs_s is not None:
            hs = hs_s.to_numpy(dtype=float)
    out = Forcing(t=t, ta=f["ta"].to_numpy() - 273.15, rh=f["rh"].to_numpy(), vw=f["vw"].to_numpy(),
                  sw=f["iswr"].to_numpy(), lw=f["ilwr"].to_numpy(), psum=f["psum"].to_numpy() * fac,
                  measured=~gfs, hs=hs, dw=f["dw"].to_numpy() if "dw" in f else None)
    if len(_FORCING_CACHE) >= 8:
        _FORCING_CACHE.pop(next(iter(_FORCING_CACHE)))
    _FORCING_CACHE[key] = (case, out)
    return out


# --------------------------------------------------------------------------------------------- physics


def conductivity(rho: np.ndarray, mult: float) -> np.ndarray:
    """Effective conductivity of snow (Sturm et al. 1997), W m-1 K-1."""
    r = np.clip(rho, 50.0, 917.0) / 1000.0
    k = np.where(r < 0.156, 0.023 + 0.234 * r, 0.138 - 1.01 * r + 3.233 * r * r)
    return np.clip(k, 0.02, 2.2) * mult


def sat_vapour_hpa(t_c: np.ndarray | float) -> np.ndarray:
    """Saturation vapour pressure over ice, hPa."""
    t = np.asarray(t_c, dtype=float)
    return 6.112 * np.exp(22.46 * t / (272.62 + t))


def dew_point_ice_c(ta_c: float, rh: float) -> float:
    e = max(rh, 0.05) * 6.112 * math.exp(17.62 * ta_c / (243.12 + ta_c))  # over water (station RH)
    x = math.log(e / 6.112)
    return 272.62 * x / (22.46 - x)


def _start_from_pit(col: Column, pit, t_air: float, tg: float) -> None:
    """Elements from the pit's layers (bottom first), split to element size; gaps take the layer below."""
    hs = pit_snow_depth(pit) or 0.0
    lays = sorted((ly for ly in pit.layers if ly.bottom_depth_m > ly.top_depth_m), key=lambda x: -x.bottom_depth_m)
    temps = sorted((p.depth_m, p.temperature_c) for p in pit.temperatures if p.depth_m <= hs + 1e-6)
    z = 0.0  # height above ground
    prev = None
    for ly in lays + [None]:
        if ly is None:
            top_h = hs
            src = prev
        else:
            bot_h, top_h = max(0.0, hs - ly.bottom_depth_m), max(0.0, hs - ly.top_depth_m)
            if bot_h > z + 1e-3 and prev is None:  # snow below the lowest recorded layer
                src = ly
                _fill(col, z, bot_h, src, hs, temps, t_air, tg)
                z = bot_h
            elif bot_h > z + 1e-3:
                _fill(col, z, bot_h, prev, hs, temps, t_air, tg)
                z = bot_h
            src = ly
        if src is None or top_h <= z + 1e-4:
            prev = src if ly is not None else prev
            continue
        _fill(col, max(z, 0.0), top_h, src, hs, temps, t_air, tg)
        z = top_h
        prev = src


def _fill(col: Column, z0: float, z1: float, ly, hs: float, temps, t_air: float, tg: float) -> None:
    code = ly.grain_primary or "UNKNOWN"
    c = _class_of(code)
    hard = ly.hardness_index if ly.hardness_index is not None else math.nan
    rho = ly.density_kg_m3 or density_from_hardness(hard if np.isfinite(hard) else None,
                                                     default=300.0 if c not in ("PP", "DF") else 150.0)
    if c == "MFcr":
        rho = max(rho, 400.0)
    reported = code if code != "UNKNOWN" else "RG"
    n = max(1, int(math.ceil((z1 - z0) / (MAX_DZ_TOP if hs - z1 < 0.3 else MAX_DZ) - 1e-9)))
    edges = np.linspace(z0, z1, n + 1)
    for a, b in zip(edges[:-1], edges[1:], strict=True):
        depth = hs - (a + b) / 2
        if temps:
            T = float(np.interp(depth, [d for d, _ in temps], [v for _, v in temps]))
        else:
            T = tg + (min(t_air, 0.0) - tg) * (a + b) / 2 / max(hs, 1e-3)
        col.append(b - a, (b - a) * rho, min(T, 0.0), c, reported, hard, rho,
                   age=0.0 if c == "PP" else 48.0 if c == "DF" else 240.0,
                   fac=1.0 if c in ("FC", "DH") else 0.0, dhp=1.0 if c == "DH" else 0.0,
                   rnd=1.0 if c == "RG" else 0.0, new=False)


def _surface_temperature(g: dict, col: Column, ta: float, vw: float, sw: float, lw: float, albedo: float
                         ) -> tuple[float, float]:
    """(surface temperature degC, melt energy J m-2 over the hour) from the linearised balance."""
    k_top = float(conductivity(col.rho[-1:], g["col_conductivity_mult"])[0])
    cond = 2.0 * k_top / max(col.dz[-1], 1e-3)
    h = g["col_turb_base"] + g["col_turb_wind"] * vw
    q_in = (1.0 - albedo) * sw + EPS_SNOW * (lw + g["col_lw_offset_wm2"])
    ts = min(ta, 0.0)
    for _ in range(3):
        tk = ts + 273.15
        f = q_in - EPS_SNOW * SIGMA * tk**4 + h * (ta - ts) + cond * (col.T[-1] - ts)
        df = -4.0 * EPS_SNOW * SIGMA * tk**3 - h - cond
        ts = ts - f / df
    if ts >= 0.0:
        tk = 273.15
        surplus = q_in - EPS_SNOW * SIGMA * tk**4 + h * ta + cond * col.T[-1]
        return 0.0, max(surplus, 0.0) * DT
    return max(ts, -60.0), 0.0


def _conduct(g: dict, col: Column, ts: float, tg: float) -> None:
    n = len(col)
    k = conductivity(col.rho, g["col_conductivity_mult"])
    cap = np.maximum(col.ice * C_ICE, 50.0) / DT  # J m-2 K-1 per second
    r = np.maximum(col.dz, 1e-4) / (2.0 * k)
    k_int = 1.0 / (r[:-1] + r[1:]) if n > 1 else np.zeros(0)  # between i and i+1
    k_bot = 1.0 / r[0]
    k_top = 1.0 / r[-1]
    lower = np.zeros(n)
    upper = np.zeros(n)
    diag = cap.copy()
    rhs = cap * col.T
    diag[0] += k_bot
    rhs[0] += k_bot * tg
    diag[-1] += k_top
    rhs[-1] += k_top * ts
    if n > 1:
        diag[:-1] += k_int
        diag[1:] += k_int
        upper[1:] = -k_int  # ab[0, j] = A[j-1, j]
        lower[:-1] = -k_int  # ab[2, j] = A[j+1, j]
    ab = np.vstack([upper, diag, lower])
    col.T = solve_banded((1, 1), ab, rhs)


def _phase(col: Column) -> None:
    """Refreeze water in cold elements; melt in elements above 0 degC."""
    capk = np.maximum(col.ice * C_ICE, 1.0)
    cold = (col.T < 0.0) & (col.water > 0.0)
    if cold.any():
        e = -col.T[cold] * capk[cold]
        frz = np.minimum(col.water[cold], e / LF)
        col.water[cold] -= frz
        col.ice[cold] += frz
        col.frz[cold] += frz
        col.T[cold] += frz * LF / capk[cold]
    warm = col.T > 0.0
    if warm.any():
        m = np.minimum(col.ice[warm], col.T[warm] * capk[warm] / LF)
        col.ice[warm] -= m
        col.water[warm] += m
        col.T[warm] = 0.0
    col.T = np.minimum(col.T, 0.0)
    col.T[col.water > 1e-6] = 0.0


def _percolate(g: dict, col: Column, inflow: float) -> None:
    """Water from the top (melt, rain) down the column; each element holds a share of its ice mass, cold snow
    refreezes what reaches it."""
    hold = g["col_water_hold_frac"]
    w = inflow
    for i in range(len(col) - 1, -1, -1):
        w += col.water[i]
        col.water[i] = 0.0
        if w <= 0:
            continue
        if col.T[i] < 0.0:
            capk = max(col.ice[i] * C_ICE, 1.0)
            frz = min(w, -col.T[i] * capk / LF)
            w -= frz
            col.ice[i] += frz
            col.frz[i] += frz
            col.T[i] += frz * LF / capk
        keep = min(w, hold * col.ice[i])
        col.water[i] = keep
        w -= keep
        if col.water[i] > 1e-6:
            col.T[i] = 0.0


def _melt_top(col: Column, melt_kg: float) -> float:
    """Melt ``melt_kg`` of ice from the top elements; returns the water made."""
    made = 0.0
    i = len(col) - 1
    while melt_kg > 1e-9 and i >= 0:
        take = min(melt_kg, col.ice[i])
        frac = take / max(col.ice[i], 1e-9)
        col.dz[i] *= 1.0 - frac
        col.ice[i] -= take
        made += take
        melt_kg -= take
        i -= 1
    return made


def _settle(g: dict, col: Column) -> None:
    rho = col.rho
    mass = col.ice + col.water
    above = np.cumsum(mass[::-1])[::-1] - mass  # load of the elements above
    sigma = G * (above + 0.5 * mass)
    cold = -np.minimum(col.T, 0.0)
    theta = col.water / (1000.0 * np.maximum(col.dz, 1e-6))  # liquid water volume fraction
    r_ = np.clip(rho, 50.0, 700.0)
    # Crocus' grain factor f2 (up to 4 for grains over about 0.3 mm): this model has no grain size, so new snow (PP,
    # DF) takes 1 and older snow the maximum
    f2 = np.where(np.isin(col.cls, ["PP", "DF"]), 1.0, OLD_SNOW_F2)
    eta = (g["col_viscosity_mult"] * 7.62237e6 * f2 / (1.0 + 60.0 * theta) * (r_ / 250.0)
           * np.exp(0.1 * cold + 0.023 * r_))
    cr = sigma / eta
    c3 = np.where(rho > 150.0, np.exp(-0.046 * (rho - 150.0)), 1.0)
    c4 = np.where(col.water > 1e-6, 2.0, 1.0)
    dm = 2.778e-6 * c3 * c4 * np.exp(-0.04 * cold) * g["col_destructive_mult"]
    dm = np.where(np.isin(col.cls, ["PP", "DF"]), dm, 0.0)
    rate = (cr + dm) * DT
    new_dz = col.dz * np.exp(-np.clip(rate, 0.0, 0.5))
    # settlement stops at the maximum density (an element already denser, such as a crust, keeps its thickness)
    col.dz = np.maximum(new_dz, np.minimum(col.dz, mass / g["col_max_density_kg_m3"]))


def _metamorphism(g: dict, col: Column, ts: float, tg: float) -> None:
    z = np.cumsum(col.dz) - col.dz / 2  # mid heights above ground
    zz = np.concatenate([[0.0], z, [col.hs]])
    tt = np.concatenate([[tg], col.T, [ts]])
    g_now = (tt[:-2] - tt[2:]) / np.maximum(zz[2:] - zz[:-2], 1e-3)  # positive: warmer below
    col.gbar = col.gbar + (g_now - col.gbar) / GRAD_AVG_H  # the diurnal swing near the surface largely cancels
    grad = np.abs(col.gbar)
    tf = np.exp(g["col_temp_coeff"] * np.minimum(col.T, 0.0))
    g0, gdh = g["col_facet_gradient_k_m"], g["col_dh_gradient_k_m"]
    dry = col.water <= 1e-6
    col.age += 1.0
    strong = dry & (grad >= g0)
    col.fac = np.where(strong, col.fac + (grad - g0) / g0 * tf / g["col_facet_hours"], col.fac)
    col.rnd = np.where(strong, np.maximum(col.rnd - 1.0 / g["col_facet_hours"], 0.0),
                       np.where(dry, col.rnd + tf / g["col_round_hours"], col.rnd))
    col.dhp = np.where(dry & (grad >= gdh) & (col.fac >= 1.0), col.dhp + tf / g["col_dh_hours"], col.dhp)
    col.wet_h = np.where(dry, 0.0, col.wet_h + 1.0)
    grain_changes(g, col, dry, tf)


def grain_changes(g: dict, col: Column, dry: np.ndarray, tf: np.ndarray) -> None:
    """Each element's grain form from its progress counters (crusts and melt forms from its water history)."""
    old_facets = g.get("col_old_facets", "off") == "on"  # ADR-128 (families without the switch: off)
    for i in range(len(col)):
        c = col.cls[i]
        if c == "SH":
            continue
        if not dry[i]:
            if col.wet_h[i] >= WET_MF_H and c not in ("MF",):
                _set(col, i, "MF")
            continue
        # dry again: a refrozen wet element (or one soaked by enough refreezing water) is a crust
        if col.frz[i] >= g["col_crust_water_frac"] * col.ice[i] and col.T[i] < -0.1:
            if c != "MFcr":
                _set(col, i, "MFcr")
            col.frz[i] = 0.0
            continue
        if col.T[i] < -0.1:
            col.frz[i] = 0.0
        if c == "MFcr":
            continue
        if c == "FC" and old_facets and not col.new[i]:
            col.rnd[i] = min(col.rnd[i], 0.99)  # ADR-128: the last pit's facets do not round back in dry snow
        if c in ("PP", "DF", "RG", "MF") and col.fac[i] >= 1.0:
            _set(col, i, "FC")
            col.rnd[i] = 0.0
        elif c == "FC" and col.dhp[i] >= 1.0:
            _set(col, i, "DH")
        elif c == "FC" and col.rnd[i] >= 1.0:
            _set(col, i, "RG")
            col.fac[i] = 0.0
        elif c == "PP" and col.age[i] * tf[i] >= g["col_pp_hours"]:
            _set(col, i, "DF")
        elif c == "DF" and col.rnd[i] >= g["col_df_hours"] / g["col_round_hours"]:
            _set(col, i, "RG")


def _set(col: Column, i: int, c: str) -> None:
    col.cls[i] = c
    col.code[i] = _BASE[c]
    col.hard0[i] = math.nan  # the observer's hardness no longer describes this snow


def _tidy(col: Column) -> None:
    if len(col):
        col.keep((col.dz > MIN_DZ) & (col.ice > 1e-4))
    while len(col) > MAX_ELEMENTS:
        # merge the adjacent pair of one class with the least combined thickness, below the top 10 elements
        best, k = None, None
        for i in range(len(col) - 11):
            if col.cls[i] != col.cls[i + 1] or col.cls[i] == "SH":
                continue
            s = col.dz[i] + col.dz[i + 1]
            if best is None or s < best:
                best, k = s, i
        if k is None:
            k = int(np.argmin(col.dz[:-1] + col.dz[1:]))
        col.merge(k)


@dataclass
class Hour:
    """One hour as the extensions see it (ADR-120)."""

    ta: float  # air temperature, degC
    vw: float  # wind speed, m s-1
    rh: float
    sw: float  # incoming shortwave, W m-2
    lw: float  # incoming longwave, W m-2
    p: float = 0.0  # precipitation, mm
    rain: float = 0.0
    dz_new: float = 0.0  # new snow added this hour, m
    ts: float = 0.0  # snow surface temperature, degC
    q_melt: float = 0.0
    since_snow: float = 0.0  # hours since snow last fell

    @property
    def clear(self) -> bool:
        """Clear sky: the sky's effective emissivity (incoming longwave / sigma T_air^4) at most 0.8."""
        return self.lw <= 0.8 * SIGMA * (self.ta + 273.15) ** 4


class ColumnExt:
    """Hooks by which another model family changes or replaces steps of the column model (ADR-120). The base does
    nothing: ``simulate`` without an extension is the column model of ADR-118, unchanged."""

    n_hist = 0  # history features kept per element

    def start(self, g: dict, col: Column, pit) -> None:
        """After the start pit's elements are in place."""

    def weather(self, g: dict, vw: float, p: float, wd: float) -> tuple[float, float]:
        """The hour's wind speed (m s-1) and precipitation (mm) as the plot gets them; ``wd``: the direction the
        wind blows from (degrees)."""
        return vw, p

    def ground_temp(self, g: dict, col: Column, tg: float, hr: Hour) -> float:
        return tg

    def before_snowfall(self, g: dict, col: Column, hr: Hour) -> None:
        """New snow is about to bury the surface (``hr.ts`` is the last hour's surface temperature)."""

    def metamorphism(self, g: dict, col: Column, ts: float, tg: float, hr: Hour) -> bool:
        """True: the extension did the grain step itself (the column's own is skipped)."""
        return False

    def after_step(self, g: dict, col: Column, hr: Hour) -> None:
        """After settlement and metamorphism, before surface hoar."""

    def hoar_growth(self, g: dict, hr: Hour, dew_c: float, rate: float) -> float:
        """Surface hoar deposited this hour (m); 0: none."""
        return rate if hr.ts < dew_c and hr.vw <= g["col_sh_max_wind_ms"] else 0.0


_NO_EXT = ColumnExt()


def simulate(case: VisibleBenchmarkCase, g: dict, ext: ColumnExt | None = None) -> tuple[Column, dict]:
    """Run the column to the valid time; returns the column and notes."""
    pit = latest_season_pit(case)
    wx = column_forcing(case, pit.t_rel_h if pit is not None else -math.inf)
    plot = str(getattr(case.site_code, "value", case.site_code))
    *_, (col, notes, _i) = run_hours(wx, pit, g, ext, plot, case.horizon_hours)  # the last: the final state
    return col, notes


def run_hours(wx: Forcing, pit, g: dict, ext: ColumnExt | None, plot: str, horizon_h: float):
    """The column stepped hour by hour over ``wx`` from ``pit`` (its layers at ``pit.t_rel_h``; snow-free from the
    first hour when None) to ``horizon_h`` (hours on ``wx.t``'s clock). Yields ``(column, notes, i)`` after every
    hour ``i`` it steps, then once more with ``i = -1`` when the notes are final (the same objects throughout,
    updated in place): the lab's ``simulate`` keeps the last; the site's main model (ADR-136) also reads the column
    at its output hours."""
    ext = ext or _NO_EXT
    notes: dict = {"start": "pit" if pit is not None else "snow-free season start", "hours": 0,
                   "snowfall": "precipitation", "sensor_snow_m": 0.0, "precip_snow_mm": 0.0, "rain_mm": 0.0,
                   "melt_mm": 0.0}
    t = wx.t
    i0 = 0 if pit is None else int(np.searchsorted(t, pit.t_rel_h - 1e-6))
    tg = g["col_ground_temp_c"]
    col = Column.empty(ext.n_hist)
    if pit is not None:
        t_air = float(np.mean(wx.ta[max(0, i0 - 24): i0 + 1])) if i0 < len(t) else -5.0
        _start_from_pit(col, pit, t_air, tg)
    ext.start(g, col, pit)
    ts = 0.0
    plot_mult = g[PLOT_GENE.get(plot, "col_precip_mult_bow")]
    hs_off = 0.0
    if wx.hs is not None and pit is not None and i0 < len(t) and np.isfinite(wx.hs[i0]):
        hs_off = (wx.hs[i0] - col.hs) * g["col_sensor_offset_weight"]
    if wx.hs is not None:
        notes["snowfall"] = "snow depth sensor, then precipitation"
    since_snow = 240.0
    for i in range(i0, len(t)):
        if t[i] > horizon_h + 1e-6:
            break
        notes["hours"] += 1
        ta, vw, rh = float(wx.ta[i]), float(wx.vw[i]), float(wx.rh[i])
        p = float(wx.psum[i]) * (plot_mult if wx.measured[i] else 1.0)
        if ext is not _NO_EXT:
            vw, p = ext.weather(g, vw, p, float(wx.dw[i]) if wx.dw is not None else 0.0)
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
        hr = Hour(ta=ta, vw=vw, rh=rh, sw=float(wx.sw[i]), lw=float(wx.lw[i]), p=p, rain=rain, dz_new=dz_new, ts=ts,
                  since_snow=since_snow)
        # 1 snowfall
        if dz_new > 0.0:
            if len(col):
                ext.before_snowfall(g, col, hr)
            t_new = min(ta, 0.0)
            if len(col) > 1 and col.cls[-1] == "SH" and col.dz[-1] < SH_MIN_BURIED_M:
                col.merge(len(col) - 2)  # too little hoar to survive burial as a layer
                if col.cls[-1] == "SH":
                    _set(col, len(col) - 1, "DF")
            if (len(col) and col.cls[-1] == "PP" and col.new[-1] and since_snow <= g["storm_gap_h"]
                    and col.dz[-1] + dz_new <= MAX_DZ_TOP):
                col.dz[-1] += dz_new
                col.ice[-1] += dz_new * rho_new
            else:
                left = dz_new
                while left > 1e-6:
                    d = min(left, MAX_DZ_TOP)
                    col.append(d, d * rho_new, t_new, "PP", "PP", new=True)
                    left -= d
            since_snow = 0.0
        else:
            since_snow += 1.0
        if not len(col):
            yield col, notes, i
            continue
        # 2 surface energy balance and melt
        albedo = g["col_albedo_old"] + (g["col_albedo_fresh"] - g["col_albedo_old"]) * math.exp(
            -since_snow / 24.0 / g["col_albedo_decay_days"])
        ts, q_melt = _surface_temperature(g, col, ta, vw, float(wx.sw[i]), float(wx.lw[i]), albedo)
        hr.ts, hr.q_melt, hr.since_snow = ts, q_melt, since_snow
        melt = _melt_top(col, q_melt / LF) if q_melt > 0 else 0.0
        notes["melt_mm"] += melt
        notes["rain_mm"] += rain
        _tidy(col)
        if not len(col):
            yield col, notes, i
            continue
        # 3 conduction and phase change
        tg_h = ext.ground_temp(g, col, tg, hr)
        _conduct(g, col, ts, tg_h)
        _phase(col)
        # 4 water
        if melt + rain > 0 or (col.water > 0).any():
            _percolate(g, col, melt + rain)
        # 5 settlement, 6 metamorphism
        _settle(g, col)
        if not ext.metamorphism(g, col, ts, tg_h, hr):
            _metamorphism(g, col, ts, tg_h)
        ext.after_step(g, col, hr)
        # 7 surface hoar
        if len(col):
            top_sh = col.cls[-1] == "SH"
            if top_sh and (vw > g["col_sh_destroy_wind_ms"] or rain > 0.05 or q_melt > 0):
                if len(col) > 1:
                    col.merge(len(col) - 2)
                    col.cls[-1] = col.cls[-1] if col.cls[-1] != "SH" else "DF"
                else:
                    col.keep(np.zeros(1, dtype=bool))
            elif p <= 0.01 and q_melt == 0.0:
                td = dew_point_ice_c(ta, rh)
                d = g["col_sh_growth_m_h"]
                grow = ext.hoar_growth(g, hr, td, d)
                if grow > 0.0:  # deposition
                    if top_sh:
                        col.dz[-1] += grow
                        col.ice[-1] += grow * 100.0
                    else:
                        col.append(grow, grow * 100.0, ts, "SH", "SH", new=True)
                elif top_sh and ts > td:  # sublimation in the day
                    col.dz[-1] -= d
                    col.ice[-1] = max(col.ice[-1] - d * 100.0, 0.0)
        _tidy(col)
        yield col, notes, i
    rounded = {k: (round(v, 3) if isinstance(v, float) else v) for k, v in notes.items()}
    notes.clear()
    notes.update(rounded)
    yield col, notes, -1


def column_layers(col: Column, g: dict, case: VisibleBenchmarkCase) -> list[Lyr]:
    """Elements -> layers (surface first), merged by grain form and hardness as SNOWPACK's are."""
    return layers_from_column(col, g, latest_season_pit(case))


def layers_from_column(col: Column, g: dict, pit) -> list[Lyr]:
    """``column_layers`` with the start pit given (None: no pit); the site's main model reads layers this way."""
    from snowagent.lab.agents.snowpack import EngineLayer, merge_engine_layers

    hs = col.hs
    rho = col.rho
    old_facets = g.get("col_old_facets", "off") == "on"
    els: list[EngineLayer] = []
    z = hs
    # thin surface hoar is reported 1 cm thick (taken from the element below), as observers draw it
    dzs = col.dz.copy()
    for i in range(len(col)):
        if col.cls[i] == "SH" and dzs[i] < 0.01 and i > 0:
            need = 0.01 - dzs[i]
            take = min(need, dzs[i - 1] * 0.5)
            dzs[i] += take
            dzs[i - 1] -= take
    for i in range(len(col) - 1, -1, -1):
        top, bot = hs - z, hs - z + dzs[i]
        z -= dzs[i]
        c = col.cls[i]
        if np.isfinite(col.hard0[i]):
            base = col.rho0[i] if np.isfinite(col.rho0[i]) else rho[i]
            hard = col.hard0[i] + (hardness_from_density(rho[i]) - hardness_from_density(base))
            if old_facets and c in ("FC", "DH"):
                hard = col.hard0[i]  # ADR-128: faceted snow does not gain hardness as it settles
        else:
            hard = hardness_from_density(rho[i]) + (g["col_hard_facet_offset"] if c in ("FC", "DH") else 0.0)
            if c == "SH":
                hard = 1.0
        if c == "MFcr":
            hard = max(hard, CRUST_MIN_HARD)
        hard = float(np.clip(hard, 1.0, 6.0))
        els.append(EngineLayer(top_depth_m=max(top, 0.0), bottom_depth_m=max(bot, 0.0), grain=col.code[i],
                               hardness_index=hard, density_kg_m3=float(rho[i])))
    merged = merge_engine_layers(els, g["hardness_merge_tol"])
    prob = g["presence_confidence"]
    floor = None
    if pit is not None and pit_snow_depth(pit):
        from snowagent.lab.agents.snowpack import NEW_SNOW_FRAC

        floor = NEW_SNOW_FRAC * pit_snow_depth(pit)
    out = []
    for ly in merged:
        lyr = Lyr(top=ly.top_depth_m, bottom=ly.bottom_depth_m, grain=ly.grain or "UNKNOWN",
                  hardness_index=ly.hardness_index, density=ly.density_kg_m3, prob=prob, source="column")
        if floor is not None and lyr.critical in NEW_WEAK and hs - (lyr.top + lyr.bottom) / 2 > floor:
            lyr.prob, lyr.extra = prob * g["col_new_weak_factor"], {"in_new_snow": True}
        out.append(lyr)
    return out


class ColumnAgent:
    def __init__(self, genome: AgentGenome) -> None:
        if genome.family != AgentFamily.column:
            raise ValueError(f"ColumnAgent needs a column genome, got {genome.family}")
        self.genome = genome
        self.agent_id = genome.agent_id

    def predict(self, case: VisibleBenchmarkCase, seed: int) -> SnowpackPrediction:
        g = self.genome.genes
        meta = {"agent_version": AGENT_VERSION}
        if case.site is None or not case.weather_observed:
            return insufficient(case, self.genome, "no visible weather", meta)
        try:
            col, notes = simulate(case, g)
        except ValueError as exc:
            return insufficient(case, self.genome, f"no forcing: {exc}"[:300], meta)
        layers = column_layers(col, g, case)
        limits = ["the lab's own column model (no SNOWPACK); flat-plot column"]
        if case.forecast_source == ForecastSource.archived_gfs:
            limits.append("forecast hours are raw GFS at the plot (no correction)")
        if any(ly.extra.get("in_new_snow") for ly in layers) and g["col_new_weak_factor"] < 1.0:
            limits.append("weak layers formed in snow since the last pit are given a lower probability")
        return build_prediction(case, self.genome, col.hs, layers, g["presence_confidence"], limits,
                                meta | notes)


__all__ = ["Column", "ColumnAgent", "ColumnExt", "Forcing", "Hour", "column_forcing", "column_layers",
           "layers_from_column", "run_hours", "simulate"]
