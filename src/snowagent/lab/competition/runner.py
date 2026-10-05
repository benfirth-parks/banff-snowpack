"""Competition runner (ADR-065): every agent predicts every case, each prediction is scored, a leaderboard is made.

Layout under ``<data root>/outputs/competitions/<run_id>/``:

- ``run.json``: the plan (case ids and case-set hash, genome hashes, seed, scoring weights and version, engine
  mode, config hash). Resuming a run with another plan is refused.
- ``genomes/<agent_id>.json``; ``library.json`` (harness side: analogue entries with their seasons).
- ``cases/<case_id>.json``: per agent the stamped prediction, status, runtime and scores; written atomically when
  the case is finished, so an interrupted run resumes at the first unfinished case.
- ``scores.parquet`` (one row per case and agent) and ``leaderboard.json`` (overall and by forecast source, plot
  and case type).

Cases run in parallel (one process per case; the agents of a case share one engine profile). Seeds per case and
agent come from the run seed, the case key and the genome hash, so a rerun reproduces every prediction. The run
manifest goes to the run registry when the run finishes. ``heldout_gap`` is the anti-memorisation hook of the
evolution loop: train-vs-held-out composite of each agent for a named season.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
import traceback
from collections.abc import Callable, Iterable
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from snowagent.lab.agents import AgentUnavailable, make_agent
from snowagent.lab.agents.common import insufficient, stamp_prediction
from snowagent.lab.agents.snowpack import (
    EngineResult,
    FakeEngine,
    FixedEngineResult,
    UnavailableEngine,
    VisiblePackageEngine,
)
from snowagent.lab.benchmark.loader import case_dirs, load_visible_case, read_manifest
from snowagent.lab.competition import scoring
from snowagent.lab.competition.incumbent import site_run_check
from snowagent.lab.competition.library import SeasonEntry, library_entry, library_for
from snowagent.lab.competition.truth import scorable, scoring_truth
from snowagent.lab.schemas.benchmark import CaseManifest, VisibleBenchmarkCase
from snowagent.lab.schemas.genome import AgentFamily, AgentGenome
from snowagent.lab.schemas.run import RunKind, RunManifest, ScoringWeights
from snowagent.lab.storage.paths import LabPaths
from snowagent.lab.storage.provenance import git_commit, new_run_id, sha256_file, software_version
from snowagent.lab.storage.registry import RunRegistry

RUNNER_VERSION = "lab-competition-1"
GROUPS = {"forecast_source": "by_forecast_source", "site_code": "by_site", "case_type": "by_case_type"}


# --------------------------------------------------------------------------------------------- engine backends


@dataclass
class EngineSpec:
    """How SNOWPACK profiles are produced: ``auto`` (reuse a qualifying site run, else run the binary from the
    visible package), ``none`` (no engine: SNOWPACK skipped, hybrid falls back), ``fake`` (tests, dry runs)."""

    kind: str = "auto"
    binary: str | None = None
    work_dir: str | None = None
    source_root: str | None = None  # the checkout whose web/data site runs may be reused (read only)

    def plan(self) -> dict:
        return {"kind": self.kind, "site_run_reuse": bool(self.source_root) and self.kind == "auto"}


class CachingBackend:
    """One engine profile per case for every agent of the case (SNOWPACK and hybrid)."""

    def __init__(self, inner) -> None:
        self.inner = inner
        self.result: EngineResult | None = None
        self.error: BaseException | None = None
        self.runtime_s: float | None = None

    def simulate(self, case: VisibleBenchmarkCase) -> EngineResult:
        if self.result is None and self.error is None:
            t0 = time.perf_counter()
            try:
                self.result = self.inner.simulate(case)
            except Exception as exc:
                self.error = exc
            self.runtime_s = round(time.perf_counter() - t0, 3)
        if self.error is not None:
            raise self.error
        return self.result


def make_backend(spec: EngineSpec, manifest: CaseManifest, plot_id: str) -> tuple[CachingBackend, dict]:
    prov: dict = {"kind": spec.kind}
    if spec.kind == "fake":
        inner = FakeEngine()
    elif spec.kind == "none":
        inner = UnavailableEngine("engine disabled for this run (--engine none)")
    else:
        inner = None
        if spec.source_root:
            res, check = site_run_check(Path(spec.source_root), manifest, plot_id)
            prov["site_run"] = check
            if res is not None:
                inner = FixedEngineResult(res)
                prov["source"] = "site_run_reuse"
        if inner is None:
            inner = VisiblePackageEngine(work_dir=Path(spec.work_dir) if spec.work_dir else None, binary=spec.binary)
            prov["source"] = "engine_run"
    return CachingBackend(inner), prov


# --------------------------------------------------------------------------------------------- one case


def case_seed(seed: int, case_key: str, genome_hash: str) -> int:
    return int(hashlib.sha256(f"{seed}:{case_key}:{genome_hash}".encode()).hexdigest()[:8], 16)


def _row_base(m: CaseManifest, g: AgentGenome) -> dict:
    return {"case_id": m.case_id, "agent_id": g.agent_id, "family": g.family.value, "label": g.label,
            "genome_hash": g.genome_hash, "site_code": str(getattr(m.site_code, "value", m.site_code)), "season": m.season, "split": m.split.value, "case_type": m.case_type.value,
            "forecast_source": m.forecast_source.value if m.forecast_source else None,
            "target_scope": m.target_scope.value, "horizon_h": m.horizon_hours}


def run_case(task: dict) -> list[dict]:
    """Every agent on one case, then scoring; writes ``cases/<case_id>.json`` and returns the score rows."""
    case_dir = Path(task["case_dir"])
    m = read_manifest(case_dir)
    case = load_visible_case(case_dir)
    genomes = [AgentGenome.model_validate(g) for g in task["genomes"]]
    entries = [SeasonEntry.model_validate(e) for e in task.get("library") or []]
    library = library_for(entries, m.season) if entries else None
    spec = EngineSpec(**task["engine"])
    backend, prov = make_backend(spec, m, case.site.plot_id if case.site else "")
    weights = ScoringWeights.model_validate(task["weights"])
    rows, preds = [], {}
    for g in genomes:
        row = _row_base(m, g)
        t0 = time.perf_counter()
        try:
            agent = make_agent(g, library if g.family == AgentFamily.analogue else None, backend)
            pred = agent.predict(case, case_seed(task["seed"], case.case_key, g.genome_hash))
            row["status"] = pred.status
            row["reason"] = pred.insufficient_data_reason
        except AgentUnavailable as exc:
            row |= {"status": "skipped", "reason": str(exc)[:300], "runtime_s": round(time.perf_counter() - t0, 3)}
            rows.append(row)
            continue
        except Exception as exc:  # an agent bug is a failed case for that agent, not a failed competition
            pred = insufficient(case, g, f"agent error: {type(exc).__name__}: {exc}"[:300],
                                {"traceback": traceback.format_exc()[-1500:]})
            row |= {"status": "error", "reason": pred.insufficient_data_reason}
        row["runtime_s"] = round(time.perf_counter() - t0, 3)
        pred = stamp_prediction(pred, m)
        preds[g.agent_id] = pred.model_dump(mode="json")
        meta = pred.model_metadata
        for k in ("snowpack_version", "engine_source", "profile_lag_h", "structure_from"):
            if k in meta:
                row[k] = meta[k]
        rows.append(row)
    truth_used = False
    if scorable(m):
        truth = scoring_truth(case_dir, m).truth_profile
        truth_used = True
        for row in rows:
            if row["status"] == "skipped":
                continue
            from snowagent.lab.schemas.prediction import SnowpackPrediction

            s = scoring.score_case(SnowpackPrediction.model_validate(preds[row["agent_id"]]), truth, m.target_scope)
            s.pop("status", None)
            row |= s
            row["composite"] = scoring.case_composite(s, weights)
    out = {"case_id": m.case_id, "case_key": m.case_key, "season": m.season, "split": m.split.value,
           "truth_used": truth_used, "target_profile_id": m.target_profile_id if truth_used else None,
           "visible_profile_ids": m.visible_profile_ids, "engine": prov | {"runtime_s": backend.runtime_s},
           "rows": rows, "predictions": preds}
    dst = Path(task["run_dir"]) / "cases" / f"{m.case_id}.json"
    tmp = dst.with_suffix(".tmp")
    tmp.write_text(json.dumps(out, default=_json_default))
    os.replace(tmp, dst)
    return rows


def _json_default(o):
    if isinstance(o, np.generic):
        return o.item()
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(f"not JSON serialisable: {type(o)}")


# --------------------------------------------------------------------------------------------- leaderboard


def _f(x) -> float | None:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), 4)


def leaderboard(df: pd.DataFrame, weights: ScoringWeights) -> list[dict]:
    """One row per agent, best composite first. Components are means over scored cases (NaN where a case cannot
    verify one); robustness and the composite are computed on the leaderboard (``scoring``)."""
    out = []
    if df.empty:
        return out
    for aid, d in df.groupby("agent_id", sort=False):
        scored = d[d["status"] != "skipped"]
        comp = scored["composite"].astype(float) if "composite" in scored else pd.Series(dtype=float)
        failures = int((scored["status"] != "ok").sum())
        mean_c = float(comp.mean()) if comp.notna().any() else math.nan
        rob = scoring.robustness(comp.tolist(), failures) if len(comp) else math.nan
        row = {"agent_id": aid, "family": d["family"].iloc[0], "label": d["label"].iloc[0], "cases": len(d),
               "scored": len(scored), "skipped": int((d["status"] == "skipped").sum()), "failures": failures,
               "composite": _f(scoring.leaderboard_composite(mean_c, rob, weights)) if len(comp) else None,
               "case_composite": _f(mean_c), "robustness": _f(rob)}
        for k in scoring.COMPONENTS:
            row[k] = _f(scored[k].astype(float).mean()) if k in scored and scored[k].notna().any() else None
        if "depth_error_m" in scored and scored["depth_error_m"].notna().any():
            e = scored["depth_error_m"].astype(float)
            row["depth_mae_m"] = _f(e.abs().mean())
            row["depth_bias_m"] = _f(e.mean())
            row["depth_coverage"] = _f(scored["depth_covered"].dropna().astype(float).mean())
        row["runtime_s_mean"] = _f(d["runtime_s"].astype(float).mean())
        row["runtime_s_median"] = _f(d["runtime_s"].astype(float).median())
        out.append(row)
    return sorted(out, key=lambda r: -(r["composite"] if r["composite"] is not None else -1))


def build_leaderboard(df: pd.DataFrame, weights: ScoringWeights) -> dict:
    lb: dict = {"overall": leaderboard(df, weights)}
    for col, key in GROUPS.items():
        lb[key] = {str(v): leaderboard(d, weights) for v, d in df.groupby(col)} if col in df else {}
    return lb


def heldout_gap(df: pd.DataFrame, season: str, weights: ScoringWeights) -> dict[str, dict]:
    """Anti-memorisation hook: per agent, the composite on the cases of every other season ("train") and on the
    named season ("heldout"), and the gap (train - heldout). A large positive gap is the signature of an agent
    that fits the seasons it was tuned on."""
    if season not in set(df["season"]):
        raise ValueError(f"no scored case of season {season}")
    train, held = leaderboard(df[df["season"] != season], weights), leaderboard(df[df["season"] == season], weights)
    h = {r["agent_id"]: r for r in held}
    out = {}
    for r in train:
        hr = h.get(r["agent_id"])
        tc, hc = r["composite"], hr["composite"] if hr else None
        out[r["agent_id"]] = {"label": r["label"], "train_composite": tc, "heldout_composite": hc,
                              "gap": _f(tc - hc) if tc is not None and hc is not None else None,
                              "train_cases": r["scored"], "heldout_cases": hr["scored"] if hr else 0}
    return out


# --------------------------------------------------------------------------------------------- the run


@dataclass
class CompetitionResult:
    run_id: str
    run_dir: Path
    scores: pd.DataFrame
    leaderboard: dict
    manifest: RunManifest | None
    heldout_gap: dict | None = None
    resumed_cases: int = 0
    warnings: list[str] = field(default_factory=list)


def select_cases(paths: LabPaths, case_set: str = "all", splits: Iterable[str] | None = None,
                 sites: Iterable[str] | None = None, case_types: Iterable[str] | None = None,
                 forecast_sources: Iterable[str] | None = None, case_ids: Iterable[str] | None = None,
                 limit: int | None = None) -> list[tuple[Path, CaseManifest]]:
    """Scorable cases of a case set (sealed-test and unscored splits are never selected), sorted by case id."""
    out = []
    want_ids = set(case_ids) if case_ids else None
    for d in case_dirs(paths, case_set):
        m = read_manifest(d)
        if not scorable(m):
            continue
        if splits and m.split.value not in set(splits):
            continue
        if sites and str(m.site_code) not in {s.upper() for s in sites}:
            continue
        if case_types and m.case_type.value not in set(case_types):
            continue
        if forecast_sources and (m.forecast_source.value if m.forecast_source else None) not in set(forecast_sources):
            continue
        if want_ids is not None and m.case_id not in want_ids:
            continue
        out.append((d, m))
    out.sort(key=lambda x: x[1].case_id)
    return out[:limit] if limit else out


def case_set_hash(cases: list[tuple[Path, CaseManifest]]) -> str:
    h = hashlib.sha256()
    for d, m in cases:
        h.update(f"{m.case_id}\0{sha256_file(d / 'manifest.json')}\n".encode())
    return h.hexdigest()


def _pool_map(fn: Callable, items: list, workers: int, progress: Callable[[int, int], None] | None):
    done = 0
    if workers <= 1:
        for it in items:
            yield fn(it)
            done += 1
            if progress:
                progress(done, len(items))
        return
    with ProcessPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(fn, it) for it in items]
        for f in as_completed(futs):
            yield f.result()
            done += 1
            if progress:
                progress(done, len(items))


def _library(paths: LabPaths, case_set: str, run_dir: Path, workers: int) -> list[SeasonEntry]:
    f = run_dir / "library.json"
    if f.is_file():
        return [SeasonEntry.model_validate(e) for e in json.loads(f.read_text())]
    dirs = case_dirs(paths, case_set)
    entries = [e for e in _pool_map(library_entry, dirs, workers, None) if e is not None]
    entries.sort(key=lambda e: (e.season, e.entry.digest.site_code, e.entry.digest.hs_now_m or 0))
    f.write_text(json.dumps([e.model_dump(mode="json") for e in entries]))
    return entries


def run_competition(paths: LabPaths, cfg, genomes: list[AgentGenome], *, case_set: str = "all",
                    splits: Iterable[str] | None = None, sites: Iterable[str] | None = None,
                    case_types: Iterable[str] | None = None, forecast_sources: Iterable[str] | None = None,
                    case_ids: Iterable[str] | None = None, limit: int | None = None, workers: int = 1,
                    run_id: str | None = None, seed: int = 0, engine: EngineSpec | None = None,
                    heldout_season: str | None = None,
                    progress: Callable[[int, int], None] | None = None) -> CompetitionResult:
    t0 = time.time()
    created = datetime.now(UTC)
    engine = engine or EngineSpec()
    ids = [g.agent_id for g in genomes]
    if len(set(ids)) != len(ids):
        raise ValueError("two genomes are identical (same agent id)")
    cases = select_cases(paths, case_set, splits, sites, case_types, forecast_sources, case_ids, limit)
    if not cases:
        raise ValueError(f"no scorable case in case set {case_set!r} with these filters")
    modes = {(m.split_mode.value if m.split_mode else "all") for _, m in cases}
    if len(modes) > 1:
        raise ValueError(f"cases of several split modes {sorted(modes)}: run one case set at a time")
    weights = cfg.scoring_weights
    plan = {"runner_version": RUNNER_VERSION, "scoring_version": scoring.SCORING_VERSION, "case_set": case_set,
            "split_mode": modes.pop(), "case_ids": [m.case_id for _, m in cases], "case_set_hash": case_set_hash(cases),
            "genome_hashes": {g.agent_id: g.genome_hash for g in genomes}, "seed": seed,
            "scoring_weights": weights.model_dump(), "config_hash": cfg.config_hash(), "engine": engine.plan()}
    plan_hash = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    run_id = run_id or new_run_id("competition", salt=plan_hash)
    run_dir = paths.outputs / "competitions" / run_id
    resumed = 0
    if (run_dir / "run.json").is_file():
        old = json.loads((run_dir / "run.json").read_text())
        if old.get("plan_hash") != plan_hash:
            raise ValueError(f"run {run_id} exists with another plan (cases, genomes, seed, weights, engine or "
                             "config differ); start a new run")
        created = datetime.fromisoformat(old["created_at"])
    (run_dir / "cases").mkdir(parents=True, exist_ok=True)
    (run_dir / "genomes").mkdir(exist_ok=True)
    if not (run_dir / "run.json").is_file():
        (run_dir / "run.json").write_text(json.dumps({"run_id": run_id, "plan_hash": plan_hash,
                                                      "created_at": created.isoformat(), **plan}, indent=1))
    for g in genomes:
        (run_dir / "genomes" / f"{g.agent_id}.json").write_text(g.model_dump_json(indent=1))
    lib = _library(paths, case_set, run_dir, workers) if any(g.family == AgentFamily.analogue for g in genomes) \
        else []
    todo = []
    for d, m in cases:
        if (run_dir / "cases" / f"{m.case_id}.json").is_file():
            resumed += 1
            continue
        todo.append({"case_dir": str(d), "run_dir": str(run_dir), "seed": seed, "weights": weights.model_dump(),
                     "genomes": [g.model_dump(mode="json") for g in genomes], "engine": engine.__dict__,
                     "library": [e.model_dump(mode="json") for e in lib if e.season != m.season] if lib else None})
    for _ in _pool_map(run_case, todo, workers, progress):
        pass
    rows, versions, profile_ids, engine_prov = [], set(), set(), []
    for _, m in cases:
        rec = json.loads((run_dir / "cases" / f"{m.case_id}.json").read_text())
        rows += rec["rows"]
        profile_ids.update(rec["visible_profile_ids"])
        if rec.get("target_profile_id"):
            profile_ids.add(rec["target_profile_id"])
        engine_prov.append(rec["engine"])
        versions.update(r["snowpack_version"] for r in rec["rows"] if r.get("snowpack_version"))
    df = pd.DataFrame(rows)
    df.to_parquet(run_dir / "scores.parquet", index=False)
    lb = build_leaderboard(df, weights)
    gap = heldout_gap(df, heldout_season, weights) if heldout_season else None
    reuse = sum(p.get("source") == "site_run_reuse" for p in engine_prov)
    reasons: dict[str, int] = {}
    for p in engine_prov:
        for r in (p.get("site_run") or {}).get("reasons", []):
            key = r.split(";")[0].split("(")[0].strip()
            reasons[key] = reasons.get(key, 0) + 1
    summary = {"run_id": run_id, "label": "decision support / research only, not an avalanche forecast",
               "cases": len(cases), "agents": ids, "resumed_cases": resumed, "engine": engine.plan() | {
                   "site_run_reused": reuse, "site_run_refusals": reasons}, "leaderboard": lb, "heldout_gap": gap}
    (run_dir / "leaderboard.json").write_text(json.dumps(summary, indent=1, default=_json_default))
    errors = int((df["status"] == "error").sum())
    warnings = [f"{errors} agent errors (scored as failures)"] if errors else []
    manifest = RunManifest(
        run_id=run_id, kind=RunKind.competition, status="ok" if not errors else "partial", created_at=created,
        finished_at=datetime.now(UTC), config_hash=cfg.config_hash(), data_hash=plan["case_set_hash"],
        software_version=software_version(), git_commit=git_commit(Path(__file__).parent),
        snowpack_version=sorted(versions)[0] if len(versions) == 1 else ("; ".join(sorted(versions)) or None),
        seed=seed, scoring_weights=weights, splits={plan["split_mode"]: sorted({m.season for _, m in cases})},
        case_ids=plan["case_ids"], agent_ids=ids, genome_hashes=plan["genome_hashes"],
        case_set_hash=plan["case_set_hash"], profile_ids_used=sorted(profile_ids),
        outputs=[str(run_dir / n) for n in ("scores.parquet", "leaderboard.json")],
        counts={"cases": len(cases), "agents": len(ids), "rows": len(df), "resumed_cases": resumed,
                "skipped": int((df["status"] == "skipped").sum()), "errors": errors, "site_run_reused": reuse},
        warnings=warnings, runtime_s=round(time.time() - t0, 1))
    reg = RunRegistry(paths.registry)
    if reg.get(run_id) is None:
        reg.record(manifest)
    paths.manifests.mkdir(parents=True, exist_ok=True)
    (paths.manifests / f"{run_id}.json").write_text(manifest.model_dump_json(indent=1))
    return CompetitionResult(run_id, run_dir, df, lb, manifest, gap, resumed, warnings)


def load_run(paths: LabPaths, run_id: str) -> tuple[pd.DataFrame, dict]:
    run_dir = paths.outputs / "competitions" / run_id
    return pd.read_parquet(run_dir / "scores.parquet"), json.loads((run_dir / "leaderboard.json").read_text())


def list_runs(paths: LabPaths) -> list[str]:
    root = paths.outputs / "competitions"
    return sorted((d.name for d in root.iterdir() if (d / "leaderboard.json").is_file()), reverse=True) \
        if root.is_dir() else []
