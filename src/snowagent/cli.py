"""snowagent command-line interface.

    snowagent doctor
    snowagent demo --output artifacts/demo
    snowagent build-domain --dem D.asc --boundary B.geojson --landcover L.asc --out W/domain
    snowagent init --domain W/domain --history H.csv --until 2026-01-15T00:00:00Z --initial-condition snow_free
    snowagent predict --domain W/domain --forecast F.csv --state latest_valid
    snowagent profile --run <run-id|dir> --lat 51.2 --lon -115.7 --lead-hours 24

A workspace is a directory holding domain/, store/ and runs/. Paths default to the
workspace containing the --domain directory.
"""

from __future__ import annotations

import dataclasses
import json
import platform
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Annotated

import pandas as pd
import typer

from snowagent.config import load_config
from snowagent.contracts import EXPERIMENTAL_LABEL, WhatIf
from snowagent.errors import SnowAgentError

app = typer.Typer(add_completion=False, help=EXPERIMENTAL_LABEL, no_args_is_help=True)


def _emit_error(exc: SnowAgentError) -> None:
    typer.echo(json.dumps(exc.to_dict(), indent=1, default=str), err=False)
    raise typer.Exit(code=2)


def _paths(domain: Path, store: Path | None, runs: Path | None) -> tuple[Path, Path, Path]:
    ddir = domain if domain.is_dir() else domain.parent
    ws = ddir.parent
    return ddir, store or ws / "store", runs or ws / "runs"


def _parse_time(s: str | None) -> datetime | None:
    if s is None:
        return None
    t = pd.Timestamp(s)
    if t.tzinfo is None:
        raise typer.BadParameter(f"time {s!r} must include a UTC designator (Z or +00:00)")
    return t.tz_convert("UTC").to_pydatetime()


# ------------------------------------------------------------------------------------------------ doctor


@app.command()
def doctor(smoke: Annotated[bool, typer.Option(help="run a short real-engine smoke column")] = True) -> None:
    """Check Python deps, the SNOWPACK engine, config keys and (optionally) a smoke run."""
    from snowagent.engine import snowpack as sp

    report: dict = {"python": platform.python_version(), "checks": {}}
    ok = True
    for mod in ("pydantic", "numpy", "pandas", "pyproj", "matplotlib", "yaml"):
        try:
            m = __import__(mod)
            report["checks"][mod] = getattr(m, "__version__", "ok")
        except ImportError as exc:
            report["checks"][mod] = f"MISSING: {exc}"
            ok = False
    try:
        eng = sp.find_engine()
        report["engine"] = {"binary": eng.binary, "version": eng.version_string}
        cfg = load_config()
        report["engine"]["config_hash"] = cfg.engine.config_hash()
        if smoke:
            report["engine"]["smoke"] = _smoke(eng, cfg)
    except SnowAgentError as exc:
        report["engine"] = exc.to_dict()
        ok = False
    report["status"] = "ok" if ok and report["engine"].get("smoke", {}).get("status", "ok") == "ok" else "failed"
    typer.echo(json.dumps(report, indent=1, default=str))
    if report["status"] != "ok":
        raise typer.Exit(code=1)


def _smoke(eng, cfg) -> dict:
    """One sloped snow-free column, 3 days of simple snowfall: proves the engine executes here."""
    import tempfile

    import numpy as np

    from snowagent.engine import snowpack as sp
    from snowagent.engine.profiles import convert_profile

    with tempfile.TemporaryDirectory() as td:
        rd = Path(td)
        (rd / "input").mkdir()
        idx = pd.date_range("2025-11-01T01:00Z", "2025-11-04T00:00Z", freq="h")
        df = pd.DataFrame({"TA": 266.0, "RH": 0.85, "VW": 2.0, "DW": 270.0, "ISWR": 0.0, "ILWR": 250.0,
                           "PSUM": np.where(np.arange(len(idx)) < 36, 1.0, 0.0), "PSUM_PH": 0.0,
                           "TSG": 273.15}, index=idx)
        sp.write_smet_forcing(rd / "input" / "SMOKE.smet", "SMOKE", 51.2, -115.7, 2000, df)
        sp.write_snowfree_sno(rd / "input" / "SMOKE.sno", "SMOKE", 51.2, -115.7, 2000, 30, 180,
                              pd.Timestamp("2025-11-01T06:00Z"))
        out = sp.run_engine(eng, rd, "SMOKE", pd.Timestamp("2025-11-04T00:00Z"), cfg.engine)
        _, profs = sp.parse_pro(out.pro)
        _, diag, _, _ = convert_profile(profs[-1], 30, "SMOKE")
        return {"status": "ok" if diag.hs_vertical_m > 0 else "failed", "wall_s": round(out.wall_s, 2),
                "hs_vertical_m": round(diag.hs_vertical_m, 3), "n_layers": diag.n_layers}


# ------------------------------------------------------------------------------------------------ engine example


@app.command("engine-example")
def engine_example(
    source: Annotated[Path, typer.Option(help="SNOWPACK source tree (Source/snowpack)")] = Path(
        "/opt/snowpack-src/snowpack-model/Source/snowpack"),
    output: Annotated[Path, typer.Option()] = Path("artifacts/engine_example"),
) -> None:
    """Run the upstream MST96 (Weissfluhjoch) example and compare with its reference output."""
    from snowagent.engine.upstream import run_mst96_example

    try:
        typer.echo(json.dumps(run_mst96_example(source, output), indent=1))
    except SnowAgentError as exc:
        _emit_error(exc)


# ------------------------------------------------------------------------------------------------ domain / init


@app.command("build-domain")
def build_domain_cmd(
    dem: Annotated[Path, typer.Option()],
    boundary: Annotated[Path, typer.Option()],
    out: Annotated[Path, typer.Option(help="output domain directory")],
    domain_id: Annotated[str, typer.Option()] = "domain",
    landcover: Annotated[Path | None, typer.Option()] = None,
    unit_size_m: Annotated[float | None, typer.Option()] = None,
    synthetic: Annotated[bool, typer.Option()] = False,
) -> None:
    from snowagent.terrain.units import build_domain, save_domain

    cfg = load_config()
    ucfg = cfg.units if unit_size_m is None else dataclasses.replace(cfg.units, unit_size_m=unit_size_m)
    try:
        d = build_domain(domain_id, dem, boundary, landcover, ucfg, synthetic)
    except SnowAgentError as exc:
        _emit_error(exc)
    save_domain(d, out / "domain.json")
    typer.echo(json.dumps({"domain": str(out / "domain.json"), "terrain_version": d.terrain_version,
                           "units": len(d.units), "supported": sum(u.supported for u in d.units)}))


@app.command()
def init(
    domain: Annotated[Path, typer.Option()],
    history: Annotated[Path, typer.Option(help="historical weather CSV (+ .meta.json)")],
    until: Annotated[str, typer.Option(help="analysis time (UTC)")],
    initial_condition: Annotated[str | None, typer.Option(help="explicit initial condition: snow_free")] = None,
    store: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """Build an analysis checkpoint by replaying history (explicit snow-free start required)."""
    from snowagent.engine import snowpack as sp
    from snowagent.state.replay import initialize_from_history
    from snowagent.state.store import StateStore
    from snowagent.terrain.units import load_domain
    from snowagent.weather.io import load_weather

    cfg = load_config()
    ddir, store_dir, _ = _paths(domain, store, None)
    try:
        d = load_domain(ddir)
        eng = sp.find_engine()
        cp = initialize_from_history(d, load_weather(history), StateStore(store_dir), _parse_time(until), eng,
                                     cfg.engine, cfg.forcing, initial_condition, cfg.init)
    except SnowAgentError as exc:
        _emit_error(exc)
    typer.echo(json.dumps({"state_id": cp.state_id, "analysis_time": cp.analysis_time.isoformat(),
                           "units": len(cp.units), "store": str(store_dir)}))


# ------------------------------------------------------------------------------------------------ predict / profile


@app.command()
def predict(
    domain: Annotated[Path, typer.Option(help="domain directory (contains domain.json)")],
    forecast: Annotated[Path, typer.Option(help="forecast run CSV (+ .meta.json)")],
    state: Annotated[str, typer.Option(help="initial state policy")] = "latest_valid",
    issue_time: Annotated[str | None, typer.Option(help="default: forecast availability time")] = None,
    actuals: Annotated[Path | None, typer.Option(help="actuals to advance the state to forecast init")] = None,
    members: Annotated[int | None, typer.Option()] = None,
    seed: Annotated[int | None, typer.Option()] = None,
    lead_hours: Annotated[str | None, typer.Option(help="comma list, e.g. 0,24,48")] = None,
    what_if_ta_k: Annotated[float, typer.Option(help="what-if: add K to air temperature")] = 0.0,
    what_if_psum_factor: Annotated[float, typer.Option(help="what-if: multiply precipitation")] = 1.0,
    store: Annotated[Path | None, typer.Option()] = None,
    runs: Annotated[Path | None, typer.Option()] = None,
) -> None:
    """terrain + forecast weather -> future snowpack field + queryable profiles + uncertainty."""
    from snowagent.engine import snowpack as sp
    from snowagent.forecast.pipeline import predict as run_predict
    from snowagent.state.store import StateStore
    from snowagent.terrain.units import load_domain
    from snowagent.weather.io import load_weather

    if state != "latest_valid":
        raise typer.BadParameter("only --state latest_valid is supported")
    cfg = load_config()
    ens = cfg.ensemble.model_copy(update={k: v for k, v in {"members": members, "seed": seed}.items() if v is not None})
    leads = cfg.lead_hours if lead_hours is None else [int(x) for x in lead_hours.split(",")]
    ddir, store_dir, runs_dir = _paths(domain, store, runs)
    t0 = time.time()
    try:
        res = run_predict(load_domain(ddir), load_weather(forecast), StateStore(store_dir), runs_dir,
                          sp.find_engine(), cfg.engine, cfg.forcing, ens, leads, _parse_time(issue_time),
                          WhatIf(ta_offset_k=what_if_ta_k, psum_factor=what_if_psum_factor),
                          load_weather(actuals) if actuals else None)
    except SnowAgentError as exc:
        _emit_error(exc)
    m = res.meta
    typer.echo(json.dumps({"run_id": res.run_id, "run_dir": str(res.run_dir), "status": m.status,
                           "initial_state_id": m.initial_state_id, "units_simulated": len(m.units_simulated),
                           "units_unsupported": len(m.units_unsupported), "members": m.request.ensemble.members,
                           "synthetic_inputs": m.synthetic_inputs, "transport_status": m.capability.transport_status,
                           "mass_budget_max_residual_kg_m2": m.mass_budget_max_residual_kg_m2,
                           "wall_s": round(time.time() - t0, 1), "label": EXPERIMENTAL_LABEL}, indent=1))


@app.command()
def profile(
    run: Annotated[str, typer.Option(help="run id or run directory")],
    lat: Annotated[float, typer.Option()],
    lon: Annotated[float, typer.Option()],
    lead_hours: Annotated[float, typer.Option()] = 24,
    member: Annotated[int | None, typer.Option(help="default 0 = control member")] = None,
    runs: Annotated[Path, typer.Option(help="runs directory for run ids")] = Path("artifacts/demo/runs"),
    plot: Annotated[Path | None, typer.Option(help="write a profile PNG")] = None,
    layers: Annotated[bool, typer.Option(help="include full layer list in output")] = True,
) -> None:
    """Query the predicted layered profile at a point and lead time."""
    from snowagent.forecast.query import dumps, query_profile, resolve_run

    try:
        rd = resolve_run(run, runs)
        res = query_profile(rd, lat, lon, lead_hours, member)
    except SnowAgentError as exc:
        _emit_error(exc)
    if plot is not None:
        from snowagent.contracts import ProfileRecord
        from snowagent.forecast.plots import write_profile_figure

        rec = ProfileRecord.model_validate(res["profile"])
        u = res["terrain_unit"]
        write_profile_figure(plot, [rec], [f"{u['unit_id']} z={u['elevation_m']:.0f} slope={u['slope_deg']:.0f} "
                                           f"asp={u['aspect_deg'] or 0:.0f}"],
                             f"{res['run_id']} member {res['profile_member']} lead {lead_hours:g} h"
                             + (" [SYNTHETIC INPUTS]" if res["synthetic_inputs"] else ""))
        res["plot"] = str(plot)
    if not layers:
        res["profile"]["layers"] = f"{len(res['profile']['layers'])} layers omitted (--layers to show)"
    typer.echo(dumps(res))


# ------------------------------------------------------------------------------------------------ observations

obs_app = typer.Typer(help="Field-observation intake (training/evaluation data; never required at runtime).")
app.add_typer(obs_app, name="obs")


@obs_app.command("inventory")
def obs_inventory(
    path: Annotated[Path, typer.Option(help="raw profile upload directory (read-only)")] = Path("profiles"),
    out: Annotated[Path, typer.Option(help="output directory (gitignored data/ by default)")] = Path("data/interim/obs"),
    include_observer: Annotated[bool, typer.Option(help="write observer names (default redacted)")] = False,
) -> None:
    """Parse profile headers, flag QC issues and summarise study-plot sites. Layers are NOT extracted."""
    from snowagent.obs.inventory import build_inventory, write_inventory

    headers, skipped = build_inventory(path)
    paths = write_inventory(headers, skipped, out, include_observer)
    flags: dict[str, int] = {}
    for h in headers:
        for q in h.qc_flags:
            k = q.split(":")[0]
            k = "location_outlier_suspect_device_gps" if k.startswith("location_") else k
            k = "header_date_differs_from_filename" if k.startswith("header_date_") else k
            flags[k] = flags.get(k, 0) + 1
    typer.echo(json.dumps({
        "profiles": len(headers), "skipped_files": len(skipped),
        "with_header_text": sum(h.header_source == "pdf_text" for h in headers),
        "with_hs": sum(h.hs_m is not None for h in headers),
        "structured_layers": sum(h.layers_status == "structured" for h in headers),
        "qc_flag_counts": dict(sorted(flags.items())), "outputs": {k: str(v) for k, v in paths.items()},
        "note": "layers exist only as rendered images in these files; not extracted",
    }, indent=1))


@obs_app.command("profiles")
def obs_profiles(
    transcriptions: Annotated[Path, typer.Option()] = Path("observations/transcriptions"),
    path: Annotated[Path, typer.Option(help="raw profile upload directory (read-only)")] = Path("profiles"),
    out: Annotated[Path, typer.Option()] = Path("data/interim/obs/observed_profiles.jsonl"),
) -> None:
    """Build observed profiles (exact structured files + validated image transcriptions), de-duplicated."""
    from snowagent.obs.observed import build_observed, summarise_observed, write_observed

    obs, stats = build_observed(transcriptions, path)
    write_observed(obs, out)
    summary = summarise_observed(obs)
    summary.to_csv(out.with_name("observed_summary.csv"), index=False)
    by_site = summary.groupby("site_key")[["exact", "transcribed", "total"]].sum().sort_values("total", ascending=False)
    typer.echo(json.dumps(stats | {"output": str(out), "usable_unique_by_site": by_site.to_dict("index")}, indent=1))


@obs_app.command("agreement")
def obs_agreement(
    observed: Annotated[Path, typer.Option()] = Path("data/interim/obs/observed_profiles.jsonl"),
    rereads: Annotated[Path | None, typer.Option(help="directory of blind re-read transcriptions")] = None,
    out: Annotated[Path, typer.Option()] = Path("data/interim/obs/transcription_agreement.json"),
) -> None:
    """Estimate transcription error: image vs exact file of the same pit, and blind re-reads."""
    from snowagent.obs.agreement import (
        compare_profiles,
        reread_pairs,
        structured_vs_transcribed_pairs,
        summarise,
    )
    from snowagent.obs.observed import to_observed
    from snowagent.obs.transcription import load_all, validate_transcription

    obs = [json.loads(line) for line in observed.read_text().splitlines() if line.strip()]
    result: dict = {}
    rows = []
    for ref, other in structured_vs_transcribed_pairs(obs):
        rows.append({"ref": ref["profile_id"], "other": other["profile_id"]} | compare_profiles(ref, other))
    result["image_vs_exact"] = {"summary": summarise(rows), "pairs": rows}
    if rereads is not None:
        rr = []
        for _p, d in load_all(rereads):
            t, errors, _f = validate_transcription(d)
            if t is not None and not errors and t.readable and t.is_snow_profile:
                rr.append(to_observed(t, None, "Etc/GMT+7"))
        primary = [o for o in obs if o["provenance"].get("method", "").startswith("transcription")]
        rows = [{"ref": a["profile_id"], "other": b["profile_id"]} | compare_profiles(a, b)
                for a, b in reread_pairs(primary, rr)]
        result["blind_reread"] = {"summary": summarise(rows), "pairs": rows}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=1))
    typer.echo(json.dumps({k: v["summary"] for k, v in result.items()} | {"output": str(out)}, indent=1))


ingest_app = typer.Typer(help="Download external data (raw files unchanged, manifest with URL/time/sha256).")
app.add_typer(ingest_app, name="ingest")


@ingest_app.command("noaa-isd")
def ingest_noaa_isd(
    raw: Annotated[Path, typer.Option()] = Path("data/raw/noaa_isd"),
    out: Annotated[Path, typer.Option()] = Path("data/interim/noaa_isd"),
    config: Annotated[Path, typer.Option()] = Path("config/external_sources.yaml"),
) -> None:
    """Download and parse NOAA ISD hourly data for the stations near the study plots."""
    import yaml

    from snowagent.ingest import noaa_isd

    cfg = yaml.safe_load(config.read_text())["noaa_isd"]
    years = range(cfg["years"][0], cfg["years"][1] + 1)
    out.mkdir(parents=True, exist_ok=True)
    summary = {}
    for name, st in cfg["stations"].items():
        recs = noaa_isd.download(st["id"], years, raw)
        files = [Path(r["path"]) for r in recs if r.get("path")]
        d = noaa_isd.parse(files)
        if not d.empty:
            d.to_csv(out / f"{name}.csv", index=False)
        summary[name] = {"years_found": len(files), "hours": len(d),
                         "first": str(d["time_utc"].min()) if len(d) else None,
                         "last": str(d["time_utc"].max()) if len(d) else None}
    typer.echo(json.dumps(summary, indent=1))


@ingest_app.command("gfs")
def ingest_gfs(
    start: Annotated[str, typer.Option(help="first run date YYYY-MM-DD")],
    end: Annotated[str, typer.Option(help="last run date YYYY-MM-DD")],
    months: Annotated[str, typer.Option(help="comma-separated months to include")] = "11,12,1,2,3,4",
    cycle: Annotated[int, typer.Option()] = 0,
    max_lead: Annotated[int, typer.Option()] = 72,
    step: Annotated[int, typer.Option()] = 3,
    workers: Annotated[int, typer.Option()] = 8,
    out: Annotated[Path, typer.Option()] = Path("data/interim/forecasts/gfs"),
) -> None:
    """Extract archived GFS 0.25 deg forecasts at the stations and study plots (skips runs already done)."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from datetime import UTC

    import yaml

    from snowagent.ingest.gfs_archive import extract_run, write_run

    st = yaml.safe_load(Path("config/stations.yaml").read_text())
    pts = {}
    for s in st["stations"]:
        if s.get("lat") is not None:
            pts[s["station_id"]] = (s["lat"], s["lon"])
        plot = s.get("study_plot") or {}
        if plot.get("lat") is not None:
            pts[s["station_id"] + "_plot"] = (plot["lat"], plot["lon"])
    keep = {int(m) for m in months.split(",")}
    runs = [d.to_pydatetime().replace(hour=cycle, tzinfo=UTC) for d in pd.date_range(start, end, freq="D")
            if d.month in keep]
    todo = [r for r in runs if not (out / f"gfs_{r.strftime('%Y%m%d%H')}.csv").exists()]
    leads = list(range(0, max_lead + 1, step))
    done, failed = 0, []

    def one(r):
        rows, prov = extract_run(r, leads, pts)
        write_run(rows, prov, out, r)

    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(one, r): r for r in todo}
        for f in as_completed(futs):
            try:
                f.result()
                done += 1
            except Exception as exc:  # noqa: BLE001 - recorded and reported, run can be retried
                failed.append(f"{futs[f].isoformat()}: {type(exc).__name__}: {exc}")
    typer.echo(json.dumps({"runs": len(runs), "already": len(runs) - len(todo), "done": done,
                           "failed": len(failed), "failures": failed[:20], "points": list(pts)}, indent=1))


@ingest_app.command("fts360")
def ingest_fts360(
    start: Annotated[str | None, typer.Option(help="default: config start")] = None,
    end: Annotated[str | None, typer.Option(help="default: now")] = None,
    stations: Annotated[str | None, typer.Option(help="comma-separated keys; default all in config")] = None,
    raw: Annotated[Path, typer.Option()] = Path("data/raw/fts360"),
    config: Annotated[Path, typer.Option()] = Path("config/external_sources.yaml"),
) -> None:
    """Download FTS360 station records (raw monthly CSVs). Needs the fts360api.com credential."""
    import yaml

    from snowagent.ingest.fts360 import fetch_station, parse_station

    cfg = yaml.safe_load(config.read_text())["fts360"]
    keys = stations.split(",") if stations else list(cfg["stations"])
    end = end or pd.Timestamp.now(tz="UTC").isoformat()
    summary = {}
    for k in keys:
        recs = fetch_station(cfg["agency"], k, cfg["stations"][k], start or cfg["start"], end, raw)
        d = parse_station(sorted((raw / k).glob("*.csv")))
        Path("data/interim/fts360").mkdir(parents=True, exist_ok=True)
        if not d.empty:
            d.to_csv(Path("data/interim/fts360") / f"{k}.csv", index=False)
        summary[k] = {"files": sum(bool(r.get("path")) for r in recs), "hours": len(d),
                      "first": str(d["time_utc"].min()) if len(d) else None,
                      "errors": [r.get("error") for r in recs if r.get("error")][:3]}
    typer.echo(json.dumps(summary, indent=1))


# ------------------------------------------------------------------------------------------------ demo


@app.command()
def demo(
    output: Annotated[Path, typer.Option()] = Path("artifacts/demo"),
    members: Annotated[int | None, typer.Option()] = None,
    seed: Annotated[int | None, typer.Option()] = None,
    force: Annotated[bool, typer.Option(help="delete an existing demo workspace first")] = False,
) -> None:
    """SYNTHETIC end-to-end demo: fixture -> terrain units -> history replay -> forecast -> profiles."""
    from snowagent.engine import snowpack as sp
    from snowagent.forecast.pipeline import predict as run_predict
    from snowagent.forecast.query import query_profile
    from snowagent.state.replay import initialize_from_history
    from snowagent.state.store import StateStore
    from snowagent.synthetic.fixture import FixtureSpec, write_fixture
    from snowagent.terrain.units import build_domain, save_domain
    from snowagent.weather.io import load_weather

    if output.exists():
        if not force:
            typer.echo(f"{output} exists; use --force to rebuild (issued runs are never overwritten in place)")
            raise typer.Exit(code=1)
        _rmtree_readonly(output)
    cfg = load_config()
    ens = cfg.ensemble.model_copy(update={k: v for k, v in {"members": members, "seed": seed}.items() if v is not None})
    log: dict = {"label": EXPERIMENTAL_LABEL, "synthetic": True, "steps": []}
    t0 = time.time()

    def step(name: str, **info) -> None:
        log["steps"].append({"step": name, "t_s": round(time.time() - t0, 1), **info})
        typer.echo(f"[{time.time() - t0:7.1f}s] {name} {json.dumps(info, default=str)}", err=True)

    try:
        eng = sp.find_engine()
        step("engine", version=eng.version_string)
        spec = FixtureSpec()
        paths = write_fixture(output / "inputs", spec)
        step("synthetic fixture written", **{k: str(v) for k, v in paths.items()})
        d = build_domain("synthetic_demo", paths["dem"], paths["boundary"], paths["landcover"], cfg.units, True)
        save_domain(d, output / "domain" / "domain.json")
        step("terrain units", n=len(d.units), supported=sum(u.supported for u in d.units),
             terrain_version=d.terrain_version)
        store = StateStore(output / "store")
        history = load_weather(paths["actuals"])
        fc = load_weather(paths["forecast"])
        cp = initialize_from_history(d, history, store, fc.meta.issue_time, eng, cfg.engine, cfg.forcing,
                                     "snow_free", cfg.init)
        step("history replay -> checkpoint", state_id=cp.state_id, analysis_time=cp.analysis_time)
        res = run_predict(d, fc, store, output / "runs", eng, cfg.engine, cfg.forcing, ens, cfg.lead_hours)
        step("forecast", run_id=res.run_id, status=res.meta.status,
             max_budget_residual=res.meta.mass_budget_max_residual_kg_m2)
        units = {u.unit_id: u for u in d.units}
        from snowagent.forecast.plots import contrast_pair

        pair = contrast_pair([units[x] for x in res.meta.units_simulated])
        examples = []
        for u in pair:
            lon, lat = u.centroid_lonlat
            q = query_profile(res.run_dir, lat, lon, 24)
            examples.append({"unit_id": u.unit_id, "lat": round(lat, 5), "lon": round(lon, 5),
                             "hs_vertical_m": q["profile"]["diagnostics"]["hs_vertical_m"],
                             "n_layers": q["profile"]["diagnostics"]["n_layers"],
                             "command": f"snowagent profile --run {res.run_id} --runs {output / 'runs'} "
                                        f"--lat {lat:.5f} --lon {lon:.5f} --lead-hours 24"})
        log.update({"domain": str(output / "domain"), "store": str(output / "store"), "run_id": res.run_id,
                    "run_dir": str(res.run_dir), "example_queries": examples,
                    "predict_command": f"snowagent predict --domain {output / 'domain'} --forecast {paths['forecast']} "
                                       "--state latest_valid"})
    except SnowAgentError as exc:
        _emit_error(exc)
    (output / "demo_summary.json").write_text(json.dumps(log, indent=1, default=str))
    typer.echo(json.dumps(log, indent=1, default=str))


def _rmtree_readonly(path: Path) -> None:
    """Delete a workspace whose sealed checkpoints are read-only (explicit --force only)."""
    import os

    for root, dirs, files in os.walk(path):
        for name in dirs + files:
            p = os.path.join(root, name)
            if not os.path.islink(p):
                os.chmod(p, 0o755 if os.path.isdir(p) else 0o644)
    os.chmod(path, 0o755)
    shutil.rmtree(path)


def main() -> None:  # pragma: no cover
    app()


if __name__ == "__main__":  # pragma: no cover
    sys.exit(app())
