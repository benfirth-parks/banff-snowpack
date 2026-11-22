"""Focused selection (ADR-135): next-pit forecasts, spring pits and crusts weigh more when survivors are chosen; the
composite, reports and locked winters are unchanged, and a plan without the key selects as before."""

from __future__ import annotations

import pandas as pd
import pytest

from snowagent.lab.genome import default_genome
from snowagent.lab.schemas.genome import AgentFamily
from snowagent.lab.training.loop import TrainOptions, _opts_from_plan, focus_weights, rank_agents

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")


def _row(agent: str, case: str, case_type: str, v: float, crust_obs: int = 0, crust_found: int = 0) -> dict:
    return {"case_id": case, "agent_id": agent, "family": "process", "label": agent, "season": "2020-2021",
            "site_code": "BOW", "case_type": case_type, "status": "ok", "composite": v, "runtime_s": 0.0,
            "robustness": 1.0, "snow_depth": v, "layer_structure": v, "critical_layers": v, "uncertainty": v,
            "wl_crust_observed": crust_obs, "wl_crust_found": crust_found}


def test_focus_weights_count_next_pit_spring_and_crusts():
    df = pd.DataFrame([_row("a", "BOW_20210115T1800Z_H72", "forecast_h72", 0.5),
                       _row("a", "BOW_20210320T1800Z_NP", "next_pit", 0.5, crust_obs=2)])
    assert focus_weights(df, 1.0).tolist() == [1.0, 4.0]
    assert focus_weights(df, 0.5).tolist() == [1.0, 2.5]


def test_focus_picks_the_agent_better_at_the_weak_spots():
    from snowagent.lab.settings import load_lab_config

    w = load_lab_config().scoring_weights
    g = [default_genome(AgentFamily.process), default_genome(AgentFamily.column)]
    winter, spring = g[0].agent_id, g[1].agent_id
    cases = [("BOW_20210115T1800Z_H72", "forecast_h72")] * 1 + [("BOW_20210410T1800Z_NP", "next_pit")]
    cases = [(f"{c}{i}", t) for i, (c, t) in enumerate(cases * 3)]
    rows = []
    for c, t in cases:
        is_spring = t == "next_pit"
        rows.append(_row(winter, c, t, 0.75 if not is_spring else 0.52, 1 if is_spring else 0, 0))
        rows.append(_row(spring, c, t, 0.58 if not is_spring else 0.60, 1 if is_spring else 0, 1 if is_spring else 0))
    df = pd.DataFrame(rows)
    plain = rank_agents(df, w, g)
    focused = rank_agents(df, w, g, focus=1.0)
    assert plain[0]["agent_id"] == winter and "focus_score" not in plain[0]
    assert focused[0]["agent_id"] == spring
    assert focused[0]["composite_exact"] == pytest.approx(
        next(r for r in plain if r["agent_id"] == spring)["composite_exact"])  # the composite itself is unchanged
    assert focused[0]["selection_score"] == focused[0]["focus_score"]


def test_focus_is_off_by_default_validated_and_kept_on_resume():
    from snowagent.lab.settings import load_lab_config

    cfg = load_lab_config()
    assert TrainOptions.from_config(cfg).focus == 0.0
    with pytest.raises(ValueError, match="focus"):
        TrainOptions.from_config(cfg, focus=5.0).validate()
    plan = {"rounds": 1, "population": 4, "survivors": 2, "mutation_strength": 0.2, "crossover_share": 0.25,
            "seed": 0, "plots": None, "case_types": None, "case_set": "all", "splits": None, "initial": [],
            "monitor_season": None, "gap_flag_rounds": 3, "gap_tolerance": 0.0, "max_redraws": 100,
            "engine": {"kind": "fake"}}
    assert _opts_from_plan(plan).focus == 0.0
    assert _opts_from_plan(plan | {"focus": 1.0}).focus == 1.0
