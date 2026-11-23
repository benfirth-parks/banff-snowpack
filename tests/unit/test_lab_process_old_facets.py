"""The process switches carry the column model's old-facets switch (ADR-142, the ADR-128 switch): fixed and off by
default, so every saved process genome keeps its id and predicts as before; on, the last pit's facets stay faceted
under the process model's own metamorphism."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.lab.agents import column as colmod  # noqa: E402
from snowagent.lab.agents.column import Hour  # noqa: E402
from snowagent.lab.agents.process import ProcessExt, genes_of  # noqa: E402
from snowagent.lab.genome import crossover, default_genome, make_genome, mutate, named_genome  # noqa: E402
from snowagent.lab.schemas.genome import AgentFamily  # noqa: E402


def _pit_facets(n=10, T=-0.5, rho=250.0):
    c = colmod.Column.empty()
    for _ in range(n):
        c.append(0.03, 0.03 * rho, T, "FC", "FC", hard0=2.0, rho0=rho, fac=1.0, new=False)
    return c


def _weak_gradient_hours(genome, c, hours=2000):
    g = genes_of(genome)
    ext = ProcessExt(g)
    hr = Hour(ta=-0.5, vw=1.0, rh=80.0, sw=0.0, lw=250.0, p=0.0, rain=0.0, dz_new=0.0, ts=-0.5, since_snow=100.0)
    for _ in range(hours):  # near 0 degC and no gradient: facets round back unless the switch keeps them
        c.T = np.full(len(c), -0.5)
        ext.metamorphism(g, c, -0.5, -0.5, hr)
    return c


@pytest.mark.parametrize("family", [AgentFamily.process, AgentFamily.process_melt])
def test_switch_is_off_by_default_and_leaves_ids_unchanged(family):
    g = default_genome(family)
    assert g.genes["col_old_facets"] == "off"
    legacy = make_genome(family, {k: v for k, v in g.genes.items() if k != "col_old_facets"})
    assert legacy.genome_hash == g.genome_hash  # a genome saved before ADR-142 keeps its id
    on = make_genome(family, dict(g.genes) | {"col_old_facets": "on"})
    assert on.genome_hash != g.genome_hash


def test_process_old_facets_keeps_the_pits_facets():
    off, on = default_genome(AgentFamily.process), named_genome("process-old-facets")
    assert on.genes["col_old_facets"] == "on"
    assert "RG" in _weak_gradient_hours(off, _pit_facets()).cls  # as before: they round back
    assert set(_weak_gradient_hours(on, _pit_facets()).cls) == {"FC"}


def test_switch_is_never_mutated_or_crossed():
    base = default_genome(AgentFamily.process)
    on = named_genome("process-old-facets")
    for seed in range(20):
        assert mutate(on, 1.0, seed).genes["col_old_facets"] == "on"
        assert mutate(base, 1.0, seed).genes["col_old_facets"] == "off"
        assert crossover(on, base, seed).genes["col_old_facets"] == "on"
