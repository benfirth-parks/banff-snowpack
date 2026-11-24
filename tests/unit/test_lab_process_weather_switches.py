"""ADR-152 weather switches of the process agent: a humidity-aware rain-snow split (Jennings and Molotch 2019) and a
wind-dependent snow gauge catch (Kochendorfer et al. 2017). Fixed and off by default, so every saved genome keeps its
id and predicts as before."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.lab.agents.column import ColumnExt, Forcing  # noqa: E402
from snowagent.lab.agents.process import ProcessExt, gauge_catch, genes_of, jennings_snow_share  # noqa: E402
from snowagent.lab.genome import default_genome, make_genome  # noqa: E402
from snowagent.lab.schemas.genome import AgentFamily  # noqa: E402


def test_switches_are_off_by_default_and_leave_ids_unchanged():
    family = AgentFamily.process
    g = default_genome(family)
    assert g.genes["proc_sw_phase"] == "off" and g.genes["proc_sw_catch"] == "off"
    legacy = make_genome(family, {k: v for k, v in g.genes.items()
                                  if k not in ("proc_sw_phase", "proc_sw_catch", "proc_phase_shift_k",
                                               "proc_sw_newsnow", "proc_sw_meltkeep")})
    assert legacy.genome_hash == g.genome_hash
    assert "proc_sw_phase" not in default_genome(AgentFamily.process_melt).genes  # process family only


def test_published_phase_band_is_warmer_in_dry_air():
    assert jennings_snow_share(1.06, 0.95) == pytest.approx(0.5, abs=0.02)  # humid: 50/50 near 1 degC
    assert jennings_snow_share(4.25, 0.45) == pytest.approx(0.5, abs=0.02)  # dry: 50/50 near 4 degC
    assert jennings_snow_share(2.0, 0.5) > 0.8 > jennings_snow_share(2.0, 1.0)
    assert jennings_snow_share(-3.0, 1.0) > 0.99 and jennings_snow_share(8.0, 0.5) < 0.01
    assert jennings_snow_share(2.0, 0.9, shift_k=1.0) == pytest.approx(jennings_snow_share(1.0, 0.9))


def test_phase_switch_replaces_the_ramp_only_when_on():
    base = genes_of(default_genome(AgentFamily.process))
    off, on = ProcessExt(base), ProcessExt(base | {"proc_sw_phase": "on"})
    ramp = ColumnExt().snow_share(base, 2.0, 0.5)
    assert off.snow_share(base, 2.0, 0.5) == ramp
    assert on.snow_share(base, 2.0, 0.5) == pytest.approx(jennings_snow_share(2.0, 0.5)) != ramp
    assert on.snow_share(base, 2.0, float("nan")) == ramp  # no humidity: the ramp


def test_gauge_catch_falls_with_wind_and_cold():
    assert gauge_catch(0.0, -5.0) == 1.0
    assert gauge_catch(5.0, -5.0) == pytest.approx(0.57, abs=0.02)
    assert gauge_catch(5.0, 3.0) > 0.85  # rain: little loss
    assert gauge_catch(20.0, -10.0) == gauge_catch(7.2, -10.0)  # capped wind


def _wx(psum, vw, gauge, factor=1.15):
    n = len(psum)
    z = np.zeros(n)
    return Forcing(t=np.arange(n, dtype=float), ta=np.full(n, -5.0), rh=np.full(n, 0.8), vw=np.asarray(vw, float),
                   sw=z, lw=z, psum=np.asarray(psum, float), measured=np.ones(n, bool), hs=None,
                   gauge=np.asarray(gauge, bool), gauge_factor=factor)


def test_catch_on_replaces_the_fixed_factor_and_norm_keeps_the_total():
    base = genes_of(default_genome(AgentFamily.process))
    wx = _wx([1.15, 1.15, 2.3, 0.0], [0.0, 6.0, 3.0, 9.0], [True, True, True, True])
    on = ProcessExt(base | {"proc_sw_catch": "on"})
    on.prepare(base, wx)
    assert on.precip(base, 0, wx, 1.15) == pytest.approx(1.0)  # calm: the raw gauge
    assert on.precip(base, 1, wx, 1.15) == pytest.approx(1.0 / gauge_catch(6.0, -5.0))
    norm = ProcessExt(base | {"proc_sw_catch": "norm"})
    norm.prepare(base, wx)
    out = [norm.precip(base, i, wx, float(p)) for i, p in enumerate(wx.psum)]
    assert sum(out) == pytest.approx(wx.psum.sum()) and out[1] > out[0]  # same total, windy hour gets more
    era5 = _wx([1.0], [6.0], [False])
    assert on.precip(base, 0, era5, 1.0) == 1.0  # reanalysis hours untouched


def test_a_genome_with_a_weather_switch_on_does_not_widen_to_process_melt():
    from snowagent.lab.genome import as_family

    on = make_genome(AgentFamily.process, dict(default_genome(AgentFamily.process).genes) | {"proc_sw_phase": "on"})
    with pytest.raises(ValueError, match="cannot become"):
        as_family(on, AgentFamily.process_melt)


def test_phase_cut_is_all_snow_or_all_rain():
    base = genes_of(default_genome(AgentFamily.process))
    cut = ProcessExt(base | {"proc_sw_phase": "cut"})
    assert cut.snow_share(base, -1.0, 0.95) == 1.0 and cut.snow_share(base, 3.0, 0.95) == 0.0
    assert cut.snow_share(base, 3.0, 0.45) == 1.0  # dry air: still snow at 3 degC
