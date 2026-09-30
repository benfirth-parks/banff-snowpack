"""Build terrain-unit forcing from a source weather series.

Correction ownership (each applied exactly once, here; the engine is configured with
PERP_TO_SLOPE=TRUE so it does not re-project):

| Correction                         | Owner            |
|------------------------------------|------------------|
| air-temperature lapse to unit elev | this module      |
| humidity (dewpoint conservation)   | this module      |
| precipitation elevation factor     | this module      |
| precipitation phase (PSUM_PH)      | this module      |
| horizontal -> slope-area PSUM      | this module      |
| direct/diffuse split (estimated)   | this module      |
| slope incidence + horizon shading  | this module      |
| sky-view diffuse + terrain reflect | this module      |
| sky-view longwave (simplified)     | this module      |
| albedo, reflected SW from snow     | SNOWPACK         |
| slope-normal layer geometry        | SNOWPACK         |
| wind                               | none (not downscaled; recorded) |
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from snowagent.contracts import TerrainUnit
from snowagent.spatial_forcing import solar

STEFAN_BOLTZMANN = 5.670374419e-8


@dataclass(frozen=True)
class ForcingConfig:
    lapse_rate_k_per_m: float = -0.0065
    precip_gradient_per_km: float = 0.0  # fractional change per km; README starts at factor 1.0
    phase_t_snow_c: float = 0.2  # all solid at/below (uncalibrated placeholder)
    phase_t_rain_c: float = 2.2  # all liquid at/above; midpoint 1.2 C = engine THRESH_RAIN default
    surrounding_albedo: float = 0.6  # albedo of surrounding terrain for reflected SW (assumption)
    terrain_emissivity: float = 0.99
    ground_temperature_k: float = 273.15  # TSG lower boundary (assumption)
    min_sun_elev_direct_deg: float = 1.0
    substeps_per_hour: int = 4
    max_direct_ratio: float = 12.0

    def assumptions(self, has_ilwr: bool) -> list[str]:
        a = [
            f"air temperature lapse rate {self.lapse_rate_k_per_m * 1000:.2f} K/km (fallback; not locally estimated)",
            "relative humidity adjusted by conserving source dewpoint (Magnus over water); RH capped at 100%",
            f"precipitation elevation gradient {self.precip_gradient_per_km:+.2f} per km (uncalibrated)",
            f"precipitation phase: linear air-temperature ramp {self.phase_t_snow_c}..{self.phase_t_rain_c} C "
            "at unit elevation (UNCALIBRATED placeholder; wet-bulb scheme pending verification)",
            "PSUM supplied to the engine per slope-parallel area (horizontal flux x cos slope)",
            "direct/diffuse split ESTIMATED from global horizontal SW via Erbs et al. (1982)",
            "source shortwave assumed unobstructed global horizontal radiation at the source location",
            "diffuse SW isotropic, scaled by unit sky-view factor; terrain-reflected SW = "
            f"albedo {self.surrounding_albedo} x global x (1 - sky view)",
            f"lower boundary: constant ground temperature {self.ground_temperature_k} K (TSG), no soil layers",
            "wind speed and direction passed through without terrain downscaling",
        ]
        if has_ilwr:
            a.append("incoming LW = sky view x source ILWR + (1 - sky view) x emission from terrain at "
                     "min(TA, 0 C) (simplified)")
        else:
            a.append("incoming LW not supplied: engine parameterizes it; no terrain LW correction applied")
        return a


def _dewpoint_c(t_c: np.ndarray, rh: np.ndarray) -> np.ndarray:
    a, b = 17.625, 243.04
    g = np.log(np.clip(rh, 1e-4, 1.0)) + a * t_c / (b + t_c)
    return b * g / (a - g)


def _es(t_c: np.ndarray) -> np.ndarray:
    return 6.1094 * np.exp(17.625 * t_c / (t_c + 243.04))


@dataclass
class UnitForcing:
    unit_id: str
    smet: pd.DataFrame  # engine-ready SI fields
    components: pd.DataFrame  # diagnostics: iswr_direct/diffuse/reflected, sunlit fraction ...
    assumptions: list[str] = field(default_factory=list)


def shortwave_geometry(index: pd.DatetimeIndex, lat: float, lon: float, unit: TerrainUnit,
                       cfg: ForcingConfig) -> pd.DataFrame:
    """Per-hour geometric factors averaged over sub-steps within each (end-of-interval) hour."""
    n = cfg.substeps_per_hour
    offsets = [(k + 0.5) / n for k in range(n)]
    az_h = np.array(unit.horizon_azimuths_deg)
    el_h = np.array(unit.horizon_elevation_deg)
    aspect = unit.aspect_deg if unit.aspect_deg is not None else 0.0
    cosz_sum = np.zeros(len(index))
    inc_sum = np.zeros(len(index))
    sunlit_sum = np.zeros(len(index))
    up_sum = np.zeros(len(index))
    for off in offsets:
        t = index - pd.Timedelta(hours=1) + pd.Timedelta(hours=off)
        zen, az = solar.sun_position(t, lat, lon)
        elev = 90.0 - zen
        up = elev > cfg.min_sun_elev_direct_deg
        hor = np.interp(az, np.append(az_h, 360.0), np.append(el_h, el_h[0]))
        visible = up & (elev > hor)
        cosi = np.clip(solar.cos_incidence(zen, az, unit.slope_deg, aspect), 0.0, None)
        cosz_sum += np.where(up, np.cos(np.radians(zen)), 0.0)
        inc_sum += np.where(visible, cosi, 0.0)
        sunlit_sum += visible
        up_sum += up
    zen_mid, _ = solar.sun_position(index - pd.Timedelta(minutes=30), lat, lon)
    return pd.DataFrame({
        "mean_cosz": cosz_sum / n,
        "mean_cos_incidence_visible": inc_sum / n,
        "sunlit_fraction": np.divide(sunlit_sum, up_sum, out=np.zeros(len(index)), where=up_sum > 0),
        "sun_up_fraction": up_sum / n,
        "zenith_mid_deg": zen_mid,
    }, index=index)


def build_unit_forcing(src: pd.DataFrame, src_meta_lat: float, src_meta_lon: float, src_elev_m: float,
                       unit: TerrainUnit, cfg: ForcingConfig, provider_elevation_adjusted: bool = False
                       ) -> UnitForcing:
    """``src`` is SI source forcing (ta rh vw dw iswr ilwr psum) indexed by UTC end-of-interval."""
    idx = src.index
    dz = unit.elevation_m - src_elev_m
    ta_c = src["ta"].to_numpy() - 273.15
    rh = src["rh"].to_numpy()
    if provider_elevation_adjusted:
        ta_u = ta_c.copy()
        rh_u = rh.copy()
    else:
        ta_u = ta_c + cfg.lapse_rate_k_per_m * dz
        td = np.minimum(_dewpoint_c(ta_c, rh), ta_c)
        rh_u = np.clip(_es(td) / _es(ta_u), 0.05, 1.0)
    pfac = max(0.0, 1.0 + cfg.precip_gradient_per_km * dz / 1000.0)
    psum_h = src["psum"].to_numpy() * pfac
    phase = np.clip((ta_u - cfg.phase_t_snow_c) / (cfg.phase_t_rain_c - cfg.phase_t_snow_c), 0.0, 1.0)
    cos_sl = math.cos(math.radians(unit.slope_deg))
    psum_slope = psum_h * cos_sl

    # --- shortwave: split, project, shade (single owner)
    lat, lon = unit.centroid_lonlat[1], unit.centroid_lonlat[0]
    geo = shortwave_geometry(idx, lat, lon, unit, cfg)
    ghi = src["iswr"].to_numpy()
    ext = solar.SOLAR_CONSTANT * solar.eccentricity(idx) * geo["mean_cosz"].to_numpy()
    kt = np.divide(ghi, ext, out=np.zeros_like(ghi), where=ext > 5.0)
    kd = np.where(ext > 5.0, solar.erbs_diffuse_fraction(kt), 1.0)
    diffuse_h = ghi * kd
    direct_h = ghi - diffuse_h
    ratio = np.divide(geo["mean_cos_incidence_visible"].to_numpy(), geo["mean_cosz"].to_numpy(),
                      out=np.zeros_like(ghi), where=geo["mean_cosz"].to_numpy() > 1e-3)
    ratio = np.clip(ratio, 0.0, cfg.max_direct_ratio)
    direct_s = direct_h * ratio
    svf = unit.sky_view_factor
    diffuse_s = diffuse_h * svf
    reflected_s = cfg.surrounding_albedo * ghi * (1.0 - svf)
    iswr_s = np.clip(direct_s + diffuse_s + reflected_s, 0.0, solar.SOLAR_CONSTANT)

    ilwr = src["ilwr"].to_numpy()
    has_ilwr = bool(np.isfinite(ilwr).all())
    if has_ilwr:
        t_terr = np.minimum(ta_u, 0.0) + 273.15
        ilwr_s = svf * ilwr + (1.0 - svf) * cfg.terrain_emissivity * STEFAN_BOLTZMANN * t_terr**4
    else:
        ilwr_s = np.full(len(idx), np.nan)

    smet = pd.DataFrame({
        "TA": ta_u + 273.15, "RH": rh_u, "VW": src["vw"].to_numpy(),
        "DW": src["dw"].fillna(0.0).to_numpy() if "dw" in src else 0.0,
        "ISWR": iswr_s, "ILWR": ilwr_s, "PSUM": psum_slope, "PSUM_PH": phase,
        "TSG": cfg.ground_temperature_k,
    }, index=idx)
    comps = pd.DataFrame({
        "iswr_source_horizontal": ghi, "iswr_direct_slope": direct_s, "iswr_diffuse_slope": diffuse_s,
        "iswr_reflected_slope": reflected_s, "iswr_slope_total": iswr_s, "diffuse_fraction": kd,
        "sunlit_fraction": geo["sunlit_fraction"].to_numpy(), "sun_up_fraction": geo["sun_up_fraction"].to_numpy(),
        "psum_horizontal": psum_h, "psum_slope_area": psum_slope, "liquid_fraction": phase,
        "ta_unit_c": ta_u, "ilwr_slope": ilwr_s,
    }, index=idx)
    return UnitForcing(unit.unit_id, smet, comps, cfg.assumptions(has_ilwr))
