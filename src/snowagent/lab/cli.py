"""``snowagent lab ...``: the Snowpack Agent Lab's commands (ADR-055). Only typer is imported here; the services
(and through them pyarrow) are imported when a command runs, so the main CLI and the daily run do not need the lab
extra."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated

import typer

from snowagent.lab import LAB_DISCLAIMER

lab_app = typer.Typer(help="Snowpack Agent Lab: research benchmark of snowpack-prediction agents (local; "
                           + LAB_DISCLAIMER + ")", no_args_is_help=True)

DataRoot = Annotated[Path, typer.Option("--data-root", help="lab data directory (generated, gitignored)")]
ConfigPath = Annotated[Path, typer.Option("--config", help="lab configuration")]


@lab_app.command("init")
def lab_init(data_root: DataRoot = Path("data/lab"), config: ConfigPath = Path("config/lab.yaml")) -> None:
    """Create the lab's data directories and run registry, and check the configuration."""
    from snowagent.lab.services.data import init_lab
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths

    cfg = load_lab_config(config)
    out = init_lab(LabPaths(data_root))
    out |= {"sites": {c.value: {"plot": s.plot_id, "lat": s.latitude, "lon": s.longitude, "elevation_m": s.elevation_m}
                      for c, s in cfg.sites.items()},
            "season_start": cfg.season_start, "split_mode": cfg.splits.mode.value,
            "splits": cfg.splits.mode_seasons() if cfg.splits.mode.value != "loso" or cfg.splits.loso_holdout
            else {"all_seasons": cfg.splits.all_seasons}, "config_hash": cfg.config_hash()}
    if cfg.splits.is_empty():
        out["note"] = "no seasons configured for the split mode (config/lab.yaml splits)"
    elif cfg.splits.warn_provisional():
        out["note"] = "splits are provisional: recommended seasons, owner to confirm (config/lab.yaml)"
    typer.echo(json.dumps(out, indent=1))


def find_app(start: Path | None = None) -> Path | None:
    """``lab_app/Home.py`` of the checkout this package runs from, else of the working directory or a parent."""
    here = Path(__file__).resolve().parents[3]  # <checkout>/src/snowagent/lab/cli.py
    for d in [here, *(Path(start or Path.cwd()).resolve() / "x").parents]:
        if (d / "lab_app" / "Home.py").is_file():
            return d / "lab_app" / "Home.py"
    return None


def app_command(home: Path, port: int, host: str) -> list[str]:
    import sys

    return [sys.executable, "-m", "streamlit", "run", str(home), "--server.port", str(port), "--server.address",
            host, "--server.headless", "true", "--browser.gatherUsageStats", "false", "--client.toolbarMode", "minimal",
            # ADR-119: the app runs the version it started with until it is restarted (its own Update and restart
            # button, or Ctrl-C), never a half-reloaded mix after a `git pull`
            "--server.fileWatcherType", "none"]


def _serve(cmd: list[str], env: dict) -> int:
    """Run the app and wait for it; start it again when it ends with the restart code (its Update and restart
    button, ADR-119), so the person's Terminal window keeps holding the app and Ctrl-C keeps stopping it."""
    import subprocess

    from snowagent.lab.services.update import RESTART_CODE, SUPERVISED_ENV

    env = env | {SUPERVISED_ENV: "1"}
    try:
        while True:
            code = subprocess.call(cmd, env=env)
            if code != RESTART_CODE:
                return code
            typer.echo("Restarting the app (its files were updated). The page in your browser comes back by itself.")
    except KeyboardInterrupt:
        return 0


@lab_app.command("app")
def lab_app_cmd(
    port: Annotated[int, typer.Option(help="port of the web interface")] = 8501,
    host: Annotated[str, typer.Option(help="127.0.0.1: this computer only (default); 0.0.0.0: also other devices on "
                                           "your network (no login: anyone on that network can use it)")]
    = "127.0.0.1",
    data_root: Annotated[Path | None, typer.Option("--data-root", help="lab data directory (default <checkout>/"
                                                                       "data/lab)")] = None,
    config: Annotated[Path | None, typer.Option("--config", help="lab configuration (default <checkout>/config/"
                                                                 "lab.yaml)")] = None,
    open_browser: Annotated[bool, typer.Option("--open/--no-open", help="open the page in your browser")] = True,
    update_check: Annotated[bool, typer.Option("--update-check/--no-update-check", help="let the app ask GitHub "
                                               "now and then whether a newer version is ready, and offer its "
                                               "Update and restart button (ADR-119)")] = True,
) -> None:
    """Start the lab's web interface (Streamlit) from any directory and print its address. Every step of the loop can
    be run from the browser; heavy work runs as background jobs that outlive the app (ADR-077). Ctrl-C stops the
    app, not the jobs."""
    import os

    from snowagent.lab.services.update import ENV as UPDATE_ENV
    from snowagent.lab.services.update import STARTED_AT_ENV, STARTED_ENV

    home = find_app()
    if home is None:
        typer.echo(json.dumps({"status": "error", "message": "lab_app/Home.py not found: run from the repository "
                               "(or install snowagent from it with pip install -e)"}))
        raise typer.Exit(code=2)
    env = os.environ.copy()
    env[UPDATE_ENV] = "1" if update_check else "0"
    for key in (STARTED_ENV, STARTED_AT_ENV):  # the app reads its own version when it starts
        env.pop(key, None)
    if data_root is not None:
        env["SNOWAGENT_LAB_DATA_ROOT"] = str(data_root.resolve())
    if config is not None:
        env["SNOWAGENT_LAB_CONFIG"] = str(config.resolve())
    url = f"http://localhost:{port}"
    typer.echo(f"Snowpack Agent Lab: {url}  [{LAB_DISCLAIMER}]")
    typer.echo(f"data: {env.get('SNOWAGENT_LAB_DATA_ROOT') or home.parents[1] / 'data' / 'lab'}")
    if host not in ("127.0.0.1", "localhost"):
        typer.echo(f"listening on {host}: other devices on your network can open http://<this computer's address>:"
                   f"{port}. There is no login: anyone on that network can start and stop jobs.")
    typer.echo("Ctrl-C stops the app; background jobs keep running (see the Jobs page).")
    if open_browser:
        import threading
        import webbrowser

        threading.Timer(2.5, webbrowser.open, args=(url,)).start()
    raise typer.Exit(code=_serve(app_command(home, port, host), env))


@lab_app.command("prepare")
def lab_prepare(
    era5: Annotated[bool, typer.Option("--era5/--no-era5", help="fetch the ERA5 months the lab reads (default on; "
                                       "about 1.5-2 h the first time, resumable)")] = True,
    workers: Annotated[int, typer.Option(help="parallel ERA5 months")] = 4,
    config: ConfigPath = Path("config/lab.yaml"),
    bundle: Annotated[bool, typer.Option("--bundle/--no-bundle", help="first copy the extracted ERA5 months from "
                                         "the repository's bundle branch (ADR-079; one download of about 0.2 GB)")]
    = True,
) -> None:
    """Fresh clone -> inputs of `lab import`, from the project's existing sources only (run from the repository
    root): station files and converted logger and dashboard history from archive/, observed profiles from
    profiles/ and observations/, and the ERA5 months of the configured seasons (NSF NCAR mirror). Never overwrites;
    rerun to resume or to retry failed months."""
    from snowagent.lab.services.prepare import prepare
    from snowagent.lab.settings import load_lab_config

    if not (Path("archive").is_dir() and Path("profiles").is_dir()):
        typer.echo(json.dumps({"status": "error", "message": "run from the repository root (archive/ and "
                               "profiles/ not found here; the pit files are not in git: run `snowagent obs pits-restore` first, ADR-165)"}))
        raise typer.Exit(code=2)
    rep = prepare(Path("."), load_lab_config(config), era5=era5, workers=workers, log=typer.echo, bundle=bundle)
    if isinstance(rep.get("era5"), dict) and rep["era5"]["failed"]:
        typer.echo(f"warning: {len(rep['era5']['failed'])} ERA5 months failed (rerun to retry; a month the mirror "
                   "has not published yet stays missing and those station gaps stay unfilled)")


@lab_app.command("prepare-area")
def lab_prepare_area(
    area: Annotated[str, typer.Argument(help="the area lab: jasper (Parker Ridge, config/lab_jasper.yaml), bald "
                                             "(Bald Hills, config/lab_bald.yaml) or bald_era5p (ADR-149)")] = "jasper",
    bundle: Annotated[bool, typer.Option("--bundle/--no-bundle", help="first copy its weather inputs from the "
                                         "repository's bundle branch (ADR-117)")] = True,
) -> None:
    """Inputs of an out-of-area lab (ADR-117), run from the repository root: its weather from the bundle branch,
    its own observed-profile set in its data root. Then `lab init`, `lab import --profiles-file <that set>` and
    `lab build-cases` with its config and data root build the cases `lab train --area-check` scores."""
    from snowagent.lab.services.prepare import prepare_area
    from snowagent.lab.training.area_check import AREAS

    if area not in AREAS:
        raise typer.BadParameter(f"unknown area {area!r}: {', '.join(AREAS)}")
    if not Path("profiles").is_dir():
        typer.echo(json.dumps({"status": "error", "message": "run from the repository root (profiles/ not found; "
                               "the pit files are not in git: run `snowagent obs pits-restore` first, ADR-165)"}))
        raise typer.Exit(code=2)
    prepare_area(area, Path("."), bundle=bundle, log=typer.echo)


@lab_app.command("import")
def lab_import(
    source: Annotated[Path, typer.Option(help="checkout whose data/ is read (read only)")] = Path("."),
    data_root: DataRoot = Path("data/lab"),
    config: ConfigPath = Path("config/lab.yaml"),
    only: Annotated[str | None, typer.Option(help="profiles or weather (default both)")] = None,
    profiles_file: Annotated[Path | None, typer.Option(help="observed-profile set (default <source>/data/interim/obs/"
                                                            "observed_profiles.jsonl)")] = None,
) -> None:
    """Import observed profiles (to depth from surface) and the plots' station weather into the lab's tables."""
    from snowagent.lab.services.data import LabImportError, import_data
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths

    if only not in (None, "profiles", "weather"):
        raise typer.BadParameter("--only must be profiles or weather")
    what = (only,) if only else ("profiles", "weather")
    try:
        report = import_data(source, LabPaths(data_root), load_lab_config(config), what, profiles_file)
    except LabImportError as exc:
        typer.echo(json.dumps({"status": "error", "message": str(exc)}, indent=1))
        raise typer.Exit(code=2) from exc
    typer.echo(json.dumps(report, indent=1, default=str))


@lab_app.command("coverage")
def lab_coverage(data_root: DataRoot = Path("data/lab"), config: ConfigPath = Path("config/lab.yaml")) -> None:
    """Profiles and weather per site and season in the lab's tables."""
    from snowagent.lab.services.data import coverage
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths

    cov = coverage(LabPaths(data_root), load_lab_config(config))
    typer.echo(json.dumps({k: v.to_dict("records") for k, v in cov.items()}, indent=1, default=str))


# --------------------------------------------------------------------------------------------- benchmark (ADR-059)

Source = Annotated[Path, typer.Option("--source", help="checkout whose archive/forecasts/gfs is read (read only)")]


def _build(data_root: Path, config: Path, source: Path, **kw) -> None:
    from snowagent.lab.benchmark.builder import CaseBuildError
    from snowagent.lab.benchmark.leakage import LeakageError
    from snowagent.lab.services.benchmark import build_cases
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths

    try:
        report = build_cases(LabPaths(data_root), load_lab_config(config), source, **kw)
    except LeakageError as exc:
        typer.echo(json.dumps({"status": "leakage", "message": str(exc), "checks": exc.report.to_dict()}, indent=1))
        raise typer.Exit(code=3) from exc
    except (CaseBuildError, ValueError) as exc:
        typer.echo(json.dumps({"status": "error", "message": str(exc)}, indent=1))
        raise typer.Exit(code=2) from exc
    report.pop("case_list")
    if len(report["exclusions"]) > 50:
        report["exclusions"] = f"{len(report['exclusions'])} (listed in the build report file)"
    typer.echo(json.dumps(report, indent=1, default=str))


@lab_app.command("build-cases")
def lab_build_cases(
    data_root: DataRoot = Path("data/lab"), config: ConfigPath = Path("config/lab.yaml"), source: Source = Path("."),
    site: Annotated[list[str] | None, typer.Option(help="BOW, GOAT, SIMP (repeat; default all)")] = None,
    case_type: Annotated[list[str] | None, typer.Option(help="forecast_h72, next_pit (repeat; default both)")] = None,
    split: Annotated[list[str] | None, typer.Option(help="only these splits of the mode (repeat)")] = None,
    holdout: Annotated[str | None, typer.Option(help="mode loso: the held-out season, e.g. 2019-2020")] = None,
    gfs_dir: Annotated[Path | None, typer.Option(help="archived GFS runs (default <source>/archive/forecasts/gfs)")]
    = None,
    prune: Annotated[bool, typer.Option(help="remove earlier cases of the set this build does not produce")] = True,
) -> None:
    """Build benchmark cases for every usable pit under the configured split mode; each case passes the leakage
    checks before it is written (exit 3 on a leak). Prints the build report (counts, exclusions by reason)."""
    _build(data_root, config, source, sites=site, case_types=case_type, splits=split, holdout=holdout,
           gfs_dir=gfs_dir, prune=prune)


@lab_app.command("build-case")
def lab_build_case(
    profile_id: Annotated[str, typer.Option(help="the target pit")],
    data_root: DataRoot = Path("data/lab"), config: ConfigPath = Path("config/lab.yaml"), source: Source = Path("."),
    case_type: Annotated[list[str] | None, typer.Option(help="forecast_h72, next_pit (default both)")] = None,
    holdout: Annotated[str | None, typer.Option(help="mode loso: the held-out season")] = None,
    gfs_dir: Annotated[Path | None, typer.Option(help="archived GFS runs")] = None,
) -> None:
    """Build the case(s) of one target pit (as_of follows the case type's rule)."""
    _build(data_root, config, source, case_types=case_type, profile_ids=[profile_id], holdout=holdout,
           gfs_dir=gfs_dir, prune=False)


@lab_app.command("check-leakage")
def lab_check_leakage(
    case_id: Annotated[str | None, typer.Option(help="one case (default: every built case)")] = None,
    case_set: Annotated[str | None, typer.Option(help="all, split or loso_<season>")] = None,
    data_root: DataRoot = Path("data/lab"), config: ConfigPath = Path("config/lab.yaml"),
) -> None:
    """Re-run the leakage checks on built cases (read only). Exit 3 if any case fails."""
    from snowagent.lab.services.benchmark import check_cases
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths

    reports = check_cases(LabPaths(data_root), load_lab_config(config), case_id, case_set)
    failed = [r.to_dict() for r in reports if r.status != "pass"]
    typer.echo(json.dumps({"checked": len(reports), "passed": len(reports) - len(failed), "failed": failed},
                          indent=1))
    if failed or (case_id and not reports):
        raise typer.Exit(code=3 if failed else 2)


@lab_app.command("cases")
def lab_cases(case_set: Annotated[str | None, typer.Option(help="all, split or loso_<season>")] = None,
              data_root: DataRoot = Path("data/lab")) -> None:
    """Built cases per case set, split, site, case type, forecast source and weather source."""
    from snowagent.lab.services.benchmark import case_index
    from snowagent.lab.storage.paths import LabPaths

    idx = case_index(LabPaths(data_root), case_set)
    if idx.empty:
        typer.echo(json.dumps({"cases": 0}))
        return
    g = idx.groupby(["case_set", "split", "site_code", "case_type", "forecast_source"]).size()
    idx["weather_source"] = idx["weather_source"].fillna("unrecorded")  # cases built before ADR-076
    gw = idx.groupby(["case_set", "split", "site_code", "case_type", "weather_source"]).size()
    typer.echo(json.dumps({"cases": len(idx), "leakage": idx["leakage_check"].value_counts().to_dict(),
                           "counts": {" ".join(k): int(v) for k, v in g.items()},
                           "weather_source_counts": {" ".join(k): int(v) for k, v in gw.items()}}, indent=1))


@lab_app.command("case-truth")
def lab_case_truth(
    case_id: Annotated[str, typer.Option(help="the case")],
    case_set: Annotated[str | None, typer.Option(help="all, split or loso_<season>")] = None,
    unseal: Annotated[bool, typer.Option("--unseal", help="read a sealed-test case's truth (asks for a typed "
                                                          "confirmation)")] = False,
    data_root: DataRoot = Path("data/lab"),
) -> None:
    """The evaluator's view of one case's withheld pit. A sealed-test case is refused unless --unseal is given and
    'UNSEAL <case_id>' is typed."""
    from snowagent.lab.benchmark.loader import SealedTruthError
    from snowagent.lab.services.benchmark import hidden_truth
    from snowagent.lab.storage.paths import LabPaths

    phrase = None
    if unseal:
        phrase = typer.prompt(f"Type 'UNSEAL {case_id}' to read sealed-test truth")
    try:
        truth = hidden_truth(LabPaths(data_root), case_id, case_set, phrase)
    except SealedTruthError as exc:
        typer.echo(json.dumps({"status": "sealed", "message": str(exc)}, indent=1))
        raise typer.Exit(code=4) from exc
    except (FileNotFoundError, ValueError) as exc:
        typer.echo(json.dumps({"status": "error", "message": str(exc)}, indent=1))
        raise typer.Exit(code=2) from exc
    typer.echo(truth.model_dump_json(indent=1))


# --------------------------------------------------------------------------------------------- competition (ADR-065)


def _genomes(agents: list[str] | None, config, paths=None):
    """Family names, standard model names, genome files and, with ``paths``, a training run's agent as
    ``<training run>/<round>/<rank>`` (kept as it ran there, so it keeps its name; an older schema version runs
    the incumbent's physics for the blocks it lacks, as its upgrade would)."""
    from snowagent.lab.genome import NAMED_MODELS, default_genome, default_genomes, load_genome, named_genome
    from snowagent.lab.schemas.genome import AgentFamily

    if not agents:
        return default_genomes(config.genome)
    out = []
    for a in agents:
        if a in {f.value for f in AgentFamily}:
            out.append(default_genome(AgentFamily(a), config.genome))
        elif a in NAMED_MODELS:
            out.append(named_genome(a, config.genome))
        elif paths is not None and not Path(a).is_file() and len(a.strip("/").split("/")) == 3:
            from snowagent.lab.training.loso import resolve_genome

            out.append(resolve_genome(paths, a, config)[0])
        else:
            out.append(load_genome(Path(a), config.genome))
    return out


def _print_board(rows: list[dict], title: str) -> None:
    typer.echo(f"\n{title}")
    typer.echo(f"{'agent':<34}{'comp':>7}{'depth':>7}{'struct':>8}{'crit':>7}{'unc':>7}{'robust':>8}{'cases':>7}"
               f"{'skip':>6}{'fail':>6}{'s/case':>8}")

    def f(x):
        return f"{x:.3f}" if x is not None else "-"

    for r in rows:
        typer.echo(f"{r['label'][:33]:<34}{f(r['composite']):>7}{f(r['snow_depth']):>7}{f(r['layer_structure']):>8}"
                   f"{f(r['critical_layers']):>7}{f(r['uncertainty']):>7}{f(r['robustness']):>8}{r['scored']:>7}"
                   f"{r['skipped']:>6}{r['failures']:>6}{f(r['runtime_s_mean']):>8}")


@lab_app.command("compete")
def lab_compete(
    agents: Annotated[list[str] | None, typer.Option(
        "--agents", help="genome JSON files, family names, standard model names such as snowpack-snow-depth, or a "
        "training run's agent as <training run>/<round>/<rank> "
        "(repeat); default: the default genome of every family")]
    = None,
    case_set: Annotated[str, typer.Option("--cases", "--case-set", help="case set: all, split or loso_<season>")]
    = "all",
    plots: Annotated[list[str] | None, typer.Option("--plots", "--site", help="BOW, GOAT, SIMP (repeat)")] = None,
    case_type: Annotated[list[str] | None, typer.Option(help="forecast_h72, next_pit (repeat)")] = None,
    forecast_source: Annotated[list[str] | None, typer.Option(help="archived_gfs, measured_standin (repeat)")]
    = None,
    weather_source: Annotated[list[str] | None, typer.Option(help="station, mixed, era5_only (repeat; ADR-076)")]
    = None,
    split: Annotated[list[str] | None, typer.Option(help="only these scored splits (repeat)")] = None,
    case_id: Annotated[list[str] | None, typer.Option(help="only these cases (repeat)")] = None,
    limit: Annotated[int | None, typer.Option(help="first N cases (by case id)")] = None,
    workers: Annotated[int, typer.Option(help="parallel cases")] = 1,
    run_id: Annotated[str | None, typer.Option(help="resume this run (same plan), or name a new one")] = None,
    seed: Annotated[int, typer.Option(help="run seed")] = 0,
    engine: Annotated[str, typer.Option(help="auto (site-run reuse when it qualifies, else the binary), none, fake")]
    = "auto",
    snowpack_bin: Annotated[str | None, typer.Option(help="SNOWPACK binary (default SNOWPACK_BIN / PATH)")] = None,
    source: Source = Path("."),
    heldout_season: Annotated[str | None, typer.Option(help="report each agent's train-vs-held-out composite gap "
                                                       "for this season")] = None,
    pit_reset: Annotated[str, typer.Option(help="how visible pits reset SNOWPACK (ADR-124): layers (adopted), depth, "
                                           "layers_model_mass, off")] = "layers",
    sensor_trust: Annotated[float | None, typer.Option(help="ADR-124 test: where the snow depth sensor drives the "
                                                       "snowfall, reset to a depth between the sensor's (0) and the "
                                                       "pit's (1) and let the sensor drive changes from there")]
    = None,
    learn_precip_cm: Annotated[float | None, typer.Option(help="ADR-124 test: learn the winter's snowfall error from "
                                                          "its pits; this many cm of snow at no error is the "
                                                          "starting belief (30 is a fair value)")] = None,
    data_root: DataRoot = Path("data/lab"), config: ConfigPath = Path("config/lab.yaml"),
) -> None:
    """Every agent predicts every scorable case (sealed-test truth is never read), each prediction is scored
    (snow depth, layer structure, critical layers, uncertainty, robustness) and a leaderboard is printed. Resumable
    with --run-id; parallel across cases with --workers."""
    from snowagent.lab.competition.runner import EngineSpec, run_competition
    from snowagent.lab.events import CompetitionFeed
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths
    from snowagent.lab.storage.provenance import new_run_id

    cfg = load_lab_config(config)
    paths = LabPaths(data_root)
    run_id = run_id or new_run_id("competition")
    try:
        genomes = _genomes(agents, cfg, paths)
        feed = CompetitionFeed(  # the Arena's live feed (ADR-078), written here, outside the prediction code
            paths.outputs / "competitions" / run_id,
            [{"agent_id": g.agent_id, "label": g.label or g.display_name, "family": g.family.value} for g in genomes],
            then=lambda d, n: typer.echo(f"  {d}/{n} cases", err=True) if d == n or d % 20 == 0 else None)
        feed.start()
        res = run_competition(
            paths, cfg, genomes, case_set=case_set, splits=split, sites=plots, case_types=case_type,
            forecast_sources=forecast_source, case_ids=case_id, limit=limit, workers=workers, run_id=run_id,
            seed=seed, engine=EngineSpec(kind=engine, binary=snowpack_bin, pit_reset=pit_reset,
                                         sensor_trust=sensor_trust, learn_precip_cm=learn_precip_cm,
                                         source_root=str(source.resolve()) if engine == "auto" else None),
            heldout_season=heldout_season, weather_sources=weather_source, progress=feed.progress)
        feed.finish()
    except ValueError as exc:
        typer.echo(json.dumps({"status": "error", "message": str(exc)}, indent=1))
        raise typer.Exit(code=2) from exc
    typer.echo(f"run {res.run_id}: {len(res.scores['case_id'].unique())} cases, {len(genomes)} agents "
               f"({res.resumed_cases} cases resumed) -> {res.run_dir}  [{LAB_DISCLAIMER}]")
    _print_board(res.leaderboard["overall"], "Leaderboard (all cases)")
    for k, title in (("by_forecast_source", "forecast source"), ("by_weather_source", "weather source"),
                     ("by_site", "plot"), ("by_case_type", "case type")):
        for v, rows in res.leaderboard.get(k, {}).items():
            _print_board(rows, f"{title}: {v}")
    if res.heldout_gap:
        typer.echo(f"\nTrain vs held-out season {heldout_season}:")
        typer.echo(json.dumps(res.heldout_gap, indent=1))
    for w in res.warnings:
        typer.echo(f"warning: {w}")


@lab_app.command("leaderboard")
def lab_leaderboard(
    run_id: Annotated[str | None, typer.Option(help="competition run (default: the latest)")] = None,
    heldout_season: Annotated[str | None, typer.Option(help="also print the train-vs-held-out gap")] = None,
    data_root: DataRoot = Path("data/lab"), config: ConfigPath = Path("config/lab.yaml"),
) -> None:
    """Print a finished competition's leaderboard (and optionally the held-out gap)."""
    from snowagent.lab.competition.runner import heldout_gap, list_runs, load_run
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths

    paths = LabPaths(data_root)
    runs = list_runs(paths)
    if not runs:
        typer.echo(json.dumps({"runs": 0}))
        raise typer.Exit(code=2)
    rid = run_id or runs[0]
    df, lb = load_run(paths, rid)
    typer.echo(f"run {rid}  [{LAB_DISCLAIMER}]")
    _print_board(lb["leaderboard"]["overall"], "Leaderboard (all cases)")
    for v, rows in lb["leaderboard"]["by_forecast_source"].items():
        _print_board(rows, f"forecast source: {v}")
    for v, rows in lb["leaderboard"].get("by_weather_source", {}).items():  # runs scored since ADR-076
        _print_board(rows, f"weather source: {v}")
    if heldout_season:
        try:
            gap = heldout_gap(df, heldout_season, load_lab_config(config).scoring_weights)
        except ValueError as exc:
            typer.echo(json.dumps({"status": "error", "message": str(exc)}))
            raise typer.Exit(code=2) from exc
        typer.echo(json.dumps(gap, indent=1))


@lab_app.command("rescore")
def lab_rescore(
    run_id: Annotated[str, typer.Option(help="finished competition run to re-score")],
    new_run_id: Annotated[str | None, typer.Option(help="id of the re-scored run (default <run_id>-<scoring "
                                                   "version>)")] = None,
    data_root: DataRoot = Path("data/lab"), config: ConfigPath = Path("config/lab.yaml"),
) -> None:
    """Re-score a competition's stored predictions under the current scoring version (ADR-074) into a new run; no
    agent runs and the source run is not changed."""
    from snowagent.lab.competition.runner import rescore_competition
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths

    try:
        res = rescore_competition(LabPaths(data_root), load_lab_config(config), run_id, new_run_id)
    except ValueError as exc:
        typer.echo(json.dumps({"status": "error", "message": str(exc)}, indent=1))
        raise typer.Exit(code=2) from exc
    typer.echo(f"run {res.run_id}: {run_id} re-scored ({len(res.scores['case_id'].unique())} cases) -> {res.run_dir}"
               f"  [{LAB_DISCLAIMER}]")
    _print_board(res.leaderboard["overall"], "Leaderboard (all cases)")


# --------------------------------------------------------------------------------------------- training (ADR-066..069)


def _train_options(cfg, rounds, population, survivors, mutation_strength, crossover_share, seed, plots, case_types,
                   initial, monitor_season, gap_flag_rounds, engine, snowpack_bin, case_set="all", splits=None,
                   screen_cases=None, family_slots=False, segment_reuse=True, weather_sources=None,
                   locked_seasons=None, selection=None, drift_penalty=None, stop_when_flat=None, trim=False,
                   standard=None, focus=None, period=None):
    from snowagent.lab.competition.runner import EngineSpec
    from snowagent.lab.training.loop import TrainOptions

    init = None
    if initial and not (len(initial) == 1 and initial[0] in ("defaults", "default")):
        init = _genomes(initial, cfg)
    opts = TrainOptions.from_config(
        cfg, rounds=rounds, population=population, survivors=survivors, mutation_strength=mutation_strength,
        crossover_share=crossover_share, seed=seed, plots=plots, case_types=case_types, initial=init,
        monitor_season=monitor_season, gap_flag_rounds=gap_flag_rounds, case_set=case_set, splits=splits,
        engine=EngineSpec(kind=engine, binary=snowpack_bin, segments=segment_reuse), screen_cases=screen_cases,
        family_slots=family_slots or None, weather_sources=weather_sources or None, locked_seasons=locked_seasons,
        selection=selection, drift_penalty=drift_penalty, stop_when_flat=stop_when_flat, trim=trim or None,
        focus=focus, period=period)
    if standard:  # ADR-134: "none" (or "plain") starts from plain SNOWPACK only, as runs did before
        from dataclasses import replace

        opts = replace(opts, standard=None if standard in ("none", "plain") else standard)
    return opts


def _gene_lists(cfg, opts, genes: list[str]) -> dict[str, list[str]]:
    """ADR-161: the --genes list per family of the initial population, each name checked against the family."""
    from snowagent.lab.genome import default_genomes

    names = [n.strip() for g in genes for n in g.split(",") if n.strip()]
    fams = sorted({g.family for g in (opts.initial or default_genomes(cfg.genome))}, key=lambda f: f.value)
    out = {}
    for f in fams:
        allowed = cfg.genome.family_genes(f)
        bad = [n for n in names if n not in allowed or allowed[n][1].fixed]
        if bad:
            raise ValueError(f"--genes: {', '.join(bad)} not an evolvable gene of the {f.value} family")
        out[f.value] = names
    return out


Rounds = Annotated[int | None, typer.Option(help="competitions to run (default training.rounds)")]
Population = Annotated[int | None, typer.Option(help="agents per round from round 2 (default training.population)")]
Survivors = Annotated[int | None, typer.Option(help="top agents kept unchanged as parents (default 2)")]
Strength = Annotated[float | None, typer.Option(help="mutation strength in (0, 1] (default 0.2)")]
CrossShare = Annotated[float | None, typer.Option(help="share of children made by crossover (default 0.25)")]
Seed = Annotated[int, typer.Option(help="seed: the same seed gives the same populations and scores")]
Plots = Annotated[list[str] | None, typer.Option("--plots", help="BOW, GOAT, SIMP (repeat; default all)")]
CaseTypes = Annotated[list[str] | None, typer.Option("--case-types", help="forecast_h72, next_pit (repeat)")]
WeatherSources = Annotated[list[str] | None, typer.Option(
    "--weather-sources", help="station, mixed, era5_only (repeat; default every case; ADR-076)")]
Initial = Annotated[list[str] | None, typer.Option(
    "--initial", help="initial genomes: JSON files or family names (repeat); default the five family defaults")]
Monitor = Annotated[str | None, typer.Option(help="season of the per-round train-vs-held-out gap (default: the most "
                                                  "recent completed season with cases at every plot)")]
GapRounds = Annotated[int | None, typer.Option(help="flag when the gap widens this many rounds in a row")]
Engine = Annotated[str, typer.Option(help="auto (the SNOWPACK binary), none (SNOWPACK skipped), fake (tests)")]
SnowpackBin = Annotated[str | None, typer.Option(help="SNOWPACK binary (default SNOWPACK_BIN / PATH)")]
Workers = Annotated[int, typer.Option(help="parallel cases")]
ScreenCases = Annotated[int | None, typer.Option(help="score a child with new SNOWPACK physics genes on a fixed "
                                                      "stratified sample of K cases first; only one beating the "
                                                      "worst survivor there is scored on all cases (default off)")]
FamilySlots = Annotated[bool, typer.Option("--family-slots", help="reserve one slot per family for a mutant of that "
                                                                  "family's best agent (default off)")]
SeedFrom = Annotated[str | None, typer.Option(
    help="add the best evolved agents of this earlier training run (its last round) to the initial population "
         "(ADR-085)")]
SeedTop = Annotated[int, typer.Option(help="how many of --seed-from's best agents to add")]
Selection = Annotated[str | None, typer.Option(
    help="how survivors are chosen: consistent (composite less a penalty for uneven results across winters and "
         "plots; the default) or composite (ADR-087)")]
DriftPenalty = Annotated[float | None, typer.Option(
    help="subtract this x the agent's drift from its family's standard settings when choosing survivors (one gene "
         "moved across its whole range = 1; default 0.002; 0 = off; ADR-089)")]
StopWhenFlat = Annotated[int | None, typer.Option(
    help="stop the run on its own once the best locked-winter score has not improved for this many rounds in a row "
         "(default 8; 0 = off; needs locked winters; ADR-094)")]
LockedSeasons = Annotated[int | None, typer.Option(
    help="the N most recent seasons never train or select agents; the leaders are scored on them every round "
         "(default 3; 0 = off; ADR-083)")]
AreaCheck = Annotated[str | None, typer.Option(
    help="also score each round's tested agents on another area's pits, never training or choosing on them: jasper "
         "(Parker Ridge; its cases from `snowagent lab prepare-area jasper`; ADR-117)")]
TrainArea = Annotated[str | None, typer.Option(
    help="also TRAIN on another area's pits: jasper (Parker Ridge; cases from `snowagent lab prepare-area jasper`). "
         "Its locked winters join the locked test. With --plots, the plots left out are never trained on and keep "
         "their own genes at their starting values, for a test at a plot the agents never saw (ADR-138)")]
Focus = Annotated[float | None, typer.Option(
    help="aim survivor selection at next-pit forecasts, spring pits (March to June) and crusts: each weighs 1 + this "
         "in the mean case score, plus a bonus for the share of crusts found; reports and locked winters are scored "
         "as usual (default 0 = off; ADR-135)")]
Period = Annotated[str | None, typer.Option(
    help="train and test on one part of the winter only: spring (pits March to June), warm (pits after at least 6 "
         "hours at or above 0 degC in the 3 days before) or spring_or_warm; training, locked winters and the area "
         "check alike (default: every case; ADR-136)")]
Trim = Annotated[bool, typer.Option(
    "--trim", help="evolve only each family's short list of genes (config genome.trimmed, from `lab sensitivity`); "
                   "the other genes keep the values they start with (ADR-123)")]
Genes = Annotated[list[str] | None, typer.Option(
    "--genes", help="evolve only these genes (repeat, or comma-separated); every other gene keeps the value it starts "
                    "with, as --trim does with the configured list (ADR-161)")]
Standard = Annotated[str | None, typer.Option(
    help="the standard SNOWPACK a run with SNOWPACK agents starts from and is measured against: it joins round 1, "
         "the drift penalty counts from its settings, and reports and the promotion check compare with it "
         "(default snowpack-era5-fix, the best standard; none = plain SNOWPACK, as before; ADR-134)")]
SegmentReuse = Annotated[bool, typer.Option("--segment-reuse/--no-segment-reuse",
                                            help="share SNOWPACK restart states between cases with identical "
                                                 "visible inputs (same results, faster; default on)")]


@lab_app.command("train")
def lab_train(
    rounds: Rounds = None, population: Population = None, survivors: Survivors = None,
    mutation_strength: Strength = None, crossover_share: CrossShare = None, seed: Seed = 0, plots: Plots = None,
    case_types: CaseTypes = None, initial: Initial = None, monitor_season: Monitor = None,
    gap_flag_rounds: GapRounds = None, workers: Workers = 1, screen_cases: ScreenCases = None,
    family_slots: FamilySlots = False, segment_reuse: SegmentReuse = True, weather_sources: WeatherSources = None,
    locked_seasons: LockedSeasons = None, selection: Selection = None, drift_penalty: DriftPenalty = None,
    stop_when_flat: StopWhenFlat = None, seed_from: SeedFrom = None, seed_top: SeedTop = 2,
    area_check: AreaCheck = None, trim: Trim = False, standard: Standard = None, focus: Focus = None,
    train_area: TrainArea = None, genes: Genes = None,
    period: Period = None,
    run_id: Annotated[str | None, typer.Option(help="name the run (default training-<time>-<hash>)")] = None,
    resume: Annotated[bool, typer.Option("--resume", help="continue --run-id (default: the latest unfinished run) "
                                                          "with its stored options")] = False,
    estimate_only: Annotated[bool, typer.Option("--estimate-only", help="print the time estimate and stop")] = False,
    engine: Engine = "auto", snowpack_bin: SnowpackBin = None,
    data_root: DataRoot = Path("data/lab"), config: ConfigPath = Path("config/lab.yaml"),
) -> None:
    """Training: round 1 scores the initial population on every training case (split mode `all`: every season);
    each later round keeps the top agents unchanged and adds mutations and crossovers of them. Prints a time estimate
    first; resumable (--resume); every round is committed atomically to the run registry. The per-round
    train-vs-held-out gap is a warning signal only; `snowagent lab check-loso` is the promotion check."""
    from snowagent.lab.competition.runner import EngineSpec
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths
    from snowagent.lab.training.loop import TrainingStopped, run_training

    cfg = load_lab_config(config)
    try:
        opts = _train_options(cfg, rounds, population, survivors, mutation_strength, crossover_share, seed, plots,
                              case_types, initial, monitor_season, gap_flag_rounds, engine, snowpack_bin,
                              screen_cases=screen_cases, family_slots=family_slots, segment_reuse=segment_reuse,
                              weather_sources=weather_sources, locked_seasons=locked_seasons,
                              selection=selection, drift_penalty=drift_penalty, stop_when_flat=stop_when_flat,
                              trim=trim, standard=standard, focus=focus, period=period)
        if area_check and not resume:
            from dataclasses import replace

            opts = replace(opts, area_check=area_check)
        if train_area and not resume:
            from dataclasses import replace

            opts = replace(opts, train_area=train_area)
        if genes and not resume:
            from dataclasses import replace

            opts = replace(opts, trim=True, trimmed_genes=_gene_lists(cfg, opts, genes))
        if seed_from and not resume:
            from dataclasses import replace

            from snowagent.lab.genome import default_genomes, widen_seeds
            from snowagent.lab.training.loop import seed_genomes

            extra, recs = seed_genomes(LabPaths(data_root), seed_from, seed_top, spec=cfg.genome)
            extra = widen_seeds(extra, opts.initial, cfg.genome)  # ADR-136: process seeds join a process_melt run
            opts = replace(opts, initial=list(opts.initial or default_genomes(cfg.genome)) + extra, seeded_from=recs)
            typer.echo(f"initial population adds {', '.join(x['label'] for x in recs)} from {seed_from}")
        res = run_training(LabPaths(data_root), cfg, opts, workers=workers, run_id=run_id, resume=resume,
                           log=typer.echo, estimate_only=estimate_only,
                           engine=EngineSpec(kind=engine, binary=snowpack_bin) if resume and snowpack_bin else None,
                           progress=lambda d, n: typer.echo(f"  {d}/{n} cases", err=True)
                           if d == n or d % 50 == 0 else None)
    except TrainingStopped as exc:
        typer.echo(json.dumps({"status": "stopped", "message": str(exc)}))
        raise typer.Exit(code=5) from exc
    except ValueError as exc:
        typer.echo(json.dumps({"status": "error", "message": str(exc)}, indent=1))
        raise typer.Exit(code=2) from exc
    if estimate_only:
        typer.echo(json.dumps(res.summary, indent=1))
        return
    s = res.summary
    typer.echo(f"\nBest composite per round  [{LAB_DISCLAIMER}]")
    for b, g in zip(s["best_per_round"], s["gap_trace"], strict=True):
        gap = f"{g['gap']:+.4f}" if g["gap"] is not None else "-"
        typer.echo(f"  round {b['round']:>3}  {b['composite']:.4f}  {b['label']:<28} gap {gap}"
                   + ("  FLAG" if g["flag"] else ""))
    w = s["winner"]
    typer.echo(f"winner {w['label']} ({w['agent_id']}, {w['family']}), composite {w['composite']}; reference "
               f"{w['reference']}")
    typer.echo("changed genes vs default: " + (", ".join(f"{k} {a} -> {b}" for k, (a, b) in
                                                         w["changed_vs_default"].items()) or "none"))
    c = s["cache"]
    seg = c.get("segments_run", 0) + c.get("segments_reused", 0)
    typer.echo(f"cache: {c['hits']} of {c['pairs']} pairs ({c['hit_rate']:.0%}); {c['engine_runs']} engine runs"
               + (f" ({c.get('engine_s', 0):.0f} s), restart segments reused {c['segments_reused']} of {seg}"
                  if seg else "") + f"; rounds took {s['wall_s_rounds_total']:.0f} s")
    if s.get("screen"):
        typer.echo(f"screen: {s['screen']['candidates']} new-physics children screened, "
                   f"{s['screen']['screened_out']} screened out")
    typer.echo(f"monitor season {s['monitor_season']}: {s['gap_note']}")


@lab_app.command("sensitivity")
def lab_sensitivity(
    family: Annotated[str, typer.Argument(help="agent family: column, process, recipe, story, scorecard ...")],
    cases: Annotated[int, typer.Option(help="training-winter cases in the stratified sample (0 = all)")] = 150,
    keep: Annotated[int | None, typer.Option(help="genes a trimmed run evolves (default 12)")] = None,
    seed: Seed = 0, workers: Workers = 1, engine: Engine = "none", snowpack_bin: SnowpackBin = None,
    data_root: DataRoot = Path("data/lab"), config: ConfigPath = Path("config/lab.yaml"),
) -> None:
    """Gene sensitivity screen (ADR-123): moves each gene of the family on its own, on training winters only (the
    locked test winters are never loaded), and ranks the genes by how far the score moves. Prints the table and
    the `genome.trimmed` line for the configuration; writes outputs/sensitivity/<family>.json."""
    from snowagent.lab.competition.runner import EngineSpec
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths
    from snowagent.lab.training import sensitivity

    cfg, paths = load_lab_config(config), LabPaths(data_root)
    try:
        res = sensitivity.screen_family(paths, cfg, family, cases=cases or None, seed=seed, workers=workers,
                                        engine=EngineSpec(kind=engine, binary=snowpack_bin),
                                        progress=lambda d, n: typer.echo(f"  {d}/{n} cases", err=True)
                                        if d == n or d % 50 == 0 else None)
    except ValueError as exc:
        typer.echo(json.dumps({"status": "error", "message": str(exc)}, indent=1))
        raise typer.Exit(code=2) from exc
    chosen = sensitivity.choose(res, cfg.genome, keep or sensitivity.KEEP)
    out = paths.outputs / "sensitivity"
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{res['family']}.json").write_text(json.dumps(res | {"chosen": chosen}, indent=1) + "\n")
    for line in sensitivity.format_table(res, chosen):
        typer.echo(line)
    typer.echo(f"genome.trimmed: {res['family']}: [{', '.join(chosen)}]  [{LAB_DISCLAIMER}]")


@lab_app.command("lineage")
def lab_lineage(
    genome: Annotated[str, typer.Argument(help="genome hash, unique prefix or agent id")],
    run_id: Annotated[str | None, typer.Option(help="look in this training run only")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="print the records as JSON")] = False,
    data_root: DataRoot = Path("data/lab"),
) -> None:
    """Ancestry of an evolved agent: parents, operator and changed genes back to the initial genomes."""
    from snowagent.lab.storage.paths import LabPaths
    from snowagent.lab.training.lineage import format_ancestry, lineage_for

    try:
        _rec, chain = lineage_for(LabPaths(data_root), genome, run_id)
    except KeyError as exc:
        typer.echo(json.dumps({"status": "error", "message": str(exc)}))
        raise typer.Exit(code=2) from exc
    if as_json:
        typer.echo(json.dumps(chain, indent=1, default=str))
        return
    for line in format_ancestry(chain):
        typer.echo(line)


@lab_app.command("check-loso")
def lab_check_loso(
    genome: Annotated[str, typer.Option(help="genome JSON file, or <training run>/<round>/<rank> (that run's options "
                                             "and seed are re-used)")],
    rounds: Rounds = None, population: Population = None, survivors: Survivors = None,
    mutation_strength: Strength = None, crossover_share: CrossShare = None,
    seed: Annotated[int | None, typer.Option(help="seed (default: the training run's, else 0)")] = None,
    plots: Plots = None, case_types: CaseTypes = None, initial: Initial = None,
    weather_sources: WeatherSources = None,
    season: Annotated[list[str] | None, typer.Option(help="only these held-out seasons (repeat; default all)")]
    = None,
    random_winters: Annotated[int | None, typer.Option(help="hold out this many winters drawn at random instead "
                                                            "(ADR-166; never the locked test winters)")] = None,
    draw_seed: Annotated[int | None, typer.Option(help="the draw number of --random-winters: the same number "
                                                       "repeats a draw (default: a new one, printed)")] = None,
    workers: Workers = 1,
    check_id: Annotated[str | None, typer.Option(help="resume this check, or name a new one")] = None,
    estimate_only: Annotated[bool, typer.Option("--estimate-only", help="print the estimate and stop")] = False,
    source: Source = Path("."), engine: Engine = "auto", snowpack_bin: SnowpackBin = None,
    data_root: DataRoot = Path("data/lab"), config: ConfigPath = Path("config/lab.yaml"),
) -> None:
    """Promotion check (CLAUDE.md principle 3): re-run the whole training once per season with that season held out
    (split mode loso), score each fold's best agent and the SNOWPACK incumbent on the held-out cases, and report per
    season, pooled, and PASS/FAIL against the rule of ADR-068. Builds missing loso case sets; prints an estimate
    first; resumable with --check-id. May take hours."""
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths
    from snowagent.lab.training.loso import check_loso

    cfg = load_lab_config(config)
    try:
        opts = _train_options(cfg, None, None, None, None, None, seed or 0, plots, case_types, initial, None, None,
                              engine, snowpack_bin, weather_sources=weather_sources)
        over = {"rounds": rounds, "population": population, "survivors": survivors,
                "mutation_strength": mutation_strength, "crossover_share": crossover_share, "seed": seed}
        res = check_loso(LabPaths(data_root), cfg, genome, opts, workers=workers, check_id=check_id,
                         source=source.resolve(), seasons=season, log=typer.echo, estimate_only=estimate_only,
                         overrides=over, random_winters=random_winters, draw_seed=draw_seed)
    except ValueError as exc:
        typer.echo(json.dumps({"status": "error", "message": str(exc)}, indent=1))
        raise typer.Exit(code=2) from exc
    if estimate_only:
        typer.echo(json.dumps(res.result["estimate"], indent=1))
        return
    r = res.result
    typer.echo(f"\nLeave-one-season-out check {res.check_id}  [{LAB_DISCLAIMER}]")
    typer.echo(f"{'season':<11}{'cases':>6}  {'fold winner':<26}{'evolved':>9}{'SNOWPACK':>10}{'diff':>8}  outcome")
    for f in r["per_season"]:
        def g(x):
            return f"{x:.4f}" if isinstance(x, float) else "-"

        typer.echo(f"{f['season']:<11}{f['holdout_cases'] or 0:>6}  {f['winner'][:25]:<26}{g(f['winner_composite']):>9}"
                   f"{g(f['incumbent_composite']):>10}{g(f['difference']):>8}  {f['outcome']}")
    if r.get("draw"):
        d = r["draw"]
        typer.echo(f"held-out winters drawn at random: draw {d['seed']}, {len(d['drawn'])} of {len(d['pool'])} "
                   f"(repeat it with --random-winters {d['winters']} --draw-seed {d['seed']})")
    typer.echo(f"pooled over {r['pooled_cases']} held-out cases: evolved {r['pooled_evolved_composite']} vs SNOWPACK "
               f"{r['pooled_incumbent_composite']}; wins {r['wins']}, losses {r['losses']}, ties {r['ties']}")
    typer.echo(f"rule: {r['rule']}")
    if r.get("differs_from_training_run"):
        typer.echo("note: reduced configuration ("
                   + ", ".join(f"{k} {v['check']} instead of {v['training_run']}"
                               for k, v in r["differs_from_training_run"].items())
                   + "): the check tests that cheaper procedure, a weaker test of the training run")
    typer.echo(f"RESULT: {'PASS' if r['passed'] else 'FAIL'}"
               + ("" if r["passed"] else " (the evolved agent stays a research entry; SNOWPACK remains the site "
                                         "model)"))


@lab_app.command("winter-test")
def lab_winter_test(
    initial: Initial = None,
    rounds: Annotated[int, typer.Option(help="training rounds per fold")] = 6,
    population: Annotated[int, typer.Option(help="agents per round")] = 10,
    survivors: Annotated[int, typer.Option(help="top agents kept unchanged as parents")] = 2,
    folds: Annotated[int, typer.Option(help="folds: the winters are dealt out in turn (winter i to fold i mod N), "
                                            "so each fold's winters are spread over the years")] = 5,
    every_winter: Annotated[bool, typer.Option("--every-winter", help="one fold per winter instead of --folds "
                                                                      "(the slowest, most thorough test)")] = False,
    include_locked: Annotated[bool, typer.Option("--include-locked", help="also hold out and train on the locked "
                                                                          "test winters (default: kept out)")]
    = False,
    seed: Seed = 0, plots: Plots = None, case_types: CaseTypes = None, weather_sources: WeatherSources = None,
    season: Annotated[list[str] | None, typer.Option(help="only these held-out winters (repeat; default all)")]
    = None,
    workers: Workers = 1,
    check_id: Annotated[str | None, typer.Option(help="resume this test, or name a new one")] = None,
    estimate_only: Annotated[bool, typer.Option("--estimate-only", help="print the estimate and stop")] = False,
    source: Source = Path("."), engine: Engine = "auto", snowpack_bin: SnowpackBin = None,
    data_root: DataRoot = Path("data/lab"), config: ConfigPath = Path("config/lab.yaml"),
) -> None:
    """Winter-by-winter test of a training procedure (ADR-125): train on all winters but the held-out ones, score
    the trained winner, its untrained start and standard SNOWPACK on the held-out winters, repeat for every fold,
    and report each winter's score with the average and spread. The locked test winters stay out. Resumable with
    --check-id. Default: the column model, 5 folds, 6 rounds of 10 agents (about 1 hour on the owner's Mac with 8
    workers, measured 2026-11-19; the estimate printed first is from this computer's own measured speeds, ADR-126)."""
    from snowagent.lab.services.winter_test import plain_summary, pts, signed, took
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths
    from snowagent.lab.training.loso import check_loso

    cfg = load_lab_config(config)
    try:
        opts = _train_options(cfg, rounds, population, survivors, None, None, seed, plots, case_types,
                              initial or ["column"], None, None, engine, snowpack_bin,
                              weather_sources=weather_sources)
        res = check_loso(LabPaths(data_root), cfg, None, opts, workers=workers, check_id=check_id,
                         source=source.resolve(), seasons=season, log=typer.echo, estimate_only=estimate_only,
                         folds=None if every_winter else folds, exclude_locked=not include_locked)
    except ValueError as exc:
        typer.echo(json.dumps({"status": "error", "message": str(exc)}, indent=1))
        raise typer.Exit(code=2) from exc
    if estimate_only:
        typer.echo(json.dumps(res.result["estimate"], indent=1))
        return
    r = res.result
    typer.echo(f"\nWinter-by-winter test {res.check_id}  [{LAB_DISCLAIMER}]")
    typer.echo(f"{'winter':<11}{'group':<10}{'pits':>6}{'trained':>9}{'untrained':>10}{'SNOWPACK':>10}"
               f"{'vs SNOWPACK':>13}{'training added':>16}")
    for w in r.get("per_winter") or []:
        typer.echo(f"{w['season']:<11}{str(w['fold']):<10}{w['cases']:>6}{pts(w['evolved']):>9}{pts(w['start']):>10}"
                   f"{pts(w['snowpack']):>10}{signed(w['difference']):>13}{signed(w['training_gain']):>16}")
    for line in [*plain_summary(r), took(r)]:
        if line:
            typer.echo(line)


@lab_app.command("group-check")
def lab_group_check(
    test_run: Annotated[str | None, typer.Option(help="training run whose locked test winters are the test (default "
                                                      "the newest run with locked winters)")] = None,
    member: Annotated[list[str] | None, typer.Option(help="group member (repeat): standard, kind:<hybrid|analogue|"
                                                          "persistence|weather_rule>, nudge:<more-snow|less-snow|"
                                                          "warmer|colder> or <training run>/<round>/<rank>; default "
                                                          "the suggested group")] = None,
    workers: Workers = 1,
    check_id: Annotated[str | None, typer.Option(help="name of the check (default a new id)")] = None,
    data_root: DataRoot = Path("data/lab"), config: ConfigPath = Path("config/lab.yaml"),
) -> None:
    """Group check (ADR-095): pool a varied group of agents into one consensus profile per pit on a training run's
    locked test winters, score it like an agent, and test whether the agents' disagreement marks the pits where they
    are wrong and whether weak layers most of them forecast are really there. Reuses saved predictions; writes the
    result and a plain-language report."""
    from snowagent.lab.services.group import load_group_check, locked_runs, run_group_check
    from snowagent.lab.services.group_report import group_report, save_group_report
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths

    cfg = load_lab_config(config)
    paths = LabPaths(data_root)
    try:
        run = test_run or next(iter(locked_runs(paths)), None)
        if run is None:
            raise ValueError("no training run with locked test winters and a finished round yet")
        res = run_group_check(paths, cfg, run, member or None, workers=workers, check_id=check_id, log=typer.echo)
    except ValueError as exc:
        typer.echo(json.dumps({"status": "error", "message": str(exc)}, indent=1))
        raise typer.Exit(code=2) from exc
    files = save_group_report(paths, group_report(load_group_check(paths, res["check_id"])), res["check_id"])
    std = res.get("standard") or {}
    typer.echo(f"\nGroup check {res['check_id']} on {res['cases']} pits  [{LAB_DISCLAIMER}]")
    if std:
        typer.echo(f"group {std['group']:.4f} vs standard SNOWPACK {std['agent']:.4f}: {res.get('vs_standard')} "
                   f"(better on {std['group_wins']}, worse on {std['group_losses']})")
    du, su = res["disagreement_useful"]
    typer.echo(f"disagreement marks wrong answers: depth {du}, layering {su}; agreed weak layers more often real: "
               f"{res['agreement_useful']}")
    typer.echo("report: " + ", ".join(str(f) for f in files))


@lab_app.command("stability-check")
def lab_stability_check(
    agent: Annotated[list[str] | None, typer.Option(help="agent whose SNOWPACK runs are read (repeat): standard, a "
                                                         "standard model's name, <training run>/<round>/<rank> or a "
                                                         "genome JSON file; default standard")] = None,
    workers: Workers = 1,
    include_locked: Annotated[bool, typer.Option(help="also the locked test winters (off: they stay a clean final "
                                                      "test)")] = False,
    plots: Annotated[list[str] | None, typer.Option("--plot", help="only these plots (BOW, GOAT, SIMP)")] = None,
    limit: Annotated[int | None, typer.Option(help="only the first N pits (a quick try)")] = None,
    check_id: Annotated[str | None, typer.Option(help="name of the check; the same name resumes it")] = None,
    data_root: DataRoot = Path("data/lab"), config: ConfigPath = Path("config/lab.yaml"),
) -> None:
    """Stability check (ADR-127): do SNOWPACK's stability numbers (SK38, critical crack length, RTA) and the
    lemons count of its layers point at the layers that broke in the withheld pits' stability tests? Runs SNOWPACK
    for every pit with a test that broke at a known depth; writes the result and a plain-language report."""
    from snowagent.lab.services.stability import report_lines, run_stability_check
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths

    cfg = load_lab_config(config)
    try:
        res = run_stability_check(LabPaths(data_root), cfg, agent or None, workers=workers,
                                  include_locked=include_locked, plots=plots or None, limit=limit,
                                  check_id=check_id, log=typer.echo)
    except ValueError as exc:
        typer.echo(json.dumps({"status": "error", "message": str(exc)}, indent=1))
        raise typer.Exit(code=2) from exc
    typer.echo("")
    for line in report_lines(res):
        typer.echo(line)


@lab_app.command("named-layers")
def lab_named_layers(
    run: Annotated[str | None, typer.Option(help="competition run to check (default: the newest)")] = None,
    table: Annotated[Path | None, typer.Option(help="also write every named layer of the pits to this CSV (for "
                                                    "studies of crusts and other named layers)")] = None,
    data_root: DataRoot = Path("data/lab"),
) -> None:
    """Named-layer check (ADR-140): where the observer named a layer by the day it formed ("Nov 5 crust"), did each
    agent of a finished competition forecast a layer of that kind there? Reads the run's stored forecasts; runs no
    agent and changes no score."""
    from snowagent.lab.competition.runner import list_runs
    from snowagent.lab.services.named_layers import named_layer_table, report_lines, run_named_layer_check
    from snowagent.lab.storage.paths import LabPaths

    paths = LabPaths(data_root)
    if table is not None:
        named_layer_table(paths).to_csv(table, index=False)
        typer.echo(f"named layers: {table}")
    run = run or next(iter(list_runs(paths)), None)
    if run is None:
        typer.echo(json.dumps({"status": "error", "message": "no competition run yet"}, indent=1))
        raise typer.Exit(code=2)
    try:
        res = run_named_layer_check(paths, run)
    except FileNotFoundError as exc:
        typer.echo(json.dumps({"status": "error", "message": str(exc)}, indent=1))
        raise typer.Exit(code=2) from exc
    for line in report_lines(res):
        typer.echo(line)


def _genome_file(path: Path):
    """A genome file, or a file that holds one under ``genome`` (config/site_main_model.json)."""
    from snowagent.lab.genome import load_genome

    d = json.loads(Path(path).read_text())
    if "genome" in d and "genes" not in d:
        import tempfile

        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(d["genome"], fh)
        return load_genome(Path(fh.name))
    return load_genome(Path(path))


@lab_app.command("known-answer")
def lab_known_answer(
    out: Annotated[Path, typer.Option("--out", help="new data root for the copy with fake pits")],
    truth: Annotated[Path, typer.Option(help="the true agent's genome file")] = Path("config/site_main_model.json"),
    noise: Annotated[float, typer.Option(help="observer noise, x the standard model (0 = none, 1 = standard)")] = 1.0,
    wobble: Annotated[list[str] | None, typer.Option(help="gene=relative sd, varied per case (repeat), e.g. "
                                                          "snow_mult_bow=0.1")] = None,
    seed: Seed = 0, workers: Workers = 1, data_root: DataRoot = Path("data/lab"),
) -> None:
    """Known-answer test (ADR-157): copy the lab's cases to --out with every withheld pit replaced by a fake pit drawn
    by the true agent plus observer noise. Train on the copy with `lab train --data-root <out>`, then read the run
    with `lab known-answer-read`."""
    from snowagent.lab.storage.paths import LabPaths
    from snowagent.lab.training import known_answer as ka

    g = _genome_file(truth)
    wob = {}
    for w in wobble or []:
        k, _, v = w.partition("=")
        wob[k.strip()] = float(v)
    nm = ka.NoiseModel().scaled(noise) if noise > 0 else ka.NoiseModel.none()
    try:
        rep = ka.build(LabPaths(data_root), LabPaths(out), g, nm, seed=seed, wobble=wob, workers=workers,
                       log=typer.echo)
    except ValueError as exc:
        typer.echo(json.dumps({"status": "error", "message": str(exc)}, indent=1))
        raise typer.Exit(code=2) from exc
    from dataclasses import asdict

    typer.echo(json.dumps(asdict(rep), indent=1))


@lab_app.command("known-answer-read")
def lab_known_answer_read(
    run_id: Annotated[str, typer.Argument(help="the training run on the copy")],
    start: Annotated[Path, typer.Option(help="the genome the run started from")],
    found: Annotated[Path | None, typer.Option(help="genome to judge (default: the run's winner)")] = None,
    data_root: DataRoot = Path("data/lab_ka"),
) -> None:
    """Per gene: where the run started, the true value and what it found (ADR-157). Writes
    outputs/known_answer/<run_id>.json in the copy."""
    from snowagent.lab.genome import make_genome
    from snowagent.lab.storage.paths import LabPaths
    from snowagent.lab.training import known_answer as ka

    paths = LabPaths(data_root)
    meta = json.loads((paths.root / "known_answer.json").read_text())
    tg = meta["truth_genome"]
    truth = make_genome(tg["family"], tg["genes"], label=tg.get("label"))
    s = _genome_file(start)
    if found is None:
        run = paths.outputs / "training" / run_id
        summ = json.loads((run / "summary.json").read_text())
        wg = summ["winner"]["genome"]
        f = make_genome(wg["family"], wg["genes"], label=wg.get("label"))
    else:
        f = _genome_file(found)
    rows = ka.gene_recovery(s, truth, f)
    od = paths.outputs / "known_answer"
    od.mkdir(parents=True, exist_ok=True)
    (od / f"{run_id}.json").write_text(json.dumps({"run_id": run_id, "rows": rows}, indent=1) + "\n")
    for r in rows:
        if r["kind"] == "choice":
            typer.echo(f"  {r['gene']:<28} start {r['start']:<4} true {r['truth']:<4} found {r['found']:<4} "
                       + ("right" if r["right"] else "WRONG"))
        else:
            c = "-" if r["closed"] is None else f"{r['closed']:+.2f}"
            typer.echo(f"  {r['gene']:<28} start {r['start_pos']:.2f} true {r['truth_pos']:.2f} found "
                       f"{r['found_pos']:.2f}  way closed {c}")


feedback_app = typer.Typer(help="Forecaster feedback (ADR-115): copy notes, distil rules, approve or reject them. "
                                "Notes are internal and stay outside the repository.")
lab_app.add_typer(feedback_app, name="feedback")


@feedback_app.command("fetch")
def feedback_fetch(since: Annotated[str | None, typer.Option(help="YYYY-MM-DD; default: the day of the newest note "
                                                                   "held")] = None,
                   folder: Annotated[Path | None, typer.Option(help="default $SNOWAGENT_FEEDBACK_DIR or "
                                                                    "data/feedback")] = None) -> None:
    """Copy new notes from the weather explorer (needs FEEDBACK_ADMIN_TOKEN in the environment)."""
    import os

    from snowagent.lab.feedback.notes import fetch_notes, load_notes, merge_notes, save_notes

    token = os.environ.get("FEEDBACK_ADMIN_TOKEN")
    if not token:
        typer.echo(json.dumps({"status": "error", "message": "FEEDBACK_ADMIN_TOKEN is not set"}))
        raise typer.Exit(code=2)
    held = load_notes(folder)
    since = since or (held[-1].received_at.date().isoformat() if held else None)
    merged = merge_notes(held, fetch_notes(token, since))
    path = save_notes(merged, folder)
    typer.echo(json.dumps({"status": "ok", "held": len(held), "now": len(merged), "file": str(path)}))


@feedback_app.command("distil")
def feedback_distil(folder: Annotated[Path | None, typer.Option(help="default $SNOWAGENT_FEEDBACK_DIR or "
                                                                     "data/feedback")] = None) -> None:
    """Ask Claude to propose rules from the notes held (needs ANTHROPIC_API_KEY and FEEDBACK_SALT). New rules are
    saved as 'proposed' and do nothing until approved."""
    import os

    from snowagent.lab.feedback.distil import distil
    from snowagent.lab.feedback.notes import load_notes
    from snowagent.lab.feedback.rules import load_rules, save_rules

    salt = os.environ.get("FEEDBACK_SALT")
    if not salt:
        typer.echo(json.dumps({"status": "error", "message": "FEEDBACK_SALT is not set (any private string; it "
                                                             "keeps writer pseudonyms stable)"}))
        raise typer.Exit(code=2)
    existing = load_rules(folder)
    new, problems, summary = distil(load_notes(folder), existing, salt)
    save_rules(existing + new, folder)
    typer.echo(json.dumps({"status": "ok", "proposed": [r.model_dump(mode="json") for r in new],
                           "skipped": problems, "summary": summary}, indent=1))


@feedback_app.command("rules")
def feedback_rules(folder: Annotated[Path | None, typer.Option()] = None) -> None:
    """List the rules on file with their status."""
    from snowagent.lab.feedback.rules import load_rules, rulebook_hash

    rules = load_rules(folder)
    for r in rules:
        typer.echo(f"{r.rule_id} [{r.status}] x{r.factor} {','.join(c.value for c in r.classes)} {r.zone.value} "
                   f"{r.plots or 'all plots'}: {r.statement}")
    typer.echo(f"approved rulebook hash: {rulebook_hash(rules)}  [{LAB_DISCLAIMER}]")


@feedback_app.command("decide")
def feedback_decide(rule_id: str, verdict: Annotated[str, typer.Argument(help="approve or reject")],
                    folder: Annotated[Path | None, typer.Option()] = None) -> None:
    """Approve or reject one rule (Ben's call). Approval lets the lab test it; it does not change the site."""
    from datetime import UTC, datetime

    from snowagent.lab.feedback.rules import decide, load_rules, save_rules

    status = {"approve": "approved", "reject": "rejected"}.get(verdict)
    if status is None:
        raise typer.BadParameter("verdict must be approve or reject")
    try:
        rules = decide(load_rules(folder), rule_id, status, datetime.now(UTC))
    except KeyError as exc:
        typer.echo(json.dumps({"status": "error", "message": f"no rule {rule_id}"}))
        raise typer.Exit(code=2) from exc
    save_rules(rules, folder)
    typer.echo(json.dumps({"status": "ok", "rule": rule_id, "now": status}))


mac_queue_app = typer.Typer(help="The Mac queue (ADR-148): heavy lab jobs the cloud queues for the owner's Mac. "
                                 "scripts/mac_runner/install.sh runs `tick` every 30 minutes.", no_args_is_help=True)
lab_app.add_typer(mac_queue_app, name="mac-queue")


@mac_queue_app.command("tick")
def mac_queue_tick(data_root: DataRoot = Path("data/lab"), config: ConfigPath = Path("config/lab.yaml"),
                   repo: Annotated[Path, typer.Option(help="the checkout (default: the working directory)")] =
                   Path(".")) -> None:
    """Resume or record queued jobs, publish the ended ones to the mac-results branch, start the next one."""
    from snowagent.lab.services.mac_queue import run_tick

    raise typer.Exit(code=run_tick(repo.resolve(), data_root, config))


@mac_queue_app.command("status")
def mac_queue_status(data_root: DataRoot = Path("data/lab")) -> None:
    """The runner's state: each queued job seen, its state and lab job, and the last lines of its log."""
    from snowagent.lab.services.mac_queue import Queue, on_mains_power, worker_cap

    q = Queue(repo=Path("."), data_root=data_root, config=Path("config/lab.yaml"))
    state = q.load_state()
    typer.echo(json.dumps({"workers": worker_cap(), "mains_power": on_mains_power(),
                           "entries": state.get("entries", {}), "heartbeat_at": state.get("heartbeat_at")}, indent=1))
    log = q.root / "runner.log"
    if log.is_file():
        typer.echo("\n".join(log.read_text().splitlines()[-15:]))


@mac_queue_app.command("check")
def mac_queue_check(files: Annotated[list[Path], typer.Argument(help="queue entry JSON files")]) -> None:
    """Validate queue entries before they are pushed to the mac-queue branch (exit 1 if any is refused)."""
    from snowagent.lab.services.mac_queue import QueueError, parse_entry

    bad = 0
    for f in files:
        try:
            e = parse_entry(json.loads(f.read_text()), f.name)
            typer.echo(f"ok   {f.name}: lab {' '.join(e.command_args(0, Path('<data root>'), Path('<config>')))}")
        except (json.JSONDecodeError, QueueError) as exc:
            bad += 1
            typer.echo(f"bad  {f.name}: {exc}")
    raise typer.Exit(code=1 if bad else 0)
