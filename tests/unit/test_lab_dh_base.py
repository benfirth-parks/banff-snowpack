"""Depth hoar only at the base of the snowpack (ADR-154, owner rule 2026-11-24): fixed switches, off by default so
every saved genome keeps its id and predictions; on, facets higher than the base stay facets (column, process,
process_melt) and SNOWPACK's depth hoar higher up is reported as facets."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.lab.agents import column as colmod  # noqa: E402
from snowagent.lab.agents.common import DH_BASE_M  # noqa: E402
from snowagent.lab.agents.process import genes_of  # noqa: E402
from snowagent.lab.agents.snowpack import EngineLayer, dh_at_base  # noqa: E402
from snowagent.lab.genome import crossover, default_genome, make_genome, mutate, named_genome  # noqa: E402
from snowagent.lab.schemas.genome import AgentFamily  # noqa: E402


def _ready_facets(n=30, dz=0.05):
    """A column of dry facets ready to become depth hoar, 1.5 m deep (bottom element first)."""
    c = colmod.Column.empty()
    for _ in range(n):
        c.append(dz, dz * 250.0, -3.0, "FC", "FC", fac=1.0, new=True)
    c.dhp[:] = 1.0
    return c


def _grains(genes: dict) -> list[str]:
    c = _ready_facets()
    colmod.grain_changes(genes, c, np.ones(len(c), dtype=bool), np.ones(len(c)))
    return list(c.cls)


@pytest.mark.parametrize("family", [AgentFamily.column, AgentFamily.process, AgentFamily.process_melt])
def test_switch_is_off_by_default_and_leaves_ids_unchanged(family):
    g = default_genome(family)
    assert g.genes["col_dh_base"] == "off" and g.genes["col_dh_base_m"] == DH_BASE_M
    legacy = make_genome(family, {k: v for k, v in g.genes.items()
                                  if k not in ("col_dh_base", "col_dh_base_m", "col_dh_base_frac")})
    assert legacy.genome_hash == g.genome_hash
    assert make_genome(family, dict(g.genes) | {"col_dh_base": "on"}).genome_hash != g.genome_hash


def test_snowpack_switch_is_off_by_default_and_leaves_ids_unchanged():
    g = named_genome("snowpack-era5-fix")
    assert g.genes["sp_dh_base"] == "off"
    legacy = make_genome(AgentFamily.snowpack, {k: v for k, v in g.genes.items()
                                                if k not in ("sp_dh_base", "sp_dh_base_m", "sp_dh_base_frac")})
    assert legacy.genome_hash == g.genome_hash


def test_off_lets_depth_hoar_form_anywhere():
    assert set(_grains(genes_of(default_genome(AgentFamily.process)))) == {"DH"}


def test_on_keeps_depth_hoar_at_the_base():
    g = genes_of(default_genome(AgentFamily.process)) | {"col_dh_base": "on"}
    cls = _grains(g)
    bottoms = np.arange(len(cls)) * 0.05
    assert all(c == "DH" for c, b in zip(cls, bottoms, strict=True) if b <= DH_BASE_M)
    assert all(c == "FC" for c, b in zip(cls, bottoms, strict=True) if b > DH_BASE_M)
    cls_low = _grains(g | {"col_dh_base_m": 0.2})
    assert cls_low.count("DH") == 5  # bottoms at 0, 5, 10, 15, 20 cm
    cls_frac = _grains(g | {"col_dh_base_frac": 0.1})
    assert cls_frac.count("DH") == 4  # lowest 10 % of 1.5 m: bottoms at 0, 5, 10, 15 cm


def test_in_a_thin_pack_only_the_bottom_half_is_base():
    g = genes_of(default_genome(AgentFamily.process)) | {"col_dh_base": "on"}
    c = _ready_facets(n=8)  # 40 cm of snow: base is its bottom 20 cm
    colmod.grain_changes(g, c, np.ones(len(c), dtype=bool), np.ones(len(c)))
    assert list(c.cls) == ["DH"] * 5 + ["FC"] * 3


def test_observed_depth_hoar_is_left_as_drawn():
    g = genes_of(default_genome(AgentFamily.process)) | {"col_dh_base": "on"}
    c = _ready_facets()
    c.cls[-1], c.code[-1] = "DH", "DH"  # the pit's own depth hoar near the top
    colmod.grain_changes(g, c, np.ones(len(c), dtype=bool), np.ones(len(c)))
    assert c.cls[-1] == "DH"


def test_switch_is_never_mutated_or_crossed():
    base = default_genome(AgentFamily.process)
    on = make_genome(AgentFamily.process, dict(base.genes) | {"col_dh_base": "on"})
    for seed in range(20):
        assert mutate(on, 1.0, seed).genes["col_dh_base"] == "on"
        assert mutate(base, 1.0, seed).genes["col_dh_base"] == "off"
        assert crossover(on, base, seed).genes["col_dh_base"] == "on"


def test_snowpack_depth_hoar_above_the_base_is_reported_as_facets():
    hs = 1.5
    layers = [EngineLayer(top_depth_m=0.0, bottom_depth_m=0.1, grain="DH", hardness_index=1.0),  # at the surface
              EngineLayer(top_depth_m=0.1, bottom_depth_m=0.9, grain="RG", hardness_index=3.0),
              EngineLayer(top_depth_m=0.9, bottom_depth_m=1.2, grain="DH", hardness_index=2.0),  # bottom 0.3 m up
              EngineLayer(top_depth_m=1.2, bottom_depth_m=1.5, grain="DH", hardness_index=2.0)]
    assert dh_at_base(layers, hs, {}) == layers
    out = dh_at_base(layers, hs, {"sp_dh_base": "on"})
    assert [ly.grain for ly in out] == ["FC", "RG", "DH", "DH"]
    assert out[0].hardness_index == 1.0
    out = dh_at_base(layers, hs, {"sp_dh_base": "on", "sp_dh_base_m": 0.2})
    assert [ly.grain for ly in out] == ["FC", "RG", "FC", "DH"]
    out = dh_at_base(layers, hs, {"sp_dh_base": "on", "sp_dh_base_frac": 0.1})  # base 0.15 m
    assert [ly.grain for ly in out] == ["FC", "RG", "FC", "DH"]
