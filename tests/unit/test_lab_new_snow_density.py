"""ADR-153 published new-snow density formulas for the process agent: Hedstrom and Pomeroy (1998) and Pahaut (1976,
as Crocus and SNOWPACK use it). Fixed and off by default, so every saved genome keeps its id and predicts as before."""

from __future__ import annotations

import numpy as np
import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.lab.agents.common import new_snow_density  # noqa: E402
from snowagent.lab.agents.process import genes_of  # noqa: E402
from snowagent.lab.genome import default_genome, make_genome  # noqa: E402
from snowagent.lab.schemas.genome import AgentFamily  # noqa: E402


def test_off_by_default_and_ids_unchanged():
    g = default_genome(AgentFamily.process)
    assert g.genes["proc_sw_newsnow"] == "off"
    legacy = make_genome(AgentFamily.process, {k: v for k, v in g.genes.items() if k != "proc_sw_newsnow"})
    assert legacy.genome_hash == g.genome_hash
    assert "proc_sw_newsnow" not in default_genome(AgentFamily.column).genes


def test_off_keeps_the_column_line():
    g = genes_of(default_genome(AgentFamily.process))
    line = g["new_snow_density_kg_m3"] + g["new_snow_density_temp_coeff"] * 2.0 + g["new_snow_density_wind_coeff"] * 3.0
    assert float(new_snow_density(-8.0, 3.0, g)) == pytest.approx(line)


def test_hedstrom_pomeroy_published_values():
    g = {"proc_sw_newsnow": "hedstrom"}
    assert float(new_snow_density(-10.0, 5.0, g)) == pytest.approx(69.0, abs=0.5)  # ~68 below about -10 degC
    assert float(new_snow_density(1.0, 0.0, g)) == pytest.approx(143.0, abs=1.0)  # 143 at +1 degC
    assert float(new_snow_density(5.0, 0.0, g)) == pytest.approx(float(new_snow_density(1.0, 0.0, g)))  # capped
    ta = np.array([-20.0, -5.0, 0.0])
    assert np.all(np.diff(new_snow_density(ta, np.zeros(3), g)) > 0)  # wind plays no part, warmer is denser


def test_pahaut_published_values():
    g = {"proc_sw_newsnow": "pahaut"}
    assert float(new_snow_density(-5.0, 4.0, g)) == pytest.approx(109.0 - 30.0 + 52.0)
    assert float(new_snow_density(-30.0, 0.0, g)) == 50.0 and float(new_snow_density(0.0, 50.0, g)) == 250.0
