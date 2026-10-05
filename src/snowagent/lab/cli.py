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
    """Built cases per case set, split, site, case type and forecast source."""
    from snowagent.lab.services.benchmark import case_index
    from snowagent.lab.storage.paths import LabPaths

    idx = case_index(LabPaths(data_root), case_set)
    if idx.empty:
        typer.echo(json.dumps({"cases": 0}))
        return
    g = idx.groupby(["case_set", "split", "site_code", "case_type", "forecast_source"]).size()
    typer.echo(json.dumps({"cases": len(idx), "leakage": idx["leakage_check"].value_counts().to_dict(),
                           "counts": {" ".join(k): int(v) for k, v in g.items()}}, indent=1))


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
