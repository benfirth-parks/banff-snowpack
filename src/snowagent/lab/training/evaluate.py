"""Score a population of genomes on a list of cases through the training cache (ADR-066).

For every (genome, case) pair the prediction key is looked up first; only cases with at least one uncached pair are
sent to a worker process, and the worker runs only the uncached genomes (the engine profile comes from the engine
cache when present). The worker writes each finished pair to the cache at once, so a killed round resumes at the
first unfinished pair. The rows of the round are then read back from the cache for every pair, cached or new, with
the genome's current label. Truth is read only by ``predict_and_score`` for cases the caller selected (the loop
selects training cases only; ADR-068).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from snowagent.lab.agents.segments import SegmentStore
from snowagent.lab.benchmark.loader import case_dirs, load_visible_case, read_manifest
from snowagent.lab.competition import scoring
from snowagent.lab.competition.library import SeasonEntry, library_entry, library_for
from snowagent.lab.competition.runner import (
    RUNNER_VERSION,
    EngineSpec,
    _pool_map,
    make_backend,
    predict_and_score,
)
from snowagent.lab.schemas.benchmark import CaseManifest
from snowagent.lab.schemas.genome import AgentFamily, AgentGenome
from snowagent.lab.schemas.run import ScoringWeights
from snowagent.lab.storage.paths import LabPaths
from snowagent.lab.storage.provenance import sha256_file
from snowagent.lab.training.cache import (
    DiskEngineCache,
    TrainingCache,
    cacheable_engine,
    code_hash,
    context_hashes,
    engine_files_hash,
    engine_identity,
    prediction_key,
    sha,
)


@dataclass(frozen=True)
class CaseRef:
    case_dir: Path
    manifest: CaseManifest
    case_hash: str  # sha256 of manifest.json (which holds the hashes of the visible and hidden files)

    @property
    def case_id(self) -> str:
        return self.manifest.case_id


def case_refs(cases: list[tuple[Path, CaseManifest]]) -> list[CaseRef]:
    return [CaseRef(d, m, sha256_file(d / "manifest.json")) for d, m in cases]


@dataclass
class EvalContext:
    """Everything a prediction depends on besides the genome and the case."""

    paths: LabPaths
    case_set: str
    seed: int
    weights: ScoringWeights
    config_hash: str
    engine: EngineSpec
    library_file: Path | None = None  # analogue library (training cases of the case set), harness side
    cache: TrainingCache = field(init=False)
    contexts: dict[AgentFamily, str] = field(init=False)
    engine_id: str = field(init=False)

    def __post_init__(self) -> None:
        self.cache = TrainingCache(self.paths.outputs / "cache")
        self.engine_id = engine_identity(self.engine.kind, self.engine.binary)
        base = {"code": code_hash(), "config": self.config_hash, "scoring": scoring.SCORING_VERSION,
                "runner": RUNNER_VERSION, "seed": self.seed, "weights": self.weights.model_dump()}
        engine = {"identity": self.engine_id, "plan": self.engine.plan(), "files": engine_files_hash()}
        lib = sha256_file(self.library_file) if self.library_file and self.library_file.is_file() else None
        self.contexts = context_hashes(base, engine, lib)

    def key(self, genome: AgentGenome, ref: CaseRef) -> str:
        return prediction_key(genome.genome_hash, ref.case_hash, self.contexts[genome.family])


def build_library(paths: LabPaths, case_set: str, cache: TrainingCache, workers: int = 1) -> Path:
    """The case set's analogue library (``truth.library_ok`` cases only: training, never holdout or sealed), stored
    once per case set content in the cache."""
    dirs = case_dirs(paths, case_set)
    tag = sha([sha256_file(d / "manifest.json") for d in dirs])
    f = cache.root / "library" / f"{case_set}-{tag[:16]}.json"
    if f.is_file():
        return f
    entries = [e for e in _pool_map(library_entry, dirs, workers, None) if e is not None]
    entries.sort(key=lambda e: (e.season, e.entry.digest.site_code, e.entry.digest.hs_now_m or 0))
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps([e.model_dump(mode="json") for e in entries]))
    tmp.replace(f)
    return f


# --------------------------------------------------------------------------------------------- worker


def evaluate_case(task: dict) -> dict:
    """Run the uncached genomes of one case (worker process); every finished pair is cached at once."""
    t0 = time.perf_counter()
    case_dir = Path(task["case_dir"])
    cache = TrainingCache(Path(task["cache_root"]))
    m = read_manifest(case_dir)
    case = load_visible_case(case_dir)
    genomes = [AgentGenome.model_validate(g) for g in task["genomes"]]
    keys = dict(zip([g.agent_id for g in genomes], task["keys"], strict=True))
    library = None
    if task.get("library_file") and any(g.family == AgentFamily.analogue for g in genomes):
        entries = [SeasonEntry.model_validate(e) for e in json.loads(Path(task["library_file"]).read_text())]
        library = library_for(entries, m.season)
    spec = EngineSpec(**task["engine"])
    segments = None
    if task.get("segments_root") and spec.segments:
        segments = SegmentStore(Path(task["segments_root"]), task.get("segments_context", ""))
    backend, _prov = make_backend(spec, m, case.site.plot_id if case.site else "", segments)
    disk = None
    if cacheable_engine(backend.inner):
        disk = backend.inner = DiskEngineCache(backend.inner, cache, task["engine_id"], task["case_hash"])
    load_s = time.perf_counter() - t0
    agent_s: dict[str, list[float]] = {}

    def store(g: AgentGenome, row: dict, pred: dict | None) -> None:
        cache.put(keys[g.agent_id], {"row": row, "prediction": pred, "case_hash": task["case_hash"],
                                     "genome_hash": g.genome_hash})

    weights = ScoringWeights.model_validate(task["weights"])
    rows, _preds, _truth = predict_and_score(case_dir, m, case, genomes, library, backend, task["seed"], weights,
                                             on_agent=store)
    runs = [round(s, 3) for s in disk.misses] if disk is not None else []
    for r in rows:
        # an agent's own time, without the engine runs it triggered (ADR-070: one per new physics)
        s = max(0.0, float(r.get("runtime_s") or 0.0) - float(r.get("engine_s") or 0.0))
        agent_s.setdefault(r["family"], []).append(s)
    seg = getattr(disk.inner, "segment_stats", None) if disk is not None else None
    return {"case_id": m.case_id, "pairs": len(genomes),
            "engine_hit": None if disk is None or disk.hit is None else not runs,
            "engine_runs": len(runs), "engine_cache_hits": disk.hits if disk is not None else 0,
            "engine_s": round(sum(runs), 3) if runs else None, "engine_runs_s": runs,
            "segments_run": seg["run"] if seg else 0, "segments_reused": seg["reused"] if seg else 0,
            "load_s": round(load_s, 3), "agent_s": agent_s, "wall_s": round(time.perf_counter() - t0, 3)}


# --------------------------------------------------------------------------------------------- population


@dataclass
class EvalResult:
    scores: pd.DataFrame
    pairs: int
    hits: int
    misses: int
    cases_run: int
    engine_runs: int
    engine_hits: int
    wall_s: float
    worker_stats: list[dict]
    segments_run: int = 0
    segments_reused: int = 0

    @property
    def hit_rate(self) -> float:
        return self.hits / self.pairs if self.pairs else 0.0

    def summary(self) -> dict:
        return {"pairs": self.pairs, "cache_hits": self.hits, "cache_misses": self.misses,
                "cache_hit_rate": round(self.hit_rate, 4), "cases_run": self.cases_run,
                "engine_runs": self.engine_runs, "engine_cache_hits": self.engine_hits,
                "engine_s": round(sum(s.get("engine_s") or 0.0 for s in self.worker_stats), 1),
                "segments_run": self.segments_run, "segments_reused": self.segments_reused,
                "segment_reuse_rate": round(self.segments_reused / (self.segments_run + self.segments_reused), 4)
                if self.segments_run + self.segments_reused else None, "wall_s": round(self.wall_s, 1)}


def interleave_groups(tasks: list[dict], refs: list[CaseRef]) -> list[dict]:
    """Tasks round-robin over (plot, season) groups, each group in case order: parallel workers start on different
    groups, and a group's later cases find the restart segments its earlier cases stored (ADR-071). Results do not
    depend on the order (each pair is cached and read back by key)."""
    group = {str(r.case_dir): (str(r.manifest.site_code), r.manifest.season) for r in refs}
    by: dict[tuple, list[dict]] = {}
    for t in tasks:
        by.setdefault(group.get(t["case_dir"], ("", "")), []).append(t)
    queues = [by[k] for k in sorted(by)]
    out: list[dict] = []
    while any(queues):
        for q in queues:
            if q:
                out.append(q.pop(0))
    return out


def evaluate_population(refs: list[CaseRef], genomes: list[AgentGenome], ctx: EvalContext, workers: int = 1,
                        progress: Callable[[int, int], None] | None = None) -> EvalResult:
    """Every genome on every case through the cache (see the module doc); one row per (case, genome)."""
    t0 = time.time()
    keys = {(g.genome_hash, r.case_hash): ctx.key(g, r) for g in genomes for r in refs}
    tasks, hits = [], 0
    for r in refs:
        todo = [g for g in genomes if not ctx.cache.has(keys[(g.genome_hash, r.case_hash)])]
        hits += len(genomes) - len(todo)
        if todo:
            tasks.append({"case_dir": str(r.case_dir), "case_hash": r.case_hash, "cache_root": str(ctx.cache.root),
                          "genomes": [g.model_dump(mode="json") for g in todo],
                          "keys": [keys[(g.genome_hash, r.case_hash)] for g in todo], "seed": ctx.seed,
                          "weights": ctx.weights.model_dump(), "engine": ctx.engine.__dict__,
                          "engine_id": ctx.engine_id, "segments_root": str(ctx.cache.segments_root),
                          "segments_context": sha({"code": code_hash(), "files": engine_files_hash()}),
                          "library_file": str(ctx.library_file) if ctx.library_file else None})
    stats = list(_pool_map(evaluate_case, interleave_groups(tasks, refs), workers, progress))
    rows = []
    for r in refs:
        for g in genomes:
            e = ctx.cache.get(keys[(g.genome_hash, r.case_hash)])
            if e is None:
                raise RuntimeError(f"no cached result for {g.agent_id} on {r.case_id} after the round ran")
            row = dict(e["row"])
            row |= {"agent_id": g.agent_id, "label": g.display_name, "family": g.family.value,
                    "genome_hash": g.genome_hash}
            rows.append(row)
    n = len(keys)
    return EvalResult(scores=pd.DataFrame(rows), pairs=n, hits=hits, misses=n - hits, cases_run=len(tasks),
                      engine_runs=sum(s.get("engine_runs", 0) for s in stats),
                      engine_hits=sum(s.get("engine_cache_hits", 0) for s in stats), wall_s=time.time() - t0,
                      worker_stats=stats, segments_run=sum(s.get("segments_run", 0) for s in stats),
                      segments_reused=sum(s.get("segments_reused", 0) for s in stats))
