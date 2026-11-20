"""Time a family's agents on this computer before an estimate relies on a built-in number (ADR-126).

``estimate.py`` multiplies per-case costs that training rounds measure. A family this computer has never trained
has no measured cost, only a number from another machine (``DEFAULT_AGENT_S``), and an estimate built on it can be
several times off: on 2026-11-19 the owner's Mac was told 3 h 18 min for a winter test of the column model that
took 53 min, because the Mac had only ever trained SNOWPACK agents and the built-in column number was five to
eight times too slow for it.

``calibrate`` measures instead, for a family never timed here and for one timed only by runs much smaller than
the run to estimate (fewer agent runs than one round of it: a trial on one plot, say, whose cases are not the
run's). The new mean then weighs as one round of that run. For each family it runs the family's starting settings
and seeded mutants of them
(``AGENTS`` agents a case, about what a round runs, because a case's first agent also prepares the case's weather)
on a stratified sample of the given cases, with the given workers, through the same worker code as a round. The
predictions and scores go to a throwaway cache that is removed at once: nothing of them is kept, shown or used to
choose anything. Each worker's first case is left out of the means (it also loads the code and warms the disk).
The measured times join the computer's running means (``timings.json``) like a round's.

Only families that run without SNOWPACK and without the analogue library are timed here: the column model and the
structures built on it, persistence and the weather rules. The SNOWPACK families' cost is the engine run, which the
rounds and the held-out scoring measure (``Timings.engine``). Callers pass training cases only: the locked test
winters are never run here. And callers time nothing while another training run is going (``run_going``): its
workers have the cores, so the timing would measure a busy computer.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from snowagent.lab.competition.runner import EngineSpec
from snowagent.lab.genome import default_genome
from snowagent.lab.schemas.genome import AgentFamily, GenomeSpec
from snowagent.lab.settings import LabConfig
from snowagent.lab.storage.paths import LabPaths
from snowagent.lab.training.cache import ENGINE_FAMILIES, TrainingCache
from snowagent.lab.training.estimate import Timings, save_timings
from snowagent.lab.training.evaluate import CaseRef, EvalContext, evaluate_population
from snowagent.lab.training.screen import screen_sample

AGENTS = 8  # agents a case: a round runs about this many, and they share the case's weather preparation
CASES_PER_WORKER = 6  # each worker's first case is left out, so five a worker are counted
MIN_CASES = 24
WORTH_S = 60.0  # an estimate shorter than this is not worth timing anything for
NOT_TIMED_HERE = frozenset({*ENGINE_FAMILIES, AgentFamily.analogue})


def can_time(timings: Timings, families: Iterable[str], enough: int = 1) -> list[str]:
    """The families among ``families`` that ``calibrate`` can time and whose time on this computer rests on fewer
    than ``enough`` measured agent runs: never timed (the default), or, with ``enough`` the agent runs of one round
    of the run to estimate, timed only by runs much smaller than it, whose cases may be other ones (a trial on
    one plot, whose cases can take half as long again as the others')."""
    out = []
    for f in dict.fromkeys(str(getattr(f, "value", f)) for f in families):
        m = timings.agent_s.get(f)
        if (m.n if m else 0) < max(1, enough) and AgentFamily(f) not in NOT_TIMED_HERE:
            out.append(f)
    return out


def run_going(paths: LabPaths) -> str | None:
    """The id of a training run of this lab that another process is running now (a run of its own, or the
    training inside a winter test or a promotion check), None when there is none. Its workers have the cores, so
    a timing made beside it would measure a busy computer, call the agents slower than they are and keep that as
    the family's mean. Told as the app tells a run that is going: the run's ``status.json`` says running and its
    process is alive. This process's own run is not another one."""
    from snowagent.lab.training.loop import training_root

    root = training_root(paths)
    for f in sorted(root.glob("*/status.json")) if root.is_dir() else []:
        try:
            status = json.loads(f.read_text())
            pid = int(status.get("pid") or 0)
        except (OSError, ValueError, TypeError, AttributeError):
            continue  # half written, or not a status
        if status.get("state") == "running" and pid not in (0, os.getpid()) and _alive(pid):
            return f.parent.name
    return None


def _alive(pid: int) -> bool:
    """A process of ours with this pid is running: it takes a signal and has not ended (a process that has ended
    but was not collected by its parent yet still takes one)."""
    try:
        os.kill(pid, 0)
    except (OSError, OverflowError):
        return False  # no such process, or not ours
    try:
        stat = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True,
                              timeout=5).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return True  # no ps: trust the signal
    return bool(stat) and not stat.startswith("Z")


def busy_note(run_id: str, families: list[str]) -> str:
    """One line for a log: why nothing was timed (``run_going``)."""
    return (f"training run {run_id} is going on this computer now, so the {' and '.join(families)} agents are "
            "not timed beside it (the timing would measure a busy computer): estimate again when it has ended")


def steady(stats: list[dict]) -> list[dict]:
    """The worker results without each worker's first case (which also loads the code and warms the disk), when at
    least half of them remain; ``stats`` is in the order the cases finished."""
    seen: set = set()
    kept = []
    for s in stats:
        if s.get("pid") in seen:
            kept.append(s)
        seen.add(s.get("pid"))
    return kept if 2 * len(kept) >= len(stats) else stats


def calibrate(paths: LabPaths, cfg: LabConfig, timings: Timings, families: Iterable[str], refs: list[CaseRef],
              workers: int = 1, *, spec: GenomeSpec | None = None, seed: int = 0, strength: float = 0.2,
              enough: int = 1, log: Callable[[str], None] | None = None) -> dict[str, dict]:
    """Time each of ``families`` that needs it (``can_time``) on a sample of ``refs`` and put the times in
    ``timings`` (saved to the cache's ``timings.json``), in place of a mean of fewer than ``enough`` agent runs.
    Returns, per family timed, the mean seconds an agent took a case, the cases counted and the seconds the
    timing took. A family that cannot be timed (no case, or its agents failed to run) keeps what it had, a
    built-in number if it was never timed, and the estimate reports that."""
    from snowagent.lab.training.loop import fill_initial

    todo = can_time(timings, families, enough)
    if not todo or not refs:
        return {}
    spec = spec or cfg.genome
    n = max(MIN_CASES, CASES_PER_WORKER * max(1, workers))
    keep = set(screen_sample(refs, n, seed))
    sample = [r for r in refs if r.case_id in keep]
    done: dict[str, dict] = {}
    real = TrainingCache(paths.outputs / "cache")
    for fam in todo:
        t0 = time.time()
        genomes = fill_initial([default_genome(AgentFamily(fam), spec)], AGENTS, seed, strength, spec)
        tmp = Path(tempfile.mkdtemp(prefix="timing-", dir=paths.outputs if paths.outputs.is_dir() else None))
        try:
            ctx = EvalContext(paths=paths, case_set="all", seed=seed, weights=cfg.scoring_weights,
                              config_hash=cfg.config_hash(), engine=EngineSpec(kind="none"))
            ctx.cache = TrainingCache(tmp)  # throwaway: nothing of these predictions or scores is kept
            stats = evaluate_population(sample, genomes, ctx, workers).worker_stats
        except Exception as exc:  # noqa: BLE001 - a timing never stops an estimate or a run
            if log:
                log(f"could not time the {fam} agents here ({type(exc).__name__}: {exc}); the estimate uses a "
                    "built-in number for them")
            continue
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        kept = [s for s in steady(stats) if s.get("agent_s", {}).get(fam)]
        if not kept:
            continue
        timings.agent_s.pop(fam, None)  # a mean of a few runs on other cases gives way to this run's cases
        timings.update(kept)
        # the timing weighs as one round of the run it was made for: the same estimate made again does not time
        # again, a later trial on other cases cannot outweigh it, and the run's own rounds soon do
        timings.agent_s[fam].n = max(timings.agent_s[fam].n, enough)
        save_timings(timings, real.timings)
        done[fam] = {"agent_s": round(timings.agent(fam), 4), "cases": len(kept), "agents": len(genomes),
                     "wall_s": round(time.time() - t0, 1)}
        if log:
            log(f"timed the {fam} agents on this computer: {done[fam]['agent_s']:.3f} s an agent a case "
                f"({len(kept)} cases, {len(genomes)} agents each, {workers} workers, {done[fam]['wall_s']:.0f} s)")
    return done


__all__ = ["AGENTS", "NOT_TIMED_HERE", "WORTH_S", "busy_note", "calibrate", "can_time", "run_going", "steady"]
