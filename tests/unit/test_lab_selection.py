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


def test_drift_counts_how_far_settings_moved_and_a_change_must_pay_for_itself():
    """ADR-089: drift is the summed share of each gene's range moved from the family default (a changed choice 1);
    with the penalty a slightly better agent that moved many settings ranks below a plainer one."""
    from snowagent.lab.genome import make_genome
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.training.loop import drift

    cfg = load_lab_config()
    spec, w = cfg.genome, cfg.scoring_weights
    base = default_genome(AgentFamily.snowpack, spec)
    assert drift(base, spec) == 0.0
    fam = spec.family_genes(AgentFamily.snowpack)
    _, wind = fam["sp_wind_mult"]
    num = make_genome(AgentFamily.snowpack, base.genes | {"sp_wind_mult": wind.min}, spec)
    assert drift(num, spec) == pytest.approx((wind.default - wind.min) / (wind.max - wind.min))
    _, dens = fam["sp_hn_density"]
    other = next(c for c in dens.choices if c != dens.default)
    both = make_genome(AgentFamily.snowpack, num.genes | {"sp_hn_density": other}, spec)
    assert drift(both, spec) == pytest.approx(drift(num, spec) + 1.0)

    df = pd.DataFrame([r | {"agent_id": a} for a, v in ((base.agent_id, 0.500), (both.agent_id, 0.501))
                       for r in _rows(a, {("2020-2021", "BOW"): v})])
    plain = rank_agents(df, w, [base, both])
    assert plain[0]["agent_id"] == both.agent_id and "drift" not in plain[0]
    pen = rank_agents(df, w, [base, both], drift_k=0.002, spec=spec)
    assert pen[0]["agent_id"] == base.agent_id
    row = next(x for x in pen if x["agent_id"] == both.agent_id)
    assert row["drift"] == pytest.approx(drift(both, spec))
    assert row["selection_score"] == pytest.approx(row["composite_exact"] - 0.002 * row["drift"])


def test_new_runs_have_a_drift_penalty_and_old_plans_none():
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.training.loop import DRIFT_K, _extensions

    cfg = load_lab_config()
    o = TrainOptions.from_config(cfg)
    assert o.drift_penalty == DRIFT_K > 0
    assert _extensions(o, [], ([], []))["drift_penalty"] == DRIFT_K
    assert "drift_penalty" not in _extensions(TrainOptions.from_config(cfg, drift_penalty=0.0), [], ([], []))
    with pytest.raises(ValueError, match="drift"):
        TrainOptions.from_config(cfg, drift_penalty=0.5).validate()
    plan = {"rounds": 1, "population": 4, "survivors": 2, "mutation_strength": 0.2, "crossover_share": 0.25,
            "seed": 0, "plots": None, "case_types": None, "case_set": "all", "splits": None, "initial": [],
            "monitor_season": None, "gap_flag_rounds": 3, "gap_tolerance": 0.0, "max_redraws": 100,
            "engine": {"kind": "fake"}}
    assert _opts_from_plan(plan).drift_penalty == 0.0  # a run started before ADR-089 resumes unchanged
    assert _opts_from_plan(plan | {"drift_penalty": 0.004}).drift_penalty == 0.004
