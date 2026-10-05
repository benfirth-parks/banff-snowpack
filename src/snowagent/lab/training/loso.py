"""The promotion check, ``snowagent lab check-loso`` (ADR-068): leave-one-season-out re-training.

CLAUDE.md principle 3: a component is promoted only if it beats the incumbent on held-out seasons. A genome that came
out of training in split mode ``all`` has seen every season, so its own scores prove nothing. The check therefore
tests the PROCEDURE that produced it: for every season S of the training run,

1. the ``loso_S`` case set is built if it is missing or stale (S is the holdout split; training cases never see S's
   pits, ADR-059);
2. the whole training is re-run with the same options and seed on the training split of ``loso_S`` only (S's truth
   is never read: cases are selected by split, the analogue library holds training cases only, the monitor season
   is chosen among the training seasons);
3. the fold's best agent (rank 1 of the last round) and the SNOWPACK incumbent (the default ``snowpack`` genome) are
   scored on S's holdout cases, the same cases for both. The checked genome itself is scored there too, labelled
   in-sample (it was trained on S): a reference, not evidence.

Rule (ADR-068): PASS when the evolved agents' pooled held-out composite (the leaderboard composite over every
held-out case, each predicted by its own fold's winner) beats the incumbent's on the same cases, AND the evolved
agent does not lose on a majority of seasons (losses <= floor(n / 2); a season counts as lost when its fold winner's
composite is below the incumbent's by more than ``TIE_TOLERANCE``). Anything else is FAIL. Only a PASS lets an
evolved agent be considered for site output, and then still with the physical checks of principle 1.

Layout ``<data root>/outputs/loso_checks/<check_id>/``: ``check.json`` (the plan), ``folds/<season>.json`` (one per
finished fold, atomic), ``holdout_scores.parquet`` and ``result.json``. Each fold's training is an ordinary training
run ``<check_id>-<season>`` (resumable); a resumed check skips finished folds.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from snowagent.lab import LAB_DISCLAIMER
from snowagent.lab.benchmark.builder import BUILDER_VERSION
from snowagent.lab.benchmark.loader import case_dirs, read_manifest
from snowagent.lab.competition.runner import EngineSpec, leaderboard, select_cases
from snowagent.lab.genome import default_genome, load_genome
from snowagent.lab.schemas.benchmark import SplitMode
from snowagent.lab.schemas.genome import AgentFamily, AgentGenome
from snowagent.lab.schemas.run import RunKind, RunManifest
from snowagent.lab.settings import LabConfig
from snowagent.lab.storage.paths import LabPaths
from snowagent.lab.storage.provenance import git_commit, new_run_id, software_version
from snowagent.lab.storage.registry import RunRegistry
from snowagent.lab.training.cache import TrainingCache
from snowagent.lab.training.estimate import (
    estimate_child_rounds,
    estimate_pairs,
    fmt_s,
    load_timings,
)
from snowagent.lab.training.evaluate import EvalContext, build_library, case_refs, evaluate_population
from snowagent.lab.training.loop import (
    TrainOptions,
    _opts_from_plan,
    load_round,
    rank_agents,
    run_training,
    training_root,
)

CHECK_VERSION = "lab-loso-check-1"
TIE_TOLERANCE = 1e-4  # composites closer than this are a tie (neither a win nor a loss)
BUILD_S = 270.0  # one case-set build on the 2026-10-05 data (build reports: 260-273 s)
RULE = ("PASS when the evolved agents' pooled held-out composite beats the SNOWPACK incumbent's on the same cases "
        "AND the evolved agent loses (composite below the incumbent's by more than 1e-4) on at most floor(n/2) of "
        "the n held-out seasons; otherwise FAIL")


@dataclass
class CheckResult:
    check_id: str
    check_dir: Path
    folds: list[dict]
    result: dict


def checks_root(paths: LabPaths) -> Path:
    return paths.outputs / "loso_checks"


def resolve_genome(paths: LabPaths, ref: str, cfg: LabConfig) -> tuple[AgentGenome, dict | None]:
    """A genome file, or ``<training run>/<round>/<rank>``; returns the genome and the run's plan (if any)."""
    p = Path(ref)
    if p.is_file():
        return load_genome(p, cfg.genome), None
    parts = ref.strip("/").split("/")
    if len(parts) != 3:
        raise ValueError(f"--genome {ref!r}: a genome JSON file or <training run>/<round>/<rank>")
    run_id, rnd, rank = parts[0], int(parts[1]), int(parts[2])
    run_dir = training_root(paths) / run_id
    if not (run_dir / "run.json").is_file():
        raise ValueError(f"no training run {run_id}")
    rd = load_round(run_dir, rnd)
    ranked = rd["leaderboard"]["ranked"]
    if not 1 <= rank <= len(ranked):
        raise ValueError(f"round {rnd} of {run_id} has ranks 1-{len(ranked)}")
    h = ranked[rank - 1]["genome_hash"]
    g = next(AgentGenome.model_validate(x["genome"]) for x in rd["population"]
             if AgentGenome.model_validate(x["genome"]).genome_hash == h)
    return g, json.loads((run_dir / "run.json").read_text())["plan"]


def loso_config(cfg: LabConfig) -> LabConfig:
    return cfg.model_copy(update={"splits": cfg.splits.model_copy(update={"mode": SplitMode.loso})})


def case_set_status(paths: LabPaths, season: str) -> str:
    """``ok``, ``missing`` or ``stale`` (built by another builder version) for ``loso_<season>``."""
    dirs = case_dirs(paths, f"loso_{season}")
    if not dirs or not (paths.benchmark / f"loso_{season}" / "build_report.json").is_file():
        return "missing"
    return "ok" if all(read_manifest(d).builder_version == BUILDER_VERSION for d in dirs) else "stale"


def _build_one(args: tuple) -> dict:
    from snowagent.lab.benchmark.builder import build_cases
    from snowagent.lab.benchmark.leakage import LeakageError

    root, cfg_json, source, season = args
    cfg = LabConfig.model_validate_json(cfg_json)
    try:
        rep = build_cases(LabPaths(Path(root)), cfg, Path(source), holdout=season)
    except LeakageError as exc:  # reported as text: the exception does not cross a process boundary intact
        return {"season": season, "error": str(exc)[:2000], "leakage": {"fail": 1}}
    return {"season": season, "cases": rep["cases"], "leakage": rep["leakage"], "runtime_s": rep["runtime_s"]}


def build_missing(paths: LabPaths, cfg: LabConfig, seasons: list[str], source: Path, workers: int,
                  log: Callable[[str], None]) -> list[dict]:
    todo = [s for s in seasons if case_set_status(paths, s) != "ok"]
    if not todo:
        return []
    log(f"building {len(todo)} leave-one-season-out case sets ({', '.join(todo)}) from {source}")
    lc = loso_config(cfg).model_dump_json()
    args = [(str(paths.root), lc, str(source), s) for s in todo]
    if workers <= 1 or len(todo) == 1:
        out = [_build_one(a) for a in args]
    else:
        with ProcessPoolExecutor(max_workers=min(workers, len(todo))) as ex:
            out = list(ex.map(_build_one, args))
    failed = []
    for o in out:
        if o.get("error"):
            log(f"  loso_{o['season']}: LEAKAGE CHECK FAILED, nothing of that case written: {o['error']}")
            failed.append(o["season"])
            continue
        log(f"  loso_{o['season']}: {o['cases']} cases, leakage {o['leakage']}, {o['runtime_s']:.0f} s")
        if o["leakage"].get("fail"):
            failed.append(o["season"])
    if failed:
        raise RuntimeError(f"case sets {', '.join(f'loso_{s}' for s in failed)} failed the leakage checks; "
                           "rerun the check to retry the build, and report a repeated failure")
    return out


def estimate_check(paths: LabPaths, cfg: LabConfig, opts: TrainOptions, seasons: list[str], workers: int,
                   reference: dict | None = None) -> dict:
    """Builds + per fold (training on the other seasons' cases, then the held-out scoring). Engine profiles of a
    fold's cases are counted as cached when the same case of the ``all`` set has a cached profile (same inputs).
    Later rounds are a range (cheapest to dearest family); with ``reference`` (the checked training run's median
    wall time of rounds 2..N, its case count and workers) an expected value scales that measured round to the
    fold's cases."""
    cache = TrainingCache(paths.outputs / "cache")
    timings = load_timings(paths, cache.timings)
    base = select_cases(paths, "all", None, opts.plots, opts.case_types)
    refs = case_refs(base)
    builds = [s for s in seasons if case_set_status(paths, s) != "ok"]
    build_s = BUILD_S * len(builds) / max(1, min(workers, len(builds) or 1)) if builds else 0.0
    initial = list(opts.initial) if opts.initial else []
    folds = {}
    for s in seasons:
        fr = [r for r in refs if r.manifest.season != s]
        hr = [r for r in refs if r.manifest.season == s]
        work = [(initial, cache.engine_cached_for(r.case_hash)) for r in fr]
        r1 = estimate_pairs(work, timings, workers, len(initial) * len(fr)).wall_s
        lo, hi = estimate_child_rounds(len(fr), opts.population - opts.survivors, timings, workers)
        hold = estimate_pairs([(initial[:3], cache.engine_cached_for(r.case_hash)) for r in hr], timings, workers,
                              3 * len(hr)).wall_s
        folds[s] = {"cases": len(fr), "holdout_cases": len(hr), "low_s": r1 + (opts.rounds - 1) * lo + hold,
                    "high_s": r1 + (opts.rounds - 1) * hi + hold}
        if reference and reference.get("round_s"):
            per = reference["round_s"] * len(fr) / reference["cases"] * reference["workers"] / max(1, workers)
            folds[s]["expected_s"] = r1 + (opts.rounds - 1) * per + hold
    total_lo = build_s + sum(f["low_s"] for f in folds.values())
    total_hi = build_s + sum(f["high_s"] for f in folds.values())
    out = {"builds": builds, "build_s": round(build_s, 1), "folds": folds, "total_low_s": round(total_lo, 1),
           "total_high_s": round(total_hi, 1), "workers": workers, "timings": timings.source}
    if all("expected_s" in f for f in folds.values()):
        out["total_expected_s"] = round(build_s + sum(f["expected_s"] for f in folds.values()), 1)
        out["expected_from"] = reference
    return out


def reference_rounds(run_dir: Path, workers: int | None = None) -> dict | None:
    """Median wall time of a training run's rounds 2..N (its own measured cost of a later round)."""
    import statistics

    from snowagent.lab.training.loop import committed_rounds

    walls = [load_round(run_dir, r)["round"]["wall_s"] for r in committed_rounds(run_dir) if r > 1]
    if not walls:
        return None
    meta = json.loads((run_dir / "run.json").read_text())
    return {"run_id": run_dir.name, "round_s": float(statistics.median(walls)),
            "cases": len(meta["plan"]["case_ids"]), "workers": (meta.get("estimate") or {}).get("workers") or workers
            or 1}


def _fold_result(paths: LabPaths, cfg: LabConfig, season: str, fold_run: str, winner: AgentGenome,
                 checked: AgentGenome, opts: TrainOptions, workers: int) -> tuple[dict, pd.DataFrame]:
    case_set = f"loso_{season}"
    cases = select_cases(paths, case_set, ["holdout"], opts.plots, opts.case_types)
    incumbent = default_genome(AgentFamily.snowpack, cfg.genome)
    genomes = list({g.genome_hash: g for g in (winner, incumbent, checked)}.values())
    out = {"season": season, "fold_run": fold_run, "holdout_cases": len(cases),
           "winner": {"agent_id": winner.agent_id, "label": winner.display_name, "family": winner.family.value,
                      "genome_hash": winner.genome_hash}}
    if not cases:
        return out | {"outcome": "no_cases"}, pd.DataFrame()
    lib = build_library(paths, case_set, TrainingCache(paths.outputs / "cache"), workers)
    ctx = EvalContext(paths=paths, case_set=case_set, seed=opts.seed, weights=cfg.scoring_weights,
                      config_hash=cfg.config_hash(), engine=opts.engine, library_file=lib)
    res = evaluate_population(case_refs(cases), genomes, ctx, workers)
    df = res.scores
    board = {r["agent_id"]: r for r in rank_agents(df, cfg.scoring_weights, genomes)}
    w, i = board[winner.agent_id], board[incumbent.agent_id]
    wc, ic = w["composite_exact"], i["composite_exact"]
    diff = None if wc is None or ic is None else wc - ic
    outcome = "tie" if diff is not None and abs(diff) <= TIE_TOLERANCE else \
        ("win" if diff is not None and diff > 0 else "loss")
    out |= {"winner_composite": w["composite"], "incumbent_composite": i["composite"],
            "difference": None if diff is None else round(diff, 4), "outcome": outcome,
            "winner_is_incumbent": winner.genome_hash == incumbent.genome_hash,
            "checked_genome_in_sample": {"agent_id": checked.agent_id,
                                         "composite": board[checked.agent_id]["composite"],
                                         "note": "in-sample: the checked genome was trained on this season"},
            "eval": res.summary(), "seasons_trained": sorted({m.season for _, m in select_cases(
                paths, case_set, ["training"], opts.plots, opts.case_types)})}
    df = df[df["genome_hash"].isin({winner.genome_hash, incumbent.genome_hash})].copy()
    df["role"] = df["genome_hash"].map(lambda h: "incumbent" if h == incumbent.genome_hash else "evolved")
    if winner.genome_hash == incumbent.genome_hash:  # the fold kept the incumbent: it is both
        ev = df.copy()
        ev["role"] = "evolved"
        df = pd.concat([df, ev], ignore_index=True)
    df["fold_season"] = season
    return out, df


def pooled_result(folds: list[dict], holdout: pd.DataFrame, cfg: LabConfig) -> dict:
    scored = [f for f in folds if f.get("outcome") in ("win", "loss", "tie")]
    n = len(scored)
    losses = sum(f["outcome"] == "loss" for f in scored)
    wins = sum(f["outcome"] == "win" for f in scored)
    pooled = {}
    if len(holdout):
        d = holdout.copy()
        d["agent_id"] = d["role"]
        d["label"] = d["role"]
        d["family"] = d["role"]
        pooled = {r["agent_id"]: r for r in leaderboard(d, cfg.scoring_weights)}
    ev, inc = pooled.get("evolved", {}).get("composite"), pooled.get("incumbent", {}).get("composite")
    beats = ev is not None and inc is not None and ev > inc
    majority_ok = n > 0 and losses <= n // 2
    return {"seasons": n, "wins": wins, "losses": losses, "ties": sum(f["outcome"] == "tie" for f in scored),
            "pooled_evolved_composite": ev, "pooled_incumbent_composite": inc,
            "pooled_difference": None if ev is None or inc is None else round(ev - inc, 4),
            "pooled_cases": int(holdout["case_id"].nunique()) if len(holdout) else 0,
            "pooled_beats_incumbent": beats, "loses_on_majority": not majority_ok,
            "passed": bool(beats and majority_ok), "rule": RULE,
            "pooled_components": {k: {c: pooled.get(k, {}).get(c) for c in
                                      ("snow_depth", "layer_structure", "critical_layers", "uncertainty",
                                       "robustness", "depth_mae_m")} for k in ("evolved", "incumbent")}}


def check_loso(paths: LabPaths, cfg: LabConfig, genome_ref: str, opts: TrainOptions | None = None, *,
               workers: int = 1, check_id: str | None = None, source: Path = Path("."),
               seasons: list[str] | None = None, log: Callable[[str], None] = print,
               estimate_only: bool = False, overrides: dict | None = None) -> CheckResult:
    """See the module doc. ``opts`` are the training options for a genome file; a ``run/round/rank`` reference
    takes the training run's own options (same seed). ``overrides`` (rounds, population, ...) replace options in
    either case: a cheaper, weaker check, recorded in the result as differing from the training run. ``seasons``
    limits the folds (default: every season of the training cases)."""
    t0 = time.time()
    checked, plan = resolve_genome(paths, genome_ref, cfg)
    reduced: dict = {}
    if plan is not None:
        opts = _opts_from_plan(plan, opts.engine if opts else None)
        if plan["case_set"] != "all":
            raise ValueError("check-loso re-trains runs of the 'all' case set")
    opts = opts or TrainOptions.from_config(cfg)
    for k, v in (overrides or {}).items():
        if v is None:
            continue
        if plan is not None and plan.get(k) != v:
            reduced[k] = {"training_run": plan.get(k), "check": v}
        setattr(opts, k, v)
    opts.validate()
    opts = TrainOptions(**(opts.__dict__ | {"case_set": "all", "splits": None, "monitor_season": None}))
    from snowagent.lab.genome import default_genomes

    if opts.initial is None:
        opts.initial = default_genomes(cfg.genome)
    all_cases = select_cases(paths, "all", None, opts.plots, opts.case_types)
    if not all_cases:
        raise ValueError("no training cases in case set 'all' (build them with snowagent lab build-cases)")
    seasons = seasons or sorted({m.season for _, m in all_cases})
    ref = reference_rounds(training_root(paths) / genome_ref.strip("/").split("/")[0], workers) \
        if plan is not None and not reduced.get("rounds") and not reduced.get("population") else None
    est = estimate_check(paths, cfg, opts, seasons, workers, ref)
    if reduced:
        log("note: this check trains with other options than the training run ("
            + ", ".join(f"{k} {v['check']} instead of {v['training_run']}" for k, v in reduced.items())
            + "): it checks that cheaper procedure, a weaker test of the run")
    log(f"check-loso estimate ({est['timings']} timings, {workers} workers): {len(seasons)} folds, "
        f"{len(est['builds'])} case sets to build ({fmt_s(est['build_s'])}), total about "
        f"{fmt_s(est['total_low_s'])}-{fmt_s(est['total_high_s'])} (later rounds between the cheapest and the dearest "
        "family)" + (f"; expected about {fmt_s(est['total_expected_s'])} from the measured rounds of "
                     f"{est['expected_from']['run_id']}" if est.get("total_expected_s") else ""))
    if estimate_only:
        return CheckResult("", Path(), [], {"estimate": est})
    plan_c = {"check_version": CHECK_VERSION, "genome": checked.model_dump(mode="json"), "genome_ref": genome_ref,
              "seasons": seasons, "rounds": opts.rounds, "population": opts.population, "survivors": opts.survivors,
              "mutation_strength": opts.mutation_strength, "crossover_share": opts.crossover_share,
              "seed": opts.seed, "plots": opts.plots, "case_types": opts.case_types,
              "initial": [g.model_dump(mode="json") for g in opts.initial], "max_redraws": opts.max_redraws,
              "gap_flag_rounds": opts.gap_flag_rounds, "gap_tolerance": opts.gap_tolerance,
              "config_hash": cfg.config_hash(), "rule": RULE, "differs_from_training_run": reduced}
    plan_hash = hashlib.sha256(json.dumps(plan_c, sort_keys=True).encode()).hexdigest()
    check_id = check_id or new_run_id("loso_check", salt=plan_hash)
    cdir = checks_root(paths) / check_id
    if (cdir / "check.json").is_file():
        old = json.loads((cdir / "check.json").read_text())
        if old["plan_hash"] != plan_hash:
            raise ValueError(f"check {check_id} exists with another plan; start a new check")
        created = datetime.fromisoformat(old["created_at"])
    else:
        created = datetime.now(UTC)
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "check.json").write_text(json.dumps({"check_id": check_id, "plan_hash": plan_hash,
                                                     "created_at": created.isoformat(), "plan": plan_c,
                                                     "estimate": est, "label": LAB_DISCLAIMER}, indent=1))
    _status(cdir, state="running", phase="building", seasons=seasons, done=[])
    build_missing(paths, cfg, seasons, source, workers, log)
    folds, frames = [], []
    for k, s in enumerate(seasons, 1):
        f = cdir / "folds" / f"{s}.json"
        if f.is_file():
            folds.append(json.loads(f.read_text()))
            hp = cdir / "folds" / f"{s}.parquet"
            if hp.is_file():
                frames.append(pd.read_parquet(hp))
            log(f"fold {k}/{len(seasons)} {s}: done earlier ({folds[-1].get('outcome')})")
            continue
        _status(cdir, state="running", phase=f"fold {k}/{len(seasons)}: training without {s}", season=s,
                done=[x["season"] for x in folds])
        fold_run = f"{check_id}-{s}"
        fopts = TrainOptions(**(opts.__dict__ | {"case_set": f"loso_{s}", "splits": ["training"]}))
        log(f"fold {k}/{len(seasons)}: training without {s} (run {fold_run})")
        exists = (training_root(paths) / fold_run / "run.json").is_file()
        tr = run_training(paths, cfg, fopts, workers=workers, run_id=fold_run, resume=exists,
                          log=lambda m, s=s: log(f"  [{s}] {m}"))
        last = load_round(tr.run_dir, fopts.rounds)
        top = last["leaderboard"]["ranked"][0]["genome_hash"]
        winner = next(AgentGenome.model_validate(x["genome"]) for x in last["population"]
                      if AgentGenome.model_validate(x["genome"]).genome_hash == top)
        _status(cdir, state="running", phase=f"fold {k}/{len(seasons)}: scoring {s}", season=s,
                done=[x["season"] for x in folds])
        out, df = _fold_result(paths, cfg, s, fold_run, winner, checked, opts, workers)
        f.parent.mkdir(parents=True, exist_ok=True)
        if len(df):
            df.to_parquet(cdir / "folds" / f"{s}.parquet", index=False)
            frames.append(df)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(out, indent=1, default=str))
        os.replace(tmp, f)
        folds.append(out)
        log(f"fold {s}: {out.get('holdout_cases')} held-out cases; winner {out['winner']['label']} "
            f"{out.get('winner_composite')} vs SNOWPACK {out.get('incumbent_composite')} -> {out.get('outcome')}")
    holdout = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if len(holdout):
        holdout.to_parquet(cdir / "holdout_scores.parquet", index=False)
    result = pooled_result(folds, holdout, cfg) | {
        "check_id": check_id, "genome_ref": genome_ref, "checked_agent_id": checked.agent_id,
        "differs_from_training_run": reduced, "rounds": opts.rounds, "population": opts.population,
        "label": LAB_DISCLAIMER, "wall_s": round(time.time() - t0, 1),
        "per_season": [{k: f.get(k) for k in ("season", "holdout_cases", "outcome", "winner_composite",
                                              "incumbent_composite", "difference")}
                       | {"winner": f["winner"]["label"], "winner_family": f["winner"]["family"]} for f in folds]}
    tmp = cdir / "result.tmp"
    tmp.write_text(json.dumps(result, indent=1, default=str))
    os.replace(tmp, cdir / "result.json")
    reg = RunRegistry(paths.registry)
    if reg.get(check_id) is None:
        reg.record(RunManifest(
            run_id=check_id, kind=RunKind.evolution, status="ok", created_at=created, finished_at=datetime.now(UTC),
            config_hash=cfg.config_hash(), data_hash=hashlib.sha256(json.dumps(
                sorted(f["fold_run"] for f in folds)).encode()).hexdigest(),
            software_version=software_version(), git_commit=git_commit(Path(__file__).parent), seed=opts.seed,
            scoring_weights=cfg.scoring_weights, splits={"loso": seasons},
            agent_ids=[checked.agent_id], genome_hashes={checked.agent_id: checked.genome_hash},
            outputs=[str(cdir / "result.json")], counts={"folds": len(folds), "wins": result["wins"],
                                                         "losses": result["losses"], "passed": int(result["passed"])},
            warnings=[] if result["passed"] else ["promotion check failed: the evolved agent stays research only"],
            runtime_s=result["wall_s"]))
    _status(cdir, state="finished", phase="finished", done=[f["season"] for f in folds])
    return CheckResult(check_id, cdir, folds, result)


def _status(cdir: Path, **kw) -> None:
    f = cdir / "status.json"
    old = json.loads(f.read_text()) if f.is_file() else {}
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(old | kw | {"updated_at": datetime.now(UTC).isoformat(), "pid": os.getpid()},
                              indent=1))
    os.replace(tmp, f)


def list_checks(paths: LabPaths) -> list[str]:
    root = checks_root(paths)
    if not root.is_dir():
        return []
    return sorted((d.name for d in root.iterdir() if (d / "check.json").is_file()), reverse=True)


def load_check(paths: LabPaths, check_id: str) -> dict:
    d = checks_root(paths) / check_id
    out = {"check": json.loads((d / "check.json").read_text()),
           "folds": [json.loads(f.read_text()) for f in sorted((d / "folds").glob("*.json"))]
           if (d / "folds").is_dir() else []}
    for name in ("result", "status"):
        f = d / f"{name}.json"
        out[name] = json.loads(f.read_text()) if f.is_file() else None
    return out


__all__ = ["RULE", "CheckResult", "EngineSpec", "check_loso", "list_checks", "load_check", "resolve_genome"]
