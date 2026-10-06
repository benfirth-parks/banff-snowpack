"""The browser's controls for the whole lab loop (ADR-077): each builds the same ``snowagent lab ...`` command a
terminal would run and starts it as a background job (``services.jobs``). Nothing heavy runs in the app."""

from __future__ import annotations

import json
from pathlib import Path

from snowagent.lab.services.jobs import KINDS, lab_command, latest_job, start_job, step
from snowagent.lab.settings import LabConfig
from snowagent.lab.storage.paths import LabPaths
from snowagent.lab.storage.provenance import new_run_id

ERA5_MIN_PER_MONTH = (5.0, 7.0)  # measured 6 min 20 s for one month, network-bound (docs/lab/run_locally.md)


def _common(paths: LabPaths, config: Path) -> list[str]:
    return ["--data-root", str(Path(paths.root).resolve()), "--config", str(Path(config).resolve())]


def _repeat(flag: str, values: list[str] | None) -> list[str]:
    return [x for v in values or [] for x in (flag, str(v))]


# --------------------------------------------------------------------------------------------- set up data


def setup_readiness(paths: LabPaths, cfg: LabConfig, root: Path) -> dict:
    """What `lab prepare`, `lab init` and `lab import` have produced so far, and the time the rest will take."""
    from snowagent.lab.services.data import data_status
    from snowagent.lab.services.prepare import OBSERVED, era5_months

    months = era5_months(cfg)
    era5_dir = root / cfg.weather.era5_dir
    cached = sum((era5_dir / f"era5_box_{y}{m:02d}.npz").is_file() for y, m in months)
    st = data_status(paths)
    return {"station_files": (root / "data/raw/fts360").is_dir(), "observed_profiles": (root / OBSERVED).is_file(),
            "era5_months": len(months), "era5_cached": cached, "era5_needed": bool(cfg.weather.era5_backfill),
            "lab_initialised": st["registry"], "imported": st["profiles"] and st["weather"]}


def setup_estimate(ready: dict, era5: bool, workers: int) -> dict:
    """Minutes per step: bootstrap and profiles about 1 min, ERA5 5-7 min per missing month and worker, import 1."""
    todo = ready["era5_months"] - ready["era5_cached"] if era5 and ready["era5_needed"] else 0
    lo, hi = (todo * m / max(1, workers) for m in ERA5_MIN_PER_MONTH)
    return {"prepare_min": (1 + lo, 1 + hi), "era5_months_todo": todo, "init_min": 0.1, "import_min": 1.0,
            "total_min": (2 + lo, 2.5 + hi)}


def start_setup(paths: LabPaths, config: Path, root: Path, *, era5: bool = True, workers: int = 4) -> dict:
    """`lab prepare` (from the repository root), then `lab init` and `lab import` into this data root."""
    steps = [step("prepare", lab_command("prepare", "--era5" if era5 else "--no-era5", "--workers", str(workers),
                                         "--config", str(Path(config).resolve()))),
             step("init", lab_command("init", *_common(paths, config))),
             step("import", lab_command("import", "--source", str(root.resolve()), *_common(paths, config)))]
    return start_job(paths, "setup", "set up data: prepare, init, import" + ("" if era5 else " (no ERA5)"), steps,
                     cwd=root)


# --------------------------------------------------------------------------------------------- cases


def start_build_cases(paths: LabPaths, config: Path, root: Path, *, case_types: list[str] | None = None,
                      holdout: str | None = None) -> dict:
    cmd = lab_command("build-cases", "--source", str(root.resolve()), *_common(paths, config),
                      *_repeat("--case-type", case_types), *(["--holdout", holdout] if holdout else []))
    return start_job(paths, "build-cases", "build cases" + (f" (held out {holdout})" if holdout else ""),
                     [step("build-cases", cmd)], cwd=root)


# --------------------------------------------------------------------------------------------- competition


def competition_command(paths: LabPaths, config: Path, root: Path, run_id: str, *, agents: list[str] | None = None,
                        case_set: str = "all", plots: list[str] | None = None,
                        case_types: list[str] | None = None, workers: int = 1, engine: str = "auto",
                        limit: int | None = None, seed: int = 0) -> list[str]:
    return lab_command("compete", "--run-id", run_id, "--cases", case_set, "--workers", str(workers), "--engine",
                       engine, "--seed", str(seed), "--source", str(root.resolve()), *_common(paths, config),
                       *_repeat("--agents", agents), *_repeat("--plots", plots), *_repeat("--case-type", case_types),
                       *(["--limit", str(int(limit))] if limit else []))


def start_competition(paths: LabPaths, config: Path, root: Path, run_id: str | None = None, **options) -> dict:
    """`lab compete` under a new run id (the same command resumes it: finished cases are kept)."""
    run_id = run_id or new_run_id("competition")
    cmd = competition_command(paths, config, root, run_id, **options)
    return start_job(paths, "compete", f"competition {run_id}", [step("compete", cmd)], cwd=root,
                     refs={"run_id": run_id})


def start_rescore(paths: LabPaths, config: Path, root: Path, run_id: str) -> dict:
    cmd = lab_command("rescore", "--run-id", run_id, *_common(paths, config))
    return start_job(paths, "rescore", f"re-score {run_id}", [step("rescore", cmd)], cwd=root,
                     refs={"run_id": run_id})


# --------------------------------------------------------------------------------------------- promotion check


def check_args(genome_ref: str, *, seasons: list[str] | None = None, rounds: int | None = None,
               population: int | None = None, workers: int = 1, engine: str = "auto") -> list[str]:
    """The options of a check-loso, without the check id: an estimate and the check it precedes share them."""
    return ["--genome", genome_ref, "--workers", str(workers), "--engine", engine, *_repeat("--season", seasons),
            *(["--rounds", str(int(rounds))] if rounds else []),
            *(["--population", str(int(population))] if population else [])]


def start_check_estimate(paths: LabPaths, config: Path, root: Path, args: list[str]) -> dict:
    cmd = lab_command("check-loso", *args, "--estimate-only", "--source", str(root.resolve()), *_common(paths, config))
    return start_job(paths, "check-estimate", f"estimate {args[1]}", [step("estimate", cmd)], cwd=root,
                     refs={"args": args})


def estimate_for(paths: LabPaths, args: list[str]) -> dict | None:
    """The latest estimate job of exactly these options (None: estimate first)."""
    job = latest_job(paths, "check-estimate")
    return job if job and (job.get("refs") or {}).get("args") == args else None


def start_check(paths: LabPaths, config: Path, root: Path, args: list[str], check_id: str) -> dict:
    """`lab check-loso` under ``check_id``; the same command with the same id resumes it (finished folds kept)."""
    cmd = lab_command("check-loso", *args, "--check-id", check_id, "--source", str(root.resolve()),
                      *_common(paths, config))
    return start_job(paths, "check-loso", f"promotion check {check_id}", [step("check-loso", cmd)], cwd=root,
                     refs={"check_id": check_id, "args": args})


def resume_check_args(paths: LabPaths, check_id: str, workers: int = 1, engine: str = "auto") -> list[str]:
    """The options that resume a check from its stored plan (a check of a ``<run>/<round>/<rank>`` reference: the
    run's options plus the overrides the check recorded)."""
    from snowagent.lab.training.loso import checks_root

    plan = json.loads((checks_root(paths) / check_id / "check.json").read_text())["plan"]
    over = plan.get("differs_from_training_run") or {}
    args = ["--genome", plan["genome_ref"], "--workers", str(workers), "--engine", engine,
            *_repeat("--season", plan["seasons"])]
    for k in ("rounds", "population", "survivors", "mutation_strength", "crossover_share", "seed"):
        if k in over:
            args += [f"--{k.replace('_', '-')}", str(over[k]["check"])]
    return args


__all__ = ["KINDS", "check_args", "competition_command", "estimate_for", "resume_check_args", "setup_estimate",
           "setup_readiness", "start_build_cases", "start_check", "start_check_estimate", "start_competition",
           "start_rescore", "start_setup"]
