"""Training services shared by the CLI and the Streamlit Training page (ADR-069): start a training run as a detached
subprocess (never inside the Streamlit process), ask a running one to stop, and read runs, rounds and promotion
checks for display."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pandas as pd

from snowagent.lab.storage.paths import LabPaths
from snowagent.lab.storage.provenance import new_run_id
from snowagent.lab.training.loop import committed_rounds, list_training_runs, load_round, training_root

__all__ = ["list_training_runs", "load_round", "round_table", "run_overview", "start_training", "stop_training",
           "training_command"]


def training_command(paths: LabPaths, config: Path, run_id: str, *, rounds: int, population: int, survivors: int,
                     mutation_strength: float, crossover_share: float, seed: int, workers: int,
                     plots: list[str] | None = None, case_types: list[str] | None = None,
                     initial: list[str] | None = None, engine: str = "auto",
                     snowpack_bin: str | None = None) -> list[str]:
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
    return cmd


def start_training(paths: LabPaths, config: Path, run_id: str | None = None, cwd: Path | None = None,
                   **options) -> dict:
    """Launch ``snowagent lab train`` detached (own session, output to ``<run>/stdout.log``); returns the run id,
    pid and command. The run keeps going when the Streamlit app stops."""
    run_id = run_id or new_run_id("training")
    cmd = training_command(paths, config, run_id, **options)
    run_dir = training_root(paths) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    log = open(run_dir / "stdout.log", "ab")  # noqa: SIM115 - handed to the child process
    try:
        proc = _launch(cmd, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                start_new_session=True, cwd=str(cwd) if cwd else None, env=os.environ.copy())
    finally:
        log.close()
    return {"run_id": run_id, "pid": proc.pid, "command": cmd}


def _launch(cmd: list[str], **kw) -> subprocess.Popen:
    return subprocess.Popen(cmd, **kw)


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


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
    except (ProcessLookupError, PermissionError):
        return False
    return True


def run_overview(paths: LabPaths, run_id: str) -> dict:
    """Plan, live status, the per-round trace (best composite, gap, flags, cache) and the summary of a run."""
    d = training_root(paths) / run_id
    meta = _json(d / "run.json") or {}
    status = _json(d / "status.json") or {}
    if status.get("state") == "running" and not pid_alive(status.get("pid")):
        status["state"] = "interrupted"  # the process is gone without a final state (killed): resume it
    rows = []
    for r in committed_rounds(d):
        info = load_round(d, r)["round"]
        rows.append({"round": r, "best": info["best"]["label"], "family": info["best"]["family"],
                     "best_composite": info["best"]["composite"], "gap": info["gap"]["gap"],
                     "gap_flag": info["gap"]["flag"], "widening_streak": info["gap"]["streak"],
                     "cache_hit_rate": info["eval"]["cache_hit_rate"], "engine_runs": info["eval"]["engine_runs"],
                     "wall_s": info["wall_s"]})
    return {"run_id": run_id, "dir": d, "plan": meta.get("plan") or {}, "estimate": meta.get("estimate"),
            "created_at": meta.get("created_at"), "status": status, "rounds": pd.DataFrame(rows),
            "summary": _json(d / "summary.json"), "fold_of_check": run_id.startswith("loso_check-")}


def round_table(paths: LabPaths, run_id: str, r: int) -> pd.DataFrame:
    """The ranked leaderboard of one committed round, with each agent's role and operator."""
    rd = load_round(training_root(paths) / run_id, r)
    roles = {p["lineage"]["genome_hash"]: (p["role"], p["lineage"]["operator"]) for p in rd["population"]}
    rows = []
    for x in rd["leaderboard"]["ranked"]:
        role, op = roles.get(x["genome_hash"], ("", ""))
        rows.append({"rank": x["rank"], "agent": x["label"], "family": x["family"], "role": role, "operator": op,
                     "composite": x.get("composite"), "snow depth": x.get("snow_depth"),
                     "layer structure": x.get("layer_structure"), "critical layers": x.get("critical_layers"),
                     "uncertainty": x.get("uncertainty"), "robustness": x.get("robustness"),
                     "scored": x.get("scored"), "failures": x.get("failures"), "agent_id": x["agent_id"],
                     "genome_hash": x["genome_hash"]})
    return pd.DataFrame(rows)
