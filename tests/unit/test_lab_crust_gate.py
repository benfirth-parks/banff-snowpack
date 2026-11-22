"""Crust gate for the process switches (ADR-141): a refreeze becomes a crust only after a warm spell of the kind that
left crusts in the training winters' pits; after such a spell the topmost refrozen element becomes one. The switch is
fixed and off, so every saved process genome loads and predicts as before."""

from __future__ import annotations

import pytest

pytest.importorskip(
    "pyarrow", reason="lab extra not installed (pip install -e '.[lab]')"
)

from snowagent.lab.agents import column as colmod  # noqa: E402
from snowagent.lab.agents.column import Hour  # noqa: E402
from snowagent.lab.agents.process import ProcessExt, Spell, genes_of  # noqa: E402
from snowagent.lab.genome import default_genome, make_genome, mutate  # noqa: E402
from snowagent.lab.schemas.genome import AgentFamily  # noqa: E402


def _genes(**kw):
    g = default_genome(AgentFamily.process)
    return genes_of(make_genome(AgentFamily.process, dict(g.genes) | kw))


def _snow(n=10, T=-5.0, rho=200.0):
    c = colmod.Column.empty()
    for _ in range(n):
        c.append(0.03, 0.03 * rho, T, "RG", "RG", new=False)
    return c


def _hour(ta, rain=0.0, sw=0.0):
    return Hour(
        ta=ta,
        vw=1.0,
        rh=80.0,
        sw=sw,
        lw=250.0,
        p=rain,
        rain=rain,
        dz_new=0.0,
        ts=min(ta, 0.0),
        since_snow=100.0,
    )


def _refreeze_after(g, hours):
    """Rain soaks the top of cold snow, then the hours given pass; returns the top element's grain form."""
    c = _snow()
    ext = ProcessExt(g)
    for hr in hours:
        ext.spell.update(g, hr)
    colmod._percolate(g, c, 3.0)
    for _ in range(48):
        colmod._conduct(g, c, -8.0, 0.0)
        colmod._phase(c)
    dry = c.water <= 1e-6
    if ext.on["crustgate"]:
        ext.gate(g, c, dry)
    colmod.grain_changes(g, c, dry, c.T * 0 + 1.0)
    return c.cls[-1]


def test_switch_is_fixed_off_and_keeps_every_process_genome():
    base = default_genome(AgentFamily.process)
    legacy = make_genome(
        AgentFamily.process,
        {
            k: v
            for k, v in base.genes.items()
            if k != "proc_sw_crustgate" and not k.startswith("proc_cg_")
        },
    )
    assert (
        legacy.genes["proc_sw_crustgate"] == "off"
        and legacy.genome_hash == base.genome_hash
    )
    on = make_genome(
        AgentFamily.process, dict(base.genes) | {"proc_sw_crustgate": "on"}
    )
    assert on.genome_hash != base.genome_hash
    for seed in range(20):
        assert mutate(on, 1.0, seed).genes["proc_sw_crustgate"] == "on"
        assert mutate(base, 1.0, seed).genes["proc_sw_crustgate"] == "off"


def test_gate_off_the_column_makes_its_crust_as_before():
    assert _refreeze_after(_genes(), [_hour(1.0)]) == "MFcr"


def test_short_dry_warm_spell_leaves_no_crust_when_the_gate_is_on():
    g = _genes(
        proc_sw_crustgate="on",
        proc_cg_rain_mm=5.0,
        proc_cg_warm_h=24.0,
        proc_cg_sun_days=3.0,
    )
    assert _refreeze_after(g, [_hour(1.0)] * 3) != "MFcr"


def test_rainy_or_long_warm_spell_makes_the_crust():
    g = _genes(
        proc_sw_crustgate="on",
        proc_cg_rain_mm=5.0,
        proc_cg_warm_h=24.0,
        proc_cg_sun_days=3.0,
    )
    assert _refreeze_after(g, [_hour(1.5, rain=1.0)] * 6) == "MFcr"
    assert _refreeze_after(g, [_hour(1.0)] * 30) == "MFcr"


def test_open_gate_turns_a_small_refreeze_into_a_crust_once_per_spell():
    g = _genes(
        proc_sw_crustgate="on",
        proc_cg_rain_mm=1.0,
        proc_cg_warm_h=24.0,
        proc_cg_sun_days=3.0,
    )
    ext = ProcessExt(g)
    for hr in [_hour(2.0, rain=0.5)] * 4:
        ext.spell.update(g, hr)
    c = _snow()
    c.frz[-1] = (
        0.01 * c.ice[-1]
    )  # a little refrozen water, well under the crust threshold
    dry = c.water <= 1e-6
    ext.gate(g, c, dry)
    colmod.grain_changes(g, c, dry, c.T * 0 + 1.0)
    assert c.cls[-1] == "MFcr" and not ext.spell.fresh


def test_strong_sun_days_in_a_row_open_the_gate():
    g = _genes(proc_sw_crustgate="on", proc_cg_sun_days=2.0, proc_cg_sun_wm2=200.0)
    s = Spell()
    for _ in range(24):
        s.update(g, _hour(-3.0, sw=250.0))
    assert not s.opens(g)
    for _ in range(24):
        s.update(g, _hour(-3.0, sw=250.0))
    assert s.opens(g)
    for _ in range(24):
        s.update(g, _hour(-3.0, sw=10.0))
    assert not s.opens(g)
