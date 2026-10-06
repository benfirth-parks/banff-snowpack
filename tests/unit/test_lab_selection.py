"""Consistency selection (ADR-087): survivors are chosen by the composite less a penalty for uneven results across
winters and plots; older plans keep selecting on the composite."""

from __future__ import annotations

import pandas as pd
import pytest

from snowagent.lab.genome import default_genome
from snowagent.lab.schemas.genome import AgentFamily
from snowagent.lab.training.loop import CONSISTENCY_K, TrainOptions, _opts_from_plan, rank_agents, spreads

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")


def _rows(agent: str, by_group: dict[tuple[str, str], float], n: int = 4) -> list[dict]:
    return [{"case_id": f"{agent}-{s}-{p}-{i}", "agent_id": agent, "family": "snowpack", "label": agent,
             "season": s, "site_code": p, "case_type": "next_pit", "status": "ok", "composite": v,
             "runtime_s": 0.0, "robustness": 1.0, "snow_depth": v, "layer_structure": v, "critical_layers": v,
             "uncertainty": v}
            for (s, p), v in by_group.items() for i in range(n)]


def test_spread_ignores_how_hard_a_winter_is_and_penalises_uneven_agents():
    from snowagent.lab.settings import load_lab_config

    w = load_lab_config().scoring_weights
    g = [default_genome(AgentFamily.snowpack), default_genome(AgentFamily.hybrid)]
    even, uneven = g[0].agent_id, g[1].agent_id
    groups = [("2020-2021", "BOW"), ("2021-2022", "BOW"), ("2020-2021", "GOAT"), ("2021-2022", "GOAT")]
    hard = {k: (0.40 if k[0] == "2021-2022" else 0.60) for k in groups}  # one winter is harder for everyone
    df = pd.DataFrame(_rows(even, {k: v + 0.02 for k, v in hard.items()})  # a little better everywhere
                      + _rows(uneven, {k: v + (0.12 if k == groups[0] else 0.0) for k, v in hard.items()}))
    ref = {k: v for k, v in hard.items()}  # standard SNOWPACK's group means: the yardstick
    sp = spreads(df, ref)
    assert sp[even] == pytest.approx(0.0) and sp[uneven] > 0.03
    by_avg = rank_agents(df, w, g)
    by_even = rank_agents(df, w, g, "consistent", ref)
    assert by_avg[0]["agent_id"] == uneven  # a bigger average from one big win
    assert by_even[0]["agent_id"] == even and by_even[0]["selection_score"] == pytest.approx(
        by_even[0]["composite_exact"] - CONSISTENCY_K * sp[even])
    assert "spread" not in by_avg[0]
    assert spreads(df[df["season"] == "2020-2021"].query("site_code == 'BOW'"), ref) == {even: 0.0, uneven: 0.0}
    assert set(spreads(df)) == {even, uneven}  # without a yardstick: the agents' own mean


def test_new_runs_select_on_consistency_and_old_plans_keep_the_composite():
    from snowagent.lab.settings import load_lab_config

    cfg = load_lab_config()
    assert TrainOptions.from_config(cfg).selection == "consistent"
    assert TrainOptions.from_config(cfg, selection="composite").selection == "composite"
    with pytest.raises(ValueError, match="selection"):
        TrainOptions.from_config(cfg, selection="best").validate()
    plan = {"rounds": 1, "population": 4, "survivors": 2, "mutation_strength": 0.2, "crossover_share": 0.25,
            "seed": 0, "plots": None, "case_types": None, "case_set": "all", "splits": None, "initial": [],
            "monitor_season": None, "gap_flag_rounds": 3, "gap_tolerance": 0.0, "max_redraws": 100,
            "engine": {"kind": "fake"}}
    assert _opts_from_plan(plan).selection == "composite"  # a run started before ADR-087 resumes unchanged
    assert _opts_from_plan(plan | {"selection": "consistent"}).selection == "consistent"
