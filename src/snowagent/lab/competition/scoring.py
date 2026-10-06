"""Scoring a prediction against the withheld pit (ADR-064; docs/lab/agents_and_scoring.md).

Five components in [0, 1] (1 = perfect), combined with the frozen weights of ``config/lab.yaml`` (``scoring``):

- ``snow_depth``: exp(-|p50 - observed| / 0.15 m): how close the middle estimate is, nothing else (ADR-074). Whether
  the observed depth lies inside p10..p90 is recorded as the diagnostic ``depth_covered`` but never scored: range
  quality belongs to ``uncertainty``, whose interval score charges the width. (Scoring version 1 added 0.25 x that
  coverage, a bonus without a width cost that evolution exploited by widening the ranges.)
- ``layer_structure`` (full-profile targets): 0.5 x ordered layer match F1 + 0.3 x grain agreement + 0.2 x hardness
  agreement. Both columns are compared on RELATIVE depth (depth / own snow depth) so a depth error is not counted
  twice. Ordered match: the longest order-preserving pairing of predicted and observed layers of the same major grain
  class (first two letters of the IACS code) whose mid-depths lie within 0.15 relative depth; F1 = 2 x pairs /
  (predicted + observed layers). Grain and hardness agreement: at 20 relative depths, the share of equal major
  classes, and 1 - |hardness index difference| / 2 (floored at 0).
- ``critical_layers`` (full-profile targets): soft critical success index over layers of concern (surface hoar,
  facets, depth hoar, crusts). A predicted layer of concern matches an observed one of the same class within 0.15
  relative depth (each used once, nearest first): hits = sum of matched presence probabilities, misses = unmatched
  observed + sum (1 - p) of matched, false alarms = sum of p of unmatched predicted; CSI = hits / (hits + misses +
  false alarms). With no observed layer of concern the score is 1 / (1 + false alarms).
- ``uncertainty``: 0.5 x (1 - Brier score of the four class-present events; a class's probability is
  1 - prod(1 - p) over its predicted layers) + 0.5 x exp(-interval score / 0.5 m) of the p10..p90 snow-depth
  interval (alpha 0.2: width + 10 x the miss). Depth-only targets: the interval part alone.
- ``robustness``: per case 1 if the agent answered (status ok), 0 if it returned insufficient data; on the
  leaderboard (1 - failure rate) x min(1, P10 / mean of the case composites), so an agent that fails or collapses on
  some cases loses it.

Components a target cannot verify (structure on a depth-only pit) are NaN and the other weights are renormalised.
An ``insufficient_data`` answer scores 0 on every component it would have been scored on. A case the agent could
not run on (``AgentUnavailable``) is not scored; the leaderboard counts it as skipped.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np

from snowagent.lab.ingest.mapping import CONCERN_CLASSES
from snowagent.lab.schemas.benchmark import TargetScope
from snowagent.lab.schemas.prediction import SnowpackPrediction
from snowagent.lab.schemas.profile import CriticalClass, SnowProfile
from snowagent.lab.schemas.run import ScoringWeights

SCORING_VERSION = "lab-scoring-2"  # 2: snow_depth without the coverage bonus (ADR-074)
COMPONENTS = ("snow_depth", "layer_structure", "critical_layers", "uncertainty")  # per case; robustness on top
DEPTH_SCALE_M = 0.15
REL_TOL = 0.15
SLICES = (np.arange(20) + 0.5) / 20
INTERVAL_ALPHA = 0.2
INTERVAL_SCALE_M = 0.5
EVENT_CLASSES = (CriticalClass.surface_hoar, CriticalClass.facets, CriticalClass.depth_hoar, CriticalClass.crust)
HARD_BASE = {"F": 1.0, "4F": 2.0, "1F": 3.0, "P": 4.0, "K": 5.0, "I": 6.0}
HARD_RE = re.compile(r"^(4F|1F|F|P|K|I)([+-]?)$")
# Every key ``score_case`` (and the case composite) adds to a score row: what a re-score replaces, keeping the rest
# of the row (status, runtimes, engine provenance). ``target_scope`` is part of the row before scoring.
SCORE_KEYS = frozenset({
    "observed_depth_m", "predicted_depth_m", "robustness", "depth_error_m", "depth_covered", "interval_score_m",
    "snow_depth", "uncertainty", "layer_structure", "critical_layers", "match_f1", "grain_agreement",
    "hardness_agreement", "observed_concern", "predicted_concern", "concern_matched", "brier", "predicted_layers",
    "observed_layers", "composite"})


@dataclass(frozen=True)
class Col:
    """A layer on relative depth: top, bottom (0 = surface, 1 = ground), major grain class, hardness, class, p."""

    top: float
    bottom: float
    major: str
    hardness: float | None
    critical: CriticalClass
    prob: float = 1.0

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2


def hardness_index(code: str | None) -> float | None:
    if not code:
        return None
    m = HARD_RE.match(code.strip())
    if not m:
        return None
    return HARD_BASE[m[1]] + {"+": 1 / 3, "-": -1 / 3, "": 0.0}[m[2]]


def major_class(grain: str | None) -> str:
    return grain[:2] if grain and grain != "UNKNOWN" else "?"


def truth_depth(truth: SnowProfile) -> float | None:
    if truth.snow_depth_m is not None and truth.snow_depth_m > 0:
        return float(truth.snow_depth_m)
    if truth.layers:
        return float(max(ly.bottom_depth_m for ly in truth.layers))
    return None


def truth_columns(truth: SnowProfile) -> list[Col]:
    hs = truth_depth(truth) or 0.0
    if hs <= 0:
        return []
    return sorted((Col(ly.top_depth_m / hs, min(ly.bottom_depth_m / hs, 1.0), major_class(ly.grain_primary),
                       ly.hardness_index if ly.hardness_index is not None else hardness_index(ly.hardness),
                       ly.critical_class) for ly in truth.layers), key=lambda c: c.top)


def predicted_columns(pred: SnowpackPrediction) -> list[Col]:
    hs = pred.bulk_state.snow_depth_m.p50 if pred.bulk_state else 0.0
    if hs <= 0:
        return []
    return sorted((Col(ly.top_depth_m.p50 / hs, min(ly.bottom_depth_m.p50 / hs, 1.0),
                       major_class(ly.grain_form[0] if ly.grain_form else None), hardness_index(ly.hardness),
                       ly.critical_class, ly.probability_present) for ly in pred.layers), key=lambda c: c.top)


def ordered_match(pred: list[Col], obs: list[Col], tol: float = REL_TOL) -> int:
    """Longest order-preserving pairing of same-class layers within ``tol`` relative depth (dynamic programming)."""
    n, m = len(pred), len(obs)
    dp = np.zeros((n + 1, m + 1), dtype=int)
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            ok = pred[i - 1].major == obs[j - 1].major and pred[i - 1].major != "?" and abs(
                pred[i - 1].mid - obs[j - 1].mid) <= tol
            dp[i, j] = max(dp[i - 1, j], dp[i, j - 1], dp[i - 1, j - 1] + 1 if ok else 0)
    return int(dp[n, m])


def _at(cols: list[Col], z: float) -> Col | None:
    for c in cols:
        if c.top <= z < c.bottom or (z == 1.0 and c.bottom >= 1.0):
            return c
    return None


def layer_structure(pred: list[Col], obs: list[Col]) -> dict[str, float]:
    f1 = 2 * ordered_match(pred, obs) / (len(pred) + len(obs)) if pred or obs else 1.0
    grain, hard = [], []
    for z in SLICES:
        o, p = _at(obs, z), _at(pred, z)
        if o is None or o.major == "?":
            continue
        grain.append(1.0 if p is not None and p.major == o.major else 0.0)
        if o.hardness is not None:
            hard.append(max(0.0, 1 - abs(p.hardness - o.hardness) / 2) if p is not None and p.hardness is not None
                        else 0.0)
    parts = {"f1": (0.5, f1), "grain": (0.3, float(np.mean(grain)) if grain else math.nan),
             "hardness": (0.2, float(np.mean(hard)) if hard else math.nan)}
    use = {k: v for k, v in parts.items() if not math.isnan(v[1])}
    score = sum(w * s for w, s in use.values()) / sum(w for w, _ in use.values())
    return {"score": score, "match_f1": f1, "grain_agreement": parts["grain"][1],
            "hardness_agreement": parts["hardness"][1]}


def critical_layers(pred: list[Col], obs: list[Col], tol: float = REL_TOL) -> dict[str, float]:
    po = [c for c in pred if c.critical in CONCERN_CLASSES]
    oo = [c for c in obs if c.critical in CONCERN_CLASSES]
    pairs = sorted(((abs(p.mid - o.mid), i, j) for i, p in enumerate(po) for j, o in enumerate(oo)
                    if p.critical == o.critical and abs(p.mid - o.mid) <= tol))
    used_p, used_o = set(), set()
    for _, i, j in pairs:
        if i not in used_p and j not in used_o:
            used_p.add(i)
            used_o.add(j)
    hits = sum(po[i].prob for i in used_p)
    misses = (len(oo) - len(used_o)) + sum(1 - po[i].prob for i in used_p)
    false = sum(c.prob for i, c in enumerate(po) if i not in used_p)
    score = 1 / (1 + false) if not oo else hits / (hits + misses + false) if hits + misses + false > 0 else 0.0
    return {"score": score, "observed_concern": len(oo), "predicted_concern": len(po), "matched": len(used_p),
            "false_alarm_weight": false}


def class_probabilities(pred: list[Col]) -> dict[str, float]:
    out = {}
    for cls in EVENT_CLASSES:
        q = 1.0
        for c in pred:
            if c.critical == cls:
                q *= 1 - c.prob
        out[cls.value] = 1 - q
    return out


def interval_score(p10: float, p90: float, y: float, alpha: float = INTERVAL_ALPHA) -> float:
    return (p90 - p10) + 2 / alpha * max(0.0, p10 - y) + 2 / alpha * max(0.0, y - p90)


def score_case(pred: SnowpackPrediction, truth: SnowProfile, target_scope: TargetScope) -> dict:
    """Component scores and their diagnostics for one prediction (already stamped with the case id)."""
    hs_t = truth_depth(truth)
    full = target_scope == TargetScope.full_profile and bool(truth.layers)
    out: dict = {"status": pred.status, "observed_depth_m": hs_t, "target_scope": target_scope.value}
    if pred.status != "ok" or pred.bulk_state is None:
        out |= {"snow_depth": 0.0 if hs_t is not None else math.nan, "uncertainty": 0.0 if hs_t is not None else
                math.nan, "layer_structure": 0.0 if full else math.nan, "critical_layers": 0.0 if full else math.nan,
                "robustness": 0.0}
        return out
    q = pred.bulk_state.snow_depth_m
    out["predicted_depth_m"] = q.p50
    out["robustness"] = 1.0
    if hs_t is None:
        out |= {"snow_depth": math.nan, "uncertainty": math.nan}
    else:
        err = q.p50 - hs_t
        covered = q.p10 <= hs_t <= q.p90
        isc = interval_score(q.p10, q.p90, hs_t)
        out |= {"depth_error_m": err, "depth_covered": covered, "interval_score_m": isc,
                "snow_depth": math.exp(-abs(err) / DEPTH_SCALE_M)}  # covered: diagnostic only (ADR-074)
        sharp = math.exp(-isc / INTERVAL_SCALE_M)
        out["uncertainty"] = sharp
    if not full:
        out |= {"layer_structure": math.nan, "critical_layers": math.nan}
        return out
    pc, oc = predicted_columns(pred), truth_columns(truth)
    ls = layer_structure(pc, oc)
    cl = critical_layers(pc, oc)
    probs = class_probabilities(pc)
    present = {c.value: float(any(o.critical == c for o in oc)) for c in EVENT_CLASSES}
    brier = float(np.mean([(probs[k] - present[k]) ** 2 for k in probs]))
    out |= {"layer_structure": ls["score"], "match_f1": ls["match_f1"], "grain_agreement": ls["grain_agreement"],
            "hardness_agreement": ls["hardness_agreement"], "critical_layers": cl["score"],
            "observed_concern": cl["observed_concern"], "predicted_concern": cl["predicted_concern"],
            "concern_matched": cl["matched"], "brier": brier, "predicted_layers": len(pc), "observed_layers": len(oc)}
    if not math.isnan(out.get("uncertainty", math.nan)):
        out["uncertainty"] = 0.5 * (1 - brier) + 0.5 * out["uncertainty"]
    else:
        out["uncertainty"] = 1 - brier
    return out


def case_composite(scores: dict, weights: ScoringWeights) -> float:
    """Weighted mean of the per-case components present (robustness is applied on the leaderboard)."""
    w = weights.model_dump()
    use = [(w[k], scores[k]) for k in COMPONENTS if scores.get(k) is not None and not math.isnan(scores[k])]
    tot = sum(a for a, _ in use)
    return sum(a * s for a, s in use) / tot if tot > 0 else math.nan


def robustness(case_composites: list[float], failures: int) -> float:
    """(1 - failure rate) x min(1, P10 / mean) of the case composites (failures included as zeros)."""
    xs = np.array([x for x in case_composites if not math.isnan(x)], dtype=float)
    n = len(xs)
    if n == 0:
        return 0.0
    mean = float(xs.mean())
    stab = min(1.0, float(np.percentile(xs, 10)) / mean) if mean > 0 else 0.0
    return (1 - failures / n) * max(0.0, stab)


def leaderboard_composite(mean_case_composite: float, robust: float, weights: ScoringWeights) -> float:
    return (1 - weights.robustness) * mean_case_composite + weights.robustness * robust
