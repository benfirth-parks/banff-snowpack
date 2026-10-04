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
import gzip
import hashlib
import json
import shutil
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd
import yaml

from snowagent.contracts import EXPERIMENTAL_LABEL

SITES = {"goats_eye": "Sunshine Village - Goat's Eye", "simpson": "Simpson", "bow_summit": "Bow Summit"}
SEASON_START = "09-15"  # ``season_start`` of config/plot_forcing.yaml when the config is not at hand


def _cfg() -> dict:
    return yaml.safe_load(Path("config/plot_forcing.yaml").read_text())


def season_start(y: int, cfg: dict | None = None) -> pd.Timestamp:
    """Snow-free start of the season ``y``-``y+1`` (``season_start`` of config/plot_forcing.yaml, 15 September),
    UTC. Without the config (an import outside the repository) the default ``SEASON_START`` applies."""
    if cfg is None:
        try:
            cfg = _cfg()
        except OSError:
            cfg = {}
    return pd.Timestamp(f"{y}-{cfg.get('season_start') or SEASON_START}", tz="UTC")


def current_season_year(now: pd.Timestamp | None = None) -> int:
    """Start year of the season in progress: from the configured season start (15 Sep) to the day before the next
    one. So from 1 Jul to 14 Sep it is the season that just ended (ADR-054): the daily build then completes that
    season instead of starting one whose forcing has not begun."""
    now = now or pd.Timestamp.now(tz="UTC")
    if now.tzinfo is None:
        now = now.tz_localize("UTC")
    return now.year if now >= season_start(now.year) else now.year - 1


# season start years with measured station forcing (ADR-030, ADR-034, ADR-035): Goat's Eye and Simpson from
# 2015-16 (Sunshine gauge from Aug 2015); Bow Summit from 2016-17 (its gauge starts 22 Mar 2016)
_Y = current_season_year()
STATION_SEASONS = {"goats_eye": range(2015, _Y + 1), "simpson": range(2015, _Y + 1),
                   "bow_summit": range(2016, _Y + 1)}
FORECAST_SEASONS = range(2021, _Y + 1)  # archived GFS runs: Nov-Apr 2021-26, every day of a live season
FORECAST_MONTHS = (11, 12, 1, 2, 3, 4)
ISSUED = Path("archive/live_forecasts")  # live-season forecasts, stored once when issued and never recomputed
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
def _cut_warning(plot: str, data: pd.DataFrame, end: pd.Timestamp, mode: str, now: pd.Timestamp) -> dict:
    """The incomplete stretch that stops a season's forcing at ``end``, as a status warning (layout of
    ``ops.update.warning``): which variables, from when to when, and how many later hours are not used."""
    complete = data.notna().all(axis=1)
    first = complete[~complete].index[0]
    later_ok = complete[first:][complete[first:]].index
    gap_end = later_ok[0] - pd.Timedelta(hours=1) if len(later_ok) else complete.index[-1]
    cols = [c for c in data.columns if data.loc[first:gap_end, c].isna().any()]
    fill = "GFS day-1 fill (00 UTC runs missing beyond the 2-day fallback)" if mode == "live" else "ERA5 fill"
    unused = int(complete[complete.index > gap_end].sum())
    msg = (f"{SITES[plot]}: weather stops at {end:%Y-%m-%d %H:%M} UTC; no {', '.join(cols)} from "
           f"{first:%Y-%m-%d %H:%M} to {gap_end:%Y-%m-%d %H:%M} UTC (no station value and no {fill})"
           + (f"; {unused} complete hours after the gap are not used" if unused else ""))
    return {"level": "warning", "source": f"forcing:{plot}", "message": msg, "last_record_utc": end.isoformat(),
            "age_h": round((now - end).total_seconds() / 3600, 1), "gap_start_utc": first.isoformat(),
            "gap_end_utc": gap_end.isoformat(), "variables": cols}


def season_forcing(plot: str, y: int, now: pd.Timestamp | None = None, warnings: list | None = None):
    """Plot forcing of one season. Where it is incomplete (no station value and no fill), the season stops at the
    hour before (live) or the day before; that cut is in the forcing notes and, if given, appended to ``warnings``."""
    from snowagent.baseline.assemble import assemble

    cfg = _cfg()
    p = cfg["plots"][plot]
    mode = "station" if y in STATION_SEASONS[plot] else "era5"
    transfer = None
    if mode == "era5":
        transfer = yaml.safe_load(Path("config/era5_transfer.yaml").read_text())["plots"][plot]
    start = season_start(y, cfg)
    end = pd.Timestamp(f"{y + 1}-{cfg['season_end']}", tz="UTC")
    now = now or pd.Timestamp.now(tz="UTC")
    end = min(end, now.floor("h"))
    pf = assemble(plot, str(start - pd.Timedelta(hours=6)), str(end), era5_only=transfer)
    complete = pf.data.notna().all(axis=1)
    if not complete.all() and mode == "station":
        # ERA5 is published months late on the mirror used here: from the first incomplete hour, fill from the
        # GFS day-1 composite (00 UTC runs, leads 1-24 h; ADR-033) instead. Station values are the same in both.
        gap = complete[~complete].index[0]
        g = assemble(plot, str(gap - pd.Timedelta(hours=6)), str(end), reanalysis="gfs_day1", gfs_fallback_days=2)
        tail = g.data.index[g.data.index >= gap]
        pf.data.loc[tail, :] = g.data.loc[tail, pf.data.columns].to_numpy()
        pf.sources.loc[tail, :] = g.sources.loc[tail, pf.sources.columns].to_numpy()
        pf.notes.append(f"live: from {gap:%Y-%m-%d %H:%M} UTC the fill is the GFS day-1 composite (ERA5 not yet "
                        "published); " + "; ".join(n for n in g.notes if "temperature fill offset" in n
                                                   or n.startswith("gfs_day1:")))
        if gap <= start:  # no ERA5 at all in this season: the ERA5 pass contributed nothing, keep its notes out
            pf.notes = [n for n in g.notes if not n.startswith("reanalysis:")] + pf.notes[-1:]
        mode = "live"
        complete = pf.data.notna().all(axis=1)
    if not complete.all():  # e.g. reanalysis not yet published for the last weeks, or the newest station hours
        first = complete[~complete].index[0] - pd.Timedelta(hours=1)
        end = first.floor("h") if mode == "live" else first.floor("D")
        cut = _cut_warning(plot, pf.data, end, mode, now)
        pf.notes.append(f"cut: {cut['message']}")
        if warnings is not None:
            warnings.append(cut)
        pf.data, pf.sources = pf.data[:end], pf.sources[:end]
    if p.get("psum_factor", 1.0) != 1.0:
        pf.data["psum"] = pf.data["psum"] * p["psum_factor"]
        pf.notes.append(f"precipitation x {p['psum_factor']} (ADR-024)")
    return pf, start, end, mode


# ------------------------------------------------------------------------------------------------ forecasts
def _forecast_one(args) -> dict | None:
    plot, issue_iso, sno, lead_csv, work, *rest = args
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
    corr = rest[0] if rest and rest[0] is not None else (p.get("gfs_correction") or {})
    ta_k, pf_ = float(corr.get("ta_offset_k", 0.0)), float(corr.get("psum_factor", 1.0))
    if ta_k or pf_ != 1.0:  # constant per-plot correction of GFS at the plot (chosen leave-one-season-out)
        uf.smet["TA"] = uf.smet["TA"] + ta_k
        uf.smet["PSUM"] = uf.smet["PSUM"] * pf_
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
            **({"gfs_correction": {"ta_offset_k": ta_k, "psum_factor": pf_}} if ta_k or pf_ != 1.0 else {}),
            **({"numerical_retry": out.extra["numerical_retry"]} if "numerical_retry" in out.extra else {}),
            **({"skipped_profiles": skipped} if skipped else {}), "P": prof, "wx": {"ta": (wx.TA - 273.15).round(1).tolist(), "rh": (wx.RH * 100).round().tolist(),
                              "vw": wx.VW.round(1).tolist(), "iswr": wx.ISWR.round().tolist(),
                              "psum": wx.PSUM.round(2).tolist()}}


def _issued_path(plot: str, season: str, issue: pd.Timestamp, base: Path = ISSUED) -> Path:
    return Path(base) / plot / season / f"{issue:%Y%m%dT%H}.json.gz"


def _issued_load(plot: str, season: str, issue: pd.Timestamp, base: Path = ISSUED) -> dict | None:
    f = _issued_path(plot, season, issue, base)
    return json.loads(gzip.decompress(f.read_bytes())) if f.exists() else None


def _issued_store(plot: str, season: str, rec: dict, run_id: str, base: Path = ISSUED,
                  now: pd.Timestamp | None = None) -> dict:
    """Keep a live forecast exactly as first produced (at ``now``). ``computed_after_issue`` marks one produced more
    than a day after its issue time (a gap filled later), which is then not an as-issued forecast."""
    issue = pd.Timestamp(rec["issue"] + ":00", tz="UTC")
    f = _issued_path(plot, season, issue, base)
    if f.exists():
        return json.loads(gzip.decompress(f.read_bytes()))
    now = now or pd.Timestamp.now(tz="UTC")
    rec = {**rec, "produced_utc": now.isoformat(timespec="seconds"), "initial_state_run_id": run_id,
           "computed_after_issue": bool(now - issue > pd.Timedelta(days=1))}
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_bytes(gzip.compress(json.dumps(rec, separators=(",", ":")).encode(), mtime=0))
    tmp.replace(f)
    return rec


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
def build_season(plot: str, y: int, out_dir: Path, work: Path, workers: int = 1, max_issues: int | None = None,
                 issued_dir: Path = ISSUED, gfs_correction: dict | None = None,
                 now: pd.Timestamp | None = None) -> dict:
    """``gfs_correction`` overrides the plot's configured GFS correction (experiments; {} = raw GFS). ``now`` is the
    build time (default: the wall clock): where the current season's forcing stops, the live block's time stamp and
    when a stored forecast was produced; nothing else here reads the clock."""
    from snowagent.baseline.assemble import source_summary
    from snowagent.baseline.evaluate import plot_pits
    from snowagent.baseline.run import plot_unit, run_season
    from snowagent.engine import snowpack as sp
    from snowagent.ingest.fts360 import load_station
    from snowagent.spatial_forcing.builder import ForcingConfig, build_unit_forcing

    cfg = _cfg()
    p = cfg["plots"][plot]
    now = now or pd.Timestamp.now(tz="UTC")
    warnings: list[dict] = []
    pf, start, end, mode = season_forcing(plot, y, now, warnings)
    measured = mode in ("station", "live")
    forecasts = measured and y in FORECAST_SEASONS
    unit = plot_unit(plot, p["lat"], p["lon"], p["elevation_m"])
    settings = sp.EngineSettings(prof_days_between=0.25, snow_days_between=1.0 if measured else 3650.0,
                                 first_backup=0.0 if measured else 400.0)  # daily states: forecasts, pit updates
    swork = Path(work) / f"{plot}_{y}"
    shutil.rmtree(swork, ignore_errors=True)
    r = run_season(pf, unit, start, end, swork, settings=settings)
    out = r["outputs"]
    every = None if measured else {t for t in pd.date_range(start, end, freq="D") + pd.Timedelta(hours=18)}
    skipped: list[dict] = []
    nowcast = _profiles(out.pro, 0.0, every, skipped)
    uf = build_unit_forcing(pf.data, p["lat"], p["lon"], p["elevation_m"], unit, ForcingConfig())
    sm = uf.smet
    pits, excluded = plot_pits(OBSERVED, plot, start, end)  # excluded: {} unless the switch left pits out (ADR-050)
    nowcast_free, steer = nowcast, None
    if measured:  # pit-steered run (ADR-038): each pit's snow depth updates the state after the pit
        from snowagent.learn.steer import steered_run

        steer = steered_run(out, sm, unit, start, end, pits, swork / "steered", settings)
        if len(steer["segments"]) > 1:
            nowcast = []
            for i, (t_from, t_to, ro) in enumerate(steer["segments"]):
                last = i == len(steer["segments"]) - 1
                nowcast += [pr for pr in _profiles(ro.pro, 0.0, every, skipped)
                            if t_from <= pd.Timestamp(pr["t"] + ":00", tz="UTC") < t_to
                            or (last and pd.Timestamp(pr["t"] + ":00", tz="UTC") == t_to)]
    hourly = {"t0": sm.index[0].strftime("%Y-%m-%dT%H"), "ta": (sm.TA - 273.15).round(1).tolist(),
              "rh": (sm.RH * 100).round().tolist(), "vw": sm.VW.round(1).tolist(), "iswr": sm.ISWR.round().tolist(),
              "psum": sm.PSUM.round(2).tolist()}
    met = sp.parse_met(out.met)
    hs_free = (met["Modelled snow depth (vertical)"] / 100).resample("D").mean()
    hs_model = (steer["hs"] / 100).resample("D").mean() if steer else hs_free
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
    if steer and steer["updates"]:
        daily["hs_model_free"] = [None if pd.isna(v) else round(v * 100, 1) for v in hs_free.reindex(days)]

    season = f"{y}-{y + 1}"
    used = dataclasses.replace(settings, calculation_step_min=float(out.extra.get("calculation_step_min",
                                                                                 settings.calculation_step_min)))
    fhash, chash = _forcing_hash(out.run_dir), used.config_hash()  # config actually run (after any retry)
    run_id = f"web-{plot}-{season}-{chash[:8]}-{fhash[:8]}"

    fc: list[dict] = []
    if forecasts:
        backups = steer["backups"] if steer else {pd.Timestamp(f.name.split(".sno")[1][:12], tz="UTC"): f
                                                  for f in (Path(out.run_dir) / "output").glob("*.sno2*")}
        lead_csv = swork / "lead.csv"
        sm.to_csv(lead_csv)
        issues = [t for t in sorted(backups) if t.hour == 0 and (mode == "live" or t.month in FORECAST_MONTHS)]
        stored = {t: _issued_load(plot, season, t, issued_dir) for t in issues} if mode == "live" else {}
        todo = [t for t in issues if stored.get(t) is None][:max_issues]
        jobs = [(plot, t.isoformat(), str(backups[t]), str(lead_csv), str(swork), gfs_correction) for t in todo]
        if workers > 1:
            with ProcessPoolExecutor(workers) as ex:
                new = [x for x in ex.map(_forecast_one, jobs) if x is not None]
        else:
            new = [x for x in map(_forecast_one, jobs) if x is not None]
        if mode == "live":  # store as issued (once); a failed or missing run is retried next time
            new = [_issued_store(plot, season, f, run_id, issued_dir, now) if "P" in f else f for f in new]
        fc = sorted([f for f in stored.values() if f is not None] + new, key=lambda f: f["issue"])

    pit_out = []
    for o in pits:
        t = pd.Timestamp(o["obs_time_utc"])
        rec = _pit_record(o)
        for key, series in (("nowcast", nowcast), ("nowcast_free", nowcast_free)):
            if key == "nowcast_free" and series is nowcast:
                continue
            near = min(series, key=lambda pr: abs((pd.Timestamp(pr["t"] + ":00", tz="UTC") - t).total_seconds()))
            dt_h = abs((pd.Timestamp(near["t"] + ":00", tz="UTC") - t).total_seconds()) / 3600
            rec[key] = {"t": near["t"], **(_score(o, near["L"]) if dt_h <= 13 else {})}
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
    payload = {"label": EXPERIMENTAL_LABEL, "site": plot, "season": season, "mode": mode,
               "run_id": run_id, "forcing_hash": fhash,
               "forcing_sources": source_summary(pf), "forcing_notes": pf.notes,
               "engine": {"version": sp.find_engine().version_string, "config_hash": chash,
                          "calculation_step_min": used.calculation_step_min,
                          **({"numerical_retry": out.extra["numerical_retry"]} if "numerical_retry" in out.extra
                             else {})},
               "nowcast_every_h": 6 if measured else 24, "nowcast": nowcast, "skipped_profiles": skipped,
               **({"nowcast_free": nowcast_free, "steer": {"weight": steer["weight"], "method": steer["method"],
                                                     "updates": steer["updates"]}}
                  if steer and steer["updates"] else {}),
               "hourly": hourly, "daily": daily, "pits": pit_out,
               **excluded}
    if mode == "live" and y == current_season_year(now):  # a past season on the GFS fill is not "live"
        payload["live"] = {"generated_utc": now.isoformat(timespec="seconds"),
                           "weather_through": end.isoformat(), "nowcast_through": nowcast[-1]["t"] if nowcast else None,
                           "latest_issue": fc[-1]["issue"] if fc else None,
                           "issued_computed_afterwards": sum(1 for f in fc if f.get("computed_after_issue"))}
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
            "pits": len(pit_out), **{k: len(v) for k, v in excluded.items()},
            **({"warnings": warnings} if warnings else {})}


def write_index(out_dir: Path, now: pd.Timestamp | None = None) -> dict:
    """sites.json: what exists, for the front-end (``generated_utc``: ``now``, default the wall clock)."""
    cfg = _cfg()
    now = now or pd.Timestamp.now(tz="UTC")
    idx = {"label": EXPERIMENTAL_LABEL, "generated_utc": now.isoformat(timespec="seconds"), "sites": []}
    for plot, name in SITES.items():
        p = cfg["plots"][plot]
        seasons = []
        for f in sorted((Path(out_dir) / plot).glob("*.json")):
            if f.name.endswith(("_forecasts.json", "_public.json")):
                continue
            meta = json.loads(f.read_text())
            ss = meta["season"]
            seasons.append({"season": ss, "mode": meta["mode"], "file": f"data/{plot}/{f.name}",
                            "forecasts": (f"data/{plot}/{ss}_forecasts.json"
                                          if (f.parent / f"{ss}_forecasts.json").exists() else None),
                            "public": f"data/{plot}/{ss}_public.json" if (f.parent / f"{ss}_public.json").exists()
                            else None,
                            "pits": len(meta["pits"]), "live": meta.get("live")})
        idx["sites"].append({"id": plot, "name": name, "lat": p["lat"], "lon": p["lon"],
                             "elevation_m": p["elevation_m"], "seasons": seasons})
    (Path(out_dir) / "sites.json").write_text(json.dumps(idx, indent=1))
    return idx


# ------------------------------------------------------------------------------------------------ public reports
PUBLIC_RADIUS_KM = 15.0


def _pub_compact(r, km: float) -> dict:
    c = " / ".join(f"{k}: {v}" for k, v in r.comments.items())
    t = r.test
    return {"id": r.source_id, "t": pd.Timestamp(r.obs_time_utc).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ"),
            "url": r.url, "title": r.title, "km": km, "elev": r.elevation_m, "bands": r.elevation_bands,
            "aspects": r.aspects, "types": r.types, "hs": r.hs_cm,
            "test": {"i": t.initiation, "f": t.fracture, "d": t.depth_cm, "c": t.crystal_types} if t else None,
            "surface": r.surface, "wh": r.whumpfing, "cr": r.cracking, "hn24": r.new_snow_24h_cm,
            "av": [{"size": a.size, "char": a.character, "trig": a.trigger, "asp": a.aspects} for a in r.avalanches],
            "comment": c[:400] + ("…" if len(c) > 400 else ""), "images": len(r.image_urls)}


def write_public(out_dir: Path, archive_dir: Path = Path("archive/min"), radius_km: float = PUBLIC_RADIUS_KM) -> dict:
    """Per plot and season: the MIN reports within ``radius_km`` (``<season>_public.json``), newest version each."""
    from snowagent.ingest.min import load_reports

    reports = load_reports(archive_dir)
    counts: dict[str, int] = {}
    for plot in SITES:
        by_season: dict[int, list] = {}
        for r in reports:
            km = r.distance_km.get(plot)
            if km is None or km > radius_km:
                continue
            by_season.setdefault(current_season_year(pd.Timestamp(r.obs_time_utc)), []).append(_pub_compact(r, km))
        d = Path(out_dir) / plot
        d.mkdir(parents=True, exist_ok=True)
        for y, rows in by_season.items():
            (d / f"{y}-{y + 1}_public.json").write_text(json.dumps(
                {"label": EXPERIMENTAL_LABEL, "source": "Avalanche Canada Mountain Information Network (public reports)",
                 "radius_km": radius_km, "reports": rows}, separators=(",", ":")))
        counts[plot] = sum(len(v) for v in by_season.values())
    return counts


def _build_job(args) -> dict:
    plot, y, out_dir, work, now = args
    try:
        return build_season(plot, y, Path(out_dir), Path(work), now=now)
    except Exception as exc:  # noqa: BLE001 - reported, others continue
        return {"site": plot, "season": f"{y}-{y + 1}", "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def build_all(out_dir: Path, work: Path, seasons: list[int], plots: list[str], workers: int = 4,
              now: pd.Timestamp | None = None) -> list[dict]:
    """Site-seasons in parallel (one process each; forecast seasons first, they take longest), all at one build
    time ``now`` (default: the wall clock when the build starts)."""
    now = now or pd.Timestamp.now(tz="UTC")
    jobs = sorted(((pl, y, str(out_dir), str(work), now) for y in seasons for pl in plots),
                  key=lambda j: (j[1] not in FORECAST_SEASONS, -j[1]))
    res = []
    with ProcessPoolExecutor(workers) as ex:
        for r in ex.map(_build_job, jobs):
            res.append(r)
            print(json.dumps(r), flush=True)
    write_index(out_dir, now)
    return res
