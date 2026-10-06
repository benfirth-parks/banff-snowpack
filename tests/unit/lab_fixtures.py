"""Synthetic inputs for the lab's tests, written into a temporary checkout (no real data; ADR-059).

- ``write_era5``: an ERA5 cache box (``data/interim/era5``) of constant fields for the given months.
- ``write_gfs``: an archived GFS run (``archive/forecasts/gfs/gfs_YYYYMMDDHH.csv``) with the archive's 3-hourly
  accumulation/average windows, at the given points.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

ERA5_LAT = np.array([50.75, 51.0, 51.25, 51.5, 51.75])
ERA5_LON = np.array([-116.75, -116.5, -116.25, -116.0, -115.75])


def write_era5(root: Path, months: list[str], ta_k: float = 263.0, precip_mm_h: float = 0.2) -> Path:
    d = Path(root) / "data/interim/era5"
    d.mkdir(parents=True, exist_ok=True)
    shape2 = (len(ERA5_LAT), len(ERA5_LON))
    np.savez(d / "era5_box_z.npz", lat=ERA5_LAT, lon=ERA5_LON, z=np.full(shape2, 2100.0 * 9.80665))
    for ym in months:
        t = pd.date_range(f"{ym}-01", periods=pd.Period(ym).days_in_month * 24, freq="h", tz="UTC")
        n = len(t)
        full = np.ones((n, *shape2))
        np.savez(d / f"era5_box_{ym.replace('-', '')}.npz", time_utc=t.tz_convert(None).values.astype("datetime64[ns]"),
                 lat=ERA5_LAT, lon=ERA5_LON, **{"2t": full * ta_k, "2d": full * (ta_k - 3), "10u": full * 2.0,
                                                "10v": full * 0.0, "sp": full * 78000.0, "tcc": full * 0.5,
                                                "mtpr": full * precip_mm_h / 3600.0, "msdwswrf": full * 150.0,
                                                "msdwlwrf": full * 250.0})
    return d


def write_gfs(root: Path, issued: str, points: list[str], ta_k: float = 265.0, max_lead: int = 72) -> Path:
    d = Path(root) / "archive/forecasts/gfs"
    d.mkdir(parents=True, exist_ok=True)
    run = pd.Timestamp(issued)
    rows = []
    for lead in range(0, max_lead + 1, 3):
        for pt in points:
            r = {"run_utc": run.isoformat(), "lead_h": lead, "point": pt, "pres_pa": 78000.0, "model_elev_m": 2100.0,
                 "tmp2m_k": ta_k + lead / 72, "rh2m_pct": 80.0, "u10_ms": 1.0, "v10_ms": 1.0}
            if lead:
                a = lead - 3  # 3-hour windows: "(L-3)-L hour acc"
                r |= {"apcp_kgm2": 0.3, "apcp_kgm2_desc": f"{a}-{lead} hour acc fcst",
                      "dswrf_wm2": 100.0, "dswrf_wm2_desc": f"{a}-{lead} hour ave fcst",
                      "dlwrf_wm2": 240.0, "dlwrf_wm2_desc": f"{a}-{lead} hour ave fcst"}
            rows.append(r)
    f = d / f"gfs_{run:%Y%m%d%H}.csv"
    pd.DataFrame(rows).to_csv(f, index=False)
    return f


# --------------------------------------------------------------------------------------------- a synthetic lab

SEASON = "2023-2024"
PITS = {  # name -> (profile_id, observed_at, n_layers, extra)
    "prev": ("2022-12-15_bow_summit_syn100", "2022-12-15T19:00:00+00:00", 3, {}),
    "p1": ("2023-12-01_bow_summit_syn101", "2023-12-01T19:00:00+00:00", 3, {}),
    "p2": ("2023-12-20_bow_summit_syn102", "2023-12-20T19:00:00+00:00", 4, {}),
    "p2dup": ("2023-12-20_bow_summit_syn102b", "2023-12-20T19:00:00+00:00", 4,
              {"duplicate_of": "2023-12-20_bow_summit_syn102"}),
    "p3": ("2024-01-10_bow_summit_syn103", "2024-01-10T19:00:00+00:00", 5, {}),
    "flagged": ("2024-01-10_bow_summit_syn104", "2024-01-11T18:00:00+00:00", 5,
                {"review_reasons": ["printed_date_2024-01-01_differs_from_filename_2024-01-11"]}),
    "depth": ("2024-01-25_bow_summit_syn105", "2024-01-25T19:40:00+00:00", 0, {"snow_depth_m": 1.5}),
    "old": ("2014-01-10_bow_summit_syn106", "2014-01-10T19:00:00+00:00", 2, {}),
}
GFS_ISSUE = "2024-01-08T00:00:00+00:00"  # reaches p3 (2024-01-10T19:00Z): as_of of p3's forecast case = 01-08T05:00Z
GFS_SHORT = "2024-01-07T00:00:00+00:00"  # ends 2024-01-10T00:00Z, before p3: the milestone-2 (fixed_horizon) run


def _profile(pid: str, t: str, n: int, extra: dict):
    from snowagent.lab.ingest.mapping import layer_of_concern
    from snowagent.lab.schemas.profile import SnowLayer, SnowProfile

    layers = []
    for i in range(n):
        g = ["PP", "RG", "FC", "MFcr", "DH"][i % 5]
        tag = "121106 Rain Crust" if (i == 1 and pid.endswith("100")) else None
        cls, concern, basis = layer_of_concern(g, None, tag)
        layers.append(SnowLayer(layer_id=f"{pid}_L{i:02d}", profile_id=pid, top_depth_m=0.2 * i,
                                bottom_depth_m=0.2 * (i + 1), grain_primary=g, hardness="1F", critical_class=cls,
                                is_layer_of_concern=concern, concern_basis=basis, date_tag=tag,
                                comment="observer note: Ben's pit" if i == 0 else None, raw={"src": pid}))
    hs = extra.pop("snow_depth_m", 0.2 * n if n else None)
    return SnowProfile(profile_id=pid, site_code="BOW", plot_id="bow_summit", observed_at=t, latitude=51.70946,
                       longitude=-116.4795, elevation_m=2040.0, terrain_class="study_plot", observer_id="obs_X",
                       source_id="synthetic", notes=f"synthetic pit {pid}", snow_depth_m=hs, layers=layers,
                       usable=bool(n), raw={"source_file": f"SYNTHETIC/{pid}.caaml"}, **extra)


def synthetic_weather(start: str = "2023-11-15T00:00:00+00:00", end: str = "2024-02-05T00:00:00+00:00"):
    """Hourly BOW rows in the processed-table layout: station temperature, precipitation and snow depth; radiation
    and pressure ERA5-filled (flagged) every hour."""
    from snowagent.lab.ingest.weather import validate_frame
    from snowagent.lab.schemas.weather import WEATHER_VARIABLES

    idx = pd.date_range(start, end, freq="h")
    n = len(idx)
    df = pd.DataFrame({"site_code": "BOW", "observed_at": idx, "source_id": "plot_stations", "kind": "observed",
                       "issued_at": pd.Series(pd.NaT, index=range(n), dtype="datetime64[ns, UTC]"),
                       "source_recorded_at": pd.Series(pd.NaT, index=range(n), dtype="datetime64[ns, UTC]"),
                       "availability_assumption": "observed_at"})
    station = {"air_temperature_k": 265.0 + 5 * np.sin(np.arange(n) / 24 * 2 * np.pi), "precipitation_mm": 0.1,
               "snow_depth_m": np.linspace(0.0, 1.6, n), "relative_humidity_frac": 0.8}
    era5 = {"shortwave_radiation_wm2": 120.0, "station_pressure_pa": 78000.0}
    for v in WEATHER_VARIABLES:
        if v in station:
            df[v], df[f"{v}_source"], df[f"{v}_qc"] = station[v], "bow_summit", "ok"
        elif v in era5:
            df[v], df[f"{v}_source"], df[f"{v}_qc"] = era5[v], "era5_cell_2244m", "filled"
        else:
            df[v], df[f"{v}_source"], df[f"{v}_qc"] = np.nan, None, "missing"
    df["quality_flag"] = "filled"
    df["provenance_id"] = "import-synthetic"
    validate_frame(df)
    return df


def write_synthetic_lab(data_root: Path, source_root: Path, config) -> dict[str, str]:
    """Processed tables of a synthetic Bow Summit season (and one pit each of two other seasons) plus one archived
    GFS run; returns the pit name -> profile_id map."""
    from snowagent.lab.schemas.observation import Observation
    from snowagent.lab.services.data import init_lab, observations_frame, profiles_frame
    from snowagent.lab.storage.paths import LabPaths
    from snowagent.lab.storage.tables import write_table

    paths = LabPaths(Path(data_root))
    init_lab(paths)
    profiles = [_profile(pid, t, n, dict(extra)) for pid, t, n, extra in PITS.values()]
    obs = [Observation(observation_id=f"{p.profile_id}_T00", site_code="BOW", observed_at=p.observed_at,
                       observation_type="stability_test", profile_id=p.profile_id, source_id="synthetic",
                       provenance_id="import-synthetic",
                       payload={"raw": "ECTN12 on Jan 4 SH", "type": "ECT", "result": "ECTN12", "score": 12.0,
                                "comment": f"Ben dug {p.profile_id}", "layer_date_tag": "Jan 4", "depth_m": 0.3})
           for p in profiles if p.layers]
    pdf, ldf = profiles_frame(profiles, config.season_start)
    write_table(pdf, paths.profiles)
    write_table(ldf, paths.layers)
    write_table(observations_frame(obs), paths.observations)
    write_table(synthetic_weather(), paths.weather)
    write_gfs(source_root, GFS_ISSUE, ["bow_summit_plot", "bow_summit"])
    write_gfs(source_root, GFS_SHORT, ["bow_summit_plot", "bow_summit"])
    write_gfs(source_root, "2023-11-28T00:00:00+00:00", ["sunshine_village_ab_env_plot"])  # no Bow point
    return {k: v[0] for k, v in PITS.items()}


def write_multiseason_lab(data_root: Path, source_root: Path, config,
                          seasons: tuple[str, ...] = ("2021-2022", "2022-2023", "2023-2024")) -> list[str]:
    """Processed tables of several synthetic Bow Summit seasons (three pits each, Dec to Jan, and the season's
    weather, a little warmer each season) plus one archived GFS run; returns the profile ids. Used by the training
    and leave-one-season-out tests (milestone 4)."""
    from snowagent.lab.services.data import init_lab, observations_frame, profiles_frame
    from snowagent.lab.storage.paths import LabPaths
    from snowagent.lab.storage.tables import write_table

    paths = LabPaths(Path(data_root))
    init_lab(paths)
    profiles, weather = [], []
    for i, season in enumerate(seasons):
        y0, y1 = (int(x) for x in season.split("-"))
        for j, (t, n) in enumerate(((f"{y0}-12-01T19:00:00+00:00", 3), (f"{y0}-12-20T19:00:00+00:00", 4),
                                    (f"{y1}-01-10T19:00:00+00:00", 4 + i % 2))):
            profiles.append(_profile(f"{t[:10]}_bow_summit_ms{i}{j}", t, n, {}))
        w = synthetic_weather(f"{y0}-11-15T00:00:00+00:00", f"{y1}-02-05T00:00:00+00:00")
        w["air_temperature_k"] = w["air_temperature_k"] + 1.5 * i
        weather.append(w)
    pdf, ldf = profiles_frame(profiles, config.season_start)
    write_table(pdf, paths.profiles)
    write_table(ldf, paths.layers)
    write_table(observations_frame([]), paths.observations)
    write_table(pd.concat(weather, ignore_index=True), paths.weather)
    write_gfs(source_root, GFS_ISSUE, ["bow_summit_plot", "bow_summit"])
    return [p.profile_id for p in profiles]
