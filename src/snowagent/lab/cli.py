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
            host, "--server.headless", "true", "--browser.gatherUsageStats", "false", "--client.toolbarMode", "minimal"]


def _serve(cmd: list[str], env: dict) -> int:
    import subprocess

    try:
        return subprocess.call(cmd, env=env)
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
) -> None:
    """Start the lab's web interface (Streamlit) from any directory and print its address. Every step of the loop can
    be run from the browser; heavy work runs as background jobs that outlive the app (ADR-077). Ctrl-C stops the
    app, not the jobs."""
    import os

    home = find_app()
    if home is None:
        typer.echo(json.dumps({"status": "error", "message": "lab_app/Home.py not found: run from the repository "
                               "(or install snowagent from it with pip install -e)"}))
        raise typer.Exit(code=2)
    env = os.environ.copy()
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
                               "profiles/ not found here)"}))
        raise typer.Exit(code=2)
    rep = prepare(Path("."), load_lab_config(config), era5=era5, workers=workers, log=typer.echo, bundle=bundle)
    if isinstance(rep.get("era5"), dict) and rep["era5"]["failed"]:
        typer.echo(f"warning: {len(rep['era5']['failed'])} ERA5 months failed (rerun to retry; a month the mirror "
                   "has not published yet stays missing and those station gaps stay unfilled)")


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


def _genomes(agents: list[str] | None, config):
    from snowagent.lab.genome import default_genome, default_genomes, load_genome
    from snowagent.lab.schemas.genome import AgentFamily

    if not agents:
        return default_genomes(config.genome)
    out = []
    for a in agents:
        if a in {f.value for f in AgentFamily}:
            out.append(default_genome(AgentFamily(a), config.genome))
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
        "--agents", help="genome JSON files or family names (repeat); default: the default genome of every family")]
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
        genomes = _genomes(agents, cfg)
        feed = CompetitionFeed(  # the Arena's live feed (ADR-078), written here, outside the prediction code
            paths.outputs / "competitions" / run_id,
            [{"agent_id": g.agent_id, "label": g.label or g.display_name, "family": g.family.value} for g in genomes],
            then=lambda d, n: typer.echo(f"  {d}/{n} cases", err=True) if d == n or d % 20 == 0 else None)
        feed.start()
        res = run_competition(
            paths, cfg, genomes, case_set=case_set, splits=split, sites=plots, case_types=case_type,
            forecast_sources=forecast_source, case_ids=case_id, limit=limit, workers=workers, run_id=run_id,
            seed=seed, engine=EngineSpec(kind=engine, binary=snowpack_bin,
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
                   locked_seasons=None):
    from snowagent.lab.competition.runner import EngineSpec
    from snowagent.lab.training.loop import TrainOptions

    init = None
    if initial and not (len(initial) == 1 and initial[0] in ("defaults", "default")):
        init = _genomes(initial, cfg)
    return TrainOptions.from_config(
        cfg, rounds=rounds, population=population, survivors=survivors, mutation_strength=mutation_strength,
        crossover_share=crossover_share, seed=seed, plots=plots, case_types=case_types, initial=init,
        monitor_season=monitor_season, gap_flag_rounds=gap_flag_rounds, case_set=case_set, splits=splits,
        engine=EngineSpec(kind=engine, binary=snowpack_bin, segments=segment_reuse), screen_cases=screen_cases,
        family_slots=family_slots or None, weather_sources=weather_sources or None, locked_seasons=locked_seasons)


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
LockedSeasons = Annotated[int | None, typer.Option(
    help="the N most recent seasons never train or select agents; the leaders are scored on them every round "
         "(default 3; 0 = off; ADR-083)")]
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
    locked_seasons: LockedSeasons = None,
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
                              weather_sources=weather_sources, locked_seasons=locked_seasons)
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
                         overrides=over)
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
