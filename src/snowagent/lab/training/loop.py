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

from snowagent.lab import LAB_DISCLAIMER, events
from snowagent.lab.agents.physics import engine_physics, physics_keys
from snowagent.lab.agents.segments import SegmentStore
from snowagent.lab.competition import scoring
from snowagent.lab.competition.runner import (
    EngineSpec,
    build_leaderboard,
    case_set_hash,
    heldout_gap,
    leaderboard,
    select_cases,
)
from snowagent.lab.genome import default_genome, default_genomes
from snowagent.lab.schemas.genome import AgentFamily, AgentGenome, GenomeSpec, default_spec
from snowagent.lab.schemas.run import RunKind, RunManifest, ScoringWeights
from snowagent.lab.settings import LabConfig, season_bounds
from snowagent.lab.storage.paths import LabPaths
from snowagent.lab.storage.provenance import git_commit, new_run_id, software_version
from snowagent.lab.storage.registry import RunRegistry
from snowagent.lab.training.cache import ENGINE_FAMILIES, TrainingCache
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
from snowagent.lab.training.evolve import children, family_slot_children, initial_records
from snowagent.lab.training.screen import screen_sample, screen_threshold

TRAINING_VERSION = "lab-training-1"
GAP_NOTE = ("warning signal only: the monitor season is also training data (split mode all; in a check-loso fold, "
            "one of the training seasons); the evidence that "
            "an evolved agent generalises is `snowagent lab check-loso`")


# ADR-083 (owner, 2026-10-06): a new run locks the 3 most recent seasons of its cases (2023-24 to 2025-26 on the data of
# 2026-10-06): they never train or select agents and analogues never draw on them; each round's leaders are scored on
# them after selection. `--locked-seasons 0` trains on every season. Kept here rather than in config/lab.yaml so that
# the setting, like every training option, stays outside the prediction cache's code hash (ADR-080).
LOCKED_SEASONS = 3
# ADR-087: new runs choose survivors by the composite less a penalty for uneven results across winters and plots
SELECTION = "consistent"
CONSISTENCY_K = 0.5  # penalty per unit of spread (standard deviation of season-plot residuals)
CONSISTENCY_MIN_CASES = 3  # a season-plot group needs this many scored cases to count
# ADR-089: new runs also subtract a small penalty for drifting from the family's standard settings, so a change has to
# pay for itself: per unit of drift (one gene moved across its whole allowed range, or one choice changed)
DRIFT_K = 0.002
DRIFT_K_MAX = 0.05


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
    screen_cases: int | None = None  # ADR-072: new physics genomes first on a stratified sample of K cases
    family_slots: bool = False  # ADR-073: one slot per family for a mutant of that family's best agent
    weather_sources: list[str] | None = None  # ADR-076: station, mixed, era5_only (default: every case)
    locked_seasons: int = 0  # ADR-083: the N most recent seasons never train or select; scored each round
    selection: str = "composite"  # ADR-087: "consistent" ranks by composite less K x season-plot spread
    drift_penalty: float = 0.0  # ADR-089: ranks by composite less this x the drift from standard settings

    @classmethod
    def from_config(cls, cfg: LabConfig, **over) -> TrainOptions:
        t = cfg.training
        base = {"rounds": t.rounds, "population": t.population, "survivors": t.survivors,
                "mutation_strength": t.mutation_strength, "crossover_share": t.crossover_share,
                "monitor_season": t.monitor_season, "gap_flag_rounds": t.gap_flag_rounds,
                "gap_tolerance": t.gap_tolerance, "max_redraws": t.max_redraws,
                "locked_seasons": LOCKED_SEASONS, "selection": SELECTION,
                "drift_penalty": DRIFT_K}
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
        if self.locked_seasons < 0:
            raise ValueError("--locked-seasons must be 0 or more")
        if self.selection not in ("composite", "consistent"):
            raise ValueError("--selection must be composite or consistent")
        if not 0 <= self.drift_penalty <= DRIFT_K_MAX:
            raise ValueError(f"--drift-penalty must be in [0, {DRIFT_K_MAX}]")
        if self.screen_cases is not None and self.screen_cases < 1:
            raise ValueError("--screen-cases must be at least 1")
        if self.family_slots and self.population - self.survivors < len(AgentFamily):
            raise ValueError(f"--family-slots needs at least {len(AgentFamily)} children per round "
                             f"(--population minus --survivors)")


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


def group_means(df: pd.DataFrame, min_cases: int = CONSISTENCY_MIN_CASES) -> pd.DataFrame:
    """Mean case composite per agent, season and plot, for groups with at least ``min_cases`` scored cases."""
    d = df[df["status"] == "ok"] if "status" in df else df
    if d.empty or "composite" not in d:
        return pd.DataFrame(columns=["agent_id", "season", "site_code", "mean", "size"])
    g = d.groupby(["agent_id", "season", "site_code"])["composite"].agg(["mean", "size"]).reset_index()
    return g[g["size"] >= min_cases]


def reference_means(df: pd.DataFrame, agent_id: str) -> dict[tuple[str, str], float]:
    """(season, plot) -> one agent's group mean: the yardstick of ``spreads`` (standard SNOWPACK's, ADR-087)."""
    g = group_means(df[df["agent_id"] == agent_id])
    return {(r.season, r.site_code): float(r.mean) for r in g.itertuples()}


def spreads(df: pd.DataFrame, ref: dict[tuple[str, str], float] | None = None) -> dict[str, float]:
    """ADR-087: agent id -> how unevenly it does across winters and plots: the standard deviation, over the
    season-plot groups, of its group mean less the yardstick's (``ref``: standard SNOWPACK's group means, so a hard
    winter counts against no one; without one, the mean of the agents ranked). 0 with fewer than two groups."""
    g = group_means(df)
    if g.empty:
        return {}
    if ref:
        g = g[[k in ref for k in zip(g["season"], g["site_code"], strict=True)]].copy()
        g["res"] = g["mean"] - [ref[k] for k in zip(g["season"], g["site_code"], strict=True)]
    else:
        g = g.copy()
        g["res"] = g["mean"] - g.groupby(["season", "site_code"])["mean"].transform("mean")
    return {a: (float(x.std(ddof=0)) if len(x) >= 2 else 0.0) for a, x in g.groupby("agent_id")["res"]}


def _yardstick(run_dir: Path, df: pd.DataFrame) -> dict[tuple[str, str], float] | None:
    """Standard SNOWPACK's season-plot means from round 1 (every initial agent is scored there; ``df`` when round 1 is
    the one being ranked); None when it was not in the run (``spreads`` then uses the agents' mean)."""
    sp = default_genome(AgentFamily.snowpack).agent_id
    f = round_dir(run_dir, 1) / "scores.parquet"
    src = pd.read_parquet(f) if f.is_file() else df
    return reference_means(src, sp) or None


def drift(genome: AgentGenome, spec: GenomeSpec | None = None) -> float:
    """ADR-089: how far an agent's settings are from its family's standard ones. Per gene |value - default| /
    (max - min), a changed choice 1, summed over the family's genes (a gene the genome lacks counts 0)."""
    spec = (spec or default_spec()).for_version(genome.schema_version)
    total = 0.0
    for name, (_block, gs) in spec.family_genes(genome.family).items():
        v = genome.genes.get(name, gs.default)
        if gs.kind == "choice":
            total += float(v != gs.default)
        else:
            total += abs(float(v) - float(gs.default)) / (float(gs.max) - float(gs.min))
    return total


def rank_agents(df: pd.DataFrame, weights: ScoringWeights, genomes: list[AgentGenome],
                selection: str = "composite", ref: dict[tuple[str, str], float] | None = None,
                drift_k: float = 0.0, spec: GenomeSpec | None = None) -> list[dict]:
    """Leaderboard rows in rank order: composite (unrounded) high first, then mean case composite, then fewer
    failures, then genome hash; an agent with nothing scored (skipped) is last. ``selection`` "consistent"
    (ADR-087) ranks by the composite less ``CONSISTENCY_K`` times the agent's season-plot spread instead; the rows
    then carry ``spread`` and ``selection_score``. ``drift_k`` > 0 (ADR-089) also subtracts ``drift_k`` times the
    agent's drift from its family's standard settings; the rows then carry ``drift`` and ``selection_score``."""
    board = {r["agent_id"]: r for r in leaderboard(df, weights)}
    spread = spreads(df, ref) if selection == "consistent" else {}
    drifts = {g.agent_id: drift(g, spec) for g in genomes} if drift_k > 0 else {}
    keyed = []
    for g in genomes:
        d = df[df["agent_id"] == g.agent_id]
        scored = d[d["status"] != "skipped"]
        comp = scored["composite"].astype(float) if "composite" in scored else pd.Series(dtype=float)
        failures = int((scored["status"] != "ok").sum())
        if comp.notna().any():
            mean_c = float(comp.mean())
            exact = scoring.leaderboard_composite(mean_c, scoring.robustness(comp.tolist(), failures), weights)
            sel = exact - CONSISTENCY_K * spread.get(g.agent_id, 0.0) if selection == "consistent" else exact
            sel -= drift_k * drifts.get(g.agent_id, 0.0)
            key = (0, -sel, -exact, -mean_c, failures, g.genome_hash)
        else:
            exact, sel, key = None, None, (1, 0.0, 0.0, 0.0, failures, g.genome_hash)
        row = dict(board.get(g.agent_id) or {"agent_id": g.agent_id, "family": g.family.value, "label":
                                             g.display_name, "composite": None, "scored": 0})
        row |= {"genome_hash": g.genome_hash, "composite_exact": exact, "label": g.display_name}
        if selection == "consistent":
            row |= {"spread": spread.get(g.agent_id), "selection_score": sel}
        if drift_k > 0:
            row |= {"drift": drifts[g.agent_id], "selection_score": sel}
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
          monitor: str | None, locked: tuple[list[str], list[str]] = ([], [])) -> dict:
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
            "scoring_weights": cfg.scoring_weights.model_dump()} | _extensions(opts, refs, locked)


def _extensions(opts: TrainOptions, refs: list[CaseRef], locked: tuple[list[str], list[str]] = ([], [])) -> dict:
    """Milestone-5 options, in the plan only when used (the owner's default plan keeps its milestone-4 form)."""
    out: dict = {}
    if opts.screen_cases:
        out["screen_cases"] = opts.screen_cases
        out["screen_case_ids"] = screen_sample(refs, opts.screen_cases, opts.seed)
    if opts.family_slots:
        out["family_slots"] = True
    if opts.weather_sources:
        out["weather_sources"] = opts.weather_sources
    if locked[0]:
        out["locked_seasons"], out["locked_case_ids"] = locked
    if opts.selection != "composite":
        out["selection"] = opts.selection  # ADR-087; a plan without the key (older runs) selects on composite
    if opts.drift_penalty:
        out["drift_penalty"] = opts.drift_penalty  # ADR-089; a plan without the key (older runs) has none
    return out


def split_locked(cases: list, n: int, seasons: list[str] | None = None) -> tuple[list, list, list[str]]:
    """ADR-083: (training cases, locked cases, locked seasons). ``seasons`` (a resume) names the locked seasons;
    otherwise they are the ``n`` most recent seasons of the selection, and none when fewer than ``n + 2`` seasons
    would remain to train on (a small selection, e.g. a quick check, trains on all of its cases)."""
    have = sorted({m.season for _, m in cases})
    if seasons is None:
        seasons = have[-n:] if n and len(have) >= n + 2 else []
    lock = set(seasons)
    return [c for c in cases if c[1].season not in lock], [c for c in cases if c[1].season in lock], list(seasons)


def _opts_from_plan(plan: dict, engine_override: EngineSpec | None = None) -> TrainOptions:
    return TrainOptions(rounds=plan["rounds"], population=plan["population"], survivors=plan["survivors"],
                        mutation_strength=plan["mutation_strength"], crossover_share=plan["crossover_share"],
                        seed=plan["seed"], plots=plan["plots"], case_types=plan["case_types"],
                        case_set=plan["case_set"], splits=plan["splits"],
                        initial=[AgentGenome.model_validate(g) for g in plan["initial"]],
                        monitor_season=plan["monitor_season"], gap_flag_rounds=plan["gap_flag_rounds"],
                        gap_tolerance=plan["gap_tolerance"], max_redraws=plan["max_redraws"],
                        engine=engine_override or EngineSpec(**plan["engine"]),
                        screen_cases=plan.get("screen_cases"), family_slots=bool(plan.get("family_slots")),
                        weather_sources=plan.get("weather_sources"),
                        locked_seasons=len(plan.get("locked_seasons") or []),
                        selection=plan.get("selection", "composite"),
                        drift_penalty=float(plan.get("drift_penalty") or 0.0))


def prepare(paths: LabPaths, cfg: LabConfig, opts: TrainOptions, run_id: str | None = None,
            resume: bool = False) -> tuple[str, dict, list[CaseRef], list[CaseRef]]:
    """Resolve the run id and plan (a resume takes the stored plan), the training cases and the locked test cases
    (ADR-083: never trained or selected on)."""
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
    cases = select_cases(paths, opts.case_set, opts.splits, opts.plots, opts.case_types,
                         weather_sources=opts.weather_sources)
    if not cases:
        raise ValueError(f"no scorable training case in case set {opts.case_set!r} with these filters")
    modes = {(m.split_mode.value if m.split_mode else "all") for _, m in cases}
    if len(modes) > 1:
        raise ValueError(f"cases of several split modes {sorted(modes)}")
    cases, locked_cases, locked = split_locked(cases, opts.locked_seasons,
                                               (plan.get("locked_seasons") or []) if resume else None)
    refs, locked_refs = case_refs(cases), case_refs(locked_cases)
    if resume:
        if case_set_hash(cases) != plan["case_set_hash"] or \
                sorted(r.case_id for r in locked_refs) != sorted(plan.get("locked_case_ids") or []):
            raise ValueError(f"the cases of run {run_id} changed since it started (rebuilt?): start a new run")
        return run_id, plan, refs, locked_refs
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
    plan = _plan(opts, cfg, refs, cases, initial, monitor, (locked, sorted(r.case_id for r in locked_refs)))
    plan_hash = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    run_id = run_id or new_run_id("training", salt=plan_hash)
    f = training_root(paths) / run_id / "run.json"
    if f.is_file():
        old = json.loads(f.read_text())
        if old["plan_hash"] != plan_hash:
            raise ValueError(f"training run {run_id} exists with another plan; use --resume to continue it, or "
                             "start a new run")
    return run_id, plan, refs, locked_refs


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
    n_fam = len(AgentFamily) if plan.get("family_slots") else 0
    kids = children(survivors, plan["population"] - len(survivors) - n_fam, r, plan["seed"],
                    plan["mutation_strength"], plan["crossover_share"], spec, seen, plan["max_redraws"])
    fam_kids: list[tuple[AgentGenome, dict]] = []
    if n_fam:
        fam_kids = family_slot_children(family_bests(run.dir, r, plan, spec), r, plan["seed"],
                                        plan["mutation_strength"], spec, seen | {g.genome_hash for g, _ in kids}
                                        | {s.genome_hash for s in survivors}, plan["max_redraws"])
    genomes = survivors + [g for g, _ in kids] + [g for g, _ in fam_kids]
    return genomes, [p["lineage"] for p in keep] + [rec for _, rec in kids] + [rec for _, rec in fam_kids], \
        ["survivor"] * len(survivors) + ["child"] * len(kids) + ["family_slot"] * len(fam_kids)


def family_bests(run_dir: Path, r: int, plan: dict, spec) -> list[AgentGenome]:
    """Each family's best fully scored agent in rounds 1..r-1 (composite, then genome hash); a family never scored
    (no engine) keeps its initial genome, else its default."""
    best: dict[str, tuple[tuple, AgentGenome]] = {}
    for i in range(1, r):
        rd = load_round(run_dir, i)
        pop = {AgentGenome.model_validate(p["genome"]).genome_hash: AgentGenome.model_validate(p["genome"])
               for p in rd["population"]}
        for row in rd["leaderboard"]["ranked"]:
            if row.get("composite_exact") is None or row["genome_hash"] not in pop:
                continue
            g = pop[row["genome_hash"]]
            key = (-row["composite_exact"], g.genome_hash)
            if g.family.value not in best or key < best[g.family.value][0]:
                best[g.family.value] = (key, g)
    initial = {AgentGenome.model_validate(x).family.value: AgentGenome.model_validate(x) for x in plan["initial"]}
    return [best[f.value][1] if f.value in best else initial.get(f.value) or default_genome(f, spec)
            for f in AgentFamily]


def _sites(refs: list[CaseRef]) -> list[str]:
    return sorted({str(r.manifest.site_code) for r in refs})


def physics_seen(run_dir: Path, rounds: list[int], sites: list[str]) -> set[tuple[str, str]]:
    """(site, physics key) of every genome fully scored in the given committed rounds."""
    out: set[tuple[str, str]] = set()
    for i in rounds:
        rd = load_round(run_dir, i)
        scored = {row["genome_hash"] for row in rd["leaderboard"]["ranked"]}
        for p in rd["population"]:
            g = AgentGenome.model_validate(p["genome"])
            if g.genome_hash in scored and g.family in ENGINE_FAMILIES:
                out.update((s, k) for s, k in physics_keys(g.genes, sites).items())
    return out


def new_physics(g: AgentGenome, sites: list[str], seen: set[tuple[str, str]]) -> bool:
    """An engine-family genome whose physics (at any plot of the cases) has not been fully scored in the run."""
    return g.family in ENGINE_FAMILIES and any((s, k) not in seen for s, k in physics_keys(g.genes, sites).items())


def case_work(refs: list[CaseRef], genomes: list[AgentGenome], ctx: EvalContext) -> list[tuple[list, int]]:
    """Per case the uncached genomes and the engine runs they need (one per physics not cached, ADR-070)."""
    out = []
    for ref in refs:
        todo = [g for g in genomes if not ctx.cache.has(ctx.key(g, ref))]
        site = str(ref.manifest.site_code)
        keys = {engine_physics(g.genes, site).key for g in todo if g.family in ENGINE_FAMILIES}
        out.append((todo, sum(ctx.cache.engine_cached_for(ref.case_hash, k) is not True for k in keys)))
    return out


def screen_round(run: _Run, plan: dict, r: int, genomes: list[AgentGenome], roles: list[str], refs: list[CaseRef],
                 ctx: EvalContext, workers: int, weights: ScoringWeights, seen: set[tuple[str, str]],
                 progress=None) -> tuple[list[AgentGenome], list[str], dict, pd.DataFrame | None]:
    """ADR-072: new physics children on the plan's sample first; those not beating the worst survivor there are
    screened out. Returns the genomes to score on all cases, the updated roles, the screen record and its scores."""
    sites = _sites(refs)
    ids = set(plan["screen_case_ids"])
    sample = [x for x in refs if x.case_id in ids]
    surv = [g for g, role in zip(genomes, roles, strict=True) if role == "survivor"]
    new = [g for g, role in zip(genomes, roles, strict=True) if role != "survivor" and new_physics(g, sites, seen)]
    rec: dict = {"sample_cases": len(sample), "candidates": len(new), "threshold": None, "agents": [],
                 "passed": 0, "screened_out": 0}
    if not new or not surv:
        return genomes, roles, rec, None
    res = evaluate_population(sample, surv + new, ctx, workers, progress)
    ranked = rank_agents(res.scores, weights, surv + new)
    thr = screen_threshold(ranked, {g.genome_hash for g in surv})
    by = {row["genome_hash"]: row for row in ranked}
    out_hashes = set()
    for g in new:
        c = by[g.genome_hash].get("composite_exact")
        ok = thr is None or (c is not None and c > thr)
        if not ok:
            out_hashes.add(g.genome_hash)
        rec["agents"].append({"agent_id": g.agent_id, "label": g.display_name, "family": g.family.value,
                              "sample_composite": c, "passed": ok})
    rec |= {"threshold": thr, "passed": len(new) - len(out_hashes), "screened_out": len(out_hashes),
            "eval": res.summary(), "survivors_on_sample": {g.agent_id: by[g.genome_hash].get("composite_exact")
                                                           for g in surv}}
    roles = ["screened_out" if g.genome_hash in out_hashes else role for g, role in zip(genomes, roles, strict=True)]
    return [g for g in genomes if g.genome_hash not in out_hashes], roles, rec, res.scores


def run_training(paths: LabPaths, cfg: LabConfig, opts: TrainOptions | None = None, *, workers: int = 1,
                 run_id: str | None = None, resume: bool = False, log: Callable[[str], None] = print,
                 progress: Callable[[int, int], None] | None = None, engine: EngineSpec | None = None,
                 estimate_only: bool = False) -> TrainingResult:
    """Run (or resume) a training run; see the module doc. ``engine`` overrides the stored engine on a resume
    (e.g. another binary path); ``estimate_only`` prints the estimate and returns before round 1."""
    t_start = time.time()
    opts = opts or TrainOptions.from_config(cfg)
    run_id, plan, refs, locked_refs = prepare(paths, cfg, opts, run_id, resume)
    if engine is not None:
        plan = plan | {"engine": engine.__dict__}
    run = _Run(paths, run_id, log)
    if resume and not estimate_only:
        (run.dir / "stop").unlink(missing_ok=True)  # a resume clears an earlier stop request (ADR-077)
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
    lib = build_library(paths, plan["case_set"], TrainingCache(paths.outputs / "cache"), workers,
                        exclude_seasons=plan.get("locked_seasons") or ())
    ctx = EvalContext(paths=paths, case_set=plan["case_set"], seed=plan["seed"], weights=weights,
                      config_hash=cfg.config_hash(), engine=engine_spec, library_file=lib,
                      scoring_version=plan.get("scoring_version"))  # a run keeps its scoring version (ADR-088)
    timings = load_timings(paths, ctx.cache.timings)
    done = committed_rounds(run.dir)
    spec = cfg.genome
    # estimate before starting (an estimate-only call writes nothing)
    emit = log if estimate_only else run.log
    if not done or estimate_only:
        first, _rec, _roles = (_population(run, plan, 1, spec) if not done else ([], [], []))
        if first:
            work = case_work(refs, first, ctx)
            est = estimate_pairs(work, timings, workers, len(first) * len(refs))
            physics = any(blk == "snowpack_physics" for f in ENGINE_FAMILIES for blk in spec.families[f])
            lo, hi = estimate_child_rounds(len(refs), plan["population"] - plan["survivors"], timings, workers,
                                           physics=physics, screen_cases=plan.get("screen_cases"))
            total_lo, total_hi = est.wall_s + (plan["rounds"] - 1) * lo, est.wall_s + (plan["rounds"] - 1) * hi
            msg = (f"estimate ({timings.source} timings, {workers} workers): round 1 {fmt_s(est.wall_s)} "
                   f"({est.uncached_pairs} of {est.pairs} agent-case pairs to run, {est.engine_runs} SNOWPACK engine "
                   f"runs)")
            if plan["rounds"] > 1:
                msg += (f"; rounds 2-{plan['rounds']} about {fmt_s(lo)}-{fmt_s(hi)} each (depends on the survivors' "
                        f"families and on how many children carry new SNOWPACK physics, each needing one engine run "
                        f"per case{', screened on ' + str(plan['screen_cases']) + ' cases first' if plan.get('screen_cases') else ''}); total about "
                        + (fmt_s(total_lo) if fmt_s(total_lo) == fmt_s(total_hi) else
                           f"{fmt_s(total_lo)}-{fmt_s(total_hi)}"))
            emit(msg)
            if est.snowpack_dominates:
                emit(f"warning: SNOWPACK-family agents are {est.snowpack_share:.0%} of the estimated cost of "
                        f"round 1 (about {fmt_s(timings.engine)} per engine run); each engine profile is cached "
                        "by its case and physics genes after its first run, so output-only children and reruns do not "
                        "pay it again; a child with new physics genes does (see --screen-cases)")
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
    events.emit(run.dir, "run_started", kind="training", run_id=run_id, rounds=plan["rounds"],
                population=plan["population"], survivors=plan["survivors"], cases=len(refs),
                monitor_season=plan["monitor_season"], resumed_rounds=len(done))
    gaps = [load_round(run.dir, r)["round"]["gap"] for r in done]
    yardstick = None  # ADR-087: standard SNOWPACK's season-plot means, read once
    rounds_info = [load_round(run.dir, r)["round"] for r in done]
    sites = _sites(refs)
    seen_phys = physics_seen(run.dir, done, sites)
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
            events.emit(run.dir, "round_started", round=r, agents=[
                {"agent_id": g.agent_id, "label": g.display_name, "family": g.family.value,
                 "genome_hash": g.genome_hash, "role": role, "operator": rec.get("operator"),
                 "parents": rec.get("parents"), "changed_genes": rec.get("changed_genes"),
                 "changed_vs_default": rec.get("changed_vs_default")}
                for g, rec, role in zip(genomes, lineage, roles, strict=True)])
            work = case_work(refs, genomes, ctx)
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

            screen, screen_df, all_genomes, all_roles = None, None, genomes, roles
            if plan.get("screen_cases") and r > 1:
                run.set_status(phase="screening")
                genomes, all_roles, screen, screen_df = screen_round(run, plan, r, all_genomes, roles, refs, ctx,
                                                                     workers, weights, seen_phys, _progress)
                if screen.get("eval"):
                    run.log(f"round {r} screen: {screen['candidates']} new-physics children on {screen['sample_cases']} "
                            f"cases, threshold {screen['threshold']:.4f} (worst survivor there); passed "
                            f"{screen['passed']}, screened out {screen['screened_out']}, "
                            f"{fmt_s(screen['eval']['wall_s'])}")
                if screen.get("eval"):
                    events.emit(run.dir, "screen", round=r, threshold=screen["threshold"],
                                sample_cases=screen["sample_cases"], agents=screen["agents"])
                run.set_status(phase="evaluating")
            by_hash = {g.genome_hash: g for g in genomes}

            def _feed(ref, rows, cached, _r=r, _by=by_hash) -> None:  # the Arena feed (ADR-078)
                for row in rows:
                    events.emit(run.dir, "case_scored", **events.score_event(
                        row, round=_r, cached=cached, pit_time=events.pit_time(ref.case_id),
                        key=ctx.key(_by[row["genome_hash"]], ref)))

            res = evaluate_population(refs, genomes, ctx, workers, _progress,
                                      on_case=_feed if events.enabled() else None,
                                      events=(str(run.dir), r) if events.enabled() else None)
            timings.update(res.worker_stats)
            save_timings(timings, ctx.cache.timings)
            n_new_phys = sum(new_physics(g, sites, seen_phys) for g in genomes)
            seen_phys |= {(s, k) for g in genomes if g.family in ENGINE_FAMILIES
                          for s, k in physics_keys(g.genes, sites).items()}
            df = res.scores
            if plan.get("selection") == "consistent" and yardstick is None:
                yardstick = _yardstick(run.dir, df)
            ranked = rank_agents(df, weights, genomes, plan.get("selection", "composite"), yardstick,
                                 float(plan.get("drift_penalty") or 0.0), spec)
            top = ranked[: plan["survivors"]]
            g_rec = gap_record(df, plan["monitor_season"], weights, ranked[:2], gaps, plan["gap_flag_rounds"],
                               plan["gap_tolerance"])
            gaps.append(g_rec)
            best = ranked[0]
            info = {"round": r, "run_id": run_id, "agents": [g.agent_id for g in all_genomes], "roles": all_roles,
                    "best": {k: best.get(k) for k in ("agent_id", "label", "family", "genome_hash", "composite",
                                                      "composite_exact", "case_composite", "robustness")},
                    "survivors_next": [t["agent_id"] for t in top], "eval": res.summary(),
                    "estimate_s": round(est.wall_s, 1), "wall_s": round(time.time() - t0, 1), "gap": g_rec,
                    "committed_at": datetime.now(UTC).isoformat(), "label": LAB_DISCLAIMER}
            if screen is not None:
                info["screen"] = screen
            info["new_physics_scored"] = n_new_phys
            locked_df = None
            if locked_refs:  # ADR-083: scored after selection, so these scores can never steer it
                run.set_status(phase="testing on locked winters")
                tested = genomes if r == 1 else [by_hash[t["genome_hash"]] for t in top]
                lres = evaluate_population(locked_refs, tested, ctx, workers, _progress)
                locked_df = lres.scores
                info["locked_test"] = locked_record(locked_df, weights, tested, plan["locked_seasons"], lres)
                run.log(f"round {r} locked test ({', '.join(plan['locked_seasons'])}, {len(locked_refs)} cases): "
                        + ", ".join(f"{a['label']} {a['composite']:.4f}" for a in info["locked_test"]["agents"]
                                    if a.get("composite") is not None))
                info["wall_s"] = round(time.time() - t0, 1)  # genomes scored on all cases with physics new to the run
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
            if screen_df is not None:
                screen_df.to_parquet(tmp / "screen_scores.parquet", index=False)
            if locked_df is not None:
                locked_df.to_parquet(tmp / "locked_scores.parquet", index=False)
            (tmp / "population.json").write_text(json.dumps(
                [{"genome": g.model_dump(mode="json"), "lineage": rec, "role": role}
                 for g, rec, role in zip(all_genomes, lineage, all_roles, strict=True)], indent=1))
            (tmp / "leaderboard.json").write_text(json.dumps(lb, indent=1, default=str))
            (tmp / "manifest.json").write_text(manifest.model_dump_json(indent=1))
            (tmp / "round.json").write_text(json.dumps(info, indent=1, default=str))
            os.replace(tmp, round_dir(run.dir, r))
            _record(paths, manifest)
            freed = SegmentStore(ctx.cache.segments_root).clear()  # ADR-071: states live for one round
            if freed:
                run.log(f"round {r}: restart store emptied ({freed / 1e6:.0f} MB)")
            rounds_info.append(info)
            gap_txt = f"gap {g_rec['gap']:+.3f} on {g_rec['monitor_season']}" if g_rec["gap"] is not None \
                else "gap n/a"
            seg = res.summary()
            seg_txt = (f", restart segments reused {seg['segments_reused']} of "
                       f"{seg['segments_reused'] + seg['segments_run']}" if seg["segments_run"] + seg["segments_reused"]
                       else "")
            run.log(f"round {r} committed: best {best['label']} composite {best['composite']:.4f}; "
                    f"next survivors {', '.join(t['label'] for t in top)}; {gap_txt}; cache hit rate "
                    f"{res.hit_rate:.0%}, {res.engine_runs} engine runs{seg_txt}, {fmt_s(time.time() - t0)}")
            events.emit(run.dir, "round_committed", round=r, best=info["best"], survivors_next=info["survivors_next"],
                        gap=g_rec, wall_s=info["wall_s"], ranked=[
                            {k: x.get(k) for k in ("rank", "agent_id", "label", "family", "genome_hash", "composite")}
                            for x in ranked])
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
    events.emit(run.dir, "run_finished", kind="training", run_id=run_id, winner=summary.get("winner", {}).get("label"))
    return TrainingResult(run_id, run.dir, rounds_info, summary, resumed)


def locked_record(df: pd.DataFrame, weights, tested: list[AgentGenome], seasons: list[str], res) -> dict:
    """A round's locked-test record: each tested agent's composite and components on the locked seasons."""
    board = {x["agent_id"]: x for x in build_leaderboard(df, weights)["overall"]}
    agents = []
    for g in tested:
        x = board.get(g.agent_id, {})
        agents.append({"agent_id": g.agent_id, "label": g.display_name, "family": g.family.value,
                       "genome_hash": g.genome_hash, "default": g.origin == "default"}
                      | {k: x.get(k) for k in ("composite", "case_composite", "snow_depth", "layer_structure",
                                               "critical_layers", "uncertainty", "robustness", "scored",
                                               "failures", "depth_mae_m")})
    return {"seasons": list(seasons), "cases": int(df["case_id"].nunique()) if len(df) else 0, "agents": agents,
            "eval": res.summary(), "note": "scored after selection: never used to choose agents"}


def finish(run: _Run, plan: dict, cfg: LabConfig, refs: list[CaseRef], rounds_info: list[dict], created: datetime,
           wall_s: float, resumed: int) -> dict:
    last = load_round(run.dir, plan["rounds"])
    winner_row = last["leaderboard"]["ranked"][0]
    pop = {AgentGenome.model_validate(p["genome"]).genome_hash: p for p in last["population"]}
    win = pop[winner_row["genome_hash"]]
    evals = [i["eval"] for i in rounds_info] + [i["screen"]["eval"] for i in rounds_info
                                                if i.get("screen", {}).get("eval")]
    pairs = sum(e["pairs"] for e in evals)
    hits = sum(e["cache_hits"] for e in evals)
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
                  "engine_runs": sum(e["engine_runs"] for e in evals),
                  "engine_cache_hits": sum(e.get("engine_cache_hits", 0) for e in evals),
                  "engine_s": round(sum(e.get("engine_s", 0.0) or 0.0 for e in evals), 1),
                  "segments_run": sum(e.get("segments_run", 0) for e in evals),
                  "segments_reused": sum(e.get("segments_reused", 0) for e in evals)},
        "screen": {"rounds": [i["round"] for i in rounds_info if i.get("screen")],
                   "candidates": sum(i.get("screen", {}).get("candidates", 0) for i in rounds_info),
                   "screened_out": sum(i.get("screen", {}).get("screened_out", 0) for i in rounds_info)}
        if plan.get("screen_cases") else None,
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
