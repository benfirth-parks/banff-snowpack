"""Scoring a prediction against the withheld pit (ADR-064, ADR-074): depth from the middle estimate only (coverage a
diagnostic), ordered layer match, critical-layer CSI (ADR-088), Brier and interval sharpness, robustness; depth-only
targets and insufficient answers."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta

import pytest

from snowagent.lab.competition import scoring
from snowagent.lab.schemas.benchmark import TargetScope
from snowagent.lab.schemas.prediction import (
    BulkState,
    Confidence,
    PredictedLayer,
    Quantiles,
    SnowpackPrediction,
)
from snowagent.lab.schemas.profile import CriticalClass, SnowLayer, SnowProfile
from snowagent.lab.settings import load_lab_config
from tests.unit.test_lab_benchmark import CONFIG

T0 = datetime(2024, 1, 10, 19, tzinfo=UTC)


# --------------------------------------------------------------------------------------------- scoring


def _truth(layers, hs=1.0) -> SnowProfile:
    return SnowProfile(profile_id="p", site_code="BOW", plot_id="bow_summit", observed_at=T0, source_id="t",
                       snow_depth_m=hs, layers=[SnowLayer(
                           layer_id=f"l{i}", profile_id="p", top_depth_m=t, bottom_depth_m=b, grain_primary=g,
                           hardness_index=h, critical_class=c, is_layer_of_concern=c in (
                               CriticalClass.facets, CriticalClass.surface_hoar, CriticalClass.crust,
                               CriticalClass.depth_hoar), concern_basis=["grain_class:x"] if c in (
                               CriticalClass.facets, CriticalClass.surface_hoar, CriticalClass.crust,
                               CriticalClass.depth_hoar) else [])
                           for i, (t, b, g, h, c) in enumerate(layers)])


def _pred(layers, hs=1.0, spread=0.1, status="ok") -> SnowpackPrediction:
    if status != "ok":
        return SnowpackPrediction(case_id="c", agent_id="a", issued_at=T0, valid_at=T0 + timedelta(hours=72),
                                  site_code="BOW", scenario={"name": "Study plot", "slope_deg": 0.0},
                                  status="insufficient_data", insufficient_data_reason="x",
                                  confidence=Confidence(overall=0))
    q = lambda x: Quantiles(p10=max(0, x - 0.01), p50=x, p90=x + 0.01)  # noqa: E731
    return SnowpackPrediction(
        case_id="c", agent_id="a", issued_at=T0, valid_at=T0 + timedelta(hours=72), site_code="BOW",
        scenario={"name": "Study plot", "slope_deg": 0.0},
        bulk_state=BulkState(snow_depth_m=Quantiles(p10=hs - spread, p50=hs, p90=hs + spread)),
        layers=[PredictedLayer(name=f"L{i}", top_depth_m=q(t), bottom_depth_m=q(b), grain_form=[g], hardness=hc,
                               probability_present=p, critical_class=c, confidence=0.7)
                for i, (t, b, g, hc, c, p) in enumerate(layers)],
        confidence=Confidence(overall=0.7))


TRUTH = [(0.0, 0.3, "DF", 1.0, CriticalClass.other), (0.3, 0.32, "SH", 1.0, CriticalClass.surface_hoar),
         (0.32, 0.7, "RG", 3.0, CriticalClass.other), (0.7, 1.0, "FC", 2.0, CriticalClass.facets)]
PERFECT = [(0.0, 0.3, "DF", "F", CriticalClass.other, 1.0), (0.3, 0.32, "SH", "F", CriticalClass.surface_hoar, 1.0),
           (0.32, 0.7, "RG", "1F", CriticalClass.other, 1.0), (0.7, 1.0, "FC", "4F", CriticalClass.facets, 1.0)]


def test_a_perfect_prediction_scores_near_one_and_a_bad_one_lower():
    s = scoring.score_case(_pred(PERFECT, spread=0.02), _truth(TRUTH), TargetScope.full_profile)
    assert s["snow_depth"] == pytest.approx(1.0) and s["layer_structure"] == pytest.approx(1.0)
    assert s["critical_layers"] == pytest.approx(1.0) and s["brier"] == pytest.approx(0.0)
    assert s["uncertainty"] > 0.95
    bad = [(0.0, 1.0, "PP", "F", CriticalClass.other, 1.0)]
    b = scoring.score_case(_pred(bad, hs=1.4, spread=0.05), _truth(TRUTH), TargetScope.full_profile)
    assert b["snow_depth"] < 0.1 and b["layer_structure"] < 0.3 and b["critical_layers"] == 0.0
    w = load_lab_config(CONFIG).scoring_weights
    assert scoring.case_composite(s, w) > 0.95 > scoring.case_composite(b, w)


def test_snow_depth_scores_the_middle_estimate_only_and_coverage_is_a_diagnostic():
    """ADR-074: snow_depth = exp(-|p50 - observed| / 0.15 m); a wider p10..p90 range that now covers the observed
    depth does not raise it, and the uncertainty score (interval score) charges the extra width."""
    t = _truth([], hs=1.2)
    narrow = scoring.score_case(_pred([], hs=1.0, spread=0.05), t, TargetScope.depth_only)
    wide = scoring.score_case(_pred([], hs=1.0, spread=0.5), t, TargetScope.depth_only)
    assert scoring.SCORING_VERSION == "lab-scoring-3"
    assert narrow["depth_covered"] is False and wide["depth_covered"] is True  # recorded, never scored
    assert narrow["snow_depth"] == pytest.approx(math.exp(-0.2 / scoring.DEPTH_SCALE_M))
    assert wide["snow_depth"] == narrow["snow_depth"]
    exact = scoring.score_case(_pred([], hs=1.2, spread=0.3), t, TargetScope.depth_only)
    assert exact["snow_depth"] == pytest.approx(1.0)
    # the interval score still judges the range: width plus 10 x the miss (alpha 0.2)
    assert narrow["interval_score_m"] == pytest.approx(0.1 + 10 * 0.15)
    assert wide["interval_score_m"] == pytest.approx(1.0)
    assert wide["uncertainty"] == pytest.approx(math.exp(-1.0 / scoring.INTERVAL_SCALE_M))


def test_score_keys_cover_every_score_field():
    """``SCORE_KEYS`` is what a re-score replaces: every field ``score_case`` adds besides status and scope."""
    w = load_lab_config(CONFIG).scoring_weights
    for s in (scoring.score_case(_pred(PERFECT), _truth(TRUTH), TargetScope.full_profile),
              scoring.score_case(_pred(PERFECT, hs=1.2), _truth([], hs=1.2), TargetScope.depth_only),
              scoring.score_case(_pred([], status="insufficient"), _truth(TRUTH), TargetScope.full_profile)):
        s["composite"] = scoring.case_composite(s, w)
        assert set(s) - {"status", "target_scope"} <= scoring.SCORE_KEYS


def test_ordered_match_is_order_preserving_and_tolerant():
    C = scoring.Col
    obs = [C(0, .3, "DF", 1, CriticalClass.other), C(.3, .7, "RG", 3, CriticalClass.other),
           C(.7, 1, "FC", 2, CriticalClass.facets)]
    swapped = [C(0, .3, "RG", 3, CriticalClass.other), C(.3, .7, "DF", 1, CriticalClass.other),
               C(.7, 1, "FC", 2, CriticalClass.facets)]
    assert scoring.ordered_match(obs, obs) == 3
    assert scoring.ordered_match(swapped, obs) == 1  # depth tolerance and order: only FC pairs
    shifted = [C(.1, .4, "DF", 1, CriticalClass.other)]
    assert scoring.ordered_match(shifted, obs) == 1


def test_critical_layers_version_2_soft_csi_and_false_alarms():
    """Scoring version 2 (runs started before ADR-088): hits, misses and false alarms weighted by probability."""
    C = scoring.Col
    v2 = "lab-scoring-2"
    obs = [C(.3, .32, "SH", 1, CriticalClass.surface_hoar)]
    hit = scoring.critical_layers([C(.28, .3, "SH", 1, CriticalClass.surface_hoar, prob=0.8)], obs, version=v2)
    assert hit["score"] == pytest.approx(0.8 / (0.8 + 0.2))
    miss_and_false = scoring.critical_layers([C(.8, .82, "SH", 1, CriticalClass.surface_hoar, prob=0.5)], obs,
                                             version=v2)
    assert miss_and_false["score"] == 0.0
    none_obs = scoring.critical_layers([C(.8, .82, "FC", 1, CriticalClass.facets, prob=0.5)], [], version=v2)
    assert none_obs["score"] == pytest.approx(1 / 1.5)
    with scoring.scoring_version(v2):  # what a training run started under version 2 scores with
        assert scoring.active_version() == v2
        assert scoring.critical_layers([C(.28, .3, "SH", 1, CriticalClass.surface_hoar, prob=0.8)], obs)["score"] \
            == pytest.approx(0.8)
    assert scoring.active_version() == scoring.SCORING_VERSION
    with pytest.raises(ValueError, match="unknown scoring version"), scoring.scoring_version("lab-scoring-1"):
        pass


def test_critical_layers_reward_finding_weak_layers_not_confidence():
    """ADR-088: a layer of concern is forecast when its probability is at least 0.5; beyond that, how sure the agent
    is earns nothing here (the Brier part of uncertainty judges it), so raising every probability cannot pay."""
    C = scoring.Col
    sh = CriticalClass.surface_hoar
    obs = [C(.3, .32, "SH", 1, sh), C(.6, .62, "FC", 1, CriticalClass.facets)]
    found = [C(.28, .3, "SH", 1, sh, prob=0.6)]
    sure = [C(.28, .3, "SH", 1, sh, prob=0.99)]
    assert scoring.critical_layers(found, obs)["score"] == scoring.critical_layers(sure, obs)["score"] == 0.5
    unsure = [C(.28, .3, "SH", 1, sh, prob=0.4)]  # below 0.5: not forecast, so the layer is a miss
    assert scoring.critical_layers(unsure, obs)["score"] == 0.0
    false = [C(.28, .3, "SH", 1, sh, prob=0.9), C(.9, .92, "SH", 1, sh, prob=0.9)]
    assert scoring.critical_layers(false, obs)["score"] == pytest.approx(1 / 3)
    assert scoring.critical_layers([C(.8, .82, "FC", 1, CriticalClass.facets, prob=0.5)], [])["score"] == 0.5
    assert scoring.critical_layers([C(.8, .82, "FC", 1, CriticalClass.facets, prob=0.3)], [])["score"] == 1.0
    # and over-confidence costs in uncertainty: a sure false alarm has a worse Brier score than a hesitant one
    t = _truth(TRUTH[:1])
    sure_false = scoring.score_case(_pred([(0.3, 0.32, "SH", "F", sh, 0.95)]), t, TargetScope.full_profile)
    unsure_false = scoring.score_case(_pred([(0.3, 0.32, "SH", "F", sh, 0.55)]), t, TargetScope.full_profile)
    assert sure_false["critical_layers"] == unsure_false["critical_layers"]
    assert sure_false["brier"] > unsure_false["brier"]


def test_depth_only_targets_and_insufficient_answers():
    t = _truth([], hs=1.2)
    s = scoring.score_case(_pred(PERFECT, hs=1.2), t, TargetScope.depth_only)
    assert math.isnan(s["layer_structure"]) and math.isnan(s["critical_layers"]) and s["snow_depth"] > 0.9
    w = load_lab_config(CONFIG).scoring_weights
    assert scoring.case_composite(s, w) == pytest.approx((w.snow_depth * s["snow_depth"] + w.uncertainty * s[
        "uncertainty"]) / (w.snow_depth + w.uncertainty))
    z = scoring.score_case(_pred([], status="insufficient"), _truth(TRUTH), TargetScope.full_profile)
    assert z["robustness"] == 0 and scoring.case_composite(z, w) == 0


def test_robustness_penalises_failures_and_collapses():
    assert scoring.robustness([0.6] * 10, 0) == pytest.approx(1.0)
    assert scoring.robustness([0.6] * 9 + [0.0], 1) == pytest.approx(0.9)  # one failure in ten
    assert scoring.robustness([0.6] * 8 + [0.0] * 2, 2) == 0.0  # the worst tenth collapsed
    assert scoring.robustness([], 0) == 0.0
