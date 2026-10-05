"""Training loop, cache, lineage and the leave-one-season-out promotion check (milestone 4, ADR-066 to ADR-069):
determinism, resume after a kill, survivors carried unchanged, unique children, cache hits, gap flagging and the
season separation of check-loso (a held-out season's truth never reaches training scoring)."""

from __future__ import annotations

import json
import shutil

import pandas as pd
import pytest
from typer.testing import CliRunner

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.lab.agents.snowpack import FakeEngine  # noqa: E402
from snowagent.lab.benchmark import builder  # noqa: E402
from snowagent.lab.benchmark.loader import case_dirs, load_visible_case  # noqa: E402
from snowagent.lab.competition.runner import EngineSpec  # noqa: E402
from snowagent.lab.genome import default_genome, mutate  # noqa: E402
from snowagent.lab.schemas.genome import AgentFamily  # noqa: E402
from snowagent.lab.settings import load_lab_config  # noqa: E402
from snowagent.lab.storage.paths import LabPaths  # noqa: E402
from snowagent.lab.storage.registry import RunRegistry  # noqa: E402
from snowagent.lab.training import evolve  # noqa: E402
from snowagent.lab.training.cache import DiskEngineCache, TrainingCache, engine_inputs  # noqa: E402
from snowagent.lab.training.loop import (  # noqa: E402
    TrainingStopped,
    TrainOptions,
    committed_rounds,
    default_monitor_season,
    gap_record,
    load_round,
    rank_agents,
    run_training,
)
from tests.unit.lab_fixtures import write_multiseason_lab  # noqa: E402
from tests.unit.test_lab_benchmark import CONFIG  # noqa: E402

SEASONS = ("2021-2022", "2022-2023", "2023-2024")


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """Three synthetic Bow Summit seasons, built once (mode all); tests copy the lab root."""
    root = tmp_path_factory.mktemp("training")
    cfg = load_lab_config(CONFIG)
    paths = LabPaths(root / "lab")
    write_multiseason_lab(paths.root, root / "checkout", cfg, SEASONS)
    builder.build_cases(paths, cfg, root / "checkout", exclude_flagged=True)
    return cfg, root


@pytest.fixture()
def lab(built, tmp_path):
    cfg, root = built
    shutil.copytree(root / "lab", tmp_path / "lab")
    return cfg, LabPaths(tmp_path / "lab"), root / "checkout"


def opts(cfg, **kw) -> TrainOptions:
    return TrainOptions.from_config(cfg, **({"rounds": 3, "population": 5, "engine": EngineSpec(kind="fake")} | kw))


def quiet(_msg: str) -> None:
    pass


def trace(run_dir, rounds):
    out = []
    for r in range(1, rounds + 1):
        rd = load_round(run_dir, r)
        out.append(([p["lineage"]["genome_hash"] for p in rd["population"]],
                    [(x["genome_hash"], x["composite_exact"]) for x in rd["leaderboard"]["ranked"]]))
    return out


# --------------------------------------------------------------------------------------------- the loop


def test_training_is_deterministic_for_a_seed_and_differs_for_another(lab, tmp_path):
    cfg, paths, _ = lab
    a = run_training(paths, cfg, opts(cfg), run_id="a", log=quiet)
    other = LabPaths(tmp_path / "other")
    shutil.copytree(paths.root / "benchmark", other.root / "benchmark")  # same cases, empty cache
    b = run_training(other, cfg, opts(cfg), run_id="b", log=quiet)
    assert trace(a.run_dir, 3) == trace(b.run_dir, 3)
    assert a.summary["winner"]["genome_hash"] == b.summary["winner"]["genome_hash"]
    c = run_training(paths, cfg, opts(cfg, seed=1), run_id="c", log=quiet)
    assert trace(c.run_dir, 3)[1][0] != trace(a.run_dir, 3)[1][0]  # round 2 children differ
    assert trace(c.run_dir, 3)[0] == trace(a.run_dir, 3)[0]  # round 1 is the initial population either way
    # the frozen weights are recorded, the plan is refused under another one
    plan = json.loads((a.run_dir / "run.json").read_text())["plan"]
    assert plan["scoring_weights"] == cfg.scoring_weights.model_dump()
    with pytest.raises(ValueError, match="another plan"):
        run_training(paths, cfg, opts(cfg, rounds=4), run_id="a", log=quiet)


def test_survivors_are_carried_unchanged_and_children_are_unique(lab):
    cfg, paths, _ = lab
    res = run_training(paths, cfg, opts(cfg, rounds=4, population=6), run_id="s", log=quiet)
    seen = set()
    for r in range(1, 5):
        rd = load_round(res.run_dir, r)
        hashes = [p["lineage"]["genome_hash"] for p in rd["population"]]
        assert len(hashes) == len(set(hashes))
        if r == 1:
            assert [p["role"] for p in rd["population"]] == ["initial"] * 5
            seen.update(hashes)
            continue
        prev = load_round(res.run_dir, r - 1)
        top2 = [x["genome_hash"] for x in prev["leaderboard"]["ranked"][:2]]
        assert hashes[:2] == top2 and [p["role"] for p in rd["population"][:2]] == ["survivor"] * 2
        prev_genomes = {p["lineage"]["genome_hash"]: p["genome"]["genes"] for p in prev["population"]}
        for p in rd["population"][:2]:
            assert p["genome"]["genes"] == prev_genomes[p["lineage"]["genome_hash"]]  # unchanged
        kids = hashes[2:]
        assert len(kids) == 4 and not set(kids) & seen  # new to the whole run
        ops = [p["lineage"]["operator"] for p in rd["population"][2:]]
        assert ops.count("mutation") == 3 and sum(o.startswith("crossover") for o in ops) == 1  # 0.25 x 4
        assert all(set(p["lineage"]["parents"]) <= set(top2) for p in rd["population"][2:])
        # a survivor scores exactly as in the previous round (cached rows)
        s_now = pd.read_parquet(res.run_dir / "rounds" / f"r{r:02d}" / "scores.parquet")
        s_prev = pd.read_parquet(res.run_dir / "rounds" / f"r{r - 1:02d}" / "scores.parquet")
        for h in top2:
            a = s_now[s_now["genome_hash"] == h].sort_values("case_id")["composite"].tolist()
            b = s_prev[s_prev["genome_hash"] == h].sort_values("case_id")["composite"].tolist()
            assert a == b
        seen.update(hashes)
    # every round is in the registry, plus the run itself
    reg = RunRegistry(paths.registry)
    assert all(reg.get(f"s-r{r:02d}").kind == "evolution" for r in range(1, 5)) and reg.get("s") is not None
    assert reg.get("s-r02").scoring_weights == cfg.scoring_weights


def test_duplicate_children_are_redrawn_and_an_impossible_draw_fails(monkeypatch):
    cfg = load_lab_config(CONFIG)
    a, b = default_genome("snowpack"), default_genome("persistence")
    real = evolve.mutate
    calls = []

    def first_duplicate(g, strength, rng, spec=None):
        calls.append(g.genome_hash)
        return g if len(calls) == 1 else real(g, strength, rng, spec)  # the first draw returns the parent

    monkeypatch.setattr(evolve, "mutate", first_duplicate)
    kids = evolve.children([a, b], 4, 2, 0, 0.2, 0.0, cfg.genome, set())
    assert len(calls) == 5 and len({k.genome_hash for k, _ in kids} | {a.genome_hash, b.genome_hash}) == 6
    monkeypatch.setattr(evolve, "mutate", lambda g, *a, **k: g)
    with pytest.raises(evolve.DuplicateGenomes):
        evolve.children([a], 1, 2, 0, 0.2, 0.0, cfg.genome, set(), max_redraws=5)
    # snowpack x persistence share only the uncertainty block: a crossover that returns its parent is mutated
    monkeypatch.setattr(evolve, "mutate", real)
    same = b.model_copy(update={"genes": b.genes | {k: a.genes[k] for k in a.genes if k in b.genes}})
    kids = evolve.children([a, same], 2, 2, 0, 0.2, 1.0, cfg.genome, set())
    assert [rec["operator"] for _, rec in kids] == ["crossover+mutation"] * 2
    assert [rec["parents"] for _, rec in kids] == [[a.genome_hash, same.genome_hash], [same.genome_hash, a.genome_hash]]


def test_resume_after_a_kill_finishes_the_same_run(lab, tmp_path):
    cfg, paths, _ = lab
    ref = run_training(paths, cfg, opts(cfg, rounds=3), run_id="ref", log=quiet)
    other = LabPaths(tmp_path / "killed")
    shutil.copytree(paths.root / "benchmark", other.root / "benchmark")
    seen = {"n": 0}

    def kill(d, n):
        seen["n"] += 1
        if seen["n"] == 20:  # round 1 has 15 cases: five cases into round 2
            raise KeyboardInterrupt("simulated kill")

    with pytest.raises(KeyboardInterrupt):
        run_training(other, cfg, opts(cfg, rounds=3), run_id="k", log=quiet, progress=kill)
    run_dir = other.outputs / "training" / "k"
    assert committed_rounds(run_dir) == [1] and json.loads((run_dir / "status.json").read_text())["state"] == "failed"
    assert not (run_dir / "summary.json").exists()
    r1 = (run_dir / "rounds" / "r01" / "round.json").stat().st_mtime_ns
    res = run_training(other, cfg, None, run_id="k", resume=True, log=quiet)
    assert res.resumed_rounds == 1 and committed_rounds(run_dir) == [1, 2, 3]
    assert (run_dir / "rounds" / "r01" / "round.json").stat().st_mtime_ns == r1  # round 1 not redone
    r2 = load_round(run_dir, 2)["round"]["eval"]
    assert r2["cache_hits"] > 2 * 15  # the survivors and the pairs finished before the kill
    assert trace(run_dir, 3) == trace(ref.run_dir, 3)
    assert RunRegistry(other.registry).get("k-r03") is not None
    # a stop file (the UI's Stop button) stops the run; --resume continues it
    stop = other.outputs / "training" / "st" / "stop"
    with pytest.raises(TrainingStopped):
        run_training(other, cfg, opts(cfg, rounds=2, seed=9), run_id="st", log=quiet,
                     progress=lambda d, n: stop.touch())
    assert json.loads((stop.parent / "status.json").read_text())["state"] == "stopped"
    stop.unlink()
    assert committed_rounds(stop.parent) == []
    done = run_training(other, cfg, None, run_id="st", resume=True, log=quiet)
    assert committed_rounds(done.run_dir) == [1, 2]


def test_cache_hits_skip_every_rerun_and_the_engine_runs_once_per_case(lab, monkeypatch):
    cfg, paths, _ = lab
    calls = []
    real = FakeEngine.simulate
    monkeypatch.setattr(FakeEngine, "simulate", lambda self, case: calls.append(case.case_key) or real(self, case))
    first = run_training(paths, cfg, opts(cfg), run_id="c1", log=quiet)
    n_cases = len(case_dirs(paths, "all"))
    assert len(calls) == n_cases  # one engine run per case for the whole run (later rounds: cached profiles)
    info = [load_round(first.run_dir, r)["round"]["eval"] for r in (1, 2, 3)]
    assert info[0]["cache_hits"] == 0 and info[0]["engine_runs"] == n_cases
    assert info[1]["cache_hits"] == 2 * n_cases and info[1]["engine_runs"] == 0  # the two survivors
    again = run_training(paths, cfg, opts(cfg), run_id="c2", log=quiet)
    assert again.summary["cache"]["hit_rate"] == 1.0 and len(calls) == n_cases
    assert trace(again.run_dir, 3) == trace(first.run_dir, 3)
    # another seed: the gene-less SNOWPACK incumbent is re-scored (seed is part of the context) but its engine
    # profile is not re-run
    run_training(paths, cfg, opts(cfg, seed=3, rounds=1), run_id="c3", log=quiet)
    assert len(calls) == n_cases


def test_engine_key_ignores_the_case_key_and_other_seasons_but_not_the_weather(lab):
    _cfg, paths, _ = lab
    d = case_dirs(paths, "all")[-1]
    case = load_visible_case(d)
    disk = DiskEngineCache(FakeEngine(), TrainingCache(paths.outputs / "cache"), "fake")
    k = disk.key_for(case)
    other = case.model_copy(update={"case_key": "0" * 16,
                                    "permitted_pits": [p for p in case.permitted_pits if p.season_offset == 0]})
    assert disk.key_for(other) == k
    w = [h.model_copy(update={"air_temperature_k": (h.air_temperature_k or 270) + 1}) for h in case.weather_observed]
    assert disk.key_for(case.model_copy(update={"weather_observed": w})) != k
    assert "case_key" not in engine_inputs(case)


def test_gap_is_recorded_each_round_and_flagged_when_it_widens(lab):
    cfg, paths, _ = lab
    res = run_training(paths, cfg, opts(cfg), run_id="g", log=quiet)
    gaps = [load_round(res.run_dir, r)["round"]["gap"] for r in (1, 2, 3)]
    assert all(g["monitor_season"] == "2023-2024" and g["gap"] is not None for g in gaps)
    assert all(len(g["agents"]) == 2 and "warning signal only" in g["note"] for g in gaps)
    # the flag logic: K widening rounds in a row
    w = cfg.scoring_weights
    rows = []
    for s, c in (("2021-2022", 0.8), ("2023-2024", 0.5)):
        rows += [{"agent_id": "a", "family": "persistence", "label": "a", "status": "ok", "season": s,
                  "composite": c, "runtime_s": 0.1, "case_id": f"{s}-{i}"} for i in range(4)]
    df = pd.DataFrame(rows)
    top = [{"agent_id": "a"}]
    prev = [{"gap": 0.1, "streak": 0}, {"gap": 0.2, "streak": 1}]
    rec = gap_record(df, "2023-2024", w, top, prev, k=2, tol=0.0)
    assert rec["widening"] and rec["streak"] == 2 and rec["flag"]
    rec = gap_record(df, "2023-2024", w, top, [{"gap": 0.9, "streak": 3}], k=2, tol=0.0)
    assert not rec["widening"] and rec["streak"] == 0 and not rec["flag"]
    assert gap_record(df, "2019-2020", w, top, prev, 2, 0.0)["gap"] is None


def test_monitor_season_default_rule():
    class M:
        def __init__(self, season, site):
            self.season, self.site_code = season, site

    ms = [M("2023-2024", "BOW"), M("2023-2024", "GOAT"), M("2024-2025", "BOW"), M("2025-2026", "BOW"),
          M("2025-2026", "GOAT"), M("2026-2027", "BOW"), M("2026-2027", "GOAT")]
    assert default_monitor_season(ms, pd.Timestamp("2026-10-05", tz="UTC")) == "2025-2026"  # 2026-27 not done
    assert default_monitor_season(ms[:3], pd.Timestamp("2026-10-05", tz="UTC")) == "2023-2024"  # 2024-25: BOW only
    assert default_monitor_season(ms, pd.Timestamp("2020-01-01", tz="UTC")) is None


def test_ranking_breaks_ties_deterministically():
    w = load_lab_config(CONFIG).scoring_weights
    g = [default_genome(f) for f in ("persistence", "analogue", "weather_rule")]
    rows = []
    for gg, c in zip(g, (0.5, 0.5, 0.6), strict=True):
        rows += [{"agent_id": gg.agent_id, "family": gg.family.value, "label": gg.display_name, "status": "ok",
                  "composite": c, "runtime_s": 0.0, "case_id": str(i), **{k: c for k in
                  ("snow_depth", "layer_structure", "critical_layers", "uncertainty")}} for i in range(5)]
    ranked = rank_agents(pd.DataFrame(rows), w, g)
    assert ranked[0]["agent_id"] == g[2].agent_id
    tied = sorted([g[0].genome_hash, g[1].genome_hash])
    assert [r["genome_hash"] for r in ranked[1:]] == tied and [r["rank"] for r in ranked] == [1, 2, 3]


def _app():
    from snowagent.cli import app

    return app


def test_train_cli_prints_the_estimate_and_the_results(lab):
    cfg, paths, _ = lab
    args = ["lab", "train", "--rounds", "2", "--population", "4", "--engine", "fake", "--data-root",
            str(paths.root), "--run-id", "cli"]
    out = CliRunner().invoke(_app(), [*args, "--estimate-only"])
    assert out.exit_code == 0, out.stdout
    assert "estimate" in out.stdout and "SNOWPACK-family agents are" in out.stdout  # engine runs dominate round 1
    assert not (paths.outputs / "training" / "cli").exists()
    out = CliRunner().invoke(_app(), args)
    assert out.exit_code == 0, out.stdout
    assert "Best composite per round" in out.stdout and "winner" in out.stdout and "warning signal only" in out.stdout
    out = CliRunner().invoke(_app(), args)  # the same run id again: the run exists (finished) with the same plan
    assert out.exit_code == 0
    out = CliRunner().invoke(_app(), [*args[:-2], "--run-id", "cli", "--seed", "4"])
    assert out.exit_code == 2 and "another plan" in out.stdout
    out = CliRunner().invoke(_app(), ["lab", "train", "--population", "2", "--survivors", "2", "--engine", "fake",
                                      "--data-root", str(paths.root)])
    assert out.exit_code == 2


def test_genome_mutants_remain_valid_agents():
    cfg = load_lab_config(CONFIG)
    g = default_genome(AgentFamily.hybrid)
    for s in range(20):
        g2 = mutate(g, 0.5, s, cfg.genome)
        assert g2.genome_hash != g.genome_hash
