"""Real-domain inputs (ADR-032/033): land-cover tiles, DEM window alignment, site units, GFS series."""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest


def test_worldcover_tile_names_and_bbox_tiles(monkeypatch, tmp_path):
    from snowagent.ingest import worldcover

    assert worldcover.tile_name(51.09, -115.75) == "ESA_WorldCover_10m_2021_v200_N51W117_Map"
    assert worldcover.tile_name(50.9, -114.1) == "ESA_WorldCover_10m_2021_v200_N48W117_Map"
    got = []
    monkeypatch.setattr(worldcover, "fetch", lambda url, dest, manifest, timeout: got.append(dest.name) or {})
    worldcover.download((-115.9, 50.9, -115.6, 51.2), tmp_path)
    assert got == ["ESA_WorldCover_10m_2021_v200_N48W117_Map.tif", "ESA_WorldCover_10m_2021_v200_N51W117_Map.tif"]
    assert set(worldcover.LEGEND.values()) <= {"open", "forest", "rock", "glacier", "water", "unknown"}


def _write_tifs(tmp_path):
    import rasterio
    from rasterio.transform import from_origin

    dem = tmp_path / "dem.tif"
    x0, y0, n = 580000.0, 5670000.0, 400  # 12 km at 30 m
    yy, xx = np.mgrid[0:n, 0:n]
    z = (2000 + 2.0 * xx + 0.5 * yy).astype("float32")
    with rasterio.open(dem, "w", driver="GTiff", height=n, width=n, count=1, dtype="float32", crs="EPSG:32611",
                       transform=from_origin(x0, y0, 30, 30), nodata=np.nan) as ds:
        ds.write(z, 1)
    lc = tmp_path / "lc.tif"
    codes = np.full((600, 600), 60, dtype="uint8")
    codes[:, :300] = 10  # west half forest, east half bare
    with rasterio.open(lc, "w", driver="GTiff", height=600, width=600, count=1, dtype="uint8", crs="EPSG:4326",
                       transform=from_origin(-116.0, 51.3, 0.0005, 0.0005), nodata=0) as ds:
        ds.write(codes, 1)
    return dem, lc


def _center():
    from pyproj import Transformer

    lon, lat = Transformer.from_crs("EPSG:32611", "EPSG:4326", always_xy=True).transform(586000.0, 5664000.0)
    return lat, lon


def test_prepare_inputs_aligns_units_with_boundary_and_maps_land_cover(tmp_path):
    from snowagent.terrain.prepare import DomainSpec, prepare_inputs
    from snowagent.terrain.units import UnitConfig, build_domain

    dem, lc = _write_tifs(tmp_path)
    spec = DomainSpec("t", *_center(), 1800.0, 600.0, buffer_m=3000.0)
    p = prepare_inputs(spec, dem, [lc], tmp_path / "out")
    side = json.loads((tmp_path / "out" / "landcover.asc.json").read_text())
    assert side["legend"]["10"] == "forest" and side["legend"]["60"] == "rock"
    d = build_domain("t", p["dem"], p["boundary"], p["landcover"], UnitConfig(unit_size_m=600.0), False)
    assert len(d.units) == 9  # 3 x 3 blocks exactly inside the 1.8 km square
    assert all(abs(u.area_planimetric_m2 - 600.0**2) < 1e-6 for u in d.units)
    assert {u.land_cover.value for u in d.units} <= {"forest", "rock"}


def test_prepare_inputs_rejects_units_not_on_the_dem_grid(tmp_path):
    from snowagent.errors import InvalidInput
    from snowagent.terrain.prepare import DomainSpec, prepare_inputs

    dem, lc = _write_tifs(tmp_path)
    with pytest.raises(InvalidInput):
        prepare_inputs(DomainSpec("t", *_center(), 2000.0, 500.0, buffer_m=3000.0), dem, [lc], tmp_path / "o")


def test_site_units_come_first_change_version_and_match_baseline_plot_unit(tmp_path):
    from snowagent.baseline.run import plot_unit
    from snowagent.errors import InvalidInput
    from snowagent.forecast.query import locate_unit
    from snowagent.terrain.prepare import DomainSpec, prepare_inputs
    from snowagent.terrain.units import UnitConfig, add_site_units, build_domain

    dem, lc = _write_tifs(tmp_path)
    lat, lon = _center()
    p = prepare_inputs(DomainSpec("t", lat, lon, 1800.0, 600.0, buffer_m=3000.0), dem, [lc], tmp_path / "o")
    d = build_domain("t", p["dem"], p["boundary"], p["landcover"], UnitConfig(unit_size_m=600.0), False)
    d2 = add_site_units(d, {"plot": (lat, lon, 2290.0)})
    assert d2.units[0].unit_id == "site_plot" and d2.terrain_version != d.terrain_version
    assert {u.terrain_version for u in d2.units} == {d2.terrain_version}
    assert locate_unit(d2, lat, lon)[0].unit_id == "site_plot"
    base = plot_unit("plot", lat, lon, 2290.0)
    keep = ("slope_deg", "aspect_deg", "sky_view_factor", "horizon_elevation_deg", "elevation_m", "land_cover")
    assert all(getattr(base, k) == getattr(d2.units[0], k) for k in keep)
    with pytest.raises(InvalidInput):
        add_site_units(d, {"far": (51.5, -115.0, 2000.0)})


def _gfs_run(path, run: str, ta0: float) -> None:
    rows = []
    for lead in range(0, 49, 3):
        r = {"run_utc": run, "lead_h": lead, "point": "p", "model_elev_m": 2000.0, "tmp2m_k": ta0 + lead,
             "rh2m_pct": 80.0, "u10_ms": 1.0, "v10_ms": 0.0}
        if lead:
            a = lead - 3 if lead % 6 else lead - 6
            r |= {"apcp_kgm2": 0.3 * (lead - a), "apcp_kgm2_desc": f"{a}-{lead} hour acc fcst",
                  "dswrf_wm2": 100.0, "dswrf_wm2_desc": f"{a}-{lead} hour ave fcst",
                  "dlwrf_wm2": 250.0, "dlwrf_wm2_desc": f"{a}-{lead} hour ave fcst"}
        rows.append(r)
    pd.DataFrame(rows).to_csv(path, index=False)


def test_gfs_day1_series_chains_daily_runs_without_using_later_runs(tmp_path):
    from snowagent.weather.sources import gfs_day1_series

    _gfs_run(tmp_path / "gfs_2026032200.csv", "2026-03-22T00:00:00+00:00", 250.0)
    _gfs_run(tmp_path / "gfs_2026032300.csv", "2026-03-23T00:00:00+00:00", 300.0)
    idx = pd.date_range("2026-03-22T01:00Z", "2026-03-24T00:00Z", freq="h")
    s, elev = gfs_day1_series("p", idx, tmp_path)
    assert elev == 2000.0 and s.notna().all().all()
    # hours of 22 Mar from run 22 (ta 250 + lead), hours of 23 Mar from run 23 (ta 300 + lead)
    assert s.loc["2026-03-22T06:00Z", "ta"] == pytest.approx(256.0)
    assert s.loc["2026-03-23T00:00Z", "ta"] == pytest.approx(274.0)  # lead 24 of run 22
    assert s.loc["2026-03-23T03:00Z", "ta"] == pytest.approx(303.0)
    assert s["psum"].sum() == pytest.approx(0.3 * 48)  # 0.3 mm h-1 in every window


def test_gfs_forecast_series_declares_issue_and_availability(tmp_path):
    from snowagent.contracts import WeatherKind
    from snowagent.weather.io import load_weather
    from snowagent.weather.sources import write_gfs_forecast

    _gfs_run(tmp_path / "gfs_2026032300.csv", "2026-03-23T00:00:00+00:00", 260.0)
    run = pd.Timestamp("2026-03-23T00:00Z")
    write_gfs_forecast(run, "p", 51.0, -115.7, tmp_path / "fc.csv", gfs_dir=tmp_path)
    w = load_weather(tmp_path / "fc.csv")
    assert w.meta.kind == WeatherKind.forecast and w.meta.source_elevation_m == 2000.0
    assert pd.Timestamp(w.meta.available_time) == run + pd.Timedelta(hours=5)
    assert w.data.index[0] == run + pd.Timedelta(hours=1) and w.data.index[-1] == run + pd.Timedelta(hours=48)
