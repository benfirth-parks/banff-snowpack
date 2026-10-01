"""Forecast hindcast: how well would the system have predicted each observed pit 1-3 days ahead?

For a pit at time T and a lead of k days: the issue time I is 00 UTC k days before T's date. The snowpack is
built from the season start to I with ACTUALS (station + ERA5, adopted corrections) - the nowcast state - and
then run from I to T with ONLY the GFS forecast issued at I (no information after I is used; the 6 h before I
come from actuals, as the engine needs a precipitation lead-in). The forecast profile at T is compared with
the pit and with the actuals-driven profile at T (which isolates the forecast's contribution to error).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import yaml

from snowagent.baseline.assemble import assemble
from snowagent.baseline.evaluate import observed_at_plot
from snowagent.baseline.run import model_profile_as_observed, plot_unit, profile_at
from snowagent.engine import snowpack as sp
from snowagent.engine.column import prepare_and_run
from snowagent.forecast.gfs_point import gfs_hourly
from snowagent.obs.agreement import compare_profiles
from snowagent.spatial_forcing.builder import ForcingConfig, build_unit_forcing


def _actuals(plot: str, p: dict, start: pd.Timestamp, end: pd.Timestamp, corrected: bool) -> pd.DataFrame:
    pf = assemble(plot, str(start - pd.Timedelta(hours=6)), str(end))
    if corrected:
        pf.data["psum"] = pf.data["psum"] * p.get("psum_factor", 1.0)
    return pf.data


def hindcast_pit(plot: str, pit: dict, lead_days: int, work: Path, gfs_dir: Path = Path("archive/forecasts/gfs"),
                 corrected: bool = True) -> dict:
    cfg = yaml.safe_load(Path("config/plot_forcing.yaml").read_text())
    p = cfg["plots"][plot]
    unit = plot_unit(plot, p["lat"], p["lon"], p["elevation_m"])
    fcfg = ForcingConfig()
    engine, settings = sp.find_engine(), sp.EngineSettings()
    T = pd.Timestamp(pit["obs_time_utc"])
    issue = T.normalize() - pd.Timedelta(days=lead_days)
    gfile = gfs_dir / f"gfs_{issue:%Y%m%d}00.csv"
    if not gfile.exists():
        return {"profile_id": pit["profile_id"], "lead_days": lead_days, "skipped": f"no GFS run {issue:%Y-%m-%d}"}
    season_year = T.year if T.month >= 8 else T.year - 1
    start = pd.Timestamp(f"{season_year}-{cfg['season_start']}", tz="UTC")
    tag = f"{plot}_{pit['profile_id'][-6:]}_L{lead_days}"

    # 1) nowcast state at the issue time from actuals
    act = _actuals(plot, p, start, T + pd.Timedelta(hours=1), corrected)
    uf_act = build_unit_forcing(act, p["lat"], p["lon"], p["elevation_m"], unit, fcfg)
    now = prepare_and_run(engine, settings, work / f"{tag}_nowcast", unit, uf_act.smet[uf_act.smet.index <= issue],
                          issue.to_pydatetime(), snowfree_start=start.to_pydatetime())
    # 2) actuals continuation to T (reference: same state, true weather)
    ref = prepare_and_run(engine, settings, work / f"{tag}_actuals", unit,
                          uf_act.smet[uf_act.smet.index >= issue - pd.Timedelta(hours=6)], T.ceil("h").to_pydatetime(),
                          initial_sno=now.sno)
    # 3) forecast continuation to T (GFS issued at I only)
    gdf = pd.read_csv(gfile)
    if p["gfs_point"] not in set(gdf["point"]):
        return {"profile_id": pit["profile_id"], "lead_days": lead_days,
                "skipped": f"GFS run {issue:%Y-%m-%d} has no point {p['gfs_point']}"}
    g, gelev = gfs_hourly(gdf, p["gfs_point"])
    g = g[g.index <= T.ceil("h")]
    if g.index[-1] < T.floor("h"):
        return {"profile_id": pit["profile_id"], "lead_days": lead_days, "skipped": "GFS run shorter than lead"}
    if corrected:
        g["psum"] = g["psum"] * p.get("psum_factor", 1.0)
    uf_fc = build_unit_forcing(g, p["lat"], p["lon"], gelev, unit, fcfg)
    lead_in = uf_act.smet[(uf_act.smet.index >= issue - pd.Timedelta(hours=6)) & (uf_act.smet.index <= issue)]
    fc_forcing = pd.concat([lead_in, uf_fc.smet[uf_fc.smet.index > issue]])
    fc = prepare_and_run(engine, settings, work / f"{tag}_forecast", unit, fc_forcing, T.ceil("h").to_pydatetime(),
                         initial_sno=now.sno)

    out = {"plot": plot, "profile_id": pit["profile_id"], "obs_time_utc": str(T), "issue_utc": str(issue),
           "lead_days": lead_days, "gfs_grid_elev_m": round(gelev)}
    for name, run in (("forecast", fc), ("actuals", ref)):
        _h, profiles = sp.parse_pro(run.pro)
        t, layers, _d = profile_at(profiles, T)
        model = model_profile_as_observed(layers)
        out[name] = compare_profiles(pit, model)
        out[name]["model_hs_cm"] = model["hs_cm"]
    out["forecast_vs_actuals"] = compare_profiles(
        model_profile_as_observed(profile_at(sp.parse_pro(ref.pro)[1], T)[1]),
        model_profile_as_observed(profile_at(sp.parse_pro(fc.pro)[1], T)[1]))
    return out


def run_hindcast(plots: list[str], leads: list[int], observed: Path, work: Path, out: Path,
                 workers: int = 6) -> list[dict]:
    from concurrent.futures import ProcessPoolExecutor

    jobs = []
    for plot in plots:
        for pit in observed_at_plot(observed, plot, pd.Timestamp("2021-11-01", tz="UTC"),
                                    pd.Timestamp("2026-05-01", tz="UTC")):
            if pd.Timestamp(pit["obs_time_utc"]).month in (11, 12, 1, 2, 3, 4):
                jobs += [(plot, pit, k) for k in leads]
    with ProcessPoolExecutor(workers) as ex:
        res = list(ex.map(_job, [(a, b, c, str(work)) for a, b, c in jobs]))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=1, default=str))
    return res


def _job(args):
    """One pit-lead. Completed results are cached in ``work/results`` so an interrupted hindcast resumes;
    skips and errors are not cached (the GFS archive grows, fixes land). Clear the cache when the model changes."""
    plot, pit, k, work = args
    cache = Path(work) / "results" / f"{pit['profile_id']}_L{k}.json"
    if cache.exists():
        return json.loads(cache.read_text())
    try:
        res = hindcast_pit(plot, pit, k, Path(work))
    except Exception as exc:  # noqa: BLE001 - one failed pit must not stop the test
        return {"plot": plot, "profile_id": pit["profile_id"], "lead_days": k,
                "error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    if "forecast" in res:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(res, default=str))
    return res
