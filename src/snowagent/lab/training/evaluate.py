"""Score a population of genomes on a list of cases through the training cache (ADR-066).

For every (genome, case) pair the prediction key is looked up first; only cases with at least one uncached pair are
sent to a worker process, and the worker runs only the uncached genomes (the engine profile comes from the engine
cache when present). A cached pair scored under another scoring identity (scoring version, scoring code or weights;
ADR-074) is re-scored by the worker from its stored prediction: no agent or engine runs for it. The worker writes each finished pair to the cache at once, so a killed round resumes at the
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
from snowagent.lab.competition.library import SeasonEntry, library_entry, library_for
from snowagent.lab.competition.runner import (
    RUNNER_VERSION,
    EngineSpec,
    make_backend,
    predict_and_score,
    score_rows,
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
    scoring_identity,
    sha,
)


def _pool_map(fn: Callable, items: list, workers: int, progress: Callable[[int, int], None] | None):
    """``fn`` over ``items`` in ``workers`` processes, yielding results as they finish. When the consumer stops early
    (a stop request raised from ``progress``, or any error), the cases not yet started are cancelled and only those
    already running are waited for: a stop takes at most one case per worker, not the rest of the round. Kept here,
    outside the prediction code, so the cache keys do not change (``CODE_EXCLUDE``)."""
    from concurrent.futures import ProcessPoolExecutor, as_completed

    done = 0
    if workers <= 1:
        for it in items:
            yield fn(it)
            done += 1
            if progress:
                progress(done, len(items))
        return
    ex = ProcessPoolExecutor(max_workers=workers)
    try:
        futs = [ex.submit(fn, it) for it in items]
        for f in as_completed(futs):
            yield f.result()
            done += 1
            if progress:
                progress(done, len(items))
    finally:
        ex.shutdown(wait=True, cancel_futures=True)


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
    scoring_id: str = field(init=False)  # what the cached rows must have been scored under (ADR-074)

    def __post_init__(self) -> None:
        self.cache = TrainingCache(self.paths.outputs / "cache")
        self.engine_id = engine_identity(self.engine.kind, self.engine.binary)
        # the prediction context: no scoring version, scoring code or weights (those are the scoring identity)
        base = {"code": code_hash(), "config": self.config_hash, "runner": RUNNER_VERSION, "seed": self.seed}
        self.scoring_id = scoring_identity(self.weights.model_dump())
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


def rescore_cached(case_dir: Path, m, cache: TrainingCache, keys: list[str], weights: ScoringWeights,
                   scoring_id: str) -> int:
    """Re-score cached pairs of one case from their stored predictions under the current scoring (ADR-074); each
    entry is rewritten with its new row and scoring identity. The truth gate is ``score_rows``'s. Returns the count."""
    entries = [(k, cache.get(k)) for k in keys]
    entries = [(k, e) for k, e in entries if e is not None]
    pairs = [(dict(e["row"]), e.get("prediction")) for _k, e in entries]
    score_rows(case_dir, m, pairs, weights)
    for (k, e), (row, _pred) in zip(entries, pairs, strict=True):
        cache.put(k, e | {"row": row, "scoring": scoring_id})
    return len(entries)


def evaluate_case(task: dict) -> dict:
    """Run the uncached genomes of one case (worker process); every finished pair is cached at once. Cached pairs
    scored under another scoring identity (``rescore_keys``) are re-scored first, without running anything."""
    t0 = time.perf_counter()
    case_dir = Path(task["case_dir"])
    cache = TrainingCache(Path(task["cache_root"]))
    m = read_manifest(case_dir)
    weights = ScoringWeights.model_validate(task["weights"])
    rescored = rescore_cached(case_dir, m, cache, task.get("rescore_keys") or [], weights, task["scoring_id"])
    if not task["genomes"]:
        return {"case_id": m.case_id, "pairs": 0, "rescored": rescored, "engine_hit": None, "engine_runs": 0,
                "engine_cache_hits": 0, "engine_s": None, "engine_runs_s": [], "segments_run": 0,
                "segments_reused": 0, "load_s": round(time.perf_counter() - t0, 3), "agent_s": {},
                "wall_s": round(time.perf_counter() - t0, 3)}
    case = load_visible_case(case_dir)
    genomes = [AgentGenome.model_validate(g) for g in task["genomes"]]
    if task.get("events_dir"):  # the Arena's feed (ADR-078); not part of any key
        from snowagent.lab import events

        events.emit(task["events_dir"], "case_started", case_id=m.case_id, agents=len(genomes),
                    round=task.get("events_round"), site_code=str(getattr(m.site_code, "value", m.site_code)),
                    case_type=m.case_type.value)
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
                                     "genome_hash": g.genome_hash, "scoring": task["scoring_id"]})

    rows, _preds, _truth = predict_and_score(case_dir, m, case, genomes, library, backend, task["seed"], weights,
                                             on_agent=store)
    runs = [round(s, 3) for s in disk.misses] if disk is not None else []
    for r in rows:
        # an agent's own time, without the engine runs it triggered (ADR-070: one per new physics)
        s = max(0.0, float(r.get("runtime_s") or 0.0) - float(r.get("engine_s") or 0.0))
        agent_s.setdefault(r["family"], []).append(s)
    seg = getattr(disk.inner, "segment_stats", None) if disk is not None else None
    return {"case_id": m.case_id, "pairs": len(genomes), "rescored": rescored,
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
    rescored: int = 0  # cached predictions re-scored under the current scoring (counted in hits; ADR-074)

    @property
    def hit_rate(self) -> float:
        return self.hits / self.pairs if self.pairs else 0.0

    def summary(self) -> dict:
        return {"pairs": self.pairs, "cache_hits": self.hits, "cache_misses": self.misses,
                "cache_hit_rate": round(self.hit_rate, 4), "rescored": self.rescored, "cases_run": self.cases_run,
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
                        progress: Callable[[int, int], None] | None = None,
                        on_case: Callable[[CaseRef, list[dict], bool], None] | None = None,
                        events: tuple[str, int] | None = None) -> EvalResult:
    """Every genome on every case through the cache (see the module doc); one row per (case, genome).
    ``on_case(ref, rows, cached)`` is called in this process with each case's rows as soon as they are known
    (every pair cached: before any work; otherwise when its worker returns); ``events`` = (run directory, round)
    makes the workers append ``case_started`` events. Neither changes a result or a key (the Arena feed, ADR-078)."""
    t0 = time.time()
    keys = {(g.genome_hash, r.case_hash): ctx.key(g, r) for g in genomes for r in refs}
    tasks, hits = [], 0
    for r in refs:
        todo, rescore = [], []
        for g in genomes:
            k = keys[(g.genome_hash, r.case_hash)]
            e = ctx.cache.get(k)
            if e is None:
                todo.append(g)
            elif e.get("scoring") != ctx.scoring_id:
                rescore.append(k)
        hits += len(genomes) - len(todo)
        if todo or rescore:
            tasks.append({"case_dir": str(r.case_dir), "case_hash": r.case_hash, "cache_root": str(ctx.cache.root),
                          "genomes": [g.model_dump(mode="json") for g in todo], "rescore_keys": rescore,
                          "scoring_id": ctx.scoring_id,
                          "keys": [keys[(g.genome_hash, r.case_hash)] for g in todo], "seed": ctx.seed,
                          "weights": ctx.weights.model_dump(), "engine": ctx.engine.__dict__,
                          "engine_id": ctx.engine_id, "segments_root": str(ctx.cache.segments_root),
                          "segments_context": sha({"code": code_hash(), "files": engine_files_hash()}),
                          "library_file": str(ctx.library_file) if ctx.library_file else None})
    def case_rows(r: CaseRef) -> list[dict]:
        out = []
        for g in genomes:
            e = ctx.cache.get(keys[(g.genome_hash, r.case_hash)])
            if e is None:
                raise RuntimeError(f"no cached result for {g.agent_id} on {r.case_id} after the round ran")
            row = dict(e["row"])
            row |= {"agent_id": g.agent_id, "label": g.display_name, "family": g.family.value,
                    "genome_hash": g.genome_hash}
            out.append(row)
        return out

    def notify(r: CaseRef, cached: bool) -> None:
        if on_case is not None:
            try:
                on_case(r, case_rows(r), cached)
            except Exception:  # noqa: BLE001, S110 - a feed callback never changes or stops the round
                pass

    if events:
        for t in tasks:
            t |= {"events_dir": events[0], "events_round": events[1]}
    queued = {t["case_dir"] for t in tasks}
    for r in refs:
        if str(r.case_dir) not in queued:
            notify(r, True)
    by_id = {r.case_id: r for r in refs}
    stats = []
    for st in _pool_map(evaluate_case, interleave_groups(tasks, refs), workers, progress):
        stats.append(st)
        if st.get("case_id") in by_id:
            notify(by_id[st["case_id"]], not st.get("pairs"))
    rows = [row for r in refs for row in case_rows(r)]
    n = len(keys)
    return EvalResult(scores=pd.DataFrame(rows), pairs=n, hits=hits, misses=n - hits,
                      cases_run=sum(1 for s in stats if s.get("pairs")), rescored=sum(s.get("rescored", 0) for s in stats),
                      engine_runs=sum(s.get("engine_runs", 0) for s in stats),
                      engine_hits=sum(s.get("engine_cache_hits", 0) for s in stats), wall_s=time.time() - t0,
                      worker_stats=stats, segments_run=sum(s.get("segments_run", 0) for s in stats),
                      segments_reused=sum(s.get("segments_reused", 0) for s in stats))
