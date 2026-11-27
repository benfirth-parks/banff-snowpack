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

The winter test (ADR-125, ``snowagent lab winter-test``) is the same machinery used to test a training PROCEDURE
rather than one agent: no checked genome, the training options given directly (a preset such as the column model),
the held-out winters grouped into a few folds (every K-th winter together, so a fold's winters are spread over the
years), and the locked test winters (ADR-083) kept out of every fold, so they stay unseen. It reports each winter's
score of three agents: the fold's trained winner, the best untrained starting agent (what training added) and
standard SNOWPACK (the bar), with the average and the spread across winters.

Layout ``<data root>/outputs/loso_checks/<check_id>/``: ``check.json`` (the plan), ``folds/<season>.json`` (one per
finished fold, atomic), ``holdout_scores.parquet`` and ``result.json``. Each fold's training is an ordinary training
run ``<check_id>-<season>`` (resumable); a resumed check skips finished folds.
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import secrets
import time
from collections.abc import Callable
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from snowagent.lab import LAB_DISCLAIMER
from snowagent.lab.agents.physics import engine_physics
from snowagent.lab.benchmark.builder import BUILDER_VERSION
from snowagent.lab.benchmark.loader import case_dirs, read_manifest
from snowagent.lab.competition import scoring
from snowagent.lab.competition.runner import EngineSpec, leaderboard, select_cases
from snowagent.lab.genome import default_genome, load_genome, named_genome
from snowagent.lab.schemas.benchmark import SplitMode
from snowagent.lab.schemas.genome import AgentFamily, AgentGenome
from snowagent.lab.schemas.run import RunKind, RunManifest
from snowagent.lab.settings import LabConfig, holdout_group
from snowagent.lab.storage.paths import LabPaths
from snowagent.lab.storage.provenance import git_commit, new_run_id, software_version
from snowagent.lab.storage.registry import RunRegistry
from snowagent.lab.training.cache import ENGINE_FAMILIES, TrainingCache
from snowagent.lab.training.estimate import (
    child_families,
    estimate_child_rounds,
    estimate_pairs,
    fmt_s,
    guess_note,
    load_timings,
    save_timings,
)
from snowagent.lab.training.evaluate import EvalContext, build_library, case_refs, evaluate_population
from snowagent.lab.training.loop import (
    LOCKED_REFERENCE,
    LOCKED_SEASONS,
    TrainOptions,
    _opts_from_plan,
    committed_rounds,
    load_round,
    rank_agents,
    run_standard,
    run_training,
    split_locked,
    training_root,
)

CHECK_VERSION = "lab-loso-check-1"
TIE_TOLERANCE = 1e-4  # composites closer than this are a tie (neither a win nor a loss)
# one case-set build of the 945 cases on a cloud core (449 s, 2026-11-19); ``build_seconds`` prefers what the
# computer the check runs on took itself
BUILD_S = 450.0
RULE = ("PASS when the evolved agents' pooled held-out composite beats the SNOWPACK incumbent's on the same cases "
        "AND the evolved agent loses (composite below the incumbent's by more than 1e-4) on at most floor(n/2) of "
        "the n held-out seasons; otherwise FAIL")


WINTER_TEST = "winter_test"  # plan["kind"] of a winter test (ADR-125); a promotion check's plan has no kind
BOOTSTRAP_N = 2000  # resamples of the winters for the 90% range of the average difference


def fold_groups(seasons: list[str], folds: int | None) -> list[list[str]]:
    """ADR-125: the held-out winters of each fold. ``folds`` None (or at least the number of winters): one winter a
    fold. Otherwise winter i (in date order) goes to fold i mod ``folds``, so each fold's winters are spread over the
    years (early reanalysis winters and later station winters in every fold) instead of one era held out at once."""
    seasons = sorted(seasons)
    if not folds or folds >= len(seasons):
        return [[s] for s in seasons]
    if folds < 2:
        raise ValueError("--folds must be at least 2 (or leave it out for one winter a fold)")
    return [[s for i, s in enumerate(seasons) if i % folds == k] for k in range(folds)]


def fold_labels(groups: list[list[str]]) -> list[str]:
    """The folds' names in files and run ids: each fold's winter when every fold holds one, as before ADR-125,
    otherwise ``fold<k>of<n>`` (the case set name holds the winters, ``loso_<w1>+<w2>+...``)."""
    if all(len(g) == 1 for g in groups):
        return [g[0] for g in groups]
    return [f"fold{k}of{len(groups)}" for k in range(1, len(groups) + 1)]


def draw_pool(seasons: list[str], *, excluded: list[str] | tuple = (), locked: list[str] | tuple = (),
              seen: list[str] | tuple = ()) -> list[str]:
    """ADR-165: the winters a random draw may hold out. Every winter of ``seasons`` except those kept out of the test
    (``excluded``), the locked test winters (``locked``, ADR-083) and any winter at or after the earliest locked one
    (so the live blind-test winter is never drawn either, even if its pits were in a case set), and the winters the
    run's seeding agents already trained on (``seen``, ADR-097: holding them out would not be a fair test)."""
    out = set(excluded) | set(seen) | set(locked)
    first_locked = min(locked) if locked else None
    return sorted(s for s in set(seasons) - out if first_locked is None or s < first_locked)


def draw_winters(pool: list[str], n: int, seed: int) -> list[str]:
    """ADR-165: ``n`` winters drawn at random from ``pool`` with draw number ``seed``: the same pool and number always
    give the same winters, so any check can be repeated, and a new number gives a new draw."""
    pool = sorted(pool)
    if n < 1:
        raise ValueError("draw at least one winter")
    if n > len(pool):
        raise ValueError(f"cannot draw {n} winters: only {len(pool)} can be held out ({', '.join(pool) or 'none'})")
    return sorted(random.Random(f"lab-winter-draw-{seed}").sample(pool, n))


def check_draw_pool(paths: LabPaths, plan: dict | None, seasons: list[str],
                    excluded: list[str] | tuple = ()) -> tuple[list[str], list[str]]:
    """ADR-165: (the winters a check's random draw may hold out, the locked winters never drawn) for a training run's
    ``plan`` (None: a genome file) and its ``seasons``. The locked winters are the run's own and the lab's most recent
    ``LOCKED_SEASONS`` (the same ones whatever the run was limited to). The app previews a draw with this too."""
    locked = sorted(set((plan or {}).get("locked_seasons") or [])
                    | set(split_locked(select_cases(paths, "all"), LOCKED_SEASONS)[2]))
    seen = seeded_saw(plan, seasons) if plan is not None else []
    return draw_pool(seasons, excluded=excluded, locked=locked, seen=seen), locked


def new_draw_seed() -> int:
    """A fresh draw number (ADR-165), short enough to read out and type back in to repeat a check."""
    return 1000 + secrets.randbelow(9000)


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


def build_seconds(paths: LabPaths) -> float:
    """One case-set build on this computer: what its ``all`` set took to build (a fold's set is the same pits and
    the same work), or ``BUILD_S`` where that build left no record."""
    try:
        s = float(json.loads((paths.benchmark / "all" / "build_report.json").read_text())["runtime_s"])
    except (OSError, ValueError, KeyError, TypeError):
        return BUILD_S
    return s if s > 0 else BUILD_S


def _holdout_hashes(paths: LabPaths, season: str) -> dict[str, str]:
    """Case id -> case hash of the held-out cases of a fold's own case set (``loso_<season>``). The held-out
    scoring runs these cases, so the engine index knows them by these hashes; the same pits of the ``all`` set have
    other hashes (another case key), whose index entries a winter test never refreshes."""
    dirs = case_dirs(paths, f"loso_{season}", "holdout")
    return {r.case_id: r.case_hash for r in case_refs([(d, read_manifest(d)) for d in dirs])}


def estimate_check(paths: LabPaths, cfg: LabConfig, opts: TrainOptions, seasons: list[str], workers: int,
                   reference: dict | None = None, bar: AgentGenome | None = None, *, time_here: bool = True,
                   log: Callable[[str], None] | None = None) -> dict:
    """Builds + per fold (training on the other seasons' cases, then the held-out scoring). Later rounds are a
    range (cheapest to dearest family); with ``reference`` (the checked training run's median wall time of rounds
    2..N, its case count and workers) an expected value scales that measured round to the fold's cases.
    ``seasons``: the folds' held-out seasons, or ``+``-joined groups of them (ADR-125); seasons in
    ``opts.exclude_seasons`` are in no fold's training.

    ADR-126. A family this computer has never timed, or timed only by runs smaller than one round of a fold, is
    timed here first (``calibrate``, on training cases outside the locked winters; ``time_here`` False or an
    estimate under a minute skips it, and so does a training run going in another process, which ``busy`` then
    names), and ``guessed`` names what still rests on a built-in number. An engine profile counts as cached only
    when the index shows one made by this code and this engine (``engine_stamp``): for a fold's training cases
    the same case of the ``all`` set (same inputs), once over the folds, as a later fold finds the profiles an
    earlier one made; for its held-out cases the fold's own case set where it is built."""
    from snowagent.lab.training import calibrate as cal
    from snowagent.lab.training.cache import engine_identity, engine_stamp

    cache = TrainingCache(paths.outputs / "cache")
    timings = load_timings(paths, cache.timings)
    base = select_cases(paths, "all", None, opts.plots, opts.case_types,
                        weather_sources=opts.weather_sources)
    refs = case_refs(base)
    status = {s: case_set_status(paths, s) for s in seasons}
    builds = [s for s in seasons if status[s] != "ok"]
    build_s = build_seconds(paths) * len(builds) / max(1, min(workers, len(builds) or 1)) if builds else 0.0
    initial = list(opts.initial) if opts.initial else []
    physics = any(blk == "snowpack_physics" for f in ENGINE_FAMILIES for blk in cfg.genome.families[f])
    out_s = set(opts.exclude_seasons or [])
    scored = [*initial[:2], bar or default_genome(AgentFamily.snowpack, cfg.genome)]
    # round 1 scores the initial agents; fewer of them than survivors are filled to the population with mutants
    # of the same families (``fill_initial``, ADR-118), which cost what their parents cost
    first = initial if len(initial) >= opts.survivors or not initial else \
        [initial[i % len(initial)] for i in range(opts.population)]
    children = child_families(initial, opts.family_slots)
    stamp = engine_stamp(engine_identity(opts.engine.kind, opts.engine.binary))
    held = {s: _holdout_hashes(paths, s) if status[s] == "ok" else {} for s in seasons}
    runs_engine = any(g.family in ENGINE_FAMILIES for g in first)
    no_engine = opts.engine.kind == "none"  # SNOWPACK is skipped: no engine run to count

    def folds_at(timings) -> tuple[dict, int]:
        folds, engine_runs = {}, 0
        made: set[str] = set()  # training cases whose incumbent profile an earlier fold's round 1 made
        for s in seasons:
            g = set(holdout_group(s))
            fr = [r for r in refs if r.manifest.season not in g and r.manifest.season not in out_s]
            hr = [r for r in refs if r.manifest.season in g]
            work = [(first, no_engine or r.case_id in made or cache.engine_cached_for(r.case_hash, stamp=stamp))
                    for r in fr]
            r1 = estimate_pairs(work, timings, workers, len(first) * len(fr))
            if runs_engine:
                made |= {r.case_id for r in fr}
            lo, hi = estimate_child_rounds(len(fr), opts.population - opts.survivors, timings, workers,
                                           physics=physics, screen_cases=opts.screen_cases, families=children)
            # the held-out scoring: the winner and its start (as two of the initial agents) and the SNOWPACK bar,
            # whose engine run counts unless a profile of this code and engine is in the cache
            hold = estimate_pairs([(scored, no_engine or cache.engine_cached_for(
                held[s].get(r.case_id, r.case_hash), engine_physics(scored[-1].genes, str(r.manifest.site_code)).key,
                stamp)) for r in hr], timings, workers, 3 * len(hr))
            engine_runs += r1.engine_runs + hold.engine_runs
            folds[s] = {"cases": len(fr), "holdout_cases": len(hr),
                        "low_s": r1.wall_s + (opts.rounds - 1) * lo + hold.wall_s,
                        "high_s": r1.wall_s + (opts.rounds - 1) * hi + hold.wall_s}
            if reference and reference.get("round_s"):
                per = reference["round_s"] * len(fr) / reference["cases"] * reference["workers"] / max(1, workers)
                folds[s]["expected_s"] = r1.wall_s + (opts.rounds - 1) * per + hold.wall_s
        return folds, engine_runs

    folds, engine_runs = folds_at(timings)
    families = sorted({g.family.value for g in first} | set(children or []))
    timed: dict = {}
    busy = None
    # a family never timed here, or timed only by runs smaller than one round of a fold (other cases, perhaps)
    enough = (opts.population - opts.survivors) * max((f["cases"] for f in folds.values()), default=0)
    todo = cal.can_time(timings, families, enough) if time_here else []
    if todo and build_s + sum(f["high_s"] for f in folds.values()) >= cal.WORTH_S:
        going = cal.run_going(paths)
        if going:  # its workers have the cores: a timing beside it would measure a busy computer
            busy = {"run_id": going, "families": todo}
            if log:
                log(cal.busy_note(going, todo))
        else:
            locked = set(split_locked(base, LOCKED_SEASONS)[2])  # never run here, whatever the test holds out
            timed = cal.calibrate(paths, cfg, timings, families,
                                  [r for r in refs if r.manifest.season not in out_s | locked], workers,
                                  seed=opts.seed, strength=opts.mutation_strength, enough=enough, log=log)
            if timed:
                folds, engine_runs = folds_at(timings)
    total_lo = build_s + sum(f["low_s"] for f in folds.values())
    total_hi = build_s + sum(f["high_s"] for f in folds.values())
    later_engine = physics and not no_engine and (
        children is None or bool({f.value for f in ENGINE_FAMILIES} & set(children)))
    out = {"builds": builds, "build_s": round(build_s, 1), "folds": folds, "total_low_s": round(total_lo, 1),
           "total_high_s": round(total_hi, 1), "workers": workers, "timings": timings.source,
           "engine_runs": engine_runs, "guessed": timings.guessed(families, bool(engine_runs) or later_engine),
           "timed_here": timed, "busy": busy}
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


def _outcome(diff: float | None) -> str | None:
    if diff is None:
        return None
    return "tie" if abs(diff) <= TIE_TOLERANCE else ("win" if diff > 0 else "loss")


def _composites(df: pd.DataFrame, cfg: LabConfig) -> dict[str, float | None]:
    """Role -> leaderboard composite (unrounded) of these score rows."""
    if df.empty:
        return {}
    d = df.copy()
    d["agent_id"] = d["role"]
    d["label"] = d["role"]
    d["family"] = d["role"]
    return {r["agent_id"]: r["composite"] for r in leaderboard(d, cfg.scoring_weights)}


def best_start(run_dir: Path, initial: list[AgentGenome]) -> tuple[AgentGenome | None, float | None]:
    """ADR-125: the best untrained starting agent of a fold, the one among ``initial`` (the families' starting
    settings, before any seeded mutants) that ranked highest on the fold's training winters in round 1, and its
    composite there."""
    hashes = {g.genome_hash: g for g in initial}
    for row in load_round(run_dir, 1)["leaderboard"]["ranked"]:
        if row["genome_hash"] in hashes:
            return hashes[row["genome_hash"]], row.get("composite")
    return None, None


def _fold_result(paths: LabPaths, cfg: LabConfig, key: str, label: str, fold_run: str, winner: AgentGenome,
                 checked: AgentGenome | None, opts: TrainOptions, workers: int, start: AgentGenome | None = None,
                 trained_composite: float | None = None, incumbent: AgentGenome | None = None,
                 start_trained_composite: float | None = None) -> tuple[dict, pd.DataFrame]:
    """Score the fold's winner, standard SNOWPACK, the checked genome (a promotion check) and the best untrained
    starting agent (a winter test) on the fold's held-out cases; ``key`` is the held-out season or group.
    ``incumbent``: the SNOWPACK bar (default: the default SNOWPACK genome, ADR-068)."""
    case_set = f"loso_{key}"
    cases = select_cases(paths, case_set, ["holdout"], opts.plots, opts.case_types,
                         weather_sources=opts.weather_sources)
    incumbent = incumbent or default_genome(AgentFamily.snowpack, cfg.genome)
    roles = {"evolved": winner, "incumbent": incumbent} | ({"start": start} if start else {})
    genomes = list({g.genome_hash: g for g in (*roles.values(), *([checked] if checked else []))}.values())
    out = {"season": label, "seasons": holdout_group(key), "fold_run": fold_run, "holdout_cases": len(cases),
           "winner": {"agent_id": winner.agent_id, "label": winner.display_name, "family": winner.family.value,
                      "genome_hash": winner.genome_hash},
           "incumbent": {"agent_id": incumbent.agent_id, "label": incumbent.display_name,
                         "genome_hash": incumbent.genome_hash}}
    if start:
        out["start"] = {"agent_id": start.agent_id, "label": start.display_name, "family": start.family.value,
                        "genome_hash": start.genome_hash}
    if trained_composite is not None:
        out["winner_training_composite"] = trained_composite  # on the winters it trained on (the fold's last round)
    if start_trained_composite is not None:
        out["start_training_composite"] = start_trained_composite  # the untrained start on the same winters (round 1)
    if not cases:
        return out | {"outcome": "no_cases"}, pd.DataFrame()
    lib = build_library(paths, case_set, TrainingCache(paths.outputs / "cache"), workers,
                        exclude_seasons=opts.exclude_seasons or ())
    ctx = EvalContext(paths=paths, case_set=case_set, seed=opts.seed, weights=cfg.scoring_weights,
                      config_hash=cfg.config_hash(), engine=opts.engine, library_file=lib)
    res = evaluate_population(case_refs(cases), genomes, ctx, workers)
    # ADR-126: this scoring's times join the computer's running means like a round's; it is where a training
    # without SNOWPACK agents runs SNOWPACK (the bar), so where its estimate learns what an engine run takes
    timings = load_timings(paths, ctx.cache.timings)
    timings.update(res.worker_stats)
    save_timings(timings, ctx.cache.timings)
    df = res.scores
    board = {r["agent_id"]: r for r in rank_agents(df, cfg.scoring_weights, genomes)}
    w, i = board[winner.agent_id], board[incumbent.agent_id]
    wc, ic = w["composite_exact"], i["composite_exact"]
    diff = None if wc is None or ic is None else wc - ic
    out |= {"winner_composite": w["composite"], "incumbent_composite": i["composite"],
            "difference": None if diff is None else round(diff, 4), "outcome": _outcome(diff),
            "winner_is_incumbent": winner.genome_hash == incumbent.genome_hash,
            "eval": res.summary(), "seasons_trained": sorted({m.season for _, m in select_cases(
                paths, case_set, ["training"], opts.plots, opts.case_types,
                weather_sources=opts.weather_sources)} - set(opts.exclude_seasons or []))}
    if start:
        out["start_composite"] = board[start.agent_id]["composite"]
    if checked:
        out["checked_genome_in_sample"] = {"agent_id": checked.agent_id,
                                           "composite": board[checked.agent_id]["composite"],
                                           "note": "in-sample: the checked genome was trained on this season"}
    # one copy of the score rows per role (an agent can hold two roles: a fold that kept SNOWPACK as its winner)
    frames = []
    for role, g in roles.items():
        part = df[df["genome_hash"] == g.genome_hash].copy()
        part["role"] = role
        frames.append(part)
    df = pd.concat(frames, ignore_index=True)
    df["fold_season"] = label
    out["winters"] = winter_rows(df, cfg)
    return out, df


def winter_rows(holdout: pd.DataFrame, cfg: LabConfig) -> list[dict]:
    """ADR-125: per held-out winter, each role's composite there and the evolved agent's difference from SNOWPACK
    and from its untrained start (positive: the trained agent is better)."""
    rows = []
    for season, d in holdout.groupby("season", sort=True):
        c = _composites(d, cfg)
        ev, inc, st = c.get("evolved"), c.get("incumbent"), c.get("start")
        diff = None if ev is None or inc is None else ev - inc
        gain = None if ev is None or st is None else ev - st
        rows.append({"season": str(season), "cases": int(d["case_id"].nunique()),
                     "fold": str(d["fold_season"].iloc[0]) if "fold_season" in d else None,
                     "evolved": None if ev is None else round(ev, 4),
                     "snowpack": None if inc is None else round(inc, 4),
                     "start": None if st is None else round(st, 4),
                     "difference": None if diff is None else round(diff, 4), "outcome": _outcome(diff),
                     "training_gain": None if gain is None else round(gain, 4)})
    return rows


def spread(values: list[float], seed: int = 0) -> dict:
    """ADR-125: the average of per-winter differences, how much they vary (standard deviation, lowest, highest),
    how many winters were better or worse, and a 90% range of the average from resampling the winters."""
    import numpy as np

    v = np.array([x for x in values if x is not None], dtype=float)
    if not len(v):
        return {"winters": 0}
    rng = np.random.default_rng(seed)
    boot = rng.choice(v, size=(BOOTSTRAP_N, len(v)), replace=True).mean(axis=1) if len(v) > 1 else v
    return {"winters": int(len(v)), "mean": round(float(v.mean()), 4),
            "sd": round(float(v.std(ddof=1)), 4) if len(v) > 1 else 0.0,
            "lowest": round(float(v.min()), 4), "highest": round(float(v.max()), 4),
            "better": int((v > TIE_TOLERANCE).sum()), "worse": int((v < -TIE_TOLERANCE).sum()),
            "range90": [round(float(np.percentile(boot, 5)), 4), round(float(np.percentile(boot, 95)), 4)]}


def pooled_result(folds: list[dict], holdout: pd.DataFrame, cfg: LabConfig) -> dict:
    # ADR-125: wins and losses count held-out winters (a fold of one winter is one winter, as before)
    winters = [w for f in folds for w in (f["winters"] if len(f.get("winters") or []) > 1 else
                                          [{"outcome": f.get("outcome")}])]
    scored = [w for w in winters if w.get("outcome") in ("win", "loss", "tie")]
    n = len(scored)
    losses = sum(w["outcome"] == "loss" for w in scored)
    wins = sum(w["outcome"] == "win" for w in scored)
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
    out = {"seasons": n, "wins": wins, "losses": losses, "ties": sum(w["outcome"] == "tie" for w in scored),
           "pooled_evolved_composite": ev, "pooled_incumbent_composite": inc,
           "pooled_difference": None if ev is None or inc is None else round(ev - inc, 4),
           "pooled_cases": int(holdout["case_id"].nunique()) if len(holdout) else 0,
           "pooled_beats_incumbent": beats, "loses_on_majority": not majority_ok,
           "passed": bool(beats and majority_ok), "rule": RULE,
           "pooled_components": {k: {c: pooled.get(k, {}).get(c) for c in
                                     ("snow_depth", "layer_structure", "critical_layers", "uncertainty",
                                      "robustness", "depth_mae_m")} for k in ("evolved", "incumbent")}}
    if "start" in pooled:
        st = pooled["start"].get("composite")
        out["pooled_start_composite"] = st
        out["pooled_training_gain"] = None if ev is None or st is None else round(ev - st, 4)
        out["pooled_components"]["start"] = {c: pooled["start"].get(c) for c in out["pooled_components"]["evolved"]}
    walls = [w for f in folds for w in f.get("round_wall_s") or []]
    if walls:  # the computing time of the training rounds and of the held-out scoring, whatever was resumed
        later = [w for f in folds for w in (f.get("round_wall_s") or [])[1:]] or walls
        out["work_s"] = round(sum(walls) + sum((f.get("eval") or {}).get("wall_s") or 0.0 for f in folds), 1)
        out["round_s_median"] = round(float(pd.Series(later).median()), 1)
    if any(f.get("winters") for f in folds):
        rows = sorted((w for f in folds for w in f.get("winters") or []), key=lambda w: w["season"])
        out["per_winter"] = rows
        out["vs_snowpack"] = spread([w["difference"] for w in rows])
        if any(w.get("training_gain") is not None for w in rows):
            out["training_gain"] = spread([w["training_gain"] for w in rows])
        # What training added on the winters it trained on (each fold's winner against the same fold's untrained
        # start, averaged over the folds) and on all the held-out pits: against the same start, so winters being
        # easier or harder cancels out. The difference is what was memorised.
        seen = [f["winner_training_composite"] - f["start_training_composite"] for f in folds
                if None not in (f.get("winner_training_composite"), f.get("start_training_composite"))]
        if seen and out.get("pooled_training_gain") is not None:
            out["gain_on_training_winters"] = round(sum(seen) / len(seen), 4)
            out["memorising_gap"] = round(out["gain_on_training_winters"] - out["pooled_training_gain"], 4)
    return out


def seeded_saw(plan: dict, seasons: list[str]) -> list[str]:
    """ADR-097: the held-out ``seasons`` that agents seeding the run (ADR-085) had already trained or been selected on.
    A fold re-runs the training from the run's initial population, seeds included, so it cannot hold those out."""
    seen = {s for x in plan.get("seeded_from") or [] for s in x.get("seasons", [])}
    return sorted(seen & set(seasons))


def check_loso(paths: LabPaths, cfg: LabConfig, genome_ref: str | None, opts: TrainOptions | None = None, *,
               workers: int = 1, check_id: str | None = None, source: Path = Path("."),
               seasons: list[str] | None = None, log: Callable[[str], None] = print,
               estimate_only: bool = False, overrides: dict | None = None, folds: int | None = None,
               exclude_locked: bool = False, random_winters: int | None = None,
               draw_seed: int | None = None) -> CheckResult:
    """See the module doc. ``opts`` are the training options for a genome file; a ``run/round/rank`` reference
    takes the training run's own options (same seed). ``overrides`` (rounds, population, ...) replace options in
    either case: a cheaper, weaker check, recorded in the result as differing from the training run. ``seasons``
    limits the held-out winters (default: every season of the training cases).

    ADR-125, the winter test: ``genome_ref`` None tests the training procedure ``opts`` itself (nothing is checked
    in-sample); ``folds`` groups the held-out winters (``fold_groups``); ``exclude_locked`` keeps the
    ``LOCKED_SEASONS`` most recent winters out of every fold's training, held-out winters and analogue library.

    ADR-165: ``random_winters`` N holds out N winters drawn at random (``draw_winters``) instead of a fixed list,
    with draw number ``draw_seed`` (a new one when None); the draw is in the plan and the result, so the check can be
    repeated exactly. Never drawn: the locked test winters, anything later, and winters kept out of the test."""
    if random_winters is not None and seasons:
        raise ValueError("give either the held-out winters or a random draw, not both")
    t0 = time.time()
    checked, plan = resolve_genome(paths, genome_ref, cfg) if genome_ref else (None, None)
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
    # a fold holds its own season out; locking more seasons inside a fold would change the procedure (ADR-083)
    opts = TrainOptions(**(opts.__dict__ | {"case_set": "all", "splits": None, "monitor_season": None,
                                            "locked_seasons": 0}))
    from snowagent.lab.genome import default_genomes

    if opts.initial is None:
        opts.initial = default_genomes(cfg.genome)
    all_cases = select_cases(paths, "all", None, opts.plots, opts.case_types,
                             weather_sources=opts.weather_sources)
    if not all_cases:
        raise ValueError("no training cases in case set 'all' (build them with snowagent lab build-cases)")
    locked: list[str] = []
    if exclude_locked:  # ADR-125: the locked test winters stay unseen by every fold. They are the lab's most
        # recent winters whatever plots or case types this test is limited to, so they are always the same ones
        locked = split_locked(select_cases(paths, "all"), LOCKED_SEASONS)[2]
        opts.exclude_seasons = sorted(set(opts.exclude_seasons or []) | set(locked))
    out_s = set(opts.exclude_seasons or [])
    draw: dict | None = None
    if random_winters is not None:
        avail = plan["seasons"] if plan is not None else sorted({m.season for _, m in all_cases})
        pool, locked_all = check_draw_pool(paths, plan, avail, excluded=sorted(out_s))
        draw_seed = new_draw_seed() if draw_seed is None else int(draw_seed)
        seasons = draw_winters(pool, int(random_winters), draw_seed)
        draw = {"winters": int(random_winters), "seed": draw_seed, "pool": pool, "drawn": seasons,
                "never_drawn": locked_all}
        log(f"held-out winters drawn at random (draw {draw_seed}, {len(seasons)} of {len(pool)} that can be held "
            f"out): {', '.join(seasons)}; never drawn: {', '.join(locked_all) or 'none'} (locked test winters)")
    seasons = seasons or sorted({m.season for _, m in all_cases} - out_s)
    if bad := sorted(set(seasons) & out_s):
        raise ValueError(f"{', '.join(bad)} cannot be held out: kept out of the test (the locked test winters)")
    groups = fold_groups(seasons, folds)
    # the bar: a promotion check's is the default SNOWPACK genome (ADR-068), or the run's standard model when it has
    # one (ADR-134); a winter test's is the best standard SNOWPACK (ADR-118's ``LOCKED_REFERENCE``), which a training
    # without SNOWPACK agents is already measured against
    bar = named_genome(LOCKED_REFERENCE, cfg.genome) if genome_ref is None else run_standard(plan, cfg.genome)
    keys = ["+".join(g) for g in groups]
    labels = fold_labels(groups)
    if plan is not None and (seen := seeded_saw(plan, seasons)):
        raise ValueError(
            f"this run started from agents of {', '.join(sorted({x['run_id'] for x in plan['seeded_from']}))}, which "
            f"had already trained on {', '.join(seen)}: every fold would start from them, so holding those winters "
            "out would not be a fair test. Check an agent of the earlier run instead (ADR-097)")
    ref = reference_rounds(training_root(paths) / genome_ref.strip("/").split("/")[0], workers) \
        if plan is not None and not reduced.get("rounds") and not reduced.get("population") else None
    est = estimate_check(paths, cfg, opts, keys, workers, ref, bar, log=log)
    if reduced:
        log("note: this check trains with other options than the training run ("
            + ", ".join(f"{k} {v['check']} instead of {v['training_run']}" for k, v in reduced.items())
            + "): it checks that cheaper procedure, a weaker test of the run")
    if locked:
        log(f"kept out of every fold (the locked test winters): {', '.join(locked)}")
    lo_t, hi_t = fmt_s(est["total_low_s"]), fmt_s(est["total_high_s"])
    log(f"{'winter test' if genome_ref is None else 'check-loso'} estimate ({est['timings']} timings, {workers} "
        f"workers): {len(keys)} folds"
        + (f" ({len(seasons)} winters dealt out in turn, so each fold's winters are spread over the years)"
           if len(keys) < len(seasons) else "")
        + f", {len(est['builds'])} case sets to build ({fmt_s(est['build_s'])}), total about "
        + (lo_t if lo_t == hi_t else f"{lo_t}-{hi_t} (later rounds between the cheapest and the dearest family)")
        + (f"; expected about {fmt_s(est['total_expected_s'])} from the measured rounds of "
           f"{est['expected_from']['run_id']}" if est.get("total_expected_s") else ""))
    if est["guessed"]:  # ADR-126: say so rather than pass another machine's number off as this computer's
        log(guess_note(est["guessed"]))
    if estimate_only:
        return CheckResult("", Path(), [], {"estimate": est})
    plan_c = {"check_version": CHECK_VERSION, "genome": checked.model_dump(mode="json") if checked else None,
              "genome_ref": genome_ref,
              "seasons": seasons, "rounds": opts.rounds, "population": opts.population, "survivors": opts.survivors,
              "mutation_strength": opts.mutation_strength, "crossover_share": opts.crossover_share,
              "seed": opts.seed, "plots": opts.plots, "case_types": opts.case_types,
              "initial": [g.model_dump(mode="json") for g in opts.initial], "max_redraws": opts.max_redraws,
              "gap_flag_rounds": opts.gap_flag_rounds, "gap_tolerance": opts.gap_tolerance,
              "config_hash": cfg.config_hash(), "rule": RULE, "differs_from_training_run": reduced,
              "scoring_version": scoring.SCORING_VERSION}  # a check never resumes under another scoring (ADR-074)
    if draw:  # ADR-165 (a check with a fixed list of winters keeps its old plan, so its hash is unchanged)
        plan_c["draw"] = draw
    if genome_ref is None or len(keys) < len(seasons) or out_s:  # ADR-125 (an older check's plan has none of these)
        plan_c |= {"kind": WINTER_TEST if genome_ref is None else "promotion", "folds": groups,
                   "bar": bar.display_name if bar else "snowpack-default",
                   "excluded_seasons": sorted(out_s), "selection": opts.selection,
                   "drift_penalty": opts.drift_penalty, "screen_cases": opts.screen_cases,
                   "family_slots": opts.family_slots, "weather_sources": opts.weather_sources}
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
    _status(cdir, state="running", phase="building", seasons=labels, done=[])
    tb = time.time()
    if build_missing(paths, cfg, keys, source, workers, log):  # the time it took, summed over a resumed test's parts
        _status(cdir, build_s=round((_read_status(cdir).get("build_s") or 0.0) + time.time() - tb, 1))
    done, frames = [], []
    n = len(keys)
    for k, (key, label) in enumerate(zip(keys, labels, strict=True), 1):
        held = key.replace("+", ", ")
        f = cdir / "folds" / f"{label}.json"
        if f.is_file():
            done.append(json.loads(f.read_text()))
            hp = cdir / "folds" / f"{label}.parquet"
            if hp.is_file():
                frames.append(pd.read_parquet(hp))
            log(f"fold {k}/{n} {label}: done earlier ({done[-1].get('outcome')})")
            continue
        _status(cdir, state="running", phase=f"fold {k}/{n}: training without {held}", season=label,
                done=[x["season"] for x in done])
        fold_run = f"{check_id}-{label}"
        fopts = TrainOptions(**(opts.__dict__ | {"case_set": f"loso_{key}", "splits": ["training"]}))
        log(f"fold {k}/{n}: training without {held} (run {fold_run})")
        exists = (training_root(paths) / fold_run / "run.json").is_file()
        tr = run_training(paths, cfg, fopts, workers=workers, run_id=fold_run, resume=exists,
                          log=lambda m, s=label: log(f"  [{s}] {m}"))
        last = load_round(tr.run_dir, fopts.rounds)
        top_row = last["leaderboard"]["ranked"][0]
        winner = next(AgentGenome.model_validate(x["genome"]) for x in last["population"]
                      if AgentGenome.model_validate(x["genome"]).genome_hash == top_row["genome_hash"])
        start, start_tc = best_start(tr.run_dir, opts.initial) if genome_ref is None else (None, None)
        _status(cdir, state="running", phase=f"fold {k}/{n}: scoring {held}", season=label,
                done=[x["season"] for x in done])
        out, df = _fold_result(paths, cfg, key, label, fold_run, winner, checked, opts, workers, start=start,
                               trained_composite=top_row.get("composite") if genome_ref is None else None,
                               incumbent=bar, start_trained_composite=start_tc)
        # how long the fold's rounds took, from the rounds' own records (so a resumed fold counts all of them)
        out["round_wall_s"] = [round(float(load_round(tr.run_dir, r)["round"]["wall_s"]), 1)
                               for r in committed_rounds(tr.run_dir)]
        f.parent.mkdir(parents=True, exist_ok=True)
        if len(df):
            df.to_parquet(cdir / "folds" / f"{label}.parquet", index=False)
            frames.append(df)
        tmp = f.with_suffix(".tmp")
        tmp.write_text(json.dumps(out, indent=1, default=str))
        os.replace(tmp, f)
        done.append(out)
        log(f"fold {label}: {out.get('holdout_cases')} held-out cases; winner {out['winner']['label']} "
            f"{out.get('winner_composite')} vs SNOWPACK {out.get('incumbent_composite')}"
            + (f" (untrained start {out['start_composite']})" if out.get("start_composite") is not None else "")
            + f" -> {out.get('outcome')}")
    holdout = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if len(holdout):
        holdout.to_parquet(cdir / "holdout_scores.parquet", index=False)
    result = pooled_result(done, holdout, cfg) | {
        "check_id": check_id, "genome_ref": genome_ref, "checked_agent_id": checked.agent_id if checked else None,
        "kind": plan_c.get("kind", "promotion"), "folds": len(keys), "excluded_seasons": sorted(out_s),
        "differs_from_training_run": reduced, "rounds": opts.rounds, "population": opts.population,
        "held_out": seasons, "draw": plan_c.get("draw"), "label": LAB_DISCLAIMER,
        "wall_s": round(time.time() - t0, 1), "build_s": _read_status(cdir).get("build_s"),
        "per_season": [{k: f.get(k) for k in ("season", "holdout_cases", "outcome", "winner_composite",
                                              "incumbent_composite", "difference")}
                       | {"winner": f["winner"]["label"], "winner_family": f["winner"]["family"]} for f in done]}
    tmp = cdir / "result.tmp"
    tmp.write_text(json.dumps(result, indent=1, default=str))
    os.replace(tmp, cdir / "result.json")
    reg = RunRegistry(paths.registry)
    if reg.get(check_id) is None:
        reg.record(RunManifest(
            run_id=check_id, kind=RunKind.evolution, status="ok", created_at=created, finished_at=datetime.now(UTC),
            config_hash=cfg.config_hash(), data_hash=hashlib.sha256(json.dumps(
                sorted(f["fold_run"] for f in done)).encode()).hexdigest(),
            software_version=software_version(), git_commit=git_commit(Path(__file__).parent), seed=opts.seed,
            scoring_weights=cfg.scoring_weights, splits={"loso": seasons},
            agent_ids=[checked.agent_id] if checked else [],
            genome_hashes={checked.agent_id: checked.genome_hash} if checked else {},
            outputs=[str(cdir / "result.json")], counts={"folds": len(done), "wins": result["wins"],
                                                         "losses": result["losses"], "passed": int(result["passed"])},
            warnings=[] if result["passed"] else (
                ["promotion check failed: the evolved agent stays research only"] if checked else
                ["the trained agents did not beat SNOWPACK on the winters they never saw"]),
            runtime_s=result["wall_s"]))
    _status(cdir, state="finished", phase="finished", done=[f["season"] for f in done])
    return CheckResult(check_id, cdir, done, result)


def _read_status(cdir: Path) -> dict:
    f = cdir / "status.json"
    return json.loads(f.read_text()) if f.is_file() else {}


def _status(cdir: Path, **kw) -> None:
    f = cdir / "status.json"
    old = _read_status(cdir)
    tmp = f.with_suffix(".tmp")
    tmp.write_text(json.dumps(old | kw | {"updated_at": datetime.now(UTC).isoformat(), "pid": os.getpid()},
                              indent=1))
    os.replace(tmp, f)


def check_kind(paths: LabPaths, check_id: str) -> str:
    """``promotion`` or ``winter_test`` (ADR-125; a check whose plan names no kind is a promotion check)."""
    try:
        plan = json.loads((checks_root(paths) / check_id / "check.json").read_text())["plan"]
    except (OSError, ValueError, KeyError):
        return "promotion"
    return plan.get("kind") or "promotion"


def list_checks(paths: LabPaths, kind: str | None = None) -> list[str]:
    """Checks newest first; ``kind`` (``promotion`` or ``winter_test``) keeps only that kind."""
    if kind is not None:
        return [c for c in list_checks(paths) if check_kind(paths, c) == kind]
    root = checks_root(paths)
    if not root.is_dir():
        return []
    def started(d: Path) -> str:
        try:
            return str(json.loads((d / "check.json").read_text()).get("created_at") or "")
        except (OSError, ValueError):
            return ""

    # newest first, so pages open on the latest check (a name order put "...-loso-reduced-3s" before a later
    # "...-loso-reduced")
    dirs = [d for d in root.iterdir() if (d / "check.json").is_file()]
    return [d.name for d in sorted(dirs, key=lambda d: (started(d), d.name), reverse=True)]


def load_check(paths: LabPaths, check_id: str) -> dict:
    d = checks_root(paths) / check_id
    out = {"check": json.loads((d / "check.json").read_text()),
           "folds": [json.loads(f.read_text()) for f in sorted((d / "folds").glob("*.json"))]
           if (d / "folds").is_dir() else []}
    for name in ("result", "status"):
        f = d / f"{name}.json"
        out[name] = json.loads(f.read_text()) if f.is_file() else None
    return out


__all__ = ["RULE", "WINTER_TEST", "CheckResult", "EngineSpec", "build_seconds", "check_draw_pool", "check_kind",
           "check_loso", "draw_pool", "draw_winters", "fold_groups", "new_draw_seed", "list_checks", "load_check", "resolve_genome", "seeded_saw", "spread"]
