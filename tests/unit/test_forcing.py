"""Terrain-conditioned forcing: solar geometry, shading, single-owner projection, elevation and phase."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from snowagent.contracts import LandCover, TerrainUnit
from snowagent.spatial_forcing import solar
from snowagent.spatial_forcing.builder import ForcingConfig, build_unit_forcing


def unit(slope=35.0, aspect=180.0, horizon=0.0, elev=2000.0, svf=None, uid="u") -> TerrainUnit:
    az = [float(a) for a in range(0, 360, 10)]
    return TerrainUnit(
        unit_id=uid, domain_id="d", terrain_version="t", crs="EPSG:32611", polygon_xy=[(0, 0), (1, 0), (1, 1)],
        centroid_xy=(0.5, 0.5), centroid_lonlat=(-115.7, 51.2), resolution_m=1000, n_dem_cells=400,
        area_planimetric_m2=1e6, area_surface_m2=1.2e6, elevation_m=elev, elevation_min_m=elev, elevation_max_m=elev,
        slope_deg=slope, aspect_deg=aspect, horizon_azimuths_deg=az, horizon_elevation_deg=[horizon] * 36,
        sky_view_factor=svf if svf is not None else (1 + math.cos(math.radians(slope))) / 2 * math.cos(
            math.radians(horizon)) ** 2,
        land_cover=LandCover.open, supported=True)


def src(days=3, start="2026-01-15T01:00:00Z", ghi_scale=1.0, ta_c=-5.0, psum=0.0) -> pd.DataFrame:
    idx = pd.date_range(start, periods=24 * days, freq="h")
    zen, _ = solar.sun_position(idx - pd.Timedelta(minutes=30), 51.2, -115.7)
    cosz = np.clip(np.cos(np.radians(zen)), 0, None)
    ghi = ghi_scale * np.where(cosz > 0.01, 1098 * cosz * np.exp(-0.057 / np.maximum(cosz, 0.01)), 0)
    return pd.DataFrame({"ta": ta_c + 273.15, "rh": 0.7, "vw": 2.0, "dw": 250.0, "iswr": ghi, "ilwr": 230.0,
                         "psum": psum}, index=idx)


def test_sun_position_reference_values():
    # Solar noon elevation at 51.2 N on the June solstice ~ 90 - 51.2 + 23.44
    t = pd.date_range("2026-06-21T18:00Z", "2026-06-21T21:00Z", freq="min")
    zen, _ = solar.sun_position(t, 51.2, -115.7)
    assert 90 - zen.min() == pytest.approx(62.24, abs=0.4)
    t = pd.date_range("2026-01-15T17:00Z", "2026-01-15T22:00Z", freq="min")
    zen, az = solar.sun_position(t, 51.2, -115.7)
    assert 90 - zen.min() == pytest.approx(90 - 51.2 - 21.1, abs=0.5)
    assert az[np.argmin(zen)] == pytest.approx(180, abs=1.5)


def test_flat_open_unit_reproduces_source_shortwave():
    s = src()
    f = build_unit_forcing(s, 51.2, -115.7, 2000, unit(slope=0, aspect=0, svf=1.0), ForcingConfig())
    assert np.allclose(f.smet["ISWR"], s["iswr"], atol=1e-6)


def test_south_slope_gets_more_winter_sun_than_north_slope():
    s = src()
    south = build_unit_forcing(s, 51.2, -115.7, 2000, unit(aspect=180), ForcingConfig()).smet["ISWR"].sum()
    north = build_unit_forcing(s, 51.2, -115.7, 2000, unit(aspect=0), ForcingConfig()).smet["ISWR"].sum()
    flat = s["iswr"].sum()
    assert south > flat > north


def test_horizon_shading_removes_direct_beam_only():
    s = src()
    open_ = build_unit_forcing(s, 51.2, -115.7, 2000, unit(aspect=180, horizon=0), ForcingConfig())
    shaded = build_unit_forcing(s, 51.2, -115.7, 2000, unit(aspect=180, horizon=30), ForcingConfig())
    assert shaded.components["iswr_direct_slope"].sum() == 0.0  # Jan sun at 51N never exceeds ~18 deg
    assert open_.components["iswr_direct_slope"].sum() > 0
    assert shaded.components["iswr_diffuse_slope"].sum() > 0  # diffuse remains
    total = shaded.components[["iswr_direct_slope", "iswr_diffuse_slope", "iswr_reflected_slope"]].sum(axis=1)
    assert np.allclose(total, shaded.smet["ISWR"])


def test_precip_projected_to_slope_area_exactly_once():
    s = src(psum=1.0, ta_c=-10)
    f = build_unit_forcing(s, 51.2, -115.7, 2000, unit(slope=40), ForcingConfig())
    assert np.allclose(f.smet["PSUM"], math.cos(math.radians(40)))
    assert np.allclose(f.components["psum_horizontal"], 1.0)


def test_elevation_lapse_and_phase_response():
    s = src(psum=1.0, ta_c=3.0)  # source at 1500 m
    low = build_unit_forcing(s, 51.2, -115.7, 1500, unit(elev=1500, slope=0, svf=1), ForcingConfig())
    high = build_unit_forcing(s, 51.2, -115.7, 1500, unit(elev=2500, slope=0, svf=1), ForcingConfig())
    assert (low.smet["TA"] - high.smet["TA"]).mean() == pytest.approx(6.5, abs=1e-6)
    assert low.smet["PSUM_PH"].mean() == 1.0 and high.smet["PSUM_PH"].mean() == 0.0
    assert (high.smet["RH"] >= low.smet["RH"]).all() and high.smet["RH"].max() <= 1.0


def test_provider_adjusted_forcing_is_not_lapsed_again():
    s = src(ta_c=-2.0)
    f = build_unit_forcing(s, 51.2, -115.7, 1500, unit(elev=2500), ForcingConfig(), provider_elevation_adjusted=True)
    assert np.allclose(f.smet["TA"], s["ta"])


def test_assumptions_record_estimated_components():
    f = build_unit_forcing(src(), 51.2, -115.7, 2000, unit(), ForcingConfig())
    text = " ".join(f.assumptions)
    assert "ESTIMATED" in text and "UNCALIBRATED" in text and "without terrain downscaling" in text
