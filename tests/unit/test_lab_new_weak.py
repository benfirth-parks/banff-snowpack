"""ADR-103: weak layers SNOWPACK forms in snow that fell since the last pit get a lower presence probability."""

from __future__ import annotations

import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.lab.agents import make_agent  # noqa: E402
from snowagent.lab.agents.common import latest_season_pit, pit_snow_depth  # noqa: E402
from snowagent.lab.agents.snowpack import (  # noqa: E402
    NEW_SNOW_FRAC,
    EngineLayer,
    EngineResult,
    FakeEngine,
    SnowpackAgent,
    new_snow_floor,
)
from snowagent.lab.benchmark.loader import load_visible_case  # noqa: E402
from snowagent.lab.genome import default_genome, named_genome, upgrade_genome  # noqa: E402
from snowagent.lab.schemas.genome import (  # noqa: E402
    GENOME_SCHEMA,
    LEGACY_BLOCKS,
    AgentFamily,
    AgentGenome,
    default_spec,
)
from snowagent.lab.services.names import nickname  # noqa: E402
from snowagent.lab.services.reports import plain_change  # noqa: E402
from tests.unit.test_lab_agents import built  # noqa: E402, F401

BLOCK = "snowpack_new_weak"


class ProfileEngine:
    """A fixed profile on top of the case's latest pit: 0.3 m of new snow with surface hoar at the top and facets
    10 cm into it, rounded old snow, depth hoar at the ground."""

    def simulate(self, case, physics=None) -> EngineResult:
        old = pit_snow_depth(latest_season_pit(case))
        hs = old + 0.3
        return EngineResult(source="fake", snow_depth_m=hs, snowpack_version="fake-0", layers=[
            EngineLayer(top_depth_m=0.0, bottom_depth_m=0.02, grain="SH", hardness_index=1.0, density_kg_m3=100),
            EngineLayer(top_depth_m=0.02, bottom_depth_m=0.15, grain="DF", hardness_index=1.0, density_kg_m3=120),
            EngineLayer(top_depth_m=0.15, bottom_depth_m=0.25, grain="FC", hardness_index=1.5, density_kg_m3=180),
            EngineLayer(top_depth_m=0.25, bottom_depth_m=hs - 0.1, grain="RG", hardness_index=3.0, density_kg_m3=300),
            EngineLayer(top_depth_m=hs - 0.1, bottom_depth_m=hs, grain="DH", hardness_index=2.0, density_kg_m3=250)])


def _pit_case(built):  # noqa: F811
    for d in built.values():
        case = load_visible_case(d)
        p = latest_season_pit(case)
        if p is not None and (pit_snow_depth(p) or 0) > 0.4:
            return case
    pytest.skip("no synthetic case with a pit deeper than 40 cm")


def _snowpack(**change) -> AgentGenome:
    return AgentGenome.model_validate({"family": "snowpack",
                                       "genes": dict(default_genome(AgentFamily.snowpack).genes) | change})


def test_the_gene_is_a_snowpack_output_gene_whose_default_keeps_snowpacks_layers():
    spec = default_spec()
    assert spec.blocks[BLOCK]["sp_new_weak_factor"].default == 1.0
    assert BLOCK in spec.families[AgentFamily.snowpack] and BLOCK not in spec.families[AgentFamily.hybrid]
    now = default_genome(AgentFamily.snowpack)
    later = {g for b in LEGACY_BLOCKS["lab-genome-6"] for g in default_spec().blocks[b]}  # this block and any since
    old = AgentGenome.model_validate({"schema_version": "lab-genome-6", "family": "snowpack",
                                      "genes": {k: v for k, v in now.genes.items() if k not in later}})
    up = upgrade_genome(old)
    assert up.schema_version == GENOME_SCHEMA and up.genes["sp_new_weak_factor"] == 1.0
    assert up.parents == [old.genome_hash]


def test_weak_layers_in_new_snow_get_the_lower_probability_and_the_rest_keep_theirs(built):  # noqa: F811
    case = _pit_case(built)
    floor = new_snow_floor(case)
    assert floor == pytest.approx(NEW_SNOW_FRAC * pit_snow_depth(latest_season_pit(case)))
    p = SnowpackAgent(_snowpack(sp_new_weak_factor=0.4), ProfileEngine()).predict(case, 0)
    probs = {ly.grain_form[0]: ly.probability_present for ly in p.layers}
    assert probs["SH"] == pytest.approx(0.28) and probs["FC"] == pytest.approx(0.28)  # in the new snow
    assert probs["DF"] == probs["RG"] == probs["DH"] == pytest.approx(0.7)  # not weak, or old snow
    assert p.model_metadata["new_snow_weak_layers"] == 2
    assert any("since the last pit" in m for m in p.confidence.main_limits)


def test_the_default_genome_and_a_case_without_a_pit_are_unchanged(built):  # noqa: F811
    case = _pit_case(built)
    p = SnowpackAgent(default_genome(AgentFamily.snowpack), ProfileEngine()).predict(case, 0)
    assert {ly.probability_present for ly in p.layers} == {0.7}
    assert "new_snow_weak_layers" not in p.model_metadata
    agent = SnowpackAgent(_snowpack(sp_new_weak_factor=0.4), ProfileEngine())
    r = ProfileEngine().simulate(case)
    assert {ly.prob for ly in agent.layers_from(r, 0.7)} == {0.7}  # no case: nothing to compare with
    nopit = case.model_copy(update={"permitted_pits": []})
    assert new_snow_floor(nopit) is None
    assert {ly.prob for ly in agent.layers_from(r, 0.7, nopit)} == {0.7}


def test_the_hybrids_engine_member_keeps_snowpacks_layers():
    agent = make_agent(default_genome(AgentFamily.hybrid), backend=FakeEngine())
    assert agent.snowpack.genome.genes["sp_new_weak_factor"] == 1.0


def test_the_named_new_snow_check_model_is_the_snow_depth_model_with_one_change():
    g, base = named_genome("snowpack-new-snow-check"), named_genome("snowpack-snow-depth")
    assert {k for k in g.genes if g.genes[k] != base.genes[k]} == {"sp_new_weak_factor"}
    assert g.genes["sp_new_weak_factor"] * g.genes["presence_confidence"] == pytest.approx(0.28)
    assert nickname(g.genome_hash, g.label) == "SNOWPACK with sensors and new-snow check"
    assert "40%" in plain_change("sp_new_weak_factor", 1.0, 0.4)
    assert "as SNOWPACK draws them" in plain_change("sp_new_weak_factor", 1.0, 1.0)
