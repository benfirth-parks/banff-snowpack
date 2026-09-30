"""Terrain derivatives, CRS/unit validation and unit construction on analytic surfaces."""

from __future__ import annotations

import json

import numpy as np
import pytest

from snowagent.errors import InvalidInput, InvalidUnits
from snowagent.terrain import derive
from snowagent.terrain.dem import Raster, points_in_polygon, read_raster, write_ascii_grid
from snowagent.terrain.units import UnitConfig, build_domain

CRS = "EPSG:32611"
SIDE = {"crs": CRS, "horizontal_units": "m", "vertical_units": "m"}


def plane(n=40, res=50.0, slope_deg=30.0, facing="S") -> Raster:
    """Plane rising toward the opposite of ``facing``."""
    g = np.tan(np.radians(slope_deg))
    r, c = np.mgrid[0:n, 0:n]
    north = (n - r) * res  # distance north
    east = c * res
    z = {"S": 1000 + g * north, "N": 3000 - g * north, "E": 3000 - g * east, "W": 1000 + g * east}[facing]
    return Raster(z.astype(float), 0.0, n * res, res, CRS, {})


@pytest.mark.parametrize("facing,aspect", [("S", 180), ("N", 0), ("E", 90), ("W", 270)])
def test_slope_aspect_of_plane(facing, aspect):
    s, a = derive.slope_aspect(plane(facing=facing))
    assert np.nanmedian(s) == pytest.approx(30.0, abs=0.01)
    med = np.nanmedian(a)
    assert min(abs(med - aspect), 360 - abs(med - aspect)) < 0.01


def test_mean_orientation_matches_plane():
    dem = plane(slope_deg=25, facing="E")
    dzdx, dzdy = derive.gradients(dem)
    s, a = derive.mean_orientation(dzdx, dzdy)
    assert s == pytest.approx(25, abs=0.01) and a == pytest.approx(90, abs=0.01)


def test_flat_horizon_and_sky_view():
    flat = Raster(np.full((30, 30), 1000.0), 0, 1500, 50.0, CRS, {})
    az = np.arange(36) * 10.0
    h = derive.horizon(flat, 750, 750, 1001, az, 2000)
    assert np.all(h == 0)
    assert derive.sky_view_factor(0, 0, az, h) == pytest.approx(1.0, abs=1e-9)


def test_tilted_plane_sky_view_is_analytic():
    az = np.arange(360) * 1.0
    svf = derive.sky_view_factor(30, 180, az, np.zeros_like(az))
    assert svf == pytest.approx((1 + np.cos(np.radians(30))) / 2, abs=2e-3)


def test_uniform_horizon_reduces_sky_view():
    az = np.arange(360) * 1.0
    svf = derive.sky_view_factor(0, 0, az, np.full_like(az, 20.0))
    assert svf == pytest.approx(np.cos(np.radians(20)) ** 2, abs=1e-3)


def test_horizon_sees_wall_to_south():
    z = np.full((60, 60), 1000.0)
    z[45:, :] = 2000.0  # 1000 m wall in the south
    dem = Raster(z, 0, 3000, 50.0, CRS, {})
    az = np.array([0.0, 180.0])
    # wall face at y = 750 m; point at y = 2250 m is 1500 m north of it, 999 m below its top
    h = derive.horizon(dem, 1500, 2250, 1001, az, 5000)
    assert h[0] == 0
    assert h[1] == pytest.approx(np.degrees(np.arctan(999 / 1500)), abs=1.5)


def test_point_in_polygon():
    ring = [(0, 0), (10, 0), (10, 10), (0, 10), (0, 0)]
    assert points_in_polygon(np.array([5, 15]), np.array([5, 5]), ring).tolist() == [True, False]


def _write_domain(tmp_path, crs=CRS, vunits="m", lc=True):
    z = plane(n=40, facing="S").data
    write_ascii_grid(tmp_path / "dem.asc", z, 590000, 5668000, 50.0, {**SIDE, "crs": crs, "vertical_units": vunits})
    if lc:
        codes = np.ones_like(z)
        codes[:, :20] = 2
        write_ascii_grid(tmp_path / "lc.asc", codes, 590000, 5668000, 50.0,
                         {**SIDE, "legend": {"1": "open", "2": "forest"}}, fmt="%d")
    ring = [[590000, 5668000], [592000, 5668000], [592000, 5670000], [590000, 5670000], [590000, 5668000]]
    (tmp_path / "b.geojson").write_text(json.dumps({"type": "Feature", "crs": {"properties": {"name": crs}},
                                                    "geometry": {"type": "Polygon", "coordinates": [ring]}}))


def test_build_domain_units_and_unsupported_forest(tmp_path):
    _write_domain(tmp_path)
    d = build_domain("t", tmp_path / "dem.asc", tmp_path / "b.geojson", tmp_path / "lc.asc",
                     UnitConfig(unit_size_m=1000), synthetic=True)
    assert len(d.units) == 4
    forest = [u for u in d.units if not u.supported]
    assert len(forest) == 2 and all("canopy" in r for u in forest for r in u.unsupported_reasons)
    for u in d.units:
        assert u.slope_deg == pytest.approx(30, abs=0.5) and u.aspect_deg == pytest.approx(180, abs=0.5)
        assert u.area_surface_m2 == pytest.approx(u.area_planimetric_m2 / np.cos(np.radians(30)), rel=0.01)
        assert u.resolution_m == 1000 and u.crs == CRS


def test_geographic_crs_rejected(tmp_path):
    _write_domain(tmp_path, crs="EPSG:4326")
    with pytest.raises(InvalidInput, match="not projected"):
        read_raster(tmp_path / "dem.asc")


def test_non_metre_vertical_units_rejected(tmp_path):
    _write_domain(tmp_path, vunits="ft")
    with pytest.raises(InvalidUnits):
        read_raster(tmp_path / "dem.asc")


def test_nodata_becomes_nan(tmp_path):
    z = np.full((5, 5), 1500.0)
    z[2, 2] = np.nan
    write_ascii_grid(tmp_path / "n.asc", z, 0, 0, 50.0, SIDE)
    r = read_raster(tmp_path / "n.asc")
    assert np.isnan(r.data[2, 2]) and np.isfinite(r.data).sum() == 24
