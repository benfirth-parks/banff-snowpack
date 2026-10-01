"""Data for the site tool (banff-snowpack.netlify.app): simulated profiles at the three study plots, the
weather that produced them, archived GFS forecasts, and the observed pits with comparison scores.

For each plot and season:
- nowcast: SNOWPACK from a snow-free 15 September with measured station weather (ERA5 fill for unmeasured
  variables; from 2015-16 at Goat's Eye and Simpson, 2016-17 at Bow Summit) or ERA5 with the station transfer
  (older seasons, ADR-025); profiles every 6 h (daily at 18 UTC for ERA5 seasons) and a restart state every day
  at 00 UTC;
- forecast (2021-22 onward, Nov-Apr): from each day's 00 UTC state, 72 h on that day's 00 UTC GFS run only
  (the 6 h before come from the nowcast forcing as precipitation lead-in); profiles every 6 h. The initial
  state uses measured weather up to the issue time (ERA5 fill for wind/radiation, i.e. a reanalysis not yet
  published then); the strict real-time chain is the Phase 2 pipeline (ADR-033);
- pits within the season: layers, tests, and scores against the nowcast and the forecasts valid at the pit.

Everything is EXPERIMENTAL structure prediction, not avalanche guidance.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import shutil
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd
import yaml

from snowagent.contracts import EXPERIMENTAL_LABEL

SITES = {"goats_eye": "Sunshine Village - Goat's Eye", "simpson": "Simpson", "bow_summit": "Bow Summit"}
# season start years with measured station forcing (ADR-030, ADR-034, ADR-035): Goat's Eye and Simpson from
# 2015-16 (Sunshine gauge from Aug 2015); Bow Summit from 2016-17 (its gauge starts 22 Mar 2016)
STATION_SEASONS = {"goats_eye": range(2015, 2026), "simpson": range(2015, 2026), "bow_summit": range(2016, 2026)}
FORECAST_SEASONS = range(2021, 2026)  # archived GFS runs (Nov-Apr)
FORECAST_MONTHS = (11, 12, 1, 2, 3, 4)
FC_HOURS = 72


def _fc_settings():  # engine settings of every forecast run (6-hourly profiles)
    from snowagent.engine import snowpack as sp

    return sp.EngineSettings(prof_days_between=0.25)
OBSERVED = Path("data/interim/obs/observed_profiles.jsonl")


def _forcing_hash(run_dir: Path) -> str:
    """sha256 (16 hex) of the SMET forcing file(s) the engine read in ``run_dir``."""
    h = hashlib.sha256()
    for f in sorted((Path(run_dir) / "input").glob("*.smet")):
        h.update(f.read_bytes())
    return h.hexdigest()[:16]


# ------------------------------------------------------------------------------------------------ layers
def web_layers(layers, hardness_tol: float = 0.5) -> list[list]:
    """Engine elements -> display layers (top first): adjacent elements with the same grain form and hand
    hardness within ``hardness_tol`` are merged (thickness-weighted density, temperature, liquid water).

    Row: [top_cm, bottom_cm, grain_form, hardness, density, temp_c, lwc_pct, flags] with flags bit 1 = crust,
    bit 2 = candidate weak layer (grain-form flag only).
    """
    rows: list[dict] = []
    for ly in sorted(layers, key=lambda x: -x.top_vertical_m):
        r = {"top": ly.top_vertical_m * 100, "bot": ly.bottom_vertical_m * 100, "g": ly.grain_form_primary or "",
             "h": ly.hand_hardness_index, "rho": ly.density_kg_m3, "T": ly.temperature_c,
             "w": ly.lwc_vol_frac * 100, "f": (1 if ly.is_crust else 0) | (2 if ly.is_candidate_weak_layer else 0)}
        m = rows[-1] if rows else None
        if m and m["g"] == r["g"] and m["f"] == r["f"] and (m["h"] is None or r["h"] is None
                                                             or abs(m["h"] - r["h"]) <= hardness_tol):
            tm, tr = m["top"] - m["bot"], r["top"] - r["bot"]
            if tm + tr > 0:
                for k in ("rho", "T", "w") + (("h",) if m["h"] is not None and r["h"] is not None else ()):
                    m[k] = (m[k] * tm + r[k] * tr) / (tm + tr)
            m["bot"] = r["bot"]
        else:
            rows.append(r)
    return [[round(r["top"], 1), round(r["bot"], 1), r["g"], None if r["h"] is None else round(r["h"], 2),
             round(r["rho"]), round(r["T"], 1), round(r["w"], 1), r["f"]] for r in rows]


def _as_observed(rows: list[list]) -> dict:
    """Display layers -> the observed-profile layout used by obs.agreement / DTW."""
    out = [{"top_cm": r[0], "bottom_cm": r[1], "grain_form": r[2] or None, "grain_class": (r[2] or "")[:2] or None,
            "hardness_index": r[3], "density_kg_m3": r[4]} for r in rows]
    return {"layers": out, "hs_cm": rows[0][0] if rows else 0.0, "height_reference": "height_above_ground",
            "temperatures": []}


def _profiles(pro_path: Path, slope_deg: float = 0.0, times: set | None = None,
              skipped: list | None = None) -> list[dict]:
    """Engine profiles for display. A profile that fails the layer contract (e.g. a vanishing 1 cm residue
    reported above 0.5 degC) is left out and listed in ``skipped`` with the reason, never altered."""
    from pydantic import ValidationError

    from snowagent.engine import snowpack as sp
    from snowagent.engine.profiles import convert_profile

    _h, raw = sp.parse_pro(pro_path)
    out = []
    for p in raw:
        if times is not None and p.time not in times:
            continue
        try:
            layers, diag, _n, _i = convert_profile(p, slope_deg, "plot")
        except (ValidationError, ValueError) as exc:
            if skipped is None:
                raise
            e = exc.errors()[0] if isinstance(exc, ValidationError) else None
            why = f"{'.'.join(map(str, e['loc']))}: {e['msg']} (got {e.get('input')})" if e else str(exc)[:120]
            skipped.append({"t": p.time.strftime("%Y-%m-%dT%H"), "reason": why})
            continue
        out.append({"t": p.time.strftime("%Y-%m-%dT%H"), "hs": round(diag.hs_vertical_m * 100, 1),
                    "swe": round(diag.swe_kg_m2_per_horizontal_area), "L": web_layers(layers)})
    return out


# ------------------------------------------------------------------------------------------------ forcing
def _cfg() -> dict:
    return yaml.safe_load(Path("config/plot_forcing.yaml").read_text())


def season_forcing(plot: str, y: int):
    from snowagent.baseline.assemble import assemble

    cfg = _cfg()
    p = cfg["plots"][plot]
    mode = "station" if y in STATION_SEASONS[plot] else "era5"
    transfer = None
    if mode == "era5":
        transfer = yaml.safe_load(Path("config/era5_transfer.yaml").read_text())["plots"][plot]
    start = pd.Timestamp(f"{y}-{cfg['season_start']}", tz="UTC")
    end = pd.Timestamp(f"{y + 1}-{cfg['season_end']}", tz="UTC")
    pf = assemble(plot, str(start - pd.Timedelta(hours=6)), str(end), era5_only=transfer)
    complete = pf.data.notna().all(axis=1)
    if not complete.all():  # e.g. reanalysis not yet published for the last weeks
        end = (complete[~complete].index[0] - pd.Timedelta(hours=1)).floor("D")
        pf.data, pf.sources = pf.data[:end], pf.sources[:end]
    if p.get("psum_factor", 1.0) != 1.0:
        pf.data["psum"] = pf.data["psum"] * p["psum_factor"]
        pf.notes.append(f"precipitation x {p['psum_factor']} (ADR-024)")
    return pf, start, end, mode


# ------------------------------------------------------------------------------------------------ forecasts
def _forecast_one(args) -> dict | None:
    plot, issue_iso, sno, lead_csv, work = args
    from snowagent.baseline.run import plot_unit
    from snowagent.engine import snowpack as sp
    from snowagent.engine.column import prepare_and_run
    from snowagent.forecast.gfs_point import gfs_hourly
    from snowagent.spatial_forcing.builder import ForcingConfig, build_unit_forcing
    from snowagent.weather.sources import gfs_run_path

    p = _cfg()["plots"][plot]
    issue = pd.Timestamp(issue_iso)
    f = gfs_run_path(issue)
    if not f.exists():
        return None
    try:
        g, gelev = gfs_hourly(pd.read_csv(f), p["gfs_point"])
    except (KeyError, IndexError):
        return None
    g = g[g.index <= issue + pd.Timedelta(hours=FC_HOURS)].dropna()
    if len(g) < FC_HOURS:
        return None
    # raw GFS: the plot precipitation factor (ADR-024) corrects the station gauge and was not tested on GFS
    unit = plot_unit(plot, p["lat"], p["lon"], p["elevation_m"])
    uf = build_unit_forcing(g, p["lat"], p["lon"], gelev, unit, ForcingConfig())
    lead = pd.read_csv(lead_csv, index_col=0, parse_dates=True)
    lead = lead[(lead.index >= issue - pd.Timedelta(hours=6)) & (lead.index <= issue)]
    forcing = pd.concat([lead, uf.smet[uf.smet.index > issue]])
    rd = Path(work) / f"fc_{issue:%Y%m%d}"
    try:
        out = prepare_and_run(sp.find_engine(), _fc_settings(), rd, unit, forcing,
                              (issue + pd.Timedelta(hours=FC_HOURS)).to_pydatetime(), initial_sno=Path(sno))
    except Exception as exc:  # noqa: BLE001 - one failed day is recorded, the season continues
        return {"issue": issue.strftime("%Y-%m-%dT%H"), "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
    times = {issue + pd.Timedelta(hours=h) for h in range(6, FC_HOURS + 1, 6)}
    skipped: list[dict] = []
    prof = _profiles(out.pro, 0.0, times, skipped)
    fhash = _forcing_hash(out.run_dir)
    shutil.rmtree(rd, ignore_errors=True)
    wx = uf.smet[uf.smet.index > issue]  # the forcing the engine received, at the plot (not raw GFS)
    return {"issue": issue.strftime("%Y-%m-%dT%H"), "gfs_surface_m": round(gelev), "forcing_hash": fhash,
            **({"numerical_retry": out.extra["numerical_retry"]} if "numerical_retry" in out.extra else {}),
            **({"skipped_profiles": skipped} if skipped else {}), "P": prof, "wx": {"ta": (wx.TA - 273.15).round(1).tolist(), "rh": (wx.RH * 100).round().tolist(),
                              "vw": wx.VW.round(1).tolist(), "iswr": wx.ISWR.round().tolist(),
                              "psum": wx.PSUM.round(2).tolist()}}


# ------------------------------------------------------------------------------------------------ pits
def _pit_record(o: dict) -> dict:
    lay = [[ly.get("top_cm"), ly.get("bottom_cm"), ly.get("grain_form"), ly.get("hardness_index"),
            ly.get("density_kg_m3")] for ly in o.get("layers", [])]
    tests = [{k: t.get(k) for k in ("type", "result", "score", "fracture_character", "height_cm")}
             for t in o.get("tests", [])]
    temps = [[t.get("height_cm"), t.get("t_c")] for t in o.get("temperatures", []) if isinstance(t, dict)]
    return {"id": o["profile_id"], "t": pd.Timestamp(o["obs_time_utc"]).strftime("%Y-%m-%dT%H:%M"),
            "hs": o.get("hs_cm"), "L": lay, "tests": tests, "temps": temps,
            "source": "exact" if not o["provenance"].get("method", "").startswith("transcription") else "transcribed"}


def _score(pit: dict, rows: list[list]) -> dict:
    from snowagent.obs.agreement import compare_profiles

    c = compare_profiles(pit, _as_observed(rows))
    keep = ("hs_diff_cm", "grain_class_agreement", "hardness_mae_index", "hardness_bias_index", "boundary_f1")
    return {k: (None if c.get(k) is None else round(float(c[k]), 3)) for k in keep}


# ------------------------------------------------------------------------------------------------ season
def build_season(plot: str, y: int, out_dir: Path, work: Path, workers: int = 1, max_issues: int | None = None
                 ) -> dict:
    from snowagent.baseline.assemble import source_summary
    from snowagent.baseline.evaluate import observed_at_plot
    from snowagent.baseline.run import plot_unit, run_season
    from snowagent.engine import snowpack as sp
    from snowagent.ingest.fts360 import load_station
    from snowagent.spatial_forcing.builder import ForcingConfig, build_unit_forcing

    cfg = _cfg()
    p = cfg["plots"][plot]
    pf, start, end, mode = season_forcing(plot, y)
    forecasts = mode == "station" and y in FORECAST_SEASONS
    unit = plot_unit(plot, p["lat"], p["lon"], p["elevation_m"])
    settings = sp.EngineSettings(prof_days_between=0.25, snow_days_between=1.0 if forecasts else 3650.0,
                                 first_backup=0.0 if forecasts else 400.0)
    swork = Path(work) / f"{plot}_{y}"
    shutil.rmtree(swork, ignore_errors=True)
    r = run_season(pf, unit, start, end, swork, settings=settings)
    out = r["outputs"]
    every = None if mode == "station" else {t for t in pd.date_range(start, end, freq="D") + pd.Timedelta(hours=18)}
    skipped: list[dict] = []
    nowcast = _profiles(out.pro, 0.0, every, skipped)
    uf = build_unit_forcing(pf.data, p["lat"], p["lon"], p["elevation_m"], unit, ForcingConfig())
    sm = uf.smet
    hourly = {"t0": sm.index[0].strftime("%Y-%m-%dT%H"), "ta": (sm.TA - 273.15).round(1).tolist(),
              "rh": (sm.RH * 100).round().tolist(), "vw": sm.VW.round(1).tolist(), "iswr": sm.ISWR.round().tolist(),
              "psum": sm.PSUM.round(2).tolist()}
    met = sp.parse_met(out.met)
    hs_model = (met["Modelled snow depth (vertical)"] / 100).resample("D").mean()
    st = p.get("hs_check", [None])[0]
    hs_obs = pd.Series(dtype=float)
    if st:
        d = load_station(st)
        if not d.empty and "hs_m" in d:
            d = d.set_index("time_utc")
            hs_obs = d["hs_m"].where(d["hs_m_qc"] == "ok").resample("D").mean()
    days = pd.date_range(start.floor("D"), end.floor("D"), freq="D")
    daily = {"d0": days[0].strftime("%Y-%m-%d"), "hs_model": [None if pd.isna(v) else round(v * 100, 1)
                                                               for v in hs_model.reindex(days)],
             "hs_station": [None if pd.isna(v) else round(v * 100, 1) for v in hs_obs.reindex(days)],
             "station": st}

    fc: list[dict] = []
    if forecasts:
        backups = {pd.Timestamp(f.name.split(".sno")[1][:12], tz="UTC"): f
                   for f in (Path(out.run_dir) / "output").glob("*.sno2*")}
        lead_csv = swork / "lead.csv"
        sm.to_csv(lead_csv)
        issues = [t for t in sorted(backups) if t.month in FORECAST_MONTHS and t.hour == 0]
        jobs = [(plot, t.isoformat(), str(backups[t]), str(lead_csv), str(swork)) for t in issues[:max_issues]]
        if workers > 1:
            with ProcessPoolExecutor(workers) as ex:
                fc = [x for x in ex.map(_forecast_one, jobs) if x is not None]
        else:
            fc = [x for x in map(_forecast_one, jobs) if x is not None]

    pits = observed_at_plot(OBSERVED, plot, start, end)
    pit_out = []
    for o in pits:
        t = pd.Timestamp(o["obs_time_utc"])
        rec = _pit_record(o)
        near = min(nowcast, key=lambda pr: abs((pd.Timestamp(pr["t"] + ":00", tz="UTC") - t).total_seconds()))
        dt_h = abs((pd.Timestamp(near["t"] + ":00", tz="UTC") - t).total_seconds()) / 3600
        rec["nowcast"] = {"t": near["t"], **(_score(o, near["L"]) if dt_h <= 13 else {})}
        rec["forecast"] = {}
        for f in fc:
            if "P" not in f:
                continue
            cand = [pr for pr in f["P"] if abs((pd.Timestamp(pr["t"] + ":00", tz="UTC") - t).total_seconds()) <= 3 * 3600]
            if cand:
                lead = (pd.Timestamp(cand[0]["t"] + ":00", tz="UTC") - pd.Timestamp(f["issue"] + ":00", tz="UTC"))
                rec["forecast"][f["issue"]] = {"t": cand[0]["t"], "lead_h": int(lead.total_seconds() // 3600),
                                               **_score(o, cand[0]["L"])}
        pit_out.append(rec)
    season = f"{y}-{y + 1}"
    used = dataclasses.replace(settings, calculation_step_min=float(out.extra.get("calculation_step_min",
                                                                                 settings.calculation_step_min)))
    fhash, chash = _forcing_hash(out.run_dir), used.config_hash()  # config actually run (after any retry)
    payload = {"label": EXPERIMENTAL_LABEL, "site": plot, "season": season, "mode": mode,
               "run_id": f"web-{plot}-{season}-{chash[:8]}-{fhash[:8]}", "forcing_hash": fhash,
               "forcing_sources": source_summary(pf), "forcing_notes": pf.notes,
               "engine": {"version": sp.find_engine().version_string, "config_hash": chash,
                          "calculation_step_min": used.calculation_step_min,
                          **({"numerical_retry": out.extra["numerical_retry"]} if "numerical_retry" in out.extra
                             else {})},
               "nowcast_every_h": 6 if mode == "station" else 24, "nowcast": nowcast, "skipped_profiles": skipped,
               "hourly": hourly, "daily": daily, "pits": pit_out}
    out_dir = Path(out_dir) / plot
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / f"{season}.json").write_text(json.dumps(payload, separators=(",", ":")))
    if fc:
        (out_dir / f"{season}_forecasts.json").write_text(json.dumps(
            {"label": EXPERIMENTAL_LABEL, "site": plot, "season": season, "hours": FC_HOURS,
             "initial_state_run_id": payload["run_id"],
             "engine": {"version": payload["engine"]["version"], "config_hash": _fc_settings().config_hash()},
             "issues": fc}, separators=(",", ":")))
    shutil.rmtree(swork, ignore_errors=True)
    return {"site": plot, "season": season, "mode": mode, "nowcast_profiles": len(nowcast),
            "forecast_issues": len([f for f in fc if "P" in f]), "forecast_errors": len([f for f in fc if "error" in f]),
            "pits": len(pit_out)}


def write_index(out_dir: Path) -> dict:
    """sites.json: what exists, for the front-end."""
    cfg = _cfg()
    idx = {"label": EXPERIMENTAL_LABEL, "generated_utc": pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"),
           "sites": []}
    for plot, name in SITES.items():
        p = cfg["plots"][plot]
        seasons = []
        for f in sorted((Path(out_dir) / plot).glob("*.json")):
            if f.name.endswith("_forecasts.json"):
                continue
            meta = json.loads(f.read_text())
            seasons.append({"season": meta["season"], "mode": meta["mode"], "file": f"data/{plot}/{f.name}",
                            "forecasts": (f"data/{plot}/{meta['season']}_forecasts.json"
                                          if (f.parent / f"{meta['season']}_forecasts.json").exists() else None),
                            "pits": len(meta["pits"])})
        idx["sites"].append({"id": plot, "name": name, "lat": p["lat"], "lon": p["lon"],
                             "elevation_m": p["elevation_m"], "seasons": seasons})
    (Path(out_dir) / "sites.json").write_text(json.dumps(idx, indent=1))
    return idx


def _build_job(args) -> dict:
    plot, y, out_dir, work = args
    try:
        return build_season(plot, y, Path(out_dir), Path(work))
    except Exception as exc:  # noqa: BLE001 - reported, others continue
        return {"site": plot, "season": f"{y}-{y + 1}", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def build_all(out_dir: Path, work: Path, seasons: list[int], plots: list[str], workers: int = 4) -> list[dict]:
    """Site-seasons in parallel (one process each; forecast seasons first, they take longest)."""
    jobs = sorted(((pl, y, str(out_dir), str(work)) for y in seasons for pl in plots),
                  key=lambda j: (j[1] not in FORECAST_SEASONS, -j[1]))
    res = []
    with ProcessPoolExecutor(workers) as ex:
        for r in ex.map(_build_job, jobs):
            res.append(r)
            print(json.dumps(r), flush=True)
    write_index(out_dir)
    return res
