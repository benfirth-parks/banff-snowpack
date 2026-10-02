import json, sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
import pandas as pd
from snowagent.web.build import _cfg, season_forcing
from snowagent.baseline.run import plot_unit
from snowagent.forecast.gfs_point import gfs_hourly
from snowagent.spatial_forcing.builder import ForcingConfig, build_unit_forcing
from snowagent.weather.sources import gfs_run_path

def season(args):
    plot, y = args
    p = _cfg()["plots"][plot]
    pf, start, end, mode = season_forcing(plot, y)
    obs = pf.data
    gauge = ~pf.sources["psum"].astype(str).str.contains("era5|gfs|casr", case=False)
    unit = plot_unit(plot, p["lat"], p["lon"], p["elevation_m"])
    rows = []
    for run in pd.date_range(f"{y}-11-01", f"{y+1}-04-30", freq="D", tz="UTC"):
        f = gfs_run_path(run)
        if not f.exists(): continue
        try:
            g, ge = gfs_hourly(pd.read_csv(f), p["gfs_point"])
        except (KeyError, IndexError):
            continue
        g = g[g.index <= run + pd.Timedelta(hours=72)].dropna()
        if len(g) < 72: continue
        uf = build_unit_forcing(g, p["lat"], p["lon"], ge, unit, ForcingConfig()).smet
        for k in range(3):
            w = (uf.index > run + pd.Timedelta(hours=24*k)) & (uf.index <= run + pd.Timedelta(hours=24*(k+1)))
            idx = uf.index[w]
            o = obs.reindex(idx)
            if o["psum"].isna().any() or o["ta"].isna().any(): continue
            gok = bool(gauge.reindex(idx).fillna(False).all()) if gauge is not None else None
            rows.append({"plot": plot, "season": y, "run": run.isoformat(), "day": k+1,
                         "p_gfs": float(uf.loc[idx, "PSUM"].sum()), "p_obs": float(o["psum"].sum()), "gauge_ok": gok,
                         "t_gfs": float(uf.loc[idx, "TA"].mean() - 273.15), "t_obs": float(o["ta"].mean() - 273.15)})
    return rows

if __name__ == "__main__":
    jobs = [(p, y) for p in ("goats_eye", "simpson", "bow_summit") for y in range(2021, 2026)]
    with ProcessPoolExecutor(4) as ex:
        rows = [r for rs in ex.map(season, jobs) for r in rs]
    Path("artifacts/gfs").mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv("artifacts/gfs/gfs_vs_obs_by_lead.csv", index=False)
    print(len(rows))
