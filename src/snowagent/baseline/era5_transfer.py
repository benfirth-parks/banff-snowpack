"""Station-to-ERA5 transfer for seasons without station records (ADR-025).

Before 2021-05 the study plots have no hourly station data, so the forcing is ERA5 alone. Transfer parameters
per plot are estimated from seasons where both exist (2021-26) and applied unchanged to earlier seasons.

Two methods are kept so they can be compared by leave-one-season-out (ADR-025):
- ``constant``: one temperature offset (station minus ERA5 at the plot elevation, all hours) and one
  precipitation catch ratio (gauge total / ERA5 total over gauge-supplied hours);
- ``phase``: temperature offsets per calendar month, separately for ERA5-wet hours (ERA5 precipitation
  > WET_MM_H) and dry hours; precipitation ratios separately for cold and warm hours, where "cold" means the
  transferred temperature (ERA5 at the plot + offset) is below 0 C. Every input of the classification is
  available from ERA5 itself, so the method can be applied where no station exists.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from snowagent.baseline.assemble import _to_elevation, assemble, casr_point_series, era5_cell_series
from snowagent.spatial_forcing.builder import ForcingConfig

WET_MM_H = 0.2
T0 = 273.15


def season_frame(plot: str, year: int, cfg: dict, era5_dir: Path = Path("data/interim/era5"),
                 reanalysis: str = "era5") -> pd.DataFrame:
    """Hourly station-vs-ERA5 pairs for one season (station temperature hours and gauge hours flagged)."""
    p = cfg["plots"][plot]
    start = pd.Timestamp(f"{year}-{cfg['season_start']}", tz="UTC")
    end = pd.Timestamp(f"{year + 1}-{cfg['season_end']}", tz="UTC")
    pf = assemble(plot, str(start), str(end), era5_dir=era5_dir, reanalysis=reanalysis)
    if reanalysis == "casr":
        e5, e5_elev = casr_point_series(plot, pf.data.index, Path("data/interim/casr"))
    else:
        e5, e5_elev = era5_cell_series(p["lat"], p["lon"], pf.data.index, era5_dir)
    e5_ta, _ = _to_elevation(e5["ta"], e5["rh"], p["elevation_m"] - e5_elev, ForcingConfig().lapse_rate_k_per_m)
    return pd.DataFrame({
        "season": year, "month": pf.data.index.month, "e5_ta": e5_ta, "e5_psum": e5["psum"],
        "st_ta": pf.data["ta"].where(pf.sources["ta"].isin(p.get("ta", []))),
        "gauge": pf.data["psum"].where(pf.sources["psum"].isin(p.get("psum", []))),
    }, index=pf.data.index)


def fit(frames: pd.DataFrame, method: str) -> dict:
    """Transfer parameters from hourly frames (one or more calibration seasons)."""
    f = frames.dropna(subset=["e5_ta", "e5_psum"])
    t = f.dropna(subset=["st_ta"])
    g = f.dropna(subset=["gauge"])
    if method == "constant":
        return {"method": "constant", "ta_offset_k": round(float((t.st_ta - t.e5_ta).mean()), 2),
                "psum_ratio": round(float(g.gauge.sum() / g.e5_psum.sum()), 3)}
    wet = t.e5_psum > WET_MM_H
    off = {}
    for m in sorted(set(f.month)):
        row = {}
        for name, sel in (("wet", wet), ("dry", ~wet)):
            d = t[(t.month == m) & sel]
            row[name] = round(float((d.st_ta - d.e5_ta).mean()), 2) if len(d) >= 48 else None
        if row["wet"] is None:
            row["wet"] = row["dry"]
        off[int(m)] = row
    cold = _transferred_ta(g, off) < T0
    return {"method": "phase", "wet_mm_h": WET_MM_H, "ta_offset_k_by_month": off,
            "psum_ratio_cold": round(float(g.gauge[cold].sum() / g.e5_psum[cold].sum()), 3),
            "psum_ratio_warm": round(float(g.gauge[~cold].sum() / g.e5_psum[~cold].sum()), 3)}


def _transferred_ta(f: pd.DataFrame, off: dict) -> pd.Series:
    wet = f.e5_psum > WET_MM_H
    o = [off[int(m)]["wet" if w else "dry"] for m, w in zip(f.month, wet, strict=True)]
    return f.e5_ta + np.array(o, dtype=float)


def offsets_for(index: pd.DatetimeIndex, e5_psum: pd.Series, transfer: dict) -> pd.Series:
    """Per-hour temperature offset for an ERA5-only season (used by the assembly)."""
    if "ta_offset_k_by_month" not in transfer:
        return pd.Series(float(transfer["ta_offset_k"]), index=index)
    off = {int(k): v for k, v in transfer["ta_offset_k_by_month"].items()}
    wet = (e5_psum > transfer.get("wet_mm_h", WET_MM_H)).to_numpy()
    vals = [off[m]["wet" if w else "dry"] if m in off else np.nan for m, w in zip(index.month, wet, strict=True)]
    return pd.Series(np.array(vals, dtype=float), index=index)


def derive(plots: list[str], seasons: list[int], method: str = "phase",
           cfg_path: Path = Path("config/plot_forcing.yaml"), reanalysis: str = "era5") -> dict:
    cfg = yaml.safe_load(Path(cfg_path).read_text())
    out: dict = {}
    for plot in plots:
        frames = {y: season_frame(plot, y, cfg, reanalysis=reanalysis) for y in seasons}
        out[plot] = fit(pd.concat(frames.values()), method)
        out[plot]["per_season"] = {f"{y}-{y + 1}": {k: v for k, v in fit(fr, "constant").items() if k != "method"}
                                   for y, fr in frames.items()}
    return out


def write(path: Path, transfer: dict, seasons: list[int]) -> None:
    header = (f"# Generated by `snowagent era5-transfer` from seasons {seasons[0]}-{seasons[-1] + 1} (ADR-025).\n"
              "# ERA5-only forcing (seasons without station data); see baseline/era5_transfer.py for the methods.\n"
              "# psum_factor from plot_forcing.yaml is applied on top with --corrected.\n")
    path.write_text(header + yaml.safe_dump({"calibration_seasons": [f"{y}-{y + 1}" for y in seasons],
                                             "plots": transfer}, sort_keys=False))
