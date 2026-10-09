"""Weak layers by kind (ADR-091): how many of the observed surface hoar, facet, depth hoar and crust layers an agent
found, and how many it forecast that were not there. A diagnostic of the training report, never part of a score.

Rows scored since this change carry the counts (``scoring.concern_by_class``: ``wl_<kind>_observed|forecast|found``).
For a run scored before it, ``from_cache`` re-reads the two agents' stored predictions from the training cache and
counts them against the withheld pits, without running any agent; that works while the prediction code, the lab
config and the engine files are the ones the run used (else the cached predictions are not found and the report
leaves the section out).
"""

from __future__ import annotations

import json

import pandas as pd

from snowagent.lab.competition import scoring
from snowagent.lab.competition.truth import TruthNotScorable, scoring_truth
from snowagent.lab.genome import load_genome
from snowagent.lab.schemas.prediction import SnowpackPrediction
from snowagent.lab.storage.paths import LabPaths

KINDS = {"surface_hoar": "Surface hoar", "facets": "Facets", "depth_hoar": "Depth hoar", "crust": "Crusts"}
COLS = [f"wl_{k}_{x}" for k in KINDS for x in ("observed", "forecast", "found")]


def has_counts(df: pd.DataFrame) -> bool:
    return all(c in df.columns for c in COLS)


def counts(df: pd.DataFrame) -> dict[str, dict[str, int]]:
    """Per kind: observed layers, forecast layers, observed layers found (sums over the scored full-profile cases)."""
    out = {}
    for k in KINDS:
        o, f, h = (int(pd.to_numeric(df[f"wl_{k}_{x}"], errors="coerce").fillna(0).sum())
                   for x in ("observed", "forecast", "found"))
        out[k] = {"observed": o, "forecast": f, "found": h, "false": f - h}
    return out


def from_cache(paths: LabPaths, cfg, run_id: str, genome_hashes: list[str], locked: bool = False
               ) -> dict[str, pd.DataFrame] | None:
    """Per genome hash, the weak-layer counts per case (index case_id) from the run's cached predictions; None when
    the cache does not hold them all (another code or config since the run)."""
    from snowagent.lab.competition.runner import EngineSpec
    from snowagent.lab.training.cache import TrainingCache
    from snowagent.lab.training.evaluate import EvalContext, build_library
    from snowagent.lab.training.loop import TrainOptions, prepare, training_root

    run_dir = training_root(paths) / run_id
    try:
        _, plan, refs, locked_refs = prepare(paths, cfg, TrainOptions.from_config(cfg), run_id, resume=True)
        genomes = [load_genome(run_dir / "genomes" / f"{h}.json", cfg.genome, upgrade=False)
                   for h in dict.fromkeys(genome_hashes)]
    except (FileNotFoundError, KeyError, ValueError, json.JSONDecodeError):
        return None
    lib = build_library(paths, plan["case_set"], TrainingCache(paths.outputs / "cache"),
                        exclude_seasons=plan.get("locked_seasons") or ())
    ctx = EvalContext(paths=paths, case_set=plan["case_set"], seed=plan["seed"], weights=cfg.scoring_weights,
                      config_hash=cfg.config_hash(), engine=EngineSpec(**plan["engine"]), library_file=lib,
                      scoring_version=plan.get("scoring_version"))
    out: dict[str, list[dict]] = {g.genome_hash: [] for g in genomes}
    for ref in (locked_refs if locked else refs):
        try:
            truth = scoring_truth(ref.case_dir, ref.manifest).truth_profile
        except (TruthNotScorable, FileNotFoundError):
            continue
        if ref.manifest.target_scope.value != "full_profile" or not truth.layers:
            continue
        oc = scoring.truth_columns(truth)
        for g in genomes:
            entry = ctx.cache.get(ctx.key(g, ref))
            if entry is None:
                return None
            pred = entry.get("prediction")
            if pred is None or entry["row"].get("status") != "ok":
                row = {c: 0 for c in COLS} | {f"wl_{k}_observed": sum(1 for o in oc if o.critical.value == k)
                                              for k in KINDS}
            else:
                pc = scoring.predicted_columns(SnowpackPrediction.model_validate(pred))
                row = scoring.concern_by_class(pc, oc)
            out[g.genome_hash].append(row | {"case_id": ref.case_id})
    return {h: pd.DataFrame(rows).set_index("case_id") if rows else pd.DataFrame(columns=COLS)
            for h, rows in out.items()}


def _pct(x: int, n: int) -> str:
    return f"{100 * x / n:.0f}%" if n else "none observed"


def table(base: dict, best: dict, base_name: str) -> pd.DataFrame:
    """The plain table: per kind, how many were in the pits, and the share each agent found and its false alarms."""
    rows = []
    for k, label in KINDS.items():
        a, b = base[k], best[k]
        n = a["observed"]
        rows.append({"kind of layer": label, "in the pits": n, f"found by {base_name}": _pct(a["found"], n),
                     "found by the evolved agent": _pct(b["found"], n),
                     f"false alarms, {base_name}": a["false"], "false alarms, evolved agent": b["false"]})
    return pd.DataFrame(rows)
