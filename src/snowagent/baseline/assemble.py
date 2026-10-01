"""Hourly SI forcing for a study plot from FTS360 stations with ERA5 filling (baseline, uncorrected).

Priority per variable: listed stations (QC "ok" only) -> ERA5 nearest cell. Every hour records the source of
each variable (``sources`` frame) so verification can be split by source. Nothing is interpolated except the
engine-adapter's own <=3 h rule downstream.

Elevation transfer (station or ERA5 cell -> plot): temperature by the forcing builder's lapse rate, humidity
by conserving dewpoint. ERA5 temperature used as fill is additionally shifted by its mean offset to the
station temperature over the same window (one constant, recorded). Gauge precipitation: hourly increments,
negative increments (evaporation/noise) set to 0 and recorded; no undercatch correction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from snowagent.ingest.fts360 import load_station
from snowagent.spatial_forcing.builder import ForcingConfig, _dewpoint_c, _es

G = 9.80665


@dataclass
class PlotForcing:
    plot_id: str
    data: pd.DataFrame  # ta rh vw dw iswr ilwr psum (SI), UTC end-of-interval
    sources: pd.DataFrame  # same index/columns, source label per value
    notes: list[str] = field(default_factory=list)


def _to_elevation(ta_k: pd.Series, rh: pd.Series | None, dz: float, lapse: float) -> tuple[pd.Series, pd.Series | None]:
    ta_new = ta_k + lapse * dz
    if rh is None:
        return ta_new, None
    tc, tn = ta_k - 273.15, ta_new - 273.15
    td = np.minimum(_dewpoint_c(tc.to_numpy(), rh.to_numpy()), tc.to_numpy())
    return ta_new, pd.Series(np.clip(_es(td) / _es(tn.to_numpy()), 0.05, 1.0), index=ta_k.index)


def era5_cell_series(lat: float, lon: float, idx: pd.DatetimeIndex, era5_dir: Path) -> tuple[pd.DataFrame, float]:
    """Nearest-cell ERA5 hourly series (SI) over ``idx`` and the cell surface height (m)."""
    months = sorted({(t.year, t.month) for t in idx})
    frames = []
    zfile = era5_dir / "era5_box_z.npz"
    z = np.load(zfile)
    i = int(np.argmin(np.abs(z["lat"] - lat)))
    j = int(np.argmin(np.abs(z["lon"] - lon)))
    elev = float(z["z"][i, j]) / G
    for y, m in months:
        f = era5_dir / f"era5_box_{y}{m:02d}.npz"
        if not f.exists():
            continue
        d = np.load(f)
        t = pd.to_datetime(d["time_utc"], utc=True)
        u, v = d["10u"][:, i, j], d["10v"][:, i, j]
        tk, tdk = d["2t"][:, i, j], d["2d"][:, i, j]
        rh = np.clip(_es(tdk - 273.15) / _es(tk - 273.15), 0.05, 1.0)
        frames.append(pd.DataFrame({
            "ta": tk, "rh": rh, "vw": np.hypot(u, v), "dw": (np.degrees(np.arctan2(-u, -v)) + 360) % 360,
            "iswr": np.clip(d["msdwswrf"][:, i, j], 0, None), "ilwr": d["msdwlwrf"][:, i, j],
            "psum": np.clip(d["mtpr"][:, i, j] * 3600.0, 0, None)}, index=t))
    if not frames:
        return pd.DataFrame(index=idx, columns=["ta", "rh", "vw", "dw", "iswr", "ilwr", "psum"], dtype=float), elev
    return pd.concat(frames).sort_index().reindex(idx), elev


def casr_point_series(plot_id: str, idx: pd.DatetimeIndex, casr_dir: Path) -> tuple[pd.DataFrame, float]:
    """CaSR v3.2 nearest-cell hourly series (SI) for a plot (from `snowagent ingest casr`) and the cell height."""
    df = pd.read_csv(casr_dir / f"{plot_id}.csv", parse_dates=["time_utc"]).set_index("time_utc")
    meta = json.loads((casr_dir / f"{plot_id}.json").read_text())
    return df.reindex(idx), float(meta["cell_elevation_m"])


def gauge_plausibility(gauge_h: pd.Series, era5_h: pd.Series, factor: float = 4.0, margin_mm: float = 15.0
                       ) -> tuple[float, pd.DatetimeIndex]:
    """Days on which a weighing gauge reports implausibly much precipitation.

    The gauge's usual catch ratio to ERA5 (median daily ratio on days where both exceed 1 mm) is estimated over
    the window; a day is implausible if gauge > ``factor`` x ratio x ERA5 and > ratio x ERA5 + ``margin_mm``.
    (Verified: flags exactly the 2024-03-28..04-10 Sunshine gauge fault, no day in nine other gauge-seasons.)
    """
    gd = gauge_h.resample("D").sum(min_count=20)
    ed = era5_h.resample("D").sum(min_count=20)
    both = (gd > 1) & (ed > 1)
    ratio = float((gd[both] / ed[both]).median()) if both.sum() >= 20 else 1.0
    bad = (gd > factor * ratio * ed) & (gd > ratio * ed + margin_mm)
    return ratio, gd.index[bad.fillna(False).to_numpy()]


def assemble(plot_id: str, start: str, end: str, cfg_path: Path = Path("config/plot_forcing.yaml"),
             fts_raw: Path = Path("data/raw/fts360"), era5_dir: Path = Path("data/interim/era5"),
             fcfg: ForcingConfig | None = None, era5_only: dict | None = None, reanalysis: str = "era5",
             casr_dir: Path = Path("data/interim/casr")) -> PlotForcing:
    """Station-first forcing with ERA5 fill, or (``era5_only`` = a plot entry of config/era5_transfer.yaml)
    ERA5 alone with the station-derived temperature offset and precipitation catch ratio (ADR-025)."""
    cfg = yaml.safe_load(Path(cfg_path).read_text())
    p = cfg["plots"][plot_id]
    elevs = cfg["station_elevation_m"]
    fcfg = fcfg or ForcingConfig()
    lapse = fcfg.lapse_rate_k_per_m
    idx = pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"), freq="h")
    notes: list[str] = []

    # "e5" is the reanalysis series: ERA5 (default) or CaSR (ADR-028); source labels carry its name
    if reanalysis == "casr":
        e5, e5_elev = casr_point_series(plot_id, idx, casr_dir)
    elif reanalysis == "gfs_day1":  # near-real-time fill for the days ERA5 is not yet published (ADR-033)
        from snowagent.weather.sources import gfs_day1_series

        e5, e5_elev = gfs_day1_series(p["gfs_point"], idx)
    else:
        e5, e5_elev = era5_cell_series(p["lat"], p["lon"], idx, era5_dir)
    rname = reanalysis
    notes.append(f"reanalysis: {rname}")
    e5_ta, e5_rh = _to_elevation(e5["ta"], e5["rh"], p["elevation_m"] - e5_elev, lapse)
    notes.append(f"ERA5 nearest cell surface height {e5_elev:.0f} m; moved {p['elevation_m'] - e5_elev:+.0f} m")

    stations: dict[str, pd.DataFrame] = {}
    for key in set() if era5_only else {s for v in ("ta", "rh", "psum") for s in p.get(v, [])}:
        d = load_station(key, fts_raw)
        stations[key] = d.set_index("time_utc").reindex(idx) if not d.empty else pd.DataFrame(index=idx)

    data = pd.DataFrame(index=idx, columns=["ta", "rh", "vw", "dw", "iswr", "ilwr", "psum"], dtype=float)
    src = pd.DataFrame("missing", index=idx, columns=data.columns)

    # temperature and humidity: stations (moved to plot elevation), then ERA5 with a constant offset
    for var in ("ta", "rh"):
        for key in [] if era5_only else p.get(var, []):
            s = stations[key]
            col, qc = ("ta_k", "ta_k_qc") if var == "ta" else ("rh_frac", "rh_frac_qc")
            if col not in s:
                continue
            ok = s[qc] == "ok"
            dz = p["elevation_m"] - elevs[key]
            ta_s = s["ta_k"].where(s.get("ta_k_qc", pd.Series("ok", index=idx)) == "ok")
            if var == "ta":
                val = _to_elevation(ta_s, None, dz, lapse)[0]
            else:
                ta_fill = ta_s.fillna(data["ta"])
                val = _to_elevation(ta_fill, s["rh_frac"].clip(0, 1), dz, lapse)[1]
            take = data[var].isna() & ok & val.notna()
            data.loc[take, var] = val[take]
            src.loc[take, var] = key
    both = data["ta"].notna() & e5_ta.notna()
    if era5_only:
        from snowagent.baseline.era5_transfer import offsets_for

        offset = offsets_for(idx, e5["psum"], era5_only)
        notes.append(f"ERA5-only ({era5_only.get('method', 'constant')} transfer): temperature offset mean "
                     f"{float(offset.mean()):+.2f} K")
    else:
        offset = float((data["ta"][both] - e5_ta[both]).mean()) if both.sum() > 24 * 14 else 0.0
        notes.append(f"ERA5 temperature fill offset {offset:+.2f} K (station minus ERA5 over {int(both.sum())} h)")
    for var, e5v in (("ta", e5_ta + offset), ("rh", e5_rh)):
        take = data[var].isna() & e5v.notna()
        data.loc[take, var] = e5v[take]
        src.loc[take, var] = rname

    # precipitation: gauge increments (with a daily plausibility check against ERA5), then ERA5
    if era5_only:
        if "psum_ratio_cold" in era5_only:
            r = pd.Series(np.where(data["ta"] < 273.15, era5_only["psum_ratio_cold"], era5_only["psum_ratio_warm"]),
                          index=idx)
            notes.append(f"ERA5-only: precipitation = ERA5 x {era5_only['psum_ratio_cold']:.3f} (cold hours) / "
                         f"{era5_only['psum_ratio_warm']:.3f} (warm hours)")
        else:
            r = pd.Series(float(era5_only["psum_ratio"]), index=idx)
            notes.append(f"ERA5-only: precipitation = ERA5 x {float(era5_only['psum_ratio']):.3f} (gauge catch ratio)")
        take = e5["psum"].notna()
        data.loc[take, "psum"] = e5["psum"][take] * r[take]
        src.loc[take, "psum"] = f"{rname}_x_gauge_ratio"
    for key in [] if era5_only else p.get("psum", []):
        s = stations[key]
        if "psum_1h_mm" not in s:
            continue
        ok = s["psum_1h_mm_qc"] == "ok"
        val = s["psum_1h_mm"].where(ok).clip(lower=0)
        neg = ok & (s["psum_1h_mm"] < 0)
        notes.append(f"{key}: {int(neg.sum())} negative gauge increments set to 0")
        ratio, bad_days = gauge_plausibility(val, e5["psum"])
        bad_hours = val.index.normalize().isin(bad_days)
        if len(bad_days):
            notes.append(f"{key}: {len(bad_days)} implausible gauge days (> 4x usual gauge/ERA5 ratio {ratio:.2f} and "
                         f"> +15 mm) replaced by ERA5 x {ratio:.2f}: {[str(d.date()) for d in bad_days]}")
        take = data["psum"].isna() & ok & ~bad_hours
        data.loc[take, "psum"] = val[take]
        src.loc[take, "psum"] = key
        fix = data["psum"].isna() & bad_hours & e5["psum"].notna()
        data.loc[fix, "psum"] = e5["psum"][fix] * ratio
        src.loc[fix, "psum"] = f"era5_x_{key}_ratio"
    take = data["psum"].isna() & e5["psum"].notna()
    data.loc[take, "psum"] = e5["psum"][take]
    src.loc[take, "psum"] = rname

    # wind and radiation: ERA5 only (no plot station measures them)
    for var in ("vw", "dw", "iswr", "ilwr"):
        take = e5[var].notna()
        data.loc[take, var] = e5[var][take]
        src.loc[take, var] = rname
    # incoming longwave belongs to ERA5's own (cell-elevation, colder) air: rescale emission to the plot air
    # temperature actually used, ILWR * (Ta_plot / Ta_cell)^4 (emissivity kept)
    ratio = (data["ta"] / e5["ta"]) ** 4
    ok = ratio.notna()
    data.loc[ok, "ilwr"] = data["ilwr"][ok] * ratio[ok]
    notes.append(f"ERA5 ILWR rescaled to plot air temperature: mean factor {float(ratio[ok].mean()):.3f}")
    notes.append("wind and radiation from ERA5 nearest cell (no plot measurement); wind not downscaled")
    return PlotForcing(plot_id, data, src, notes)


def source_summary(pf: PlotForcing) -> dict:
    return {v: pf.sources[v].value_counts(normalize=True).round(3).to_dict() for v in pf.sources.columns}
