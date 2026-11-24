"""Melt forms resist faceting (ADR-156): with the faceting kinetics off (ADR-143), ``brake`` keeps the kinetics'
density brake on melt forms, and ``crust`` starts a hard, dry melt form of the start pit as a melt-freeze crust.
The switch is fixed and off, so every saved process genome loads and predicts as before."""

from __future__ import annotations

import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.lab.agents import column as colmod  # noqa: E402
from snowagent.lab.agents.column import Hour  # noqa: E402
from snowagent.lab.agents.process import ProcessExt, genes_of  # noqa: E402
from snowagent.lab.genome import default_genome, make_genome  # noqa: E402
from snowagent.lab.schemas.genome import AgentFamily  # noqa: E402

FH = {"proc_sw_kinetics": "off", "proc_sw_duration": "on", "proc_facet_excess_cap": 0.2, "col_facet_hours": 24.0}


def _genes(**kw):
    g = default_genome(AgentFamily.process)
    return genes_of(make_genome(AgentFamily.process, dict(g.genes) | FH | kw))


def _column():
    """Cold dry snow with a dense, pencil-hard melt form in the middle."""
    c = colmod.Column.empty()
    for k in range(9):
        rho = 420.0 if k == 4 else 220.0
        cls_ = "MF" if k == 4 else "RG"
        c.append(0.05, 0.05 * rho, -6.0 + 0.5 * k, cls_, cls_, hard0=4.0 if k == 4 else 2.0, rho0=rho, new=False)
    return c


def _hour():
    return Hour(ta=-15.0, vw=1.0, rh=80.0, sw=0.0, lw=200.0, p=0.0, rain=0.0, dz_new=0.0, ts=-15.0,
                since_snow=100.0)


def _after(g, hours=400):
    c = _column()
    ext = ProcessExt(g)
    ext.start(g, c, None)
    c.gbar[:] = -40.0  # a strong gradient through the whole column
    for _ in range(hours):
        c.T[:] = [-6.0 + 2.0 * k for k in range(len(c))][::-1]  # colder on top: 40 K/m over 5 cm elements
        ext.metamorphism(g, c, -15.0, -0.5, _hour())
    return c


def test_off_by_default_and_ids_unchanged():
    g = default_genome(AgentFamily.process)
    assert g.genes["proc_sw_meltkeep"] == "off"
    legacy = make_genome(AgentFamily.process, {k: v for k, v in g.genes.items() if k != "proc_sw_meltkeep"})
    assert legacy.genome_hash == g.genome_hash
    assert "proc_sw_meltkeep" not in default_genome(AgentFamily.column).genes


def test_without_the_switch_a_dense_melt_form_facets():
    c = _after(_genes())
    assert c.cls[4] == "FC" and c.cls[2] == "FC"


def test_brake_keeps_a_dense_melt_form_and_facets_the_snow_around_it():
    c = _after(_genes(proc_sw_meltkeep="brake"))
    assert c.cls[4] == "MF" and c.cls[2] == "FC"


def test_crust_starts_a_hard_pit_melt_form_as_a_crust_with_its_hardness():
    g = _genes(proc_sw_meltkeep="crust")
    c = _column()
    ProcessExt(g).start(g, c, None)
    assert c.cls[4] == "MFcr" and c.hard0[4] == 4.0 and c.cls[3] == "RG"
    soft = _column()
    soft.hard0[4] = 3.0
    ProcessExt(g).start(g, soft, None)
    assert soft.cls[4] == "MF"  # a softer melt form stays a melt form
