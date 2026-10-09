"""Training services shared by the CLI and the Streamlit Training page (ADR-069): start a training run as a detached
subprocess (never inside the Streamlit process), ask a running one to stop, and read runs, rounds and promotion
checks for display."""

from __future__ import annotations

import json
import statistics
import sys
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from snowagent.lab.services.jobs import pid_alive
from snowagent.lab.services.names import nickname
from snowagent.lab.storage.paths import LabPaths
from snowagent.lab.storage.provenance import new_run_id
from snowagent.lab.training.loop import committed_rounds, list_training_runs, load_round, training_root

__all__ = ["list_training_runs", "load_round", "resume_command", "resume_training", "round_table", "run_overview",
           "running_training", "start_training", "stop_training", "time_left", "training_command", "best_so_far", "PRESETS",
           "seen_locked"]

# The Training page's presets (ADR-081): the options a run needs, the rest stay at the configuration's defaults.
# Times are for an Apple-silicon Mac with 8 workers and about 945 cases (2026-10-06): about 8 s per SNOWPACK run,
# 1-2.5 h for a round whose children carry new SNOWPACK physics, so an 8-hour night covers 4-5 rounds and Resume
# continues the run on later nights.
PRESETS: dict[str, dict] = {
    "Overnight (about 8 hours, resume on later nights)": {
        "rounds": 20, "population": 10, "survivors": 2, "screen_cases": 30, "plots": None, "case_types": None},
    "Quick check (about 20 minutes, one plot)": {
        "rounds": 2, "population": 4, "survivors": 2, "screen_cases": 0, "plots": ["SIMP"],
        "case_types": ["next_pit"]},
    "Custom": {},
}


def training_command(paths: LabPaths, config: Path, run_id: str, *, rounds: int, population: int, survivors: int,
                     mutation_strength: float, crossover_share: float, seed: int, workers: int,
                     plots: list[str] | None = None, case_types: list[str] | None = None,
                     initial: list[str] | None = None, engine: str = "auto",
                     snowpack_bin: str | None = None, screen_cases: int | None = None,
                     family_slots: bool = False, locked_seasons: int | None = None,
                     selection: str | None = None, drift_penalty: float | None = None,
                     seed_from: str | None = None, seed_top: int = 2,
                     stop_when_flat: int | None = None) -> list[str]:
    cmd = [sys.executable, "-m", "snowagent.cli", "lab", "train", "--run-id", run_id, "--data-root",
           str(Path(paths.root).resolve()), "--config", str(Path(config).resolve()), "--rounds", str(rounds),
           "--population", str(population), "--survivors", str(survivors), "--mutation-strength",
           str(mutation_strength), "--crossover-share", str(crossover_share), "--seed", str(seed), "--workers",
           str(workers), "--engine", engine]
    for p in plots or []:
        cmd += ["--plots", p]
    for c in case_types or []:
        cmd += ["--case-types", c]
    for g in initial or []:
        cmd += ["--initial", g]
    if snowpack_bin:
        cmd += ["--snowpack-bin", snowpack_bin]
    if screen_cases:
        cmd += ["--screen-cases", str(int(screen_cases))]  # ADR-072
    if family_slots:
        cmd += ["--family-slots"]  # ADR-073
    if locked_seasons is not None:
        cmd += ["--locked-seasons", str(int(locked_seasons))]  # ADR-083
    if seed_from:
        cmd += ["--seed-from", seed_from, "--seed-top", str(int(seed_top))]  # ADR-085
    if selection:
        cmd += ["--selection", selection]  # ADR-087
    if drift_penalty is not None:
        cmd += ["--drift-penalty", f"{float(drift_penalty):g}"]  # ADR-089
    if stop_when_flat is not None:
        cmd += ["--stop-when-flat", str(int(stop_when_flat))]  # ADR-094
    return cmd


def seen_locked(paths: LabPaths, seed_from: str | None, locked_n: int) -> list[str]:
    """ADR-085: the winters a new run would lock (the ``locked_n`` most recent known to ``seed_from``) that the
    agents of ``seed_from`` already trained or were selected on; [] when there is no overlap or nothing to check."""
    if not seed_from or not locked_n:
        return []
    plan = (_json(training_root(paths) / seed_from / "run.json") or {}).get("plan") or {}
    trained = set(plan.get("seasons") or []) | {s for x in plan.get("seeded_from") or [] for s in x.get("seasons", [])}
    known = sorted(trained | set(plan.get("locked_seasons") or []))
    return sorted(trained & set(known[-locked_n:])) if len(known) >= locked_n + 2 else []


def resume_command(paths: LabPaths, config: Path, run_id: str, workers: int, snowpack_bin: str | None = None
                   ) -> list[str]:
    """``lab train --resume``: the run continues with its stored options (only workers and the binary are given)."""
    cmd = [sys.executable, "-m", "snowagent.cli", "lab", "train", "--resume", "--run-id", run_id, "--data-root",
           str(Path(paths.root).resolve()), "--config", str(Path(config).resolve()), "--workers", str(workers)]
    return cmd + (["--snowpack-bin", snowpack_bin] if snowpack_bin else [])


def running_training(paths: LabPaths) -> str | None:
    """A training run whose process is alive, wherever it was started (the app or a terminal)."""
    for run_id in list_training_runs(paths):
        status = _json(training_root(paths) / run_id / "status.json") or {}
        if status.get("state") == "running" and pid_alive(status.get("pid")):
            return run_id
    return None


def start_training(paths: LabPaths, config: Path, run_id: str | None = None, cwd: Path | None = None,
                   **options) -> dict:
    """Launch ``snowagent lab train`` as a background job (ADR-069, ADR-077: own session, output to
    ``<run>/stdout.log``); returns the run id, job id, pid and command. The run keeps going when the app stops.
    Refused (``JobBusy``) while another training runs."""
    from snowagent.lab.services.jobs import JobBusy, start_job, step

    other = running_training(paths)
    if other:
        raise JobBusy(f"training run {other} is already running; stop it or wait for it to finish")
    run_id = run_id or new_run_id("training")
    cmd = training_command(paths, config, run_id, **options)
    run_dir = training_root(paths) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    resume = resume_command(paths, config, run_id, int(options.get("workers", 1)), options.get("snowpack_bin"))
    job = start_job(paths, "train", f"training {run_id}", [step("train", cmd, stopped_codes=(5,))],
                    cwd=Path(cwd or Path.cwd()), log=run_dir / "stdout.log", refs={"run_id": run_id},
                    resume=[step("train (resume)", resume, stopped_codes=(5,))])
    return {"run_id": run_id, "pid": job["pid"], "job_id": job["job_id"], "command": cmd}


def resume_training(paths: LabPaths, config: Path, run_id: str, *, workers: int = 1, cwd: Path | None = None,
                    snowpack_bin: str | None = None) -> dict:
    """Continue a stopped or interrupted run (``lab train --resume``) as a background job; clears its stop request."""
    from snowagent.lab.services.jobs import JobBusy, start_job, step

    other = running_training(paths)
    if other:
        raise JobBusy(f"training run {other} is already running; stop it or wait for it to finish")
    (training_root(paths) / run_id / "stop").unlink(missing_ok=True)
    cmd = resume_command(paths, config, run_id, workers, snowpack_bin)
    job = start_job(paths, "train", f"training {run_id} (resumed)", [step("train (resume)", cmd, stopped_codes=(5,))],
                    cwd=Path(cwd or Path.cwd()), log=training_root(paths) / run_id / "stdout.log",
                    refs={"run_id": run_id}, resume=[step("train (resume)", cmd, stopped_codes=(5,))])
    return {"run_id": run_id, "pid": job["pid"], "job_id": job["job_id"], "command": cmd}


def stop_training(paths: LabPaths, run_id: str) -> Path:
    """Ask a running training to stop (it stops at the next case; ``--resume`` continues it)."""
    f = training_root(paths) / run_id / "stop"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.touch()
    return f


def _json(f: Path) -> dict | None:
    try:
        return json.loads(f.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def run_overview(paths: LabPaths, run_id: str) -> dict:
    """Plan, live status, the per-round trace (best composite, gap, flags, cache) and the summary of a run."""
    d = training_root(paths) / run_id
    meta = _json(d / "run.json") or {}
    status = _json(d / "status.json") or {}
    if status.get("state") == "running" and not pid_alive(status.get("pid")):
        status["state"] = "interrupted"  # the process is gone without a final state (killed): resume it
    rows, locked = [], None
    for r in committed_rounds(d):
        info = load_round(d, r)["round"]
        lt = info.get("locked_test") or {}
        mine = next((a for a in lt.get("agents", []) if a["agent_id"] == info["best"]["agent_id"]), {})
        if lt and locked is None:  # round 1 tested every initial agent: the incumbent's score is the bar
            inc = next((a for a in lt["agents"] if a["label"] == "snowpack-default"), None) or next(
                (a for a in lt["agents"] if a["family"] == info["best"]["family"] and a.get("default")), None)
            locked = {"seasons": lt["seasons"], "cases": lt["cases"],
                      "incumbent": inc and {"label": inc["label"], "composite": inc.get("composite")}}
        rows.append({"round": r, "best": info["best"]["label"], "family": info["best"]["family"],
                     "locked_composite": mine.get("composite"),
                     "best_composite": info["best"]["composite"], "gap": info["gap"]["gap"],
                     "gap_flag": info["gap"]["flag"], "widening_streak": info["gap"]["streak"],
                     "cache_hit_rate": info["eval"]["cache_hit_rate"], "engine_runs": info["eval"]["engine_runs"],
                     "wall_s": info["wall_s"]})
    return {"run_id": run_id, "dir": d, "plan": meta.get("plan") or {}, "estimate": meta.get("estimate"),
            "created_at": meta.get("created_at"), "status": status, "rounds": pd.DataFrame(rows),
            "summary": _json(d / "summary.json"), "fold_of_check": run_id.startswith("loso_check-"),
            "locked": locked}


def time_left(ov: dict, now: datetime | None = None) -> float | None:
    """Seconds a running training run still needs, from its own measured rounds: the median wall time of rounds 2
    onwards (round 1 mostly reads the cache), else round 1's, else the current round's estimate (an upper bound).
    The current round counts what it has left of that time or, once it runs longer, what its own progress implies.
    None when the run is not running or nothing is known."""
    status, plan, trace = ov["status"], ov["plan"], ov["rounds"]
    if status.get("state") != "running" or not plan.get("rounds"):
        return None
    walls = trace.loc[trace["round"] >= 2, "wall_s"].tolist() if len(trace) else []
    walls = walls or (trace["wall_s"].tolist() if len(trace) else [])
    per = statistics.median(walls) if walls else status.get("estimate_s")
    if not per:
        return None
    now = now or datetime.now(UTC)
    r = int(status.get("round") or 0)
    current = per
    if status.get("round_started_at"):
        elapsed = (now - datetime.fromisoformat(status["round_started_at"])).total_seconds()
        done, total = status.get("done") or 0, status.get("total") or 0
        by_progress = elapsed * (total - done) / done if 0 < done <= total else 0.0
        current = max(per - elapsed, by_progress, 0.0)
    return current + max(int(plan["rounds"]) - r, 0) * per


def best_so_far(paths: LabPaths, scoring_version: str) -> dict | None:
    """The best agent of any training run scored under ``scoring_version`` (runs of other versions do not compare,
    ADR-074): its run, round, label, family and composite, with the best composite of that run's round 1."""
    best = None
    for run_id in list_training_runs(paths):
        d = training_root(paths) / run_id
        meta = _json(d / "run.json") or {}
        if (meta.get("plan") or meta).get("scoring_version") != scoring_version or run_id.startswith("loso_check-"):
            continue
        rounds = committed_rounds(d)
        if not rounds:
            continue
        top = load_round(d, rounds[-1])["round"]["best"]
        if best is None or top["composite"] > best["composite"]:
            best = {"run_id": run_id, "round": rounds[-1], "label": top["label"], "family": top["family"],
                    "composite": top["composite"], "round1": load_round(d, rounds[0])["round"]["best"]["composite"]}
    return best


def round_table(paths: LabPaths, run_id: str, r: int) -> pd.DataFrame:
    """The ranked leaderboard of one committed round, with each agent's name (ADR-084), role and operator."""
    rd = load_round(training_root(paths) / run_id, r)
    roles = {p["lineage"]["genome_hash"]: (p["role"], p["lineage"]["operator"]) for p in rd["population"]}
    rows = []
    for x in rd["leaderboard"]["ranked"]:
        role, op = roles.get(x["genome_hash"], ("", ""))
        rows.append({"rank": x["rank"], "name": nickname(x["genome_hash"], x["label"]), "agent": x["label"],
                     "family": x["family"], "role": role, "operator": op,
                     "composite": x.get("composite"), "snow depth": x.get("snow_depth"),
                     "layer structure": x.get("layer_structure"), "critical layers": x.get("critical_layers"),
                     "uncertainty": x.get("uncertainty"), "robustness": x.get("robustness"),
                     "scored": x.get("scored"), "failures": x.get("failures"),
                     **({"unevenness": x["spread"]} if "spread" in x else {}),
                     **({"drift": x["drift"]} if "drift" in x else {}), "agent_id": x["agent_id"],
                     "genome_hash": x["genome_hash"]})
    return pd.DataFrame(rows)
