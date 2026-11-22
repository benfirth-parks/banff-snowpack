"""Time estimates for training rounds and the leave-one-season-out check (ADR-066).

Measured per-case costs: the agent time of each family (without the engine), one SNOWPACK engine run per case
(shared by every SNOWPACK and hybrid agent of the case, then cached), and the per-case load (reading the visible
package and the manifest). They start from the latest competition run in the lab (milestone 3: about 12 s per
engine run, 0.02-0.24 s per other agent) and are updated from every training round and every held-out scoring
(``<data root>/outputs/cache/timings.json``, running means). A round's wall time is the sum over the cases with
uncached work divided by the workers.

ADR-126: a family this computer has never timed has only a built-in number from another machine. Before an estimate
relies on one, ``calibrate`` times the family here; where that is not possible the estimate names what it guessed
(``Timings.guessed``), so that no number from another machine is passed off as this computer's.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from snowagent.lab.schemas.genome import AgentFamily, AgentGenome
from snowagent.lab.storage.paths import LabPaths
from snowagent.lab.training.cache import ENGINE_FAMILIES

# Before anything is measured (no competition run in the lab): milestone-3 means on the 2026-10-05 case set, 4 workers.
# The column model and the structures built on it: measured on the 4-core cloud machine on 2026-11-19 with 10 agents
# a case (ADR-126; the owner's Mac needs about 0.06-0.10 s). They were guesses of 0.5 s (0.2 for the recipes) until
# then, which made the Mac's first winter-test estimate about four times too long. Another machine's numbers either
# way: ``calibrate`` replaces them with this computer's own before an estimate uses them.
DEFAULT_AGENT_S = {"persistence": 0.05, "weather_rule": 0.23, "analogue": 0.02, "snowpack": 0.02, "hybrid": 0.24,
                   "column": 0.13, "process": 0.15, "process_melt": 0.15, "story": 0.15, "recipe": 0.07, "scorecard": 0.13}
DEFAULT_ENGINE_S = 11.8
DEFAULT_LOAD_S = 0.45  # loading one visible package (about 2,500 hours and the visible pits) and its manifest
SNOWPACK_DOMINANCE = 0.5  # warn when SNOWPACK-family work is more than this share of the estimated cost


@dataclass
class Mean:
    n: int = 0
    mean: float = 0.0

    def add(self, values: list[float]) -> None:
        for v in values:
            self.n += 1
            self.mean += (float(v) - self.mean) / self.n


@dataclass
class Timings:
    agent_s: dict[str, Mean] = field(default_factory=dict)
    engine_s: Mean = field(default_factory=Mean)
    load_s: Mean = field(default_factory=Mean)
    source: str = "defaults"

    def agent(self, family: str) -> float:
        m = self.agent_s.get(family)
        return m.mean if m and m.n else DEFAULT_AGENT_S.get(family, 0.25)

    def timed(self, family: str) -> bool:
        """Whether this computer has measured the family's agent time (False: ``agent`` is a built-in number)."""
        m = self.agent_s.get(family)
        return bool(m and m.n)

    def guessed(self, families: Iterable[str], engine: bool = False) -> list[str]:
        """ADR-126: what an estimate of these families rests on without a measurement from this computer: the
        families never timed here, and ``engine`` (the SNOWPACK run) when the estimate counts engine runs and none
        was timed here."""
        out = sorted({str(getattr(f, "value", f)) for f in families} - {f for f in self.agent_s if self.timed(f)})
        return [*out, "engine"] if engine and not self.engine_s.n else out

    @property
    def engine(self) -> float:
        return self.engine_s.mean if self.engine_s.n else DEFAULT_ENGINE_S

    @property
    def load(self) -> float:
        return self.load_s.mean if self.load_s.n else DEFAULT_LOAD_S

    def to_dict(self) -> dict:
        return {"source": self.source, "agent_s": {k: asdict(v) for k, v in self.agent_s.items()},
                "engine_s": asdict(self.engine_s), "load_s": asdict(self.load_s)}

    @classmethod
    def from_dict(cls, d: dict) -> Timings:
        return cls(agent_s={k: Mean(**v) for k, v in d.get("agent_s", {}).items()},
                   engine_s=Mean(**d.get("engine_s", {})), load_s=Mean(**d.get("load_s", {})),
                   source=d.get("source", "file"))

    def update(self, worker_stats: list[dict]) -> None:
        for s in worker_stats:
            if not s.get("pairs"):  # a re-score only (ADR-074): no agent ran, nothing to time
                continue
            for fam, vals in s["agent_s"].items():
                self.agent_s.setdefault(fam, Mean()).add(vals)
            if s.get("engine_runs_s"):
                self.engine_s.add(s["engine_runs_s"])  # one sample per engine run (one per new physics)
            elif s.get("engine_s"):
                self.engine_s.add([s["engine_s"]])
            self.load_s.add([s["load_s"]])
        if worker_stats:
            self.source = "measured"


def from_competitions(paths: LabPaths) -> Timings | None:
    """Starting numbers from the latest competition run (per-agent runtimes; engine runtime per case)."""
    import pandas as pd

    root = paths.outputs / "competitions"
    runs = sorted((d for d in root.iterdir() if (d / "scores.parquet").is_file()), key=lambda d: d.stat().st_mtime,
                  reverse=True) if root.is_dir() else []
    for run in runs:
        df = pd.read_parquet(run / "scores.parquet", columns=["status"])
        if (df["status"] == "skipped").all():
            continue
        t = Timings(source=f"competition {run.name}")
        engine: dict[str, float] = {}
        for f in (run / "cases").glob("*.json"):
            rt = (json.loads(f.read_text()).get("engine") or {}).get("runtime_s")
            if rt:
                engine[f.stem] = float(rt)
        if engine:
            t.engine_s.add(list(engine.values()))
        full = pd.read_parquet(run / "scores.parquet", columns=["case_id", "family", "runtime_s", "status"])
        full = full[full["status"] != "skipped"]
        for fam, d in full.groupby("family"):
            vals = d["runtime_s"].astype(float).tolist()
            if fam == "snowpack" and engine:  # the SNOWPACK agent ran its case's engine first (genome order)
                vals = [max(0.0, v - engine.get(c, 0.0)) for c, v in zip(d["case_id"], vals, strict=True)]
            t.agent_s[fam] = Mean()
            t.agent_s[fam].add(vals)
        return t
    return None


def load_timings(paths: LabPaths, cache_file: Path) -> Timings:
    if cache_file.is_file():
        return Timings.from_dict(json.loads(cache_file.read_text()))
    return from_competitions(paths) or Timings()


def save_timings(t: Timings, cache_file: Path) -> None:
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(t.to_dict(), indent=1))
    tmp.replace(cache_file)


@dataclass
class Estimate:
    wall_s: float
    cpu_s: float
    uncached_pairs: int
    pairs: int
    engine_runs: int
    cases_with_work: int
    snowpack_share: float
    by_family_s: dict[str, float]

    def to_dict(self) -> dict:
        return {k: (round(v, 1) if isinstance(v, float) else v) for k, v in asdict(self).items()}

    @property
    def snowpack_dominates(self) -> bool:
        return self.cpu_s > 0 and self.snowpack_share > SNOWPACK_DOMINANCE


def estimate_pairs(case_work: list[tuple[list[AgentGenome], bool | int | None]], timings: Timings, workers: int,
                   pairs: int) -> Estimate:
    """``case_work``: per case, the uncached genomes and either the number of engine runs it needs (one per physics
    whose profile is not cached, ADR-070) or, as before, whether its incumbent engine profile is cached (None:
    unknown, counted as not cached)."""
    by_fam: dict[str, float] = {}
    engine_runs = cases = n = 0
    sp = load = 0.0
    for genomes, engine_cached in case_work:
        if not genomes:
            continue
        cases += 1
        load += timings.load
        for g in genomes:
            n += 1
            by_fam[g.family.value] = by_fam.get(g.family.value, 0.0) + timings.agent(g.family.value)
            if g.family in ENGINE_FAMILIES:
                sp += timings.agent(g.family.value)
        if isinstance(engine_cached, int) and not isinstance(engine_cached, bool):
            runs = engine_cached
        else:
            runs = int(not engine_cached and any(g.family in ENGINE_FAMILIES for g in genomes))
        if runs:
            engine_runs += runs
            by_fam["engine"] = by_fam.get("engine", 0.0) + runs * timings.engine
            sp += runs * timings.engine
    cpu = load + sum(by_fam.values())
    return Estimate(wall_s=cpu / max(1, workers), cpu_s=cpu, uncached_pairs=n, pairs=pairs, engine_runs=engine_runs,
                    cases_with_work=cases, snowpack_share=sp / cpu if cpu else 0.0, by_family_s=by_fam)


def child_families(initial: list, family_slots: bool = False) -> list[str] | None:
    """ADR-125: the families a run's children can belong to, from its initial population (genomes or their stored
    form): a child keeps a parent's family, so a run started from the column model alone never runs SNOWPACK. None
    (any family) with family slots, which add the standard families, or without an initial population."""
    if family_slots or not initial:
        return None
    return sorted({str(g["family"] if isinstance(g, dict) else g.family.value) for g in initial})


def estimate_child_rounds(n_cases: int, n_children: int, timings: Timings, workers: int,
                          physics: bool = False, screen_cases: int | None = None,
                          pass_share: float = 0.5, families: list[str] | None = None) -> tuple[float, float]:
    """Wall time of a later round, children only: the cheapest family (no engine run) and the dearest. Without
    physics genes every engine profile is cached after round 1; with them (ADR-070) the dearest case is every child
    a new physics needing one engine run per case; with ``screen_cases`` K (ADR-072) a new physics runs on K cases
    and, for the ``pass_share`` that beats the worst survivor there, on the rest. ``families`` (ADR-125,
    ``child_families``): the only families the children can have; engine runs count only when one of them runs
    the engine."""
    fam = [timings.agent(f) for f in (families or [f.value for f in AgentFamily])]
    if families is not None and not {f.value for f in ENGINE_FAMILIES} & set(families):
        physics = False
    base = n_cases * timings.load
    lo = (base + n_cases * n_children * min(fam)) / max(1, workers)
    hi_agent = n_cases * n_children * max(fam)
    if not physics:
        return lo, (base + hi_agent) / max(1, workers)
    if screen_cases:
        k = min(screen_cases, n_cases)
        runs = n_children * (k + pass_share * (n_cases - k))
        base += k * timings.load
    else:
        runs = n_children * n_cases
    return lo, (base + hi_agent + runs * timings.engine) / max(1, workers)


def guess_note(guessed: list[str]) -> str:
    """ADR-126, one line for a log: what an estimate rests on without a measurement from this computer
    (``Timings.guessed``)."""
    what = ["a SNOWPACK run" if g == "engine" else f"the {g} agents" for g in guessed]
    return (f"note: this computer has not timed {' or '.join(what)} yet: the estimate uses another machine's "
            "speed there and can be several times off; the time left shown while it runs is measured")


def fmt_s(s: float) -> str:
    if s < 90:
        return f"{s:.0f} s"
    if s < 5400:
        return f"{s / 60:.0f} min"
    return f"{s / 3600:.1f} h"
