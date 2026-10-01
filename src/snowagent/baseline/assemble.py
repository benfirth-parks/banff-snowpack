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

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from snowagent.ingest.fts360 import parse_station
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


def assemble(plot_id: str, start: str, end: str, cfg_path: Path = Path("config/plot_forcing.yaml"),
             fts_raw: Path = Path("data/raw/fts360"), era5_dir: Path = Path("data/interim/era5"),
             fcfg: ForcingConfig | None = None) -> PlotForcing:
    cfg = yaml.safe_load(Path(cfg_path).read_text())
    p = cfg["plots"][plot_id]
    elevs = cfg["station_elevation_m"]
    fcfg = fcfg or ForcingConfig()
    lapse = fcfg.lapse_rate_k_per_m
    idx = pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"), freq="h")
    notes: list[str] = []

    e5, e5_elev = era5_cell_series(p["lat"], p["lon"], idx, era5_dir)
    e5_ta, e5_rh = _to_elevation(e5["ta"], e5["rh"], p["elevation_m"] - e5_elev, lapse)
    notes.append(f"ERA5 nearest cell surface height {e5_elev:.0f} m; moved {p['elevation_m'] - e5_elev:+.0f} m")

    stations: dict[str, pd.DataFrame] = {}
    for key in {s for v in ("ta", "rh", "psum") for s in p.get(v, [])}:
        d = parse_station(sorted((fts_raw / key).glob("*.csv")))
        stations[key] = d.set_index("time_utc").reindex(idx) if not d.empty else pd.DataFrame(index=idx)

    data = pd.DataFrame(index=idx, columns=["ta", "rh", "vw", "dw", "iswr", "ilwr", "psum"], dtype=float)
    src = pd.DataFrame("missing", index=idx, columns=data.columns)

    # temperature and humidity: stations (moved to plot elevation), then ERA5 with a constant offset
    for var in ("ta", "rh"):
        for key in p.get(var, []):
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
    offset = float((data["ta"][both] - e5_ta[both]).mean()) if both.sum() > 24 * 14 else 0.0
    notes.append(f"ERA5 temperature fill offset {offset:+.2f} K (station minus ERA5 over {int(both.sum())} h)")
    for var, e5v in (("ta", e5_ta + offset), ("rh", e5_rh)):
        take = data[var].isna() & e5v.notna()
        data.loc[take, var] = e5v[take]
        src.loc[take, var] = "era5"

    # precipitation: gauge increments, then ERA5
    for key in p.get("psum", []):
        s = stations[key]
        if "psum_1h_mm" not in s:
            continue
        ok = s["psum_1h_mm_qc"] == "ok"
        val = s["psum_1h_mm"]
        neg = ok & (val < 0)
        notes.append(f"{key}: {int(neg.sum())} negative gauge increments set to 0")
        take = data["psum"].isna() & ok
        data.loc[take, "psum"] = val[take].clip(lower=0)
        src.loc[take, "psum"] = key
    take = data["psum"].isna() & e5["psum"].notna()
    data.loc[take, "psum"] = e5["psum"][take]
    src.loc[take, "psum"] = "era5"

    # wind and radiation: ERA5 only (no plot station measures them)
    for var in ("vw", "dw", "iswr", "ilwr"):
        take = e5[var].notna()
        data.loc[take, var] = e5[var][take]
        src.loc[take, var] = "era5"
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
