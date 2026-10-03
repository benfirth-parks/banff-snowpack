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


@app.command("prepare-domain")
def prepare_domain_cmd(
    domain_id: Annotated[str, typer.Option()],
    center_lat: Annotated[float, typer.Option()],
    center_lon: Annotated[float, typer.Option()],
    out: Annotated[Path, typer.Option(help="workspace; writes inputs/ and domain/domain.json")],
    size_m: Annotated[float, typer.Option(help="square domain side (multiple of the unit size)")] = 6000.0,
    unit_size_m: Annotated[float, typer.Option(help="unit side; a multiple of the 30 m DEM cell")] = 600.0,
    buffer_m: Annotated[float, typer.Option(help="DEM margin for horizons")] = 15000.0,
    dem_tif: Annotated[Path, typer.Option()] = Path("data/interim/terrain/study_dem_utm11_30m.tif"),
    landcover_raw: Annotated[Path, typer.Option()] = Path("data/raw/esa_worldcover"),
    sites: Annotated[str, typer.Option(help="comma list of study plots (config/plot_forcing.yaml) as site units")] = "",
) -> None:
    """Real terrain domain: Copernicus DEM window + ESA WorldCover land cover + square boundary -> units."""
    import yaml
    from pyproj import Transformer

    from snowagent.ingest.worldcover import download
    from snowagent.terrain.prepare import DomainSpec, prepare_inputs
    from snowagent.terrain.units import add_site_units, build_domain, save_domain

    cfg = load_config()
    spec = DomainSpec(domain_id, center_lat, center_lon, size_m, unit_size_m, buffer_m)
    half = (size_m / 2 + buffer_m) * 1.5  # generous lon/lat box for the tiles
    to_ll = Transformer.from_crs(spec.crs, "EPSG:4326", always_xy=True)
    cx, cy = Transformer.from_crs("EPSG:4326", spec.crs, always_xy=True).transform(center_lon, center_lat)
    (w, s_), (e, n) = to_ll.transform(cx - half, cy - half), to_ll.transform(cx + half, cy + half)
    tiles = [Path(r["path"]) for r in download((w, s_, e, n), landcover_raw) if r.get("path")]
    try:
        paths = prepare_inputs(spec, dem_tif, tiles, out / "inputs")
        d = build_domain(domain_id, paths["dem"], paths["boundary"], paths["landcover"],
                         dataclasses.replace(cfg.units, unit_size_m=unit_size_m), False)
        plots = yaml.safe_load(Path("config/plot_forcing.yaml").read_text())["plots"]
        d = add_site_units(d, {k: (plots[k]["lat"], plots[k]["lon"], float(plots[k]["elevation_m"]))
                               for k in sites.split(",") if k})
    except SnowAgentError as exc:
        _emit_error(exc)
    save_domain(d, out / "domain" / "domain.json")
    lc: dict[str, int] = {}
    for u in d.units:
        lc[u.land_cover.value] = lc.get(u.land_cover.value, 0) + 1
    typer.echo(json.dumps({"domain": str(out / "domain" / "domain.json"), "terrain_version": d.terrain_version,
                           "units": len(d.units), "supported": sum(u.supported for u in d.units),
                           "land_cover_units": lc, "inputs": {k: str(v) for k, v in paths.items()}}, indent=1))


@app.command("case-inputs")
def case_inputs_cmd(
    plot: Annotated[str, typer.Option(help="plot whose stations drive the domain (config/plot_forcing.yaml)")],
    gfs_run: Annotated[str, typer.Option(help="GFS initial time, e.g. 2026-03-23T00:00:00Z")],
    out: Annotated[Path, typer.Option(help="directory for history/recent/forecast series")],
    season_start: Annotated[str | None, typer.Option(help="snow-free start; default config season start")] = None,
) -> None:
    """Weather series for one archived-forecast case with honest availability times (ADR-033)."""
    import yaml

    from snowagent.weather.sources import case_inputs

    run = pd.Timestamp(gfs_run)
    run = run.tz_localize("UTC") if run.tzinfo is None else run.tz_convert("UTC")
    if season_start is None:
        cfg = yaml.safe_load(Path("config/plot_forcing.yaml").read_text())
        y = run.year if run.month >= 8 else run.year - 1
        season_start = f"{y}-{cfg['season_start']}"
    res = case_inputs(plot, run, out, pd.Timestamp(season_start, tz="UTC"))
    (out / "case.json").write_text(json.dumps(res, indent=1, default=str))
    typer.echo(json.dumps(res, indent=1, default=str))


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

    from snowagent.ingest.gfs_archive import extract_run, run_complete, write_run

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
    leads = list(range(0, max_lead + 1, step))

    todo = [r for r in runs if not run_complete(out / f"gfs_{r.strftime('%Y%m%d%H')}.csv", pts, max_lead)]
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


@ingest_app.command("casr")
def ingest_casr(
    raw: Annotated[Path, typer.Option()] = Path("data/raw/casr"),
    out: Annotated[Path, typer.Option()] = Path("data/interim/casr"),
    download_only: Annotated[bool, typer.Option()] = False,
) -> None:
    """CaSR v3.2 reanalysis (README §6): download the study tile, extract hourly SI series at each plot."""
    import yaml

    from snowagent.ingest.casr import download, extract_point, write_point

    recs = download(raw)
    typer.echo(json.dumps({"files": len(recs), "new": sum(r["status"] == "downloaded" for r in recs),
                           "missing": [r["url"] for r in recs if r["status"] == "not_found"]}, indent=1))
    if download_only:
        return
    cfg = yaml.safe_load(Path("config/plot_forcing.yaml").read_text())
    for plot, p in cfg["plots"].items():
        df, meta = extract_point(raw, p["lat"], p["lon"])
        write_point(df, meta | {"plot": plot}, out / f"{plot}.csv")
        typer.echo(f"{plot}: {len(df)} h {df.index.min()} .. {df.index.max()}, cell {meta['distance_km']} km away, "
                   f"{meta['cell_elevation_m']} m")


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
                      "errors": [r.get("error") for r in recs if r.get("error")][:3],
                      "warnings": [r["warning"] for r in recs if r.get("warning")]}
    typer.echo(json.dumps(summary, indent=1))


@ingest_app.command("byk")
def ingest_byk(
    files: Annotated[list[Path] | None, typer.Argument(help="user export files to archive (zip/CSV/XML)")] = None,
    station_report: Annotated[list[Path] | None, typer.Option(
        help="per-station Power BI report (.pbix) whose logger table to extract (needs pbixray; ADR-036)")] = None,
    raw: Annotated[Path, typer.Option()] = Path("archive/byk_export"),
    out: Annotated[Path, typer.Option()] = Path("data/interim/byk_export"),
) -> None:
    """Archive the user's logger-database exports unchanged and convert them per station (ADR-030, ADR-036)."""
    from snowagent.ingest.byk_export import STATION_TABLES, archive, convert, extract_station_table

    for r in archive(list(files or []), raw):
        typer.echo(f"{r['status']}: {r['original_name']} sha256 {r['sha256'][:12]}")
    for pbix in station_report or []:
        r = extract_station_table(pbix, raw / STATION_TABLES)
        typer.echo(f"extracted {r['logger_table']}: {r['rows']} rows {r['first']} .. {r['last']} -> {r['path']}")
    typer.echo(json.dumps(convert(raw, out), indent=1))


@ingest_app.command("fts-dashboard")
def ingest_fts_dashboard(
    pbix: Annotated[Path | None, typer.Argument(help="dashboard .pbix to extract (needs pbixray); omit to convert")] = None,
    out: Annotated[Path, typer.Option()] = Path("data/interim/fts_dashboard"),
) -> None:
    """Visitor Safety dashboard history: archive the record table (no credentials) and convert per station."""
    from snowagent.ingest.fts_dashboard import convert, extract

    if pbix is not None:
        typer.echo(json.dumps(extract(pbix), indent=1))
    typer.echo(json.dumps(convert(out_dir=out), indent=1))


@ingest_app.command("min")
def ingest_min(
    start: Annotated[str | None, typer.Option("--from", help="first observation date (default: 14 days ago)")] = None,
    end: Annotated[str | None, typer.Option("--to", help="last observation date (default: today, UTC)")] = None,
    radius_km: Annotated[float, typer.Option(help="keep reports within this distance of any study plot")] = 15.0,
    archive_dir: Annotated[Path, typer.Option()] = Path("archive/min"),
) -> None:
    """Avalanche Canada MIN public reports near the plots: archive new/edited reports unchanged (ADR-037)."""
    from datetime import date, timedelta

    from snowagent.ingest.min import update

    b = date.fromisoformat(end) if end else pd.Timestamp.now(tz="UTC").date()
    a = date.fromisoformat(start) if start else b - timedelta(days=14)
    typer.echo(json.dumps(update(a, b, archive_dir, radius_km), indent=1))


@ingest_app.command("era5")
def ingest_era5(
    start: Annotated[str, typer.Option(help="first month YYYY-MM")] = "1996-09",
    end: Annotated[str, typer.Option(help="last month YYYY-MM")] = "2026-06",
    months: Annotated[str, typer.Option(help="months to include")] = "9,10,11,12,1,2,3,4,5,6",
    workers: Annotated[int, typer.Option()] = 6,
    out: Annotated[Path, typer.Option()] = Path("data/interim/era5"),
) -> None:
    """ERA5 hourly box over the study plots (skips months already done)."""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    from snowagent.ingest.era5 import extract_month

    keep = {int(m) for m in months.split(",")}
    todo = [(d.year, d.month) for d in pd.date_range(start, end, freq="MS") if d.month in keep
            and not (out / f"era5_box_{d.year}{d.month:02d}.npz").exists()]
    failed = []
    with ProcessPoolExecutor(workers) as ex:  # h5py serialises threads; use processes
        futs = {ex.submit(extract_month, y, m, out): (y, m) for y, m in todo}
        for f in as_completed(futs):
            try:
                f.result()
            except Exception as exc:  # noqa: BLE001 - recorded; rerun retries
                failed.append(f"{futs[f]}: {type(exc).__name__}: {str(exc)[:120]}")
    typer.echo(json.dumps({"months": len(todo), "failed": len(failed), "failures": failed[:20]}, indent=1))


@app.command("baseline")
def baseline(
    plots: Annotated[str, typer.Option()] = "goats_eye,bow_summit,simpson",
    seasons: Annotated[str, typer.Option(help="season start years, comma list or a range like 1996-2020")]
    = "2021,2022,2023,2024,2025",
    observed: Annotated[Path, typer.Option()] = Path("data/interim/obs/observed_profiles.jsonl"),
    out: Annotated[Path, typer.Option()] = Path("artifacts/baseline"),
    corrected: Annotated[bool, typer.Option(help="apply adopted corrections (psum_factor per plot)")] = False,
    era5_only: Annotated[bool, typer.Option(help="reanalysis-only forcing with the transfer file (ADR-025)")] = False,
    reanalysis: Annotated[str, typer.Option(help="era5 | casr (ADR-028)")] = "era5",
    transfer_file: Annotated[Path, typer.Option()] = Path("config/era5_transfer.yaml"),
    workers: Annotated[int, typer.Option()] = 1,
) -> None:
    """Uncorrected baseline: SNOWPACK at each study plot vs station snow depth and observed pits."""
    from concurrent.futures import ProcessPoolExecutor

    from snowagent.baseline.run import save_summary

    if "-" in seasons:
        a, b = (int(x) for x in seasons.split("-"))
        years = list(range(a, b + 1))
    else:
        years = [int(x) for x in seasons.split(",")]
    import yaml

    tf = yaml.safe_load(transfer_file.read_text())["plots"] if era5_only else {}
    jobs = [(plot, y, str(observed), str(out), corrected, tf.get(plot) if era5_only else None, reanalysis)
            for plot in plots.split(",") for y in years]
    with ProcessPoolExecutor(max(1, workers)) as ex:
        done = list(ex.map(_baseline_season, jobs))
    results = dict(done)
    for key, r in done:
        typer.echo(f"{key}: hs={r.get('hs', r.get('error'))} profiles={r.get('profiles', {}).get('pairs')}")
    save_summary(out / "baseline_results.json", results)
    typer.echo(f"wrote {out / 'baseline_results.json'} ({EXPERIMENTAL_LABEL})")


def _baseline_season(job: tuple) -> tuple[str, dict]:
    """One plot-season of `snowagent baseline` (module level so it can run in a process pool)."""
    import yaml

    from snowagent.baseline.assemble import assemble, source_summary
    from snowagent.baseline.evaluate import ghcnd_snwd, hs_scores, observed_at_plot, profile_scores
    from snowagent.baseline.run import plot_unit, run_season
    from snowagent.ingest.fts360 import load_station

    plot, y, observed, out, corrected, era5_only, *rest = job
    reanalysis = rest[0] if rest else "era5"
    observed, out = Path(observed), Path(out)
    cfg = yaml.safe_load(Path("config/plot_forcing.yaml").read_text())
    p = cfg["plots"][plot]
    if isinstance(era5_only, dict):  # explicit transfer parameters (leave-one-season-out folds)
        transfer = era5_only
    else:
        transfer = yaml.safe_load(Path("config/era5_transfer.yaml").read_text())["plots"][plot] if era5_only else None
    unit = plot_unit(plot, p["lat"], p["lon"], p["elevation_m"])
    start = pd.Timestamp(f"{y}-{cfg['season_start']}", tz="UTC")
    end = pd.Timestamp(f"{y + 1}-{cfg['season_end']}", tz="UTC")
    key = f"{plot}_{y}-{y + 1}"
    try:
        pf = assemble(plot, str(start - pd.Timedelta(hours=6)), str(end), era5_only=transfer,  # PSUM lead-in
                      reanalysis=reanalysis)
        complete = pf.data.notna().all(axis=1)
        if not complete.all():  # e.g. ERA5 fluxes not yet published for the last weeks: stop earlier
            last_ok = complete[~complete].index[0] - pd.Timedelta(hours=1)
            if complete[:last_ok].all() and last_ok > start + pd.Timedelta(days=60):
                end = last_ok.floor("D")
                pf.data, pf.sources = pf.data[:end], pf.sources[:end]
                pf.notes.append(f"season truncated at {end} (forcing incomplete afterwards)")
        if corrected and p.get("psum_factor", 1.0) != 1.0:
            pf.data["psum"] = pf.data["psum"] * p["psum_factor"]
            pf.notes.append(f"CORRECTED: precipitation x {p['psum_factor']} (ADR-024)")
        r = run_season(pf, unit, start, end, out / "runs")
    except Exception as exc:  # noqa: BLE001 - reported per season, others continue
        return key, {"error": f"{type(exc).__name__}: {str(exc)[:300]}"}
    col = "Modelled snow depth (vertical)"
    hs_model = r["met"][col] / 100.0 if col in r["met"] else None  # engine reports cm
    hs, swe = {}, {}
    if hs_model is not None:
        for st in p.get("hs_check", []):
            d = load_station(st)
            if not d.empty and "hs_m" in d:
                d = d.set_index("time_utc")
                sc = hs_scores(hs_model, d["hs_m"].where(d["hs_m_qc"] == "ok"))
                if sc.get("days", 0) >= 10:
                    hs[st] = sc
        swe_model = r["met"].get("SWE (of snowpack)")
        for st in p.get("swe_check", []) if swe_model is not None else []:
            d = load_station(st)
            if not d.empty and "swe_mm" in d:  # scored like HS, in m water equivalent
                d = d.set_index("time_utc")
                sc = hs_scores(swe_model / 1000.0, d["swe_mm"].where(d["swe_mm_qc"] == "ok") / 1000.0)
                if sc.get("days", 0) >= 10:
                    swe[st] = sc
        for st in p.get("hs_check_ghcnd", []):
            sc = hs_scores(hs_model, ghcnd_snwd(Path(f"archive/ghcnd/{st}.csv.gz")))
            if sc.get("days", 0) >= 10:
                hs[f"ghcnd_{st}"] = sc
    obs = observed_at_plot(observed, plot, start, end) if observed.exists() else []
    rows, summary = profile_scores(r["profiles"], obs)
    return key, {"forcing_sources": source_summary(pf), "forcing_notes": pf.notes, "hs": hs, "swe": swe,
                 "profiles": summary, "profile_pairs": rows, "run_dir": r["run_dir"],
                 "engine": r["outputs"].extra}


@app.command("era5-transfer")
def era5_transfer(
    plots: Annotated[str, typer.Option()] = "goats_eye,bow_summit,simpson",
    seasons: Annotated[str, typer.Option()] = "2021,2022,2023,2024,2025",
    method: Annotated[str, typer.Option(help="constant | phase (ADR-025)")] = "phase",
    reanalysis: Annotated[str, typer.Option(help="era5 | casr")] = "era5",
    out: Annotated[Path, typer.Option()] = Path("config/era5_transfer.yaml"),
) -> None:
    """Derive the ERA5-only transfer (temperature offsets, precipitation ratios) per plot from station seasons."""
    from snowagent.baseline.era5_transfer import derive, write

    years = [int(x) for x in seasons.split(",")]
    t = derive(plots.split(","), years, method, reanalysis=reanalysis)
    write(out, t, years)
    typer.echo(json.dumps(t, indent=1))


@app.command("era5-transfer-loso")
def era5_transfer_loso(
    plots: Annotated[str, typer.Option()] = "goats_eye,bow_summit,simpson",
    seasons: Annotated[str, typer.Option()] = "2021,2022,2023,2024,2025",
    methods: Annotated[str, typer.Option()] = "constant,phase",
    reanalysis: Annotated[str, typer.Option(help="era5 | casr")] = "era5",
    observed: Annotated[Path, typer.Option()] = Path("data/interim/obs/observed_profiles.jsonl"),
    out: Annotated[Path, typer.Option()] = Path("artifacts/era5_only/loso"),
    workers: Annotated[int, typer.Option()] = 4,
) -> None:
    """Leave-one-season-out comparison of ERA5-only transfer methods: each held-out season is run with
    parameters fitted on the other seasons only, then scored against its snow-depth sensors and pits."""
    from concurrent.futures import ProcessPoolExecutor

    import yaml

    from snowagent.baseline.era5_transfer import fit, season_frame
    from snowagent.baseline.run import save_summary

    cfg = yaml.safe_load(Path("config/plot_forcing.yaml").read_text())
    years = [int(x) for x in seasons.split(",")]
    jobs, params = [], {}
    for plot in plots.split(","):
        frames = {y: season_frame(plot, y, cfg, reanalysis=reanalysis) for y in years}
        for k in years:
            train = pd.concat([f for y, f in frames.items() if y != k])
            for m in methods.split(","):
                t = fit(train, m)
                params[f"{m}/{plot}_{k}"] = t
                jobs.append((plot, k, str(observed), str(out / m), True, t, reanalysis))
    with ProcessPoolExecutor(max(1, workers)) as ex:
        done = list(ex.map(_baseline_season, jobs))
    results = {f"{j[3].split('/')[-1]}/{key}": r for j, (key, r) in zip(jobs, done, strict=True)}
    save_summary(out / "loso_results.json", {"params": params, "results": results})
    typer.echo(f"wrote {out / 'loso_results.json'} ({len(results)} held-out plot-seasons)")


@app.command("hindcast")
def hindcast(
    plots: Annotated[str, typer.Option()] = "goats_eye,bow_summit,simpson",
    leads: Annotated[str, typer.Option(help="forecast lead in days")] = "1,2,3",
    observed: Annotated[Path, typer.Option()] = Path("data/interim/obs/observed_profiles.jsonl"),
    out: Annotated[Path, typer.Option()] = Path("artifacts/hindcast/hindcast_results.json"),
    workers: Annotated[int, typer.Option()] = 6,
) -> None:
    """Forecast hindcast at every pit: nowcast from actuals, then run on the GFS forecast issued 1-3 days before."""
    from snowagent.baseline.hindcast import run_hindcast

    res = run_hindcast(plots.split(","), [int(x) for x in leads.split(",")], observed, out.parent / "runs", out,
                       workers)
    done = [r for r in res if "forecast" in r]
    typer.echo(json.dumps({"jobs": len(res), "done": len(done), "skipped": sum("skipped" in r for r in res),
                           "errors": sum("error" in r for r in res), "output": str(out)}, indent=1))


update_app = typer.Typer(help="Periodic update for the site tool (ADR-037; runbook docs/operations.md).")
app.add_typer(update_app, name="update")


@update_app.command("bootstrap")
def update_bootstrap() -> None:
    """Fresh checkout: restore raw station files from archive/ and the interim conversions the forcing reads."""
    from snowagent.ops.update import bootstrap

    typer.echo(json.dumps(bootstrap(), indent=1, default=str))


def _update_done(res: dict, code: int) -> None:
    """Print the whole result, then exit with the run's code (0 ok, 2 a step failed; ADR-044)."""
    typer.echo(json.dumps(res, indent=1, default=str))
    if code:
        raise typer.Exit(code=code)


@update_app.command("fetch")
def update_fetch() -> None:
    """New FTS360 records, GFS runs, ERA5 months, MIN reports, and the profile inbox (all archived unchanged).
    Exit code 2 when a step failed (listed in failed_steps; the other steps ran)."""
    from snowagent.ops.update import exit_code, fetch

    res = fetch()
    _update_done(res, exit_code(res))


@update_app.command("build")
def update_build(
    workers: Annotated[int, typer.Option()] = 4,
    out: Annotated[Path, typer.Option()] = Path("web/data"),
) -> None:
    """Observed set, live season (three plots), public-report files, site index and status.json (with warnings).
    Exit code 2 when a step failed (listed in failed_steps and status.json; the other steps ran)."""
    from snowagent.ops.update import build, exit_code

    res = build(workers=workers, out_dir=out)
    _update_done(res, exit_code(res))


@obs_app.command("inbox")
def obs_inbox() -> None:
    """File dropped-in profiles from profiles/inbox into the season/site folders (bytes unchanged; ADR-037)."""
    from snowagent.obs.inbox import process_inbox

    typer.echo(json.dumps(process_inbox(), indent=1))


@app.command("web-build")
def web_build(
    plots: Annotated[str, typer.Option()] = "goats_eye,simpson,bow_summit",
    seasons: Annotated[str, typer.Option(help="season start years, comma list or a range like 1996-2025")]
    = "1996-2025",
    out: Annotated[Path, typer.Option(help="data folder of the static site (published with web/)")] = Path("web/data"),
    work: Annotated[Path, typer.Option(help="engine scratch space")] = Path("artifacts/web_work"),
    workers: Annotated[int, typer.Option()] = 4,
) -> None:
    """Site tool data (ADR-035): season runs, daily archived GFS forecasts and pit scores per plot."""
    from snowagent.web.build import build_all

    if "-" in seasons:
        a, b = (int(x) for x in seasons.split("-"))
        years = list(range(a, b + 1))
    else:
        years = [int(x) for x in seasons.split(",")]
    res = build_all(out, work, years, plots.split(","), workers)
    typer.echo(json.dumps({"jobs": len(res), "errors": [r for r in res if "error" in r],
                           "index": str(out / "sites.json")}, indent=1))


@app.command("phase2-report")
def phase2_report(
    workspace: Annotated[Path, typer.Option(help="workspace with domain/, store/, runs/, weather/case.json")],
    pit_id: Annotated[str | None, typer.Option(help="withheld pit (observed after issue) to score afterwards")] = None,
    observed: Annotated[Path, typer.Option()] = Path("data/interim/obs/observed_profiles.jsonl"),
    dtw: Annotated[bool, typer.Option(help="pairwise DTW between unit profiles (R)")] = True,
) -> None:
    """Phase 2 acceptance on a finished real-data run: distinct profiles, leakage audit + probes, withheld pit."""
    from snowagent.engine import snowpack as sp
    from snowagent.errors import DataLeakage, ImmutableRecord
    from snowagent.forecast.acceptance import distinctness, leakage_audit, withheld_pit, write_json
    from snowagent.forecast.pipeline import load_meta
    from snowagent.forecast.pipeline import predict as run_predict
    from snowagent.state.store import StateStore
    from snowagent.terrain.units import load_domain
    from snowagent.weather.io import load_weather

    case = json.loads((workspace / "weather" / "case.json").read_text())
    runs = sorted(p for p in (workspace / "runs").iterdir() if (p / "manifest.json").exists())
    if len(runs) != 1:
        raise typer.BadParameter(f"expected exactly one forecast run in {workspace / 'runs'}, found {len(runs)}")
    run_dir = runs[0]
    meta = load_meta(run_dir)
    d = load_domain(workspace / "domain")
    sites = {u.unit_id for u in d.units if u.unit_id.startswith("site_")}
    inputs = {"history": Path(case["history"]["path"]), "recent": Path(case["recent"]["path"]),
              "forecast": Path(case["forecast"]["path"])}
    report: dict = {"label": EXPERIMENTAL_LABEL, "run_id": meta.run_id, "case": case,
                    "observed_units_excluded": sorted(sites)}
    report["distinctness"] = distinctness(run_dir, sites, dtw=dtw)
    report["leakage"] = leakage_audit(run_dir, workspace / "store", inputs)
    # refusal probes: both must fail before any engine run starts
    cfg = load_config()
    fc = load_weather(inputs["forecast"])
    probes = []
    early = pd.Timestamp(fc.meta.available_time) - pd.Timedelta(hours=1)
    for name, kwargs, expected in (
            ("issue 1 h before the GFS run was available", {"issue_time": early.to_pydatetime()}, DataLeakage),
            ("rerun of the issued forecast", {"issue_time": meta.request.issue_time}, ImmutableRecord)):
        try:
            run_predict(d, fc, StateStore(workspace / "store"), workspace / "runs", sp.find_engine(), cfg.engine,
                        cfg.forcing, meta.request.ensemble, meta.request.output_lead_hours,
                        actuals=load_weather(inputs["recent"]), **kwargs)
            probes.append({"probe": name, "pass": False, "detail": "not refused"})
        except expected as exc:
            probes.append({"probe": name, "pass": True, "detail": f"{type(exc).__name__}: {str(exc)[:160]}"})
    report["leakage"]["probes"] = probes
    report["leakage"]["all_pass"] = report["leakage"]["all_pass"] and all(p["pass"] for p in probes)
    if pit_id:
        pit = next(json.loads(x) for x in observed.read_text().splitlines() if json.loads(x)["profile_id"] == pit_id)
        if pd.Timestamp(pit["obs_time_utc"]) <= pd.Timestamp(meta.request.issue_time):
            raise typer.BadParameter("the withheld pit must be observed after the issue time")
        report["withheld_pit"] = withheld_pit(run_dir, pit, sorted(sites)[0])
    write_json(report, workspace / "acceptance.json")
    summary = {"run_id": meta.run_id, "leakage_all_pass": report["leakage"]["all_pass"],
               "distinct": {k: {kk: v[kk] for kk in ("n_units", "unique_profiles", "hs_m")}
                            for k, v in report["distinctness"]["leads"].items()},
               "dtw_pairwise": report["distinctness"].get("dtw_pairwise_lead_last"),
               "withheld_pit": {k: report["withheld_pit"][k] for k in ("profile_id", "lead_hours", "pit_hs_cm")}
               | {"control": {k: report["withheld_pit"]["control"].get(k) for k in
                              ("model_hs_cm", "hs_diff_cm", "grain_class_agreement", "hardness_mae_index")},
                  "dtw": report["withheld_pit"]["dtw_control"]} if pit_id else None,
               "output": str(workspace / "acceptance.json")}
    typer.echo(json.dumps(summary, indent=1, default=str))


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
