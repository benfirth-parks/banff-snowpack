"""SYNTHETIC terrain and weather fixture for software/process demonstration.

Everything produced here is invented. It is positioned at a Canadian Rockies
latitude only so that solar geometry is realistic. Profiles computed from it
demonstrate software and process behaviour; they say nothing about Banff
accuracy and are not observations.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from snowagent.contracts import Provenance, WeatherKind, WeatherMeta
from snowagent.spatial_forcing import solar
from snowagent.terrain.dem import write_ascii_grid
from snowagent.weather.io import write_weather

CRS = "EPSG:32611"  # UTM 11N
X0, Y0 = 590000.0, 5668000.0  # lower-left corner (~51.16 N, 115.70 W); SYNTHETIC placement
SYNTH_TAG = "SYNTHETIC - invented for software demonstration; not real terrain, weather or observations"


@dataclass(frozen=True)
class FixtureSpec:
    size_km: float = 8.0
    dem_res_m: float = 50.0
    seed: int = 20260930
    history_start: str = "2025-10-01T00:00:00Z"
    history_end: str = "2026-01-18T00:00:00Z"  # actuals continue past issue time on purpose (leakage tests)
    forecast_init: str = "2026-01-15T00:00:00Z"
    forecast_available: str = "2026-01-15T04:30:00Z"
    forecast_hours: int = 72
    station_xy_km: tuple[float, float] = (4.0, 1.0)
    station_elev_m: float = 1550.0


def _smooth(a: np.ndarray, passes: int) -> np.ndarray:
    for _ in range(passes):
        p = np.pad(a, 1, mode="edge")
        a = (p[:-2, :-2] + p[:-2, 1:-1] + p[:-2, 2:] + p[1:-1, :-2] + p[1:-1, 1:-1] + p[1:-1, 2:]
             + p[2:, :-2] + p[2:, 1:-1] + p[2:, 2:]) / 9.0
    return a


def synthetic_dem(spec: FixtureSpec) -> tuple[np.ndarray, np.ndarray]:
    """Return (elevation, landcover codes) with row 0 = north edge."""
    n = int(spec.size_km * 1000 / spec.dem_res_m)
    c = (np.arange(n) + 0.5) * spec.dem_res_m / 1000.0
    xk, yk_up = np.meshgrid(c, c)  # yk_up increases with row index; flip so row 0 is north
    yk = yk_up[::-1]
    z = (1500.0 + 25.0 * yk
         + 1300.0 * np.exp(-(((yk - 5.2) / 1.1) ** 2))  # E-W main ridge: N and S faces
         + 950.0 * np.exp(-(((xk - 2.5) / 0.75) ** 2)) / (1 + np.exp((yk - 5.0) * 3))  # N-S spur: E and W faces
         + 450.0 * np.exp(-(((xk - 6.3) / 0.8) ** 2) - ((yk - 1.3) / 0.8) ** 2))  # southern knob: shading
    rng = np.random.default_rng(spec.seed)
    z = z + _smooth(rng.normal(0, 25.0, z.shape), 4)
    slope_proxy = np.hypot(*np.gradient(z, spec.dem_res_m))
    lc = np.full(z.shape, 1, dtype=float)  # 1 = open
    lc[z < 1680] = 2  # forest below synthetic treeline
    lc[np.degrees(np.arctan(slope_proxy)) > 50] = 3  # rock
    return z, lc


def boundary_ring(spec: FixtureSpec) -> list[list[float]]:
    s = spec.size_km
    pts_km = [(0.3, 1.6), (1.6, 0.3), (s - 0.3, 0.3), (s - 0.3, s - 1.6), (s - 1.6, s - 0.3), (0.3, s - 0.3)]
    ring = [[X0 + x * 1000, Y0 + y * 1000] for x, y in pts_km]
    return ring + [ring[0]]


# --------------------------------------------------------------------------- weather

# (start, end, regime, precip mm/h at station, temperature anomaly K)
TRUTH_EVENTS = [
    ("2025-10-08T00", "2025-10-09T12", "storm", 0.6, 1.0),
    ("2025-10-20T06", "2025-10-22T00", "storm", 0.7, -2.0),
    ("2025-10-23T00", "2025-10-30T00", "clear", 0.0, -1.0),
    ("2025-11-05T00", "2025-11-07T12", "storm", 0.9, -3.0),
    ("2025-11-12T00", "2025-11-24T00", "clear", 0.0, -7.0),  # cold clear spell: facets / surface hoar
    ("2025-11-24T06", "2025-11-27T00", "storm", 0.8, -4.0),  # buries it
    ("2025-12-02T12", "2025-12-04T00", "storm", 0.8, 7.0),  # warm storm: rain at lower elevations
    ("2025-12-04T00", "2025-12-12T00", "clear", 0.0, -6.0),  # refreeze, near-crust faceting
    ("2025-12-12T00", "2025-12-19T00", "storm", 0.6, -3.0),
    ("2025-12-22T00", "2026-01-02T00", "clear", 0.0, 2.0),  # mild sunny: sun-exposed melt-freeze
    ("2026-01-03T00", "2026-01-06T00", "storm", 0.7, -2.0),
    ("2026-01-07T00", "2026-01-14T18", "clear", 0.0, -5.0),
    ("2026-01-15T12", "2026-01-16T18", "storm", 0.8, 3.0),  # verifying truth in forecast window
    ("2026-01-16T18", "2026-01-18T00", "clear", 0.0, -3.0),
]
# what the synthetic NWP run issued 2026-01-15T00Z predicted (timing and amount errors)
FORECAST_EVENTS = [
    ("2026-01-15T15", "2026-01-16T21", "storm", 0.7, 2.0),
    ("2026-01-16T21", "2026-01-18T00", "clear", 0.0, -2.0),
]
REGIMES = {  # cloud fraction, RH, wind m/s
    "background": (0.5, 0.75, 3.0),
    "storm": (1.0, 0.95, 7.0),
    "clear": (0.1, 0.62, 2.0),
}


def generate_weather(index: pd.DatetimeIndex, events, spec: FixtureSpec, seed: int, lat: float, lon: float
                     ) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n = len(index)
    cloud = np.full(n, REGIMES["background"][0])
    rh = np.full(n, REGIMES["background"][1])
    wind = np.full(n, REGIMES["background"][2])
    anom = np.zeros(n)
    rate = np.zeros(n)
    for s, e, regime, pr, ta in events:
        m = (index >= pd.Timestamp(s, tz="UTC")) & (index < pd.Timestamp(e, tz="UTC"))
        cloud[m], rh[m], wind[m] = REGIMES[regime]
        anom[m] = ta
        rate[m] = pr
    k = np.ones(7) / 7.0
    anom = np.convolve(np.pad(anom, 3, mode="edge"), k, mode="valid")
    days = (index - pd.Timestamp(spec.history_start)).total_seconds().to_numpy() / 86400.0
    seasonal = 6.0 - 14.0 * days / 106.0
    local_solar_h = (index.hour.to_numpy() + lon / 15.0) % 24
    amp = 1.5 + 5.0 * (1 - cloud)
    diurnal = amp * np.cos(2 * np.pi * (local_solar_h - 15.0) / 24.0)
    noise = np.convolve(rng.normal(0, 0.6, n + 5), np.ones(6) / 6, mode="valid")[:n]
    ta_c = seasonal + anom + diurnal + noise
    rh = np.clip(rh + rng.normal(0, 0.03, n), 0.3, 1.0)
    wind = np.clip(wind * rng.gamma(6.0, 1 / 6.0, n), 0.2, 25.0)
    dw = (250.0 + rng.normal(0, 25.0, n)) % 360
    psum = rate * np.clip(rng.gamma(4.0, 0.25, n), 0.0, None)
    zen, _ = solar.sun_position(index - pd.Timedelta(minutes=30), lat, lon)
    cosz = np.clip(np.cos(np.radians(zen)), 0, None)
    ghi_clear = np.where(cosz > 0.01, 1098.0 * cosz * np.exp(-0.057 / np.maximum(cosz, 0.01)), 0.0)
    iswr = ghi_clear * (1 - 0.75 * cloud**3.4)
    t_k = ta_c + 273.15
    e_hpa = rh * 6.1094 * np.exp(17.625 * ta_c / (ta_c + 243.04))
    eps = np.clip(1.24 * (e_hpa / t_k) ** (1 / 7) * (1 + 0.22 * cloud**2), 0.5, 1.0)
    ilwr = eps * 5.670374419e-8 * t_k**4
    return pd.DataFrame({"ta": t_k, "rh": rh, "vw": wind, "dw": dw, "iswr": iswr, "ilwr": ilwr, "psum": psum},
                        index=index)


def write_fixture(out_dir: Path, spec: FixtureSpec | None = None) -> dict[str, Path]:
    spec = spec or FixtureSpec()
    out_dir = Path(out_dir)
    terr = out_dir / "terrain"
    wx = out_dir / "weather"
    terr.mkdir(parents=True, exist_ok=True)
    wx.mkdir(parents=True, exist_ok=True)
    z, lc = synthetic_dem(spec)
    side = {"crs": CRS, "horizontal_units": "m", "synthetic": True, "note": SYNTH_TAG}
    write_ascii_grid(terr / "SYNTHETIC_dem.asc", z, X0, Y0, spec.dem_res_m,
                     {**side, "vertical_units": "m", "vertical_datum": "synthetic"})
    write_ascii_grid(terr / "SYNTHETIC_landcover.asc", lc, X0, Y0, spec.dem_res_m,
                     {**side, "legend": {"1": "open", "2": "forest", "3": "rock"}}, fmt="%d")
    boundary = {"type": "FeatureCollection", "crs": {"type": "name", "properties": {"name": CRS}},
                "features": [{"type": "Feature", "properties": {"name": "SYNTHETIC demo domain", "note": SYNTH_TAG},
                              "geometry": {"type": "Polygon", "coordinates": [boundary_ring(spec)]}}]}
    (terr / "SYNTHETIC_boundary.geojson").write_text(json.dumps(boundary, indent=1))

    from pyproj import Transformer

    sx, sy = X0 + spec.station_xy_km[0] * 1000, Y0 + spec.station_xy_km[1] * 1000
    lon, lat = Transformer.from_crs(CRS, "EPSG:4326", always_xy=True).transform(sx, sy)
    created = datetime(2026, 9, 30, tzinfo=UTC)  # fixed: fixture content must be reproducible

    hist_idx = pd.date_range(spec.history_start, spec.history_end, freq="h", inclusive="right")
    truth = generate_weather(hist_idx, TRUTH_EVENTS, spec, spec.seed + 1, lat, lon)
    hist_meta = WeatherMeta(
        series_id="SYNTHETIC_valley_station_actuals", kind=WeatherKind.actuals, source="synthetic-generator",
        station_id="SYNTH_VALLEY", lat=lat, lon=lon, source_elevation_m=spec.station_elev_m,
        timestamp_convention="end_of_interval", accumulation_interval_s=3600, availability_latency_s=3600,
        wind_height_m=10.0, met_height_m=2.0,
        provenance=Provenance(source="snowagent.synthetic.fixture", description=SYNTH_TAG, created_utc=created,
                              synthetic=True, notes=["scripted storm/clear regimes; see TRUTH_EVENTS"]))
    write_weather(wx / "SYNTHETIC_actuals.csv", hist_meta, truth)

    init = pd.Timestamp(spec.forecast_init)
    fc_idx = pd.date_range(init, init + pd.Timedelta(hours=spec.forecast_hours), freq="h", inclusive="right")
    fc = generate_weather(fc_idx, FORECAST_EVENTS, spec, spec.seed + 2, lat, lon)
    fc_meta = WeatherMeta(
        series_id=f"SYNTHETIC_nwp_{init:%Y%m%dT%H}Z", kind=WeatherKind.forecast, source="synthetic-generator",
        model="SYNTHETIC-NWP", model_version="0", lat=lat, lon=lon, source_elevation_m=spec.station_elev_m,
        timestamp_convention="end_of_interval", accumulation_interval_s=3600,
        issue_time=init.to_pydatetime(), available_time=pd.Timestamp(spec.forecast_available).to_pydatetime(),
        wind_height_m=10.0, met_height_m=2.0,
        provenance=Provenance(source="snowagent.synthetic.fixture", description=SYNTH_TAG, created_utc=created,
                              synthetic=True, notes=["synthetic forecast with deliberate timing/amount error"]))
    write_weather(wx / f"SYNTHETIC_forecast_{init:%Y%m%dT%H}Z.csv", fc_meta, fc)
    (out_dir / "SYNTHETIC_README.txt").write_text(SYNTH_TAG + "\n")
    return {"dem": terr / "SYNTHETIC_dem.asc", "landcover": terr / "SYNTHETIC_landcover.asc",
            "boundary": terr / "SYNTHETIC_boundary.geojson", "actuals": wx / "SYNTHETIC_actuals.csv",
            "forecast": wx / f"SYNTHETIC_forecast_{init:%Y%m%dT%H}Z.csv"}
