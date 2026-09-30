"""Real-engine integration fixtures. Skipped (with the reason) when SNOWPACK is unavailable.

The mini workspace is SYNTHETIC: a 3 km x 2 km E-W ridge (north- and south-facing
units at equal elevation) with a forested strip (unsupported), ~6 weeks of scripted
history from an explicit snow-free start, and a 48 h synthetic forecast.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from pyproj import Transformer

from snowagent.contracts import EnsembleConfig, Provenance, WeatherKind, WeatherMeta
from snowagent.engine import snowpack as sp
from snowagent.errors import EngineUnavailable
from snowagent.spatial_forcing.builder import ForcingConfig
from snowagent.state.replay import InitPolicy, initialize_from_history
from snowagent.state.store import StateStore
from snowagent.synthetic.fixture import FixtureSpec, generate_weather
from snowagent.terrain.dem import write_ascii_grid
from snowagent.terrain.units import UnitConfig, build_domain
from snowagent.weather.io import load_weather, write_weather

CRS = "EPSG:32611"
X0, Y0 = 600000.0, 5668000.0
HIST_START, ANALYSIS, FC_HOURS = "2025-10-25T00:00:00Z", "2025-12-10T00:00:00Z", 48
EVENTS = [
    ("2025-10-28T00", "2025-10-30T00", "storm", 0.8, -4.0),
    ("2025-11-03T00", "2025-11-12T00", "clear", 0.0, -5.0),
    ("2025-11-12T00", "2025-11-15T00", "storm", 0.8, -3.0),
    ("2025-11-15T00", "2025-11-25T00", "clear", 0.0, -2.0),
    ("2025-11-25T00", "2025-11-28T00", "storm", 0.7, -3.0),
    ("2025-11-28T00", "2025-12-12T00", "clear", 0.0, 1.0),
]
FC_EVENTS = [("2025-12-10T06", "2025-12-11T06", "storm", 0.6, -2.0), ("2025-12-11T06", "2025-12-12T00", "clear", 0.0, -3.0)]


def engine_or_skip() -> sp.EngineInfo:
    try:
        return sp.find_engine()
    except EngineUnavailable as exc:
        pytest.skip(f"real SNOWPACK engine unavailable: {exc.message} Recovery: {exc.details['recovery_command']}")


@dataclass
class MiniWorkspace:
    root: Path
    domain: object
    store: StateStore
    actuals: object
    forecast: object
    forecast_path: Path
    engine: sp.EngineInfo
    settings: sp.EngineSettings
    fcfg: ForcingConfig
    ens: EnsembleConfig
    checkpoint: object


def _prov():
    return Provenance(source="tests.integration", description="SYNTHETIC test fixture", synthetic=True)


def build_mini(root: Path, engine: sp.EngineInfo) -> MiniWorkspace:
    res, nx, ny = 50.0, 60, 40
    c = (np.arange(nx) + 0.5) * res
    r = (np.arange(ny) + 0.5) * res
    xx, yy_up = np.meshgrid(c, r)
    yy = yy_up[::-1]
    z = 1900.0 + 700.0 * np.exp(-((yy - 1000.0) / 420.0) ** 2)
    lc = np.ones_like(z)
    lc[:, 40:] = 2  # forest strip -> unsupported units
    side = {"crs": CRS, "horizontal_units": "m", "synthetic": True}
    t = root / "terrain"
    t.mkdir(parents=True)
    write_ascii_grid(t / "dem.asc", z, X0, Y0, res, {**side, "vertical_units": "m"})
    write_ascii_grid(t / "lc.asc", lc, X0, Y0, res, {**side, "legend": {"1": "open", "2": "forest"}}, fmt="%d")
    ring = [[X0 + 10, Y0 + 10], [X0 + 2990, Y0 + 10], [X0 + 2990, Y0 + 1990], [X0 + 10, Y0 + 1990], [X0 + 10, Y0 + 10]]
    (t / "b.geojson").write_text(json.dumps({"type": "Feature", "crs": {"properties": {"name": CRS}},
                                             "geometry": {"type": "Polygon", "coordinates": [ring]}}))
    domain = build_domain("mini", t / "dem.asc", t / "b.geojson", t / "lc.asc", UnitConfig(unit_size_m=1000), True)

    lon, lat = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform(X0 + 1500, Y0 + 1000)
    spec = FixtureSpec(history_start=HIST_START)
    idx = pd.date_range(HIST_START, pd.Timestamp(ANALYSIS) + pd.Timedelta(hours=FC_HOURS), freq="h", inclusive="right")
    truth = generate_weather(idx, EVENTS, spec, 11, lat, lon)
    wx = root / "weather"
    am = WeatherMeta(series_id="mini_actuals", kind=WeatherKind.actuals, source="synthetic", lat=lat, lon=lon,
                     source_elevation_m=1900, timestamp_convention="end_of_interval", accumulation_interval_s=3600,
                     availability_latency_s=3600, wind_height_m=10, met_height_m=2, provenance=_prov())
    write_weather(wx / "actuals.csv", am, truth)
    init = pd.Timestamp(ANALYSIS)
    fidx = pd.date_range(init, init + pd.Timedelta(hours=FC_HOURS), freq="h", inclusive="right")
    fc = generate_weather(fidx, FC_EVENTS, spec, 12, lat, lon)
    fm = WeatherMeta(series_id="mini_fc", kind=WeatherKind.forecast, source="synthetic", model="SYNTH", lat=lat,
                     lon=lon, source_elevation_m=1900, timestamp_convention="end_of_interval",
                     accumulation_interval_s=3600, issue_time=init.to_pydatetime(),
                     available_time=(init + pd.Timedelta(hours=4)).to_pydatetime(), wind_height_m=10, met_height_m=2,
                     provenance=_prov())
    write_weather(wx / "fc.csv", fm, fc)
    actuals, forecast = load_weather(wx / "actuals.csv"), load_weather(wx / "fc.csv")
    store = StateStore(root / "store")
    settings, fcfg = sp.EngineSettings(), ForcingConfig()
    cp = initialize_from_history(domain, actuals, store, init.to_pydatetime(), engine, settings, fcfg, "snow_free",
                                 InitPolicy())
    ens = EnsembleConfig(members=3, seed=5, ta_sigma_k=1.0, psum_log_sigma=0.3, iswr_rel_sigma=0.1,
                         ilwr_sigma_wm2=10, ar1_hourly=0.9)
    return MiniWorkspace(root, domain, store, actuals, forecast, wx / "fc.csv", engine, settings, fcfg, ens, cp)


@pytest.fixture(scope="session")
def engine() -> sp.EngineInfo:
    return engine_or_skip()


@pytest.fixture(scope="session")
def mini(tmp_path_factory, engine) -> MiniWorkspace:
    return build_mini(tmp_path_factory.mktemp("mini"), engine)


@pytest.fixture(scope="session")
def mini_run(mini):
    from snowagent.forecast.pipeline import predict

    return predict(mini.domain, mini.forecast, mini.store, mini.root / "runs", mini.engine, mini.settings, mini.fcfg,
                   mini.ens, [0, 12, 24, 48])
