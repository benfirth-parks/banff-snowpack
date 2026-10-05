"""The local training loop, ``snowagent lab train`` (owner, 2026-10-05; ADR-066, ADR-067).

"One training round should be taking the historical weather forecasts and weather actuals before every observed pit
for all seasons. The two top agents then get mutated to create a new set of agents we can have compete against each
other." So:

- Round 1 scores the initial population (the five family defaults, or genome files) on every training case of the
  case set (split mode ``all`` by default: every season; a ``loso_<season>`` set in the promotion check, training
  split only).
- Each later round keeps the top ``survivors`` (default 2) unchanged and fills the population with mutations of each
  survivor and crossovers of the survivors (``evolve.children``). Agents are ranked by the leaderboard composite
  (the frozen scoring weights; the loop never changes them), ties broken by the mean case composite, then fewer
  failures, then the genome hash.
- Every round logs the train-vs-held-out composite gap of the top two on the monitor season (the milestone-3 hook,
  ``runner.heldout_gap``) and flags it when it widens ``gap_flag_rounds`` rounds in a row. In split mode ``all`` the
  monitor season is ALSO training data: the gap is a warning signal only. The proof is the leave-one-season-out
  check (``loso``, ADR-068).

Layout under ``<data root>/outputs/training/<run_id>/``: ``run.json`` (the plan; a resume uses it unchanged),
``status.json`` (live progress for the UI), ``train.log``, ``genomes/<genome hash>.json`` and ``rounds/rNN/``
(``round.json``, ``population.json`` with each genome's lineage, ``leaderboard.json``, ``scores.parquet``), then
``summary.json``. A round is written to a temporary directory and renamed into place (atomic), then recorded in the
run registry as an ``evolution`` run ``<run_id>-rNN``; a resume re-records a committed round the registry lacks.
The same seed gives the same populations and scores; a killed run resumes at the first uncommitted round, whose
finished (genome, case) pairs come from the cache.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from snowagent.lab import LAB_DISCLAIMER
from snowagent.lab.competition import scoring
from snowagent.lab.competition.runner import (
    EngineSpec,
    build_leaderboard,
    case_set_hash,
    heldout_gap,
    leaderboard,
    select_cases,
)
from snowagent.lab.genome import default_genomes
from snowagent.lab.schemas.genome import AgentGenome
from snowagent.lab.schemas.run import RunKind, RunManifest, ScoringWeights
from snowagent.lab.settings import LabConfig, season_bounds
from snowagent.lab.storage.paths import LabPaths
from snowagent.lab.storage.provenance import git_commit, new_run_id, software_version
from snowagent.lab.storage.registry import RunRegistry
from snowagent.lab.training.cache import TrainingCache
from snowagent.lab.training.estimate import (
    estimate_child_rounds,
    estimate_pairs,
    fmt_s,
    load_timings,
    save_timings,
)
from snowagent.lab.training.evaluate import (
    CaseRef,
    EvalContext,
    build_library,
    case_refs,
    evaluate_population,
)
from snowagent.lab.training.evolve import children, initial_records

TRAINING_VERSION = "lab-training-1"
GAP_NOTE = ("warning signal only: the monitor season is also training data (split mode all; in a check-loso fold, "
            "one of the training seasons); the evidence that "
            "an evolved agent generalises is `snowagent lab check-loso`")


class TrainingStopped(RuntimeError):
    """A stop was requested (``stop`` file in the run directory); resume with ``--resume``."""


@dataclass
class TrainOptions:
    rounds: int = 10
    population: int = 10
    survivors: int = 2
    mutation_strength: float = 0.2
    crossover_share: float = 0.25
    seed: int = 0
    plots: list[str] | None = None
    case_types: list[str] | None = None
    case_set: str = "all"
    splits: list[str] | None = None  # the promotion check trains on ["training"] of a loso case set
    initial: list[AgentGenome] | None = None  # default: the five family defaults
    monitor_season: str | None = None  # None: the default rule (``default_monitor_season``)
    gap_flag_rounds: int = 3
    gap_tolerance: float = 0.0
    max_redraws: int = 100
    engine: EngineSpec = field(default_factory=lambda: EngineSpec(kind="auto"))

    @classmethod
    def from_config(cls, cfg: LabConfig, **over) -> TrainOptions:
        t = cfg.training
        base = {"rounds": t.rounds, "population": t.population, "survivors": t.survivors,
                "mutation_strength": t.mutation_strength, "crossover_share": t.crossover_share,
                "monitor_season": t.monitor_season, "gap_flag_rounds": t.gap_flag_rounds,
                "gap_tolerance": t.gap_tolerance, "max_redraws": t.max_redraws}
        return cls(**(base | {k: v for k, v in over.items() if v is not None}))

    def validate(self) -> None:
        if self.rounds < 1:
            raise ValueError("--rounds must be at least 1")
        if not 1 <= self.survivors < self.population:
            raise ValueError("--survivors must be at least 1 and smaller than --population")
        if not 0 < self.mutation_strength <= 1:
            raise ValueError("--mutation-strength must be in (0, 1]")
        if not 0 <= self.crossover_share <= 1:
            raise ValueError("--crossover-share must be in [0, 1]")


@dataclass
class TrainingResult:
    run_id: str
    run_dir: Path
    rounds: list[dict]
    summary: dict
    resumed_rounds: int = 0


# --------------------------------------------------------------------------------------------- helpers


def default_monitor_season(manifests, now: datetime | None = None, season_start: str = "09-15") -> str | None:
    """The most recent completed season (its 15 Sep end has passed) whose cases cover every plot that has cases in
    the selection; else the most recent completed season; None without one."""
    now = pd.Timestamp(now or datetime.now(UTC))
    plots_all = {str(m.site_code) for m in manifests}
    by_season: dict[str, set[str]] = {}
    for m in manifests:
        by_season.setdefault(m.season, set()).add(str(m.site_code))
    done = sorted((s for s in by_season if season_bounds(s, season_start)[1] <= now), reverse=True)
    for s in done:
        if by_season[s] == plots_all:
            return s
    return done[0] if done else None


def rank_agents(df: pd.DataFrame, weights: ScoringWeights, genomes: list[AgentGenome]) -> list[dict]:
    """Leaderboard rows in rank order: composite (unrounded) high first, then mean case composite, then fewer
    failures, then genome hash; an agent with nothing scored (skipped) is last."""
    board = {r["agent_id"]: r for r in leaderboard(df, weights)}
    keyed = []
    for g in genomes:
        d = df[df["agent_id"] == g.agent_id]
        scored = d[d["status"] != "skipped"]
        comp = scored["composite"].astype(float) if "composite" in scored else pd.Series(dtype=float)
        failures = int((scored["status"] != "ok").sum())
        if comp.notna().any():
            mean_c = float(comp.mean())
            exact = scoring.leaderboard_composite(mean_c, scoring.robustness(comp.tolist(), failures), weights)
            key = (0, -exact, -mean_c, failures, g.genome_hash)
        else:
            exact, key = None, (1, 0.0, 0.0, failures, g.genome_hash)
        row = dict(board.get(g.agent_id) or {"agent_id": g.agent_id, "family": g.family.value, "label":
                                             g.display_name, "composite": None, "scored": 0})
        row |= {"genome_hash": g.genome_hash, "composite_exact": exact, "label": g.display_name}
        keyed.append((key, row))
    keyed.sort(key=lambda x: x[0])
    return [r | {"rank": i + 1} for i, (_k, r) in enumerate(keyed)]


def gap_record(df: pd.DataFrame, monitor: str | None, weights: ScoringWeights, top: list[dict],
               previous: list[dict], k: int, tol: float) -> dict:
    """Train-vs-held-out composite gap of the top agents on the monitor season; widening streak and flag."""
    rec: dict = {"monitor_season": monitor, "agents": {}, "gap": None, "widening": False, "streak": 0,
                 "flag": False, "note": GAP_NOTE}
    if monitor is None or monitor not in set(df["season"]):
        rec["note"] = "no monitor season in the selected cases: gap not computed"
        return rec
    gaps = heldout_gap(df, monitor, weights)
    vals = []
    for r in top:
        g = gaps.get(r["agent_id"])
        if g is not None:
            rec["agents"][r["agent_id"]] = g
            if g["gap"] is not None:
                vals.append(g["gap"])
    rec["gap"] = round(sum(vals) / len(vals), 4) if vals else None
    prev = next((p for p in reversed(previous) if p.get("gap") is not None), None)
    if rec["gap"] is not None and prev is not None and rec["gap"] > prev["gap"] + tol:
        rec["widening"] = True
        rec["streak"] = prev.get("streak", 0) + 1
    rec["flag"] = rec["streak"] >= k
    return rec


def _atomic_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=str))
    os.replace(tmp, path)


def training_root(paths: LabPaths) -> Path:
    return paths.outputs / "training"


def round_dir(run_dir: Path, r: int) -> Path:
    return run_dir / "rounds" / f"r{r:02d}"


def committed_rounds(run_dir: Path) -> list[int]:
    d = run_dir / "rounds"
    if not d.is_dir():
        return []
    return sorted(int(p.name[1:]) for p in d.iterdir() if p.name.startswith("r") and (p / "round.json").is_file())


def load_round(run_dir: Path, r: int) -> dict:
    d = round_dir(run_dir, r)
    return {"round": json.loads((d / "round.json").read_text()),
            "population": json.loads((d / "population.json").read_text()),
            "leaderboard": json.loads((d / "leaderboard.json").read_text())}


# --------------------------------------------------------------------------------------------- the run


class _Run:
    def __init__(self, paths: LabPaths, run_id: str, log: Callable[[str], None]) -> None:
        self.paths = paths
        self.run_id = run_id
        self.dir = training_root(paths) / run_id
        self._log = log
        self.status: dict = {}

    def log(self, msg: str) -> None:
        line = f"{datetime.now(UTC):%Y-%m-%dT%H:%M:%SZ} {msg}"
        self.dir.mkdir(parents=True, exist_ok=True)
        with open(self.dir / "train.log", "a") as fh:
            fh.write(line + "\n")
        self._log(msg)

    def set_status(self, **kw) -> None:
        self.status |= kw | {"updated_at": datetime.now(UTC).isoformat(), "pid": os.getpid()}
        _atomic_json(self.dir / "status.json", self.status)

    def check_stop(self) -> None:
        if (self.dir / "stop").exists():
            raise TrainingStopped(f"stop requested for {self.run_id}")


def _plan(opts: TrainOptions, cfg: LabConfig, refs: list[CaseRef], cases, initial: list[AgentGenome],
          monitor: str | None) -> dict:
    return {"training_version": TRAINING_VERSION, "scoring_version": scoring.SCORING_VERSION,
            "case_set": opts.case_set, "splits": opts.splits, "plots": opts.plots, "case_types": opts.case_types,
            "split_mode": sorted({(m.split_mode.value if m.split_mode else "all") for _, m in cases})[0],
            "case_ids": [r.case_id for r in refs], "case_set_hash": case_set_hash(cases),
            "seasons": sorted({r.manifest.season for r in refs}), "rounds": opts.rounds,
            "population": opts.population, "survivors": opts.survivors,
            "mutation_strength": opts.mutation_strength, "crossover_share": opts.crossover_share, "seed": opts.seed,
            "max_redraws": opts.max_redraws, "initial": [g.model_dump(mode="json") for g in initial],
            "monitor_season": monitor, "gap_flag_rounds": opts.gap_flag_rounds, "gap_tolerance": opts.gap_tolerance,
            "engine": opts.engine.__dict__, "config_hash": cfg.config_hash(),
            "scoring_weights": cfg.scoring_weights.model_dump()}


def _opts_from_plan(plan: dict, engine_override: EngineSpec | None = None) -> TrainOptions:
    return TrainOptions(rounds=plan["rounds"], population=plan["population"], survivors=plan["survivors"],
                        mutation_strength=plan["mutation_strength"], crossover_share=plan["crossover_share"],
                        seed=plan["seed"], plots=plan["plots"], case_types=plan["case_types"],
                        case_set=plan["case_set"], splits=plan["splits"],
                        initial=[AgentGenome.model_validate(g) for g in plan["initial"]],
                        monitor_season=plan["monitor_season"], gap_flag_rounds=plan["gap_flag_rounds"],
                        gap_tolerance=plan["gap_tolerance"], max_redraws=plan["max_redraws"],
                        engine=engine_override or EngineSpec(**plan["engine"]))


def prepare(paths: LabPaths, cfg: LabConfig, opts: TrainOptions, run_id: str | None = None,
            resume: bool = False) -> tuple[str, dict, list[CaseRef]]:
    """Resolve the run id and plan (a resume takes the stored plan) and the training cases."""
    opts.validate()
    if resume:
        run_id = run_id or latest_unfinished(paths)
        if run_id is None:
            raise ValueError("no unfinished training run to resume (name one with --run-id)")
        f = training_root(paths) / run_id / "run.json"
        if not f.is_file():
            raise ValueError(f"no training run {run_id} to resume")
        plan = json.loads(f.read_text())["plan"]
        opts = _opts_from_plan(plan)
    cases = select_cases(paths, opts.case_set, opts.splits, opts.plots, opts.case_types)
    if not cases:
        raise ValueError(f"no scorable training case in case set {opts.case_set!r} with these filters")
    modes = {(m.split_mode.value if m.split_mode else "all") for _, m in cases}
    if len(modes) > 1:
        raise ValueError(f"cases of several split modes {sorted(modes)}")
    refs = case_refs(cases)
    if resume:
        if case_set_hash(cases) != plan["case_set_hash"]:
            raise ValueError(f"the cases of run {run_id} changed since it started (rebuilt?): start a new run")
        return run_id, plan, refs
    initial = list(opts.initial or default_genomes(cfg.genome))
    hashes = [g.genome_hash for g in initial]
    if len(set(hashes)) != len(hashes):
        raise ValueError("two initial genomes are identical (same genome hash)")
    if len(initial) < opts.survivors:
        raise ValueError(f"the initial population ({len(initial)}) is smaller than --survivors {opts.survivors}")
    seasons = {m.season for _, m in cases}
    monitor = opts.monitor_season or default_monitor_season([m for _, m in cases], season_start=cfg.season_start)
    if opts.monitor_season and opts.monitor_season not in seasons:
        raise ValueError(f"monitor season {opts.monitor_season} has no selected training case")
    plan = _plan(opts, cfg, refs, cases, initial, monitor)
    plan_hash = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    run_id = run_id or new_run_id("training", salt=plan_hash)
    f = training_root(paths) / run_id / "run.json"
    if f.is_file():
        old = json.loads(f.read_text())
        if old["plan_hash"] != plan_hash:
            raise ValueError(f"training run {run_id} exists with another plan; use --resume to continue it, or "
                             "start a new run")
    return run_id, plan, refs


def latest_unfinished(paths: LabPaths) -> str | None:
    root = training_root(paths)
    runs = sorted((d for d in root.iterdir() if (d / "run.json").is_file() and not (d / "summary.json").is_file()),
                  key=lambda d: d.stat().st_mtime, reverse=True) if root.is_dir() else []
    return runs[0].name if runs else None


def _manifest(run_id: str, plan: dict, created: datetime, refs: list[CaseRef], genomes: list[AgentGenome],
              df: pd.DataFrame, counts: dict, outputs: list[str], runtime_s: float, cfg: LabConfig,
              warnings: list[str]) -> RunManifest:
    profile_ids = set()
    for r in refs:
        profile_ids.update(r.manifest.visible_profile_ids)
        if r.manifest.target_profile_id:
            profile_ids.add(r.manifest.target_profile_id)
    versions = sorted({v for v in df.get("snowpack_version", pd.Series(dtype=object)).dropna().unique()})
    errors = int((df["status"] == "error").sum()) if len(df) else 0
    return RunManifest(
        run_id=run_id, kind=RunKind.evolution, status="ok" if not errors else "partial", created_at=created,
        finished_at=datetime.now(UTC), config_hash=cfg.config_hash(), data_hash=plan["case_set_hash"],
        software_version=software_version(), git_commit=git_commit(Path(__file__).parent),
        snowpack_version=versions[0] if len(versions) == 1 else ("; ".join(versions) or None), seed=plan["seed"],
        scoring_weights=cfg.scoring_weights, splits={plan["split_mode"]: plan["seasons"]}, case_ids=plan["case_ids"],
        agent_ids=[g.agent_id for g in genomes], genome_hashes={g.agent_id: g.genome_hash for g in genomes},
        case_set_hash=plan["case_set_hash"], profile_ids_used=sorted(profile_ids), outputs=outputs,
        counts=counts | {"errors": errors}, warnings=warnings, runtime_s=round(runtime_s, 1))


def _record(paths: LabPaths, m: RunManifest) -> None:
    reg = RunRegistry(paths.registry)
    if reg.get(m.run_id) is None:
        reg.record(m)


def _population(run: _Run, plan: dict, r: int, spec) -> tuple[list[AgentGenome], list[dict], list[str]]:
    """Round r's genomes, their lineage records and roles."""
    if r == 1:
        init = [AgentGenome.model_validate(g) for g in plan["initial"]]
        return init, initial_records(init, spec), ["initial"] * len(init)
    prev = load_round(run.dir, r - 1)
    by_hash = {AgentGenome.model_validate(p["genome"]).genome_hash: p for p in prev["population"]}
    ranked = [row for row in prev["leaderboard"]["ranked"] if row.get("composite_exact") is not None]
    keep = [by_hash[row["genome_hash"]] for row in ranked[: plan["survivors"]]]
    survivors = [AgentGenome.model_validate(p["genome"]) for p in keep]
    seen = set()
    for i in range(1, r):
        seen.update(AgentGenome.model_validate(p["genome"]).genome_hash for p in load_round(run.dir, i)["population"])
    kids = children(survivors, plan["population"] - len(survivors), r, plan["seed"], plan["mutation_strength"],
                    plan["crossover_share"], spec, seen, plan["max_redraws"])
    genomes = survivors + [g for g, _ in kids]
    return genomes, [p["lineage"] for p in keep] + [rec for _, rec in kids], \
        ["survivor"] * len(survivors) + ["child"] * len(kids)


def run_training(paths: LabPaths, cfg: LabConfig, opts: TrainOptions | None = None, *, workers: int = 1,
                 run_id: str | None = None, resume: bool = False, log: Callable[[str], None] = print,
                 progress: Callable[[int, int], None] | None = None, engine: EngineSpec | None = None,
                 estimate_only: bool = False) -> TrainingResult:
    """Run (or resume) a training run; see the module doc. ``engine`` overrides the stored engine on a resume
    (e.g. another binary path); ``estimate_only`` prints the estimate and returns before round 1."""
    t_start = time.time()
    opts = opts or TrainOptions.from_config(cfg)
    run_id, plan, refs = prepare(paths, cfg, opts, run_id, resume)
    if engine is not None:
        plan = plan | {"engine": engine.__dict__}
    run = _Run(paths, run_id, log)
    plan_hash = hashlib.sha256(json.dumps({k: v for k, v in plan.items()}, sort_keys=True).encode()).hexdigest()
    run_json = run.dir / "run.json"
    if run_json.is_file():
        meta = json.loads(run_json.read_text())
        created = datetime.fromisoformat(meta["created_at"])
    else:
        created = datetime.now(UTC)
        meta = {"run_id": run_id, "plan_hash": plan_hash, "created_at": created.isoformat(), "plan": plan,
                "label": LAB_DISCLAIMER}
    engine_spec = EngineSpec(**plan["engine"])
    weights = cfg.scoring_weights
    if weights.model_dump() != plan["scoring_weights"]:
        raise ValueError("the scoring weights changed since this run started; the loop never changes them")
    lib = build_library(paths, plan["case_set"], TrainingCache(paths.outputs / "cache"), workers)
    ctx = EvalContext(paths=paths, case_set=plan["case_set"], seed=plan["seed"], weights=weights,
                      config_hash=cfg.config_hash(), engine=engine_spec, library_file=lib)
    timings = load_timings(paths, ctx.cache.timings)
    done = committed_rounds(run.dir)
    spec = cfg.genome
    # estimate before starting (an estimate-only call writes nothing)
    emit = log if estimate_only else run.log
    if not done or estimate_only:
        first, _rec, _roles = (_population(run, plan, 1, spec) if not done else ([], [], []))
        if first:
            work = [([g for g in first if not ctx.cache.has(ctx.key(g, r))], ctx.cache.engine_cached_for(r.case_hash))
                    for r in refs]
            est = estimate_pairs(work, timings, workers, len(first) * len(refs))
            lo, hi = estimate_child_rounds(len(refs), plan["population"] - plan["survivors"], timings, workers)
            total_lo, total_hi = est.wall_s + (plan["rounds"] - 1) * lo, est.wall_s + (plan["rounds"] - 1) * hi
            msg = (f"estimate ({timings.source} timings, {workers} workers): round 1 {fmt_s(est.wall_s)} "
                   f"({est.uncached_pairs} of {est.pairs} agent-case pairs to run, {est.engine_runs} SNOWPACK engine "
                   f"runs)")
            if plan["rounds"] > 1:
                msg += (f"; rounds 2-{plan['rounds']} about {fmt_s(lo)}-{fmt_s(hi)} each (depends on the survivors' "
                        f"families; engine profiles cached); total about "
                        + (fmt_s(total_lo) if fmt_s(total_lo) == fmt_s(total_hi) else
                           f"{fmt_s(total_lo)}-{fmt_s(total_hi)}"))
            emit(msg)
            if est.snowpack_dominates:
                emit(f"warning: SNOWPACK-family agents are {est.snowpack_share:.0%} of the estimated cost of "
                        f"round 1 (about {fmt_s(timings.engine)} per engine run); each case's engine profile is "
                        "cached after its first run, so later rounds and reruns do not pay it again")
            meta["estimate"] = {"round1": est.to_dict(), "later_round_s": [round(lo, 1), round(hi, 1)],
                                "total_s": [round(total_lo, 1), round(total_hi, 1)], "workers": workers,
                                "timings": timings.source}
            if estimate_only:
                return TrainingResult(run_id, run.dir, [], {"estimate": meta["estimate"]}, len(done))
    _atomic_json(run_json, meta)
    log(f"training run {run_id}: {len(refs)} cases of case set {plan['case_set']} (seasons {plan['seasons'][0]} "
        f"to {plan['seasons'][-1]}), {plan['rounds']} rounds, population {plan['population']}, survivors {plan['survivors']}, seed {plan['seed']}"
        f" -> {run.dir}  [{LAB_DISCLAIMER}]")
    # registry repair for rounds committed before a kill
    for r in done:
        info = json.loads((round_dir(run.dir, r) / "manifest.json").read_text())
        _record(paths, RunManifest.model_validate(info))
    resumed = len(done)
    if resumed:
        run.log(f"resuming after round {done[-1]} ({resumed} rounds committed)")
    run.set_status(state="running", run_id=run_id, rounds=plan["rounds"], round=done[-1] if done else 0,
                   phase="starting", done=0, total=len(refs), started_at=run.status.get("started_at")
                   or datetime.now(UTC).isoformat())
    gaps = [load_round(run.dir, r)["round"]["gap"] for r in done]
    rounds_info = [load_round(run.dir, r)["round"] for r in done]
    try:
        for r in range(len(done) + 1, plan["rounds"] + 1):
            run.check_stop()
            t0 = time.time()
            genomes, lineage, roles = _population(run, plan, r, spec)
            for g in genomes:
                f = run.dir / "genomes" / f"{g.genome_hash}.json"
                if not f.is_file():
                    f.parent.mkdir(parents=True, exist_ok=True)
                    f.write_text(g.model_dump_json(indent=1))
            work = [([g for g in genomes if not ctx.cache.has(ctx.key(g, ref))],
                     ctx.cache.engine_cached_for(ref.case_hash)) for ref in refs]
            est = estimate_pairs(work, timings, workers, len(genomes) * len(refs))
            run.log(f"round {r}/{plan['rounds']}: {len(genomes)} agents ({roles.count('survivor')} survivors), "
                    f"{est.uncached_pairs} of {est.pairs} pairs to run, estimate {fmt_s(est.wall_s)}")
            run.set_status(round=r, phase="evaluating", done=0, total=est.cases_with_work,
                           estimate_s=round(est.wall_s, 1), round_started_at=datetime.now(UTC).isoformat())

            def _progress(d: int, n: int, _r=r) -> None:
                if d == n or d % 10 == 0:
                    run.set_status(done=d, total=n)
                if progress:
                    progress(d, n)
                run.check_stop()

            res = evaluate_population(refs, genomes, ctx, workers, _progress)
            timings.update(res.worker_stats)
            save_timings(timings, ctx.cache.timings)
            df = res.scores
            ranked = rank_agents(df, weights, genomes)
            top = ranked[: plan["survivors"]]
            g_rec = gap_record(df, plan["monitor_season"], weights, ranked[:2], gaps, plan["gap_flag_rounds"],
                               plan["gap_tolerance"])
            gaps.append(g_rec)
            best = ranked[0]
            info = {"round": r, "run_id": run_id, "agents": [g.agent_id for g in genomes], "roles": roles,
                    "best": {k: best.get(k) for k in ("agent_id", "label", "family", "genome_hash", "composite",
                                                      "composite_exact", "case_composite", "robustness")},
                    "survivors_next": [t["agent_id"] for t in top], "eval": res.summary(),
                    "estimate_s": round(est.wall_s, 1), "wall_s": round(time.time() - t0, 1), "gap": g_rec,
                    "committed_at": datetime.now(UTC).isoformat(), "label": LAB_DISCLAIMER}
            lb = build_leaderboard(df, weights) | {"ranked": ranked}
            manifest = _manifest(f"{run_id}-r{r:02d}", plan, datetime.fromisoformat(run.status["round_started_at"]),
                                 refs, genomes, df, {"round": r, "cases": len(refs), "agents": len(genomes),
                                                     "pairs": res.pairs, "cache_hits": res.hits,
                                                     "engine_runs": res.engine_runs},
                                 [str(round_dir(run.dir, r))], time.time() - t0, cfg,
                                 ["gap widened " + str(g_rec["streak"]) + " rounds in a row"] if g_rec["flag"] else [])
            tmp = run.dir / "rounds" / f".r{r:02d}.tmp"
            if tmp.exists():
                shutil.rmtree(tmp)
            tmp.mkdir(parents=True)
            df.to_parquet(tmp / "scores.parquet", index=False)
            (tmp / "population.json").write_text(json.dumps(
                [{"genome": g.model_dump(mode="json"), "lineage": rec, "role": role}
                 for g, rec, role in zip(genomes, lineage, roles, strict=True)], indent=1))
            (tmp / "leaderboard.json").write_text(json.dumps(lb, indent=1, default=str))
            (tmp / "manifest.json").write_text(manifest.model_dump_json(indent=1))
            (tmp / "round.json").write_text(json.dumps(info, indent=1, default=str))
            os.replace(tmp, round_dir(run.dir, r))
            _record(paths, manifest)
            rounds_info.append(info)
            gap_txt = f"gap {g_rec['gap']:+.3f} on {g_rec['monitor_season']}" if g_rec["gap"] is not None \
                else "gap n/a"
            run.log(f"round {r} committed: best {best['label']} composite {best['composite']:.4f}; "
                    f"next survivors {', '.join(t['label'] for t in top)}; {gap_txt}; cache hit rate "
                    f"{res.hit_rate:.0%}, {res.engine_runs} engine runs, {fmt_s(time.time() - t0)}")
            if g_rec["flag"]:
                run.log(f"FLAG round {r}: the train-vs-held-out gap of the top two on {g_rec['monitor_season']} widened "
                        f"{g_rec['streak']} rounds in a row ({GAP_NOTE})")
    except TrainingStopped as exc:
        run.set_status(state="stopped", phase="stopped", message=str(exc))
        run.log(f"stopped: {exc}; resume with --resume --run-id {run_id}")
        raise
    except BaseException as exc:
        run.set_status(state="failed", phase="failed", message=f"{type(exc).__name__}: {exc}"[:500])
        raise
    summary = finish(run, plan, cfg, refs, rounds_info, created, time.time() - t_start, resumed)
    return TrainingResult(run_id, run.dir, rounds_info, summary, resumed)


def finish(run: _Run, plan: dict, cfg: LabConfig, refs: list[CaseRef], rounds_info: list[dict], created: datetime,
           wall_s: float, resumed: int) -> dict:
    last = load_round(run.dir, plan["rounds"])
    winner_row = last["leaderboard"]["ranked"][0]
    pop = {AgentGenome.model_validate(p["genome"]).genome_hash: p for p in last["population"]}
    win = pop[winner_row["genome_hash"]]
    pairs = sum(i["eval"]["pairs"] for i in rounds_info)
    hits = sum(i["eval"]["cache_hits"] for i in rounds_info)
    prior = json.loads((run.dir / "summary.json").read_text()) if (run.dir / "summary.json").is_file() else {}
    summary = {
        "run_id": run.run_id, "label": LAB_DISCLAIMER, "rounds": plan["rounds"], "cases": len(refs),
        "case_set": plan["case_set"], "seed": plan["seed"],
        "best_per_round": [{"round": i["round"], "agent_id": i["best"]["agent_id"], "label": i["best"]["label"],
                            "family": i["best"]["family"], "composite": i["best"]["composite"]} for i in rounds_info],
        "winner": {"agent_id": winner_row["agent_id"], "label": winner_row["label"], "family": winner_row["family"],
                   "genome_hash": winner_row["genome_hash"], "composite": winner_row["composite"],
                   "changed_vs_default": win["lineage"]["changed_vs_default"], "genome": win["genome"],
                   "reference": f"{run.run_id}/{plan['rounds']}/1"},
        "gap_trace": [{"round": i["round"], "gap": i["gap"]["gap"], "flag": i["gap"]["flag"],
                       "streak": i["gap"]["streak"]} for i in rounds_info],
        "monitor_season": plan["monitor_season"], "gap_note": GAP_NOTE,
        "flags": [i["round"] for i in rounds_info if i["gap"]["flag"]],
        "cache": {"pairs": pairs, "hits": hits, "hit_rate": round(hits / pairs, 4) if pairs else 0.0,
                  "engine_runs": sum(i["eval"]["engine_runs"] for i in rounds_info)},
        "wall_s_this_invocation": round(wall_s, 1), "round_wall_s": [i["wall_s"] for i in rounds_info],
        "wall_s_rounds_total": round(sum(i["wall_s"] for i in rounds_info), 1), "resumed_rounds": resumed,
        "finished_at": prior.get("finished_at") or datetime.now(UTC).isoformat()}
    _atomic_json(run.dir / "summary.json", summary)
    genomes = [AgentGenome.model_validate(p["genome"]) for p in last["population"]]
    df = pd.read_parquet(round_dir(run.dir, plan["rounds"]) / "scores.parquet")
    _record(run.paths, _manifest(run.run_id, plan, created, refs, genomes, df,
                                 {"rounds": plan["rounds"], "cases": len(refs), "pairs": pairs, "cache_hits": hits},
                                 [str(run.dir / "summary.json")], summary["wall_s_rounds_total"], cfg,
                                 [f"gap flagged in rounds {summary['flags']}"] if summary["flags"] else []))
    run.set_status(state="finished", phase="finished", round=plan["rounds"],
                   finished_at=summary["finished_at"])
    run.log(f"finished: winner {summary['winner']['label']} ({summary['winner']['family']}) composite "
            f"{summary['winner']['composite']}; cache hit rate {summary['cache']['hit_rate']:.0%}")
    return summary


def list_training_runs(paths: LabPaths) -> list[str]:
    root = training_root(paths)
    if not root.is_dir():
        return []
    return [d.name for d in sorted((d for d in root.iterdir() if (d / "run.json").is_file()),
                                   key=lambda d: json.loads((d / "run.json").read_text())["created_at"],
                                   reverse=True)]
