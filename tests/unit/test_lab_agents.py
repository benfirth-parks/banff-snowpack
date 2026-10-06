"""Lab agents on the synthetic benchmark (ADR-062): every family predicts from the visible case only, in the prediction
contract; SNOWPACK runs through a fake engine (and the real one when it is installed); without an engine the
SNOWPACK agent is skipped, not scored, and the hybrid falls back to its other members."""

from __future__ import annotations

import inspect
import json
import shutil

import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.lab.agents import (  # noqa: E402
    AgentUnavailable,
    AnalogueAgent,
    AnalogueLibrary,
    HybridAgent,
    PersistenceAgent,
    SnowpackEngineAgent,
    WeatherRuleAgent,
    make_agent,
)
from snowagent.lab.agents.analogue import AnalogueEntry, AnalogueLayer, digest  # noqa: E402
from snowagent.lab.agents.common import latest_season_pit, pit_snow_depth, stamp_prediction  # noqa: E402
from snowagent.lab.agents.snowpack import FakeEngine, UnavailableEngine, merge_engine_layers  # noqa: E402
from snowagent.lab.benchmark import builder  # noqa: E402
from snowagent.lab.benchmark.loader import case_dirs, load_visible_case, read_manifest  # noqa: E402
from snowagent.lab.genome import default_genome, default_genomes, mutate  # noqa: E402
from snowagent.lab.schemas.benchmark import VisibleBenchmarkCase  # noqa: E402
from snowagent.lab.schemas.genome import AgentFamily  # noqa: E402
from snowagent.lab.settings import load_lab_config  # noqa: E402
from snowagent.lab.storage.paths import LabPaths  # noqa: E402
from tests.unit.lab_fixtures import write_synthetic_lab  # noqa: E402
from tests.unit.test_lab_benchmark import CONFIG  # noqa: E402


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("agents")
    cfg = load_lab_config(CONFIG)
    paths = LabPaths(tmp / "lab")
    write_synthetic_lab(paths.root, tmp / "checkout", cfg)
    builder.build_cases(paths, cfg, tmp / "checkout", exclude_flagged=True)
    return {d.name: d for d in case_dirs(paths)}


def _case(built, name) -> VisibleBenchmarkCase:
    return load_visible_case(built[name])


def test_predict_takes_only_the_visible_case_and_a_seed():
    for cls in (PersistenceAgent, WeatherRuleAgent, AnalogueAgent, SnowpackEngineAgent, HybridAgent):
        params = list(inspect.signature(cls.predict).parameters)
        assert params == ["self", "case", "seed"], cls
        assert inspect.signature(cls.predict).parameters["case"].annotation in ("VisibleBenchmarkCase",
                                                                                VisibleBenchmarkCase)


@pytest.mark.parametrize("family", [AgentFamily.persistence, AgentFamily.weather_rule, AgentFamily.hybrid])
def test_agents_predict_every_case_in_the_contract(built, family):
    agent = make_agent(default_genome(family), backend=FakeEngine())
    for name, d in built.items():
        case = load_visible_case(d)
        p = agent.predict(case, seed=1)
        assert p.case_id == case.case_key and p.agent_id == agent.agent_id
        assert p.model_metadata["genome_hash"] == agent.genome.genome_hash
        if p.status == "ok":
            hs = p.bulk_state.snow_depth_m
            assert 0 <= hs.p10 <= hs.p50 <= hs.p90
            tops = [ly.top_depth_m.p50 for ly in p.layers]
            assert tops == sorted(tops) and all(ly.bottom_depth_m.p50 <= hs.p50 + 1e-6 for ly in p.layers)
            assert all(0 <= ly.probability_present <= 1 for ly in p.layers)
        stamped = stamp_prediction(p, read_manifest(d))  # the harness puts back the case id and real times
        assert stamped.case_id == read_manifest(d).case_id
        # no id, date or free text of the case leaks into the agent's answer
        text = json.dumps(p.model_dump(mode="json"))
        assert "bow_summit_syn" not in text and "2024-" not in text and "2023-" not in text, name


def test_persistence_carries_the_last_pit_and_adds_measured_new_snow(built):
    case = _case(built, "BOW_20240110T1900Z_NP")
    p = PersistenceAgent(default_genome(AgentFamily.persistence)).predict(case, 0)
    assert p.status == "ok" and p.layers
    pit = latest_season_pit(case)
    pit_grains = {ly.grain_primary for ly in pit.layers}
    assert pit_grains <= {g for ly in p.layers for g in ly.grain_form}  # the pit's layers carried with their grains
    # the synthetic sensor rises 1.6 m over the season: the carried column is deeper than the pit
    assert p.bulk_state.snow_depth_m.p50 > pit_snow_depth(pit)


def test_weather_rule_builds_layers_from_weather_alone(built):
    case = _case(built, "BOW_20240110T1900Z_H72")
    g = default_genome(AgentFamily.weather_rule)
    nudged = WeatherRuleAgent(g).predict(case, 0)
    assert nudged.status == "ok" and len(nudged.layers) >= 2
    off = g.model_copy(update={"genes": g.genes | {"pit_depth_nudge": 0.0}})
    free = WeatherRuleAgent(off).predict(case, 0)
    assert free.bulk_state.snow_depth_m.p50 != nudged.bulk_state.snow_depth_m.p50


def test_analogue_uses_only_the_library_and_says_when_it_is_empty(built):
    case = _case(built, "BOW_20240110T1900Z_H72")
    agent = AnalogueAgent(default_genome(AgentFamily.analogue))
    p = agent.predict(case, 0)
    assert p.status == "insufficient_data" and "another season" in p.insufficient_data_reason
    others = [load_visible_case(d) for n, d in built.items() if n != "BOW_20240110T1900Z_H72"]
    lib = AnalogueLibrary(entries=[AnalogueEntry(digest=digest(c), truth_hs_m=1.0 + i / 10, truth_layers=[
        AnalogueLayer(top_depth_m=0.0, bottom_depth_m=0.3, grain="DF", hardness_index=1.5),
        AnalogueLayer(top_depth_m=0.3, bottom_depth_m=1.0, grain="FC", hardness_index=2.0)])
        for i, c in enumerate(others)])
    p = AnalogueAgent(default_genome(AgentFamily.analogue), lib).predict(case, 0)
    assert p.status == "ok" and {tuple(ly.grain_form) for ly in p.layers} == {("DF",), ("FC",)}
    assert p.model_metadata["library_size"] == len(others)
    # the library carries no season, date or id: nothing to memorise a case by
    assert set(AnalogueEntry.model_fields) == {"digest", "truth_hs_m", "truth_layers"}
    assert "season" not in json.dumps(lib.model_dump(mode="json"))


def test_snowpack_agent_with_a_fake_engine_and_without_one(built):
    case = _case(built, "BOW_20240110T1900Z_H72")
    fake = FakeEngine(0.9)
    p = SnowpackEngineAgent(default_genome(AgentFamily.snowpack), fake).predict(case, 0)
    assert p.status == "ok" and fake.calls == 1
    assert [ly.grain_form for ly in p.layers] == [["DF"], ["FC"]]
    assert p.model_metadata["engine_source"] == "fake" and p.model_metadata["snowpack_version"] == "fake-0"
    with pytest.raises(AgentUnavailable):
        SnowpackEngineAgent(default_genome(AgentFamily.snowpack), UnavailableEngine("no binary")).predict(case, 0)


def test_hybrid_blends_members_and_falls_back_without_the_engine(built):
    case = _case(built, "BOW_20240110T1900Z_H72")
    g = default_genome(AgentFamily.hybrid)
    p = HybridAgent(g, FakeEngine(0.9)).predict(case, 0)
    m = p.model_metadata
    assert p.status == "ok" and m["structure_from"] == "snowpack" and set(m["members"]) == {
        "snowpack", "persistence", "rule"}
    assert abs(sum(v["weight"] for v in m["members"].values()) - 1) < 1e-6
    q = HybridAgent(g, UnavailableEngine("no binary")).predict(case, 0)
    assert q.status == "ok" and "snowpack" not in q.model_metadata["members"]
    assert q.model_metadata["structure_from"] == "persistence"
    assert any("SNOWPACK unavailable" in s for s in q.confidence.main_limits)


def test_mutated_genomes_still_make_valid_agents(built):
    case = _case(built, "BOW_20240110T1900Z_H72")
    for g in default_genomes():
        for i in range(3):
            child = mutate(g, 0.8, i)
            p = make_agent(child, AnalogueLibrary(), FakeEngine()).predict(case, i)
            assert p.agent_id == child.agent_id and p.status in ("ok", "insufficient_data")


def test_merge_engine_layers_joins_similar_neighbours():
    from snowagent.lab.agents.snowpack import EngineLayer

    layers = [EngineLayer(top_depth_m=0.0, bottom_depth_m=0.1, grain="DF", hardness_index=1.0),
              EngineLayer(top_depth_m=0.1, bottom_depth_m=0.2, grain="DF", hardness_index=1.3),
              EngineLayer(top_depth_m=0.2, bottom_depth_m=0.3, grain="FC", hardness_index=2.0)]
    m = merge_engine_layers(layers, 0.5)
    assert [(ly.grain, ly.bottom_depth_m) for ly in m] == [("DF", 0.2), ("FC", 0.3)]


@pytest.mark.skipif(shutil.which("snowpack") is None and not __import__("os").path.exists(
    "/opt/snowpack/bin/snowpack"), reason="SNOWPACK binary not installed")
def test_real_engine_runs_from_the_visible_package(built, tmp_path):
    from snowagent.lab.agents.snowpack import VisiblePackageEngine

    case = _case(built, "BOW_20240110T1900Z_H72")
    eng = VisiblePackageEngine(work_dir=tmp_path)
    try:
        r = eng.simulate(case)
    except AgentUnavailable as exc:
        pytest.skip(str(exc))
    assert r.source == "engine_run" and r.snow_depth_m > 0 and r.layers
    assert abs(r.profile_lag_h) <= 0.5 and r.forcing_hash and r.config_hash
