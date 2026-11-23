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


# --- ADR-143: faceting counts the hours above the faceting gradient -----------------------------------------------


def _strong_gradient_hours(genome, hours, grad_k_m):
    """One dry element held under a steady gradient: its faceting progress after the hours given."""
    g = genes_of(genome)
    ext = ProcessExt(g)
    c = colmod.Column.empty()
    c.append(0.05, 0.05 * 200.0, -5.0, "RG", "RG", new=True)
    hr = Hour(ta=-10.0, vw=1.0, rh=80.0, sw=0.0, lw=250.0, p=0.0, rain=0.0, dz_new=0.0, ts=-10.0, since_snow=100.0)
    tg = -5.0 + grad_k_m * 0.025  # ground side warmer: gradient across the element's half thickness each side
    ts = -5.0 - grad_k_m * 0.025
    for _ in range(hours):
        c.T = np.array([-5.0])
        ext.metamorphism(g, c, ts, tg, hr)
    return float(c.fac[0])


@pytest.mark.parametrize("family", [AgentFamily.process, AgentFamily.process_melt])
def test_duration_switch_is_off_by_default_and_leaves_ids_unchanged(family):
    g = default_genome(family)
    assert g.genes["proc_sw_duration"] == "off"
    legacy = make_genome(family, {k: v for k, v in g.genes.items() if k not in ("proc_sw_duration",
                                                                                "proc_facet_excess_cap")})
    assert legacy.genome_hash == g.genome_hash


def test_duration_switch_caps_how_much_a_strong_gradient_counts():
    off = make_genome(AgentFamily.process, dict(default_genome(AgentFamily.process).genes)
                      | {"proc_sw_kinetics": "off"})
    on = make_genome(AgentFamily.process, dict(off.genes) | {"proc_sw_duration": "on", "proc_facet_excess_cap": 0.2})
    weak_off, weak_on = _strong_gradient_hours(off, 300, 11.5), _strong_gradient_hours(on, 300, 11.5)
    strong_off, strong_on = _strong_gradient_hours(off, 300, 100.0), _strong_gradient_hours(on, 300, 100.0)
    assert weak_on == pytest.approx(weak_off) and weak_on > 0  # just above the threshold: unchanged
    assert strong_off > 5 * strong_on > 0  # far above it: the excess counts at most the cap


def test_standard_process_hours_model():
    g = named_genome("process-facet-hours")
    assert (g.genes["proc_sw_duration"], g.genes["proc_sw_kinetics"], g.genes["col_old_facets"]) == ("on", "off", "on")
    assert mutate(g, 1.0, 3).genes["proc_sw_duration"] == "on"
