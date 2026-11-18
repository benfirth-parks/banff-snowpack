"""The column model (ADR-116): the lab's own snowpack model, no SNOWPACK. It predicts every synthetic case in the
contract from the visible case only, starts from the latest pit, conserves mass, makes crusts from melt and rain,
facets under a strong gradient, and is opt-in in training (default populations and family slots unchanged)."""

from __future__ import annotations

import json

import numpy as np
import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.lab.agents import ColumnAgent, make_agent  # noqa: E402
from snowagent.lab.agents import column as colmod  # noqa: E402
from snowagent.lab.agents.common import latest_season_pit, pit_snow_depth, stamp_prediction  # noqa: E402
from snowagent.lab.benchmark import builder  # noqa: E402
from snowagent.lab.benchmark.loader import case_dirs, load_visible_case, read_manifest  # noqa: E402
from snowagent.lab.genome import default_genome, default_genomes  # noqa: E402
from snowagent.lab.schemas.genome import STANDARD_FAMILIES, AgentFamily  # noqa: E402
from snowagent.lab.settings import load_lab_config  # noqa: E402
from snowagent.lab.storage.paths import LabPaths  # noqa: E402
from snowagent.lab.training.loop import fill_initial, slot_families  # noqa: E402
from tests.unit.lab_fixtures import write_synthetic_lab  # noqa: E402
from tests.unit.test_lab_benchmark import CONFIG  # noqa: E402


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("column")
    cfg = load_lab_config(CONFIG)
    paths = LabPaths(tmp / "lab")
    write_synthetic_lab(paths.root, tmp / "checkout", cfg)
    builder.build_cases(paths, cfg, tmp / "checkout", exclude_flagged=True)
    return {d.name: d for d in case_dirs(paths)}


def _genes(**kw):
    g = default_genome(AgentFamily.column)
    return g.model_copy(update={"genes": g.genes | kw})


def test_column_predicts_every_case_in_the_contract(built):
    agent = make_agent(default_genome(AgentFamily.column))
    assert isinstance(agent, ColumnAgent)
    ok = 0
    for name, d in built.items():
        case = load_visible_case(d)
        p = agent.predict(case, seed=1)
        assert p.case_id == case.case_key and p.agent_id == agent.agent_id
        if p.status == "ok":
            ok += 1
            hs = p.bulk_state.snow_depth_m
            assert 0 <= hs.p10 <= hs.p50 <= hs.p90
            tops = [ly.top_depth_m.p50 for ly in p.layers]
            assert tops == sorted(tops) and all(ly.bottom_depth_m.p50 <= hs.p50 + 1e-6 for ly in p.layers)
            assert p.model_metadata["family"] == "column"
        stamp_prediction(p, read_manifest(d))
        text = json.dumps(p.model_dump(mode="json"))
        assert "bow_summit_syn" not in text and "2024-" not in text and "2023-" not in text, name
    assert ok


def test_column_starts_from_the_latest_pit(built):
    case = load_visible_case(built["BOW_20240110T1900Z_NP"])
    pit = latest_season_pit(case)
    col, notes = colmod.simulate(case, default_genome(AgentFamily.column).genes)
    assert notes["start"] == "pit"
    pit_codes = {ly.grain_primary for ly in pit.layers}
    assert pit_codes & set(col.code)  # pit layers carried with their grain forms
    assert col.hs > 0.5 * pit_snow_depth(pit)


def test_more_precipitation_means_deeper_snow_without_a_sensor(built):
    case = load_visible_case(built["BOW_20240110T1900Z_H72"])
    lo = colmod.simulate(case, _genes(col_precip_mult_bow=0.6, col_sensor_add_m=0.08).genes)[0].hs
    hi = colmod.simulate(case, _genes(col_precip_mult_bow=1.6, col_sensor_add_m=0.08).genes)[0].hs
    assert hi >= lo


def _snow(n=20, T=-5.0, rho=200.0):
    c = colmod.Column.empty()
    for _ in range(n):
        c.append(0.03, 0.03 * rho, T, "RG", "RG", new=False)
    return c


def test_rain_on_cold_snow_refreezes_into_a_crust_and_keeps_mass():
    g = default_genome(AgentFamily.column).genes
    c = _snow()
    m0 = c.ice.sum() + c.water.sum()
    colmod._percolate(g, c, 6.0)  # 6 mm of rain on -5 degC snow
    assert c.ice.sum() + c.water.sum() == pytest.approx(m0 + 6.0)
    assert c.frz[-1] > 0
    for _ in range(48):  # the held water refreezes as the snow cools again
        colmod._conduct(g, c, -8.0, 0.0)
        colmod._phase(c)
    assert c.water[-1] == pytest.approx(0.0, abs=1e-6)
    colmod._metamorphism(g, c, -8.0, 0.0)
    assert c.cls[-1] == "MFcr"


def test_strong_gradient_makes_facets_weak_gradient_does_not():
    g = default_genome(AgentFamily.column).genes
    strong = _snow(n=10)
    strong.T = np.linspace(-1.0, -12.0, 10)  # 0.3 m, about 37 K/m
    weak = _snow(n=10)
    weak.T = np.linspace(-4.0, -5.0, 10)
    for _ in range(400):
        colmod._metamorphism(g, strong, -13.0, 0.0)
        colmod._metamorphism(g, weak, -5.0, -4.0)
    assert "FC" in strong.cls or "DH" in strong.cls
    assert not ({"FC", "DH"} & set(weak.cls))


def test_conduction_relaxes_towards_the_boundaries():
    g = default_genome(AgentFamily.column).genes
    c = _snow(n=10, T=-10.0)
    for _ in range(24 * 20):
        colmod._conduct(g, c, -10.0, 0.0)
    assert c.T[0] > c.T[-1] and -10.0 <= c.T.min() and c.T.max() <= 0.0


def test_column_is_opt_in_for_training():
    assert AgentFamily.column not in STANDARD_FAMILIES
    assert AgentFamily.column not in {g.family for g in default_genomes()}
    assert slot_families(None) == list(STANDARD_FAMILIES)
    assert slot_families([default_genome(AgentFamily.column)])[-1] == AgentFamily.column
    filled = fill_initial([default_genome(AgentFamily.column)], 6, 0, 0.3, None)
    assert len(filled) == 6 and len({g.genome_hash for g in filled}) == 6
    assert all(g.family == AgentFamily.column for g in filled)
    assert fill_initial([default_genome(AgentFamily.column)], 6, 0, 0.3, None) == filled  # seeded


def test_a_run_without_snowpack_agents_shows_standard_snowpack_on_the_locked_winters():
    from snowagent.lab.genome import named_genome
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.training.loop import LOCKED_REFERENCE, TrainOptions, _extensions, locked_best

    cfg = load_lab_config(CONFIG)
    col = TrainOptions.from_config(cfg, initial=[default_genome(AgentFamily.column)])
    assert _extensions(col, [], (["2025-2026"], ["c1"]))["locked_reference"] == LOCKED_REFERENCE
    named_genome(LOCKED_REFERENCE)  # a standard model the lab knows
    std = TrainOptions.from_config(cfg)
    assert "locked_reference" not in _extensions(std, [], (["2025-2026"], ["c1"]))
    assert "locked_reference" not in _extensions(col, [], ([], []))
    info = {"locked_test": {"agents": [{"composite": 0.5}, {"composite": 0.6, "reference": True}]}}
    assert locked_best(info) == 0.5  # the bar does not count as the run's own best
