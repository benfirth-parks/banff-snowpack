"""Known-answer test (ADR-157): fake pits from a known agent, observer noise, and the per-gene recovery table."""

from __future__ import annotations

import json

import numpy as np
import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.lab.agents import make_agent  # noqa: E402
from snowagent.lab.benchmark import builder  # noqa: E402
from snowagent.lab.benchmark.loader import case_dirs, load_hidden_truth, load_visible_case, read_manifest  # noqa: E402
from snowagent.lab.competition import scoring  # noqa: E402
from snowagent.lab.competition.runner import stamp_prediction  # noqa: E402
from snowagent.lab.genome import default_genome, make_genome  # noqa: E402
from snowagent.lab.schemas.genome import AgentFamily  # noqa: E402
from snowagent.lab.schemas.prediction import SnowpackPrediction  # noqa: E402
from snowagent.lab.settings import load_lab_config  # noqa: E402
from snowagent.lab.storage.paths import LabPaths  # noqa: E402
from snowagent.lab.training import known_answer as ka  # noqa: E402
from tests.unit.lab_fixtures import write_synthetic_lab  # noqa: E402
from tests.unit.test_lab_benchmark import CONFIG  # noqa: E402


def _layers():
    return [ka._L(0.0, 0.10, "PP", 1.0), ka._L(0.10, 0.105, "DF", 1.3), ka._L(0.105, 0.30, "RG", 2.7),
            ka._L(0.30, 0.31, "MFcr", 4.0), ka._L(0.31, 0.60, "FC", 2.0), ka._L(0.60, 0.80, "DH", 1.7)]


def test_hardness_text_round_trips_thirds():
    for h in (1.0, 4 / 3, 5 / 3, 2.0, 3.0, 11 / 3, 6.0):
        assert scoring.hardness_index(ka.hardness_text(h)) == pytest.approx(h, abs=1e-6)
    assert ka.hardness_text(None) is None and ka.hardness_text(9.0) == "I"


def test_no_noise_keeps_the_pit_apart_from_merging_like_neighbours():
    hs, ls = ka.add_noise(0.80, _layers(), ka.NoiseModel.none(), np.random.default_rng(0))
    assert hs == pytest.approx(0.80)
    assert [a.grain for a in ls] == ["PP", "DF", "RG", "MFcr", "FC", "DH"]
    assert [(a.top, a.bottom) for a in ls] == [(a.top, a.bottom) for a in _layers()]


def test_noise_keeps_order_cover_and_vocabulary():
    for seed in range(50):
        hs, ls = ka.add_noise(0.80, _layers(), ka.NoiseModel(), np.random.default_rng(seed))
        assert ls[0].top == 0.0 and ls[-1].bottom == pytest.approx(hs)
        assert all(a.bottom > a.top for a in ls)
        assert all(b.top == pytest.approx(a.bottom) for a, b in zip(ls, ls[1:], strict=False))
        assert any(a.grain == "MFcr" for a in ls)  # a crust is never merged away for being thin
        assert all(a.hard is None or 1.0 <= a.hard <= 6.0 for a in ls)


def test_noise_is_seeded_and_moves_something():
    a = ka.add_noise(0.8, _layers(), ka.NoiseModel(), ka.case_rng(3, "c1"))
    b = ka.add_noise(0.8, _layers(), ka.NoiseModel(), ka.case_rng(3, "c1"))
    c = ka.add_noise(0.8, _layers(), ka.NoiseModel(), ka.case_rng(4, "c1"))
    assert a == b and a != c


def test_gene_recovery_reads_the_way_closed():
    start = default_genome(AgentFamily.process)
    truth = make_genome(AgentFamily.process, start.genes | {"proc_facet_peak_c": -3.0, "proc_sw_crust": "off"})
    found = make_genome(AgentFamily.process, start.genes | {"proc_facet_peak_c": -4.0, "proc_sw_crust": "off"})
    rows = {r["gene"]: r for r in ka.gene_recovery(start, truth, found)}
    assert rows["proc_facet_peak_c"]["closed"] == pytest.approx(0.5)
    assert rows["proc_sw_crust"]["right"] and rows["proc_sw_crust"]["moved_by_truth"]
    assert rows["proc_drift_wind_ms"]["closed"] is None and not rows["proc_drift_wind_ms"]["moved_by_truth"]
    assert "col_old_facets" not in rows  # fixed genes are not evolved, so not judged


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("ka")
    cfg = load_lab_config(CONFIG)
    src = LabPaths(tmp / "lab")
    write_synthetic_lab(src.root, tmp / "checkout", cfg)
    builder.build_cases(src, cfg, tmp / "checkout", exclude_flagged=True)
    return src, tmp


def test_build_replaces_only_the_withheld_pits(built):
    src, tmp = built
    truth = default_genome(AgentFamily.process)
    dst = LabPaths(tmp / "ka")
    rep = ka.build(src, dst, truth, ka.NoiseModel.none(), seed=1)
    assert rep.replaced >= 1 and rep.replaced + sum(rep.dropped.values()) == rep.cases
    meta = json.loads((dst.root / "known_answer.json").read_text())
    assert meta["truth_genome_hash"] == truth.genome_hash
    before = {d.name: d for d in case_dirs(src)}
    for d in case_dirs(dst):
        m = read_manifest(d)
        old = before[d.name]
        for name in m.visible_hashes:  # visible files untouched
            assert (d / "visible" / name).read_bytes() == (old / "visible" / name).read_bytes()
        t = load_hidden_truth(d)  # hashes verified
        assert t.truth_profile.source_id == ka.SOURCE_ID
        assert t.truth_profile.profile_id == load_hidden_truth(old).truth_profile.profile_id
        # without noise the true agent's own prediction matches its fake pit layer for layer
        pred = stamp_prediction(make_agent(truth).predict(load_visible_case(d), 0), m)
        s = scoring.score_case(SnowpackPrediction.model_validate(pred.model_dump(mode="json")), t.truth_profile,
                               m.target_scope)
        if t.truth_profile.layers and s.get("layer_structure") == s.get("layer_structure"):
            assert s["match_f1"] > 0.6
        assert s["snow_depth"] == pytest.approx(1.0, abs=1e-3)
    with pytest.raises(ValueError):
        ka.build(src, dst, truth, ka.NoiseModel.none())
