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

from snowagent.lab.agents.physics import EnginePhysics, engine_physics  # noqa: E402
from snowagent.lab.agents.snowpack import FakeEngine  # noqa: E402
from snowagent.lab.benchmark import builder  # noqa: E402
from snowagent.lab.benchmark.loader import case_dirs, load_visible_case, read_manifest  # noqa: E402
from snowagent.lab.competition import truth as truth_mod  # noqa: E402
from snowagent.lab.competition.runner import EngineSpec  # noqa: E402
from snowagent.lab.genome import default_genome, mutate  # noqa: E402
from snowagent.lab.schemas.genome import AgentFamily, AgentGenome  # noqa: E402
from snowagent.lab.settings import load_lab_config  # noqa: E402
from snowagent.lab.storage.paths import LabPaths  # noqa: E402
from snowagent.lab.storage.registry import RunRegistry  # noqa: E402
from snowagent.lab.training import evolve, loso  # noqa: E402
from snowagent.lab.training.cache import DiskEngineCache, TrainingCache, engine_inputs  # noqa: E402
from snowagent.lab.training.lineage import ancestry, format_ancestry, lineage_for, lineage_index  # noqa: E402
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
    assert committed_rounds(stop.parent) == []
    done = run_training(other, cfg, None, run_id="st", resume=True, log=quiet)  # clears the stop request
    assert committed_rounds(done.run_dir) == [1, 2] and not stop.exists()


def test_cache_hits_skip_every_rerun_and_the_engine_runs_once_per_case_and_physics(lab, monkeypatch):
    cfg, paths, _ = lab
    calls = []
    real = FakeEngine.simulate
    monkeypatch.setattr(FakeEngine, "simulate", lambda self, case, physics=None: calls.append(
        (case.case_key, (physics or EnginePhysics()).key)) or real(self, case, physics))
    first = run_training(paths, cfg, opts(cfg), run_id="c1", log=quiet)
    dirs = case_dirs(paths, "all")
    n_cases = len(dirs)
    # each (case, physics) runs once in the whole run; the incumbent's physics once per case (round 1)
    assert len(calls) == len(set(calls))
    assert sum(k == "default" for _c, k in calls) == n_cases
    genomes = [AgentGenome.model_validate(p["genome"]) for r in (1, 2, 3)
               for p in load_round(first.run_dir, r)["population"]]
    sites = [str(read_manifest(d).site_code) for d in dirs]
    expected = sum(len({engine_physics(g.genes, s).key for g in genomes
                        if g.family in (AgentFamily.snowpack, AgentFamily.hybrid)}) for s in sites)
    assert len(calls) == expected  # output-only mutants reuse their physics' profile
    info = [load_round(first.run_dir, r)["round"]["eval"] for r in (1, 2, 3)]
    assert info[0]["cache_hits"] == 0 and info[0]["engine_runs"] == n_cases
    assert info[1]["cache_hits"] == 2 * n_cases  # the two survivors
    n = len(calls)
    again = run_training(paths, cfg, opts(cfg), run_id="c2", log=quiet)
    assert again.summary["cache"]["hit_rate"] == 1.0 and len(calls) == n
    assert trace(again.run_dir, 3) == trace(first.run_dir, 3)
    # another seed: the SNOWPACK incumbent is re-scored (seed is part of the context) but its engine profile is not
    # re-run
    run_training(paths, cfg, opts(cfg, seed=3, rounds=1), run_id="c3", log=quiet)
    assert len(calls) == n


def test_a_scoring_change_re_scores_cached_predictions_and_keeps_every_engine_profile(lab, monkeypatch):
    """ADR-074: scores are not part of the prediction or engine keys. Under a new scoring identity every cached pair
    is re-scored from its stored prediction (no agent, no engine run), and the rows are those of the new scorer."""
    from snowagent.lab.competition import runner, scoring

    cfg, paths, _ = lab
    first = run_training(paths, cfg, opts(cfg, rounds=1), run_id="s1", log=quiet)
    real = scoring.score_case

    def halved(pred, truth, scope):
        out = real(pred, truth, scope)
        if out.get("depth_error_m") is not None:
            out["snow_depth"] = out["snow_depth"] / 2
        return out

    monkeypatch.setattr(scoring, "SCORING_VERSION", "lab-scoring-test")
    monkeypatch.setattr(scoring, "score_case", halved)
    monkeypatch.setattr(runner, "make_agent", lambda *a, **k: pytest.fail("an agent ran"))
    monkeypatch.setattr(FakeEngine, "simulate", lambda *a, **k: pytest.fail("the engine ran"))
    again = run_training(paths, cfg, opts(cfg, rounds=1), run_id="s2", log=quiet)
    info = load_round(again.run_dir, 1)["round"]["eval"]
    assert info["rescored"] == info["pairs"] == info["cache_hits"] and info["engine_runs"] == 0
    key = ["case_id", "agent_id"]
    a = pd.read_parquet(first.run_dir / "rounds" / "r01" / "scores.parquet").set_index(key).sort_index()
    b = pd.read_parquet(again.run_dir / "rounds" / "r01" / "scores.parquet").set_index(key).sort_index()
    ok = a["depth_error_m"].notna()
    pd.testing.assert_series_equal(b.loc[ok, "snow_depth"], a.loc[ok, "snow_depth"] / 2)
    for col in ("layer_structure", "critical_layers", "uncertainty", "depth_error_m", "runtime_s"):
        pd.testing.assert_series_equal(a[col], b[col])
    # once re-scored, the entries are current: a third run re-scores nothing
    third = run_training(paths, cfg, opts(cfg, rounds=1), run_id="s3", log=quiet)
    info3 = load_round(third.run_dir, 1)["round"]["eval"]
    assert info3["rescored"] == 0 and info3["cache_hits"] == info3["pairs"]


def test_the_prediction_code_hash_leaves_out_the_scorer(tmp_path, monkeypatch):
    """Editing the scoring module changes the scoring hash, not the code hash that keys predictions and engine
    profiles (ADR-074)."""
    from snowagent.lab.training import cache as cache_mod

    src = tmp_path / "snowagent"
    shutil.copytree(cache_mod.SRC, src, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(cache_mod, "SRC", src)
    code0, score0 = cache_mod.code_hash.__wrapped__(), cache_mod.scoring_hash.__wrapped__()
    f = src / "lab" / "competition" / "scoring.py"
    f.write_text(f.read_text() + "\n# a scoring change\n")
    assert cache_mod.code_hash.__wrapped__() == code0 and cache_mod.scoring_hash.__wrapped__() != score0
    g = src / "lab" / "agents" / "common.py"
    g.write_text(g.read_text() + "\n# a prediction change\n")
    assert cache_mod.code_hash.__wrapped__() != code0


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


# --------------------------------------------------------------------------------------------- lineage


def test_lineage_records_parents_operator_and_changed_genes(lab):
    cfg, paths, _ = lab
    res = run_training(paths, cfg, opts(cfg, rounds=4), run_id="l", log=quiet)
    idx = lineage_index(paths, "l")
    w = res.summary["winner"]["genome_hash"]
    chain = ancestry(idx, w)
    assert chain[0]["genome_hash"] == w and chain[-1]["operator"] == "initial"
    for rec in chain:
        if rec.get("repeat") or rec["operator"] == "initial":
            continue
        parent = idx[rec["parents"][0]]["genome"]["genes"]
        child = idx[rec["genome_hash"]]["genome"]["genes"]
        assert rec["changed_genes"] == {k: [parent[k], child[k]] for k in rec["changed_genes"]}
        assert rec["changed_genes"] and all(parent[k] != child[k] for k in rec["changed_genes"])
    m = next(r for r in idx.values() if r["operator"] == "mutation")
    assert m["mutation_strength"] == 0.2 and len(m["parents"]) == 1
    lines = format_ancestry(lineage_for(paths, m["agent_id"])[1])
    assert lines[0].startswith(m["agent_id"]) and "mutation of" in lines[0] and "changed vs first parent" in lines[1]
    out = CliRunner().invoke(_app(), ["lab", "lineage", m["genome_hash"][:12], "--data-root", str(paths.root)])
    assert out.exit_code == 0 and m["agent_id"] in out.stdout
    out = CliRunner().invoke(_app(), ["lab", "lineage", "nonexistent", "--data-root", str(paths.root)])
    assert out.exit_code == 2


# --------------------------------------------------------------------------------------------- check-loso


def test_check_loso_never_lets_a_held_out_season_reach_training(lab, monkeypatch):
    cfg, paths, source = lab
    seed_run = run_training(paths, cfg, opts(cfg, rounds=2, population=4), run_id="w", log=quiet)
    phase = {"now": "training"}
    reads = []
    real_truth = truth_mod.load_hidden_truth

    def spy(case_dir, *a, **k):
        m = read_manifest(case_dir)
        reads.append((phase["now"], m.case_set, m.split.value, m.season))
        return real_truth(case_dir, *a, **k)

    monkeypatch.setattr(truth_mod, "load_hidden_truth", spy)
    real_fold = loso._fold_result

    def fold(*a, **k):
        phase["now"] = "holdout"
        try:
            return real_fold(*a, **k)
        finally:
            phase["now"] = "training"

    monkeypatch.setattr(loso, "_fold_result", fold)
    res = loso.check_loso(paths, cfg, f"{seed_run.run_id}/2/1", None, source=source, log=quiet,
                          check_id="chk", workers=1)
    assert [f["season"] for f in res.folds] == list(SEASONS)
    for s in SEASONS:
        cs = f"loso_{s}"
        train_reads = [r for r in reads if r[1] == cs and r[0] == "training"]
        hold_reads = [r for r in reads if r[1] == cs and r[0] == "holdout"]
        assert train_reads and all(r[2] == "training" and r[3] != s for r in train_reads)
        assert hold_reads and all(r[3] == s for r in hold_reads if r[2] == "holdout")
        fold_run = paths.outputs / "training" / f"chk-{s}"
        for r in committed_rounds(fold_run):
            sc = pd.read_parquet(fold_run / "rounds" / f"r{r:02d}" / "scores.parquet")
            assert s not in set(sc["season"]) and set(sc["split"]) == {"training"}
            assert load_round(fold_run, r)["round"]["gap"]["monitor_season"] != s
        plan = json.loads((fold_run / "run.json").read_text())["plan"]
        assert plan["seed"] == 0 and plan["rounds"] == 2 and plan["population"] == 4 and s not in plan["seasons"]
    r = res.result
    assert r["seasons"] == 3 and r["wins"] + r["losses"] + r["ties"] == 3 and r["pooled_cases"] > 0
    assert r["passed"] == (r["pooled_evolved_composite"] > r["pooled_incumbent_composite"]
                           and r["losses"] <= 1)
    assert RunRegistry(paths.registry).get("chk").counts["folds"] == 3
    # resumable: finished folds are not re-run
    monkeypatch.setattr(loso, "run_training", lambda *a, **k: (_ for _ in ()).throw(AssertionError("re-ran")))
    again = loso.check_loso(paths, cfg, f"{seed_run.run_id}/2/1", None, source=source, log=quiet,
                            check_id="chk", workers=1)
    assert again.result["passed"] == r["passed"]


def test_promotion_rule():
    cfg = load_lab_config(CONFIG)

    def fold(season, outcome):
        return {"season": season, "outcome": outcome, "winner": {"label": "x", "family": "hybrid"}}

    def holdout(ev, inc):
        rows = []
        for role, c in (("evolved", ev), ("incumbent", inc)):
            rows += [{"role": role, "status": "ok", "composite": c, "case_id": str(i), "runtime_s": 0.0,
                      **{k: c for k in ("snow_depth", "layer_structure", "critical_layers", "uncertainty")}}
                     for i in range(4)]
        return pd.DataFrame(rows)

    folds = [fold("a", "win"), fold("b", "loss"), fold("c", "tie")]
    assert loso.pooled_result(folds, holdout(0.6, 0.5), cfg)["passed"]
    assert not loso.pooled_result(folds, holdout(0.5, 0.6), cfg)["passed"]  # pooled composite lower
    lost = [fold("a", "loss"), fold("b", "loss"), fold("c", "win")]
    r = loso.pooled_result(lost, holdout(0.6, 0.5), cfg)
    assert not r["passed"] and r["loses_on_majority"]
    assert not loso.pooled_result(folds, holdout(0.5, 0.5), cfg)["passed"]  # equal is not better


def test_check_loso_genome_references_and_estimate(lab):
    cfg, paths, source = lab
    run_training(paths, cfg, opts(cfg, rounds=2, population=4), run_id="e", log=quiet)
    g, plan = loso.resolve_genome(paths, "e/2/1", cfg)
    assert plan["seed"] == 0 and g.genome_hash == load_round(paths.outputs / "training" / "e", 2)["leaderboard"][
        "ranked"][0]["genome_hash"]
    with pytest.raises(ValueError):
        loso.resolve_genome(paths, "e/2/99", cfg)
    with pytest.raises(ValueError):
        loso.resolve_genome(paths, "nope", cfg)
    res = loso.check_loso(paths, cfg, "e/2/1", None, source=source, log=quiet, estimate_only=True)
    est = res.result["estimate"]
    assert set(est["folds"]) == set(SEASONS) and est["builds"] == list(SEASONS) and est["total_high_s"] > 0
    msgs: list[str] = []
    cheap = loso.check_loso(paths, cfg, "e/2/1", None, source=source, log=msgs.append, estimate_only=True,
                            overrides={"rounds": 1, "population": None})
    assert any("1 instead of 2" in m and "weaker test" in m for m in msgs)
    assert cheap.result["estimate"]["total_high_s"] < est["total_high_s"]
    assert not loso.list_checks(paths)


# --------------------------------------------------------------------------------------------- milestone 5


def test_screening_scores_new_physics_on_the_sample_first_and_is_deterministic(lab, tmp_path):
    cfg, paths, _ = lab
    o = opts(cfg, rounds=3, population=6, screen_cases=4, initial=[default_genome(AgentFamily.snowpack),
                                                                   default_genome(AgentFamily.hybrid)])
    res = run_training(paths, cfg, o, run_id="sc", log=quiet)
    plan = json.loads((res.run_dir / "run.json").read_text())["plan"]
    ids = plan["screen_case_ids"]
    assert len(ids) == 4 and len(set(ids)) == 4 and plan["screen_cases"] == 4
    for r in (2, 3):
        rd = load_round(res.run_dir, r)
        info = rd["round"]
        sc = info["screen"]
        assert sc["sample_cases"] == 4 and sc["candidates"] >= 1
        roles = {p["lineage"]["genome_hash"]: p["role"] for p in rd["population"]}
        ranked = {x["genome_hash"] for x in rd["leaderboard"]["ranked"]}
        out = {h for h, role in roles.items() if role == "screened_out"}
        assert sc["screened_out"] == len(out) and not out & ranked  # never ranked, so never a survivor
        for a in sc["agents"]:  # passed = beat the worst survivor on the same sample
            assert a["passed"] == (a["sample_composite"] is not None and a["sample_composite"] > sc["threshold"])
        sample = pd.read_parquet(res.run_dir / "rounds" / f"r{r:02d}" / "screen_scores.parquet")
        assert set(sample["case_id"]) == set(ids)
        full = pd.read_parquet(res.run_dir / "rounds" / f"r{r:02d}" / "scores.parquet")
        assert set(full["genome_hash"]) == ranked and not out & set(full["genome_hash"])
    # same seed, other lab copy: same sample, same screen decisions, same populations
    other = LabPaths(tmp_path / "other")
    shutil.copytree(paths.root / "benchmark", other.root / "benchmark")
    again = run_training(other, cfg, o, run_id="sc", log=quiet)
    assert json.loads((again.run_dir / "run.json").read_text())["plan"]["screen_case_ids"] == ids
    assert trace(again.run_dir, 3) == trace(res.run_dir, 3)
    assert [load_round(again.run_dir, r)["round"]["screen"]["agents"] for r in (2, 3)] == \
        [load_round(res.run_dir, r)["round"]["screen"]["agents"] for r in (2, 3)]


def test_screening_off_keeps_the_owner_plan_and_resume_keeps_the_options(lab):
    cfg, paths, _ = lab
    res = run_training(paths, cfg, opts(cfg, rounds=1), run_id="plain", log=quiet)
    plan = json.loads((res.run_dir / "run.json").read_text())["plan"]
    assert "screen_cases" not in plan and "family_slots" not in plan  # the milestone-4 plan, unchanged
    o = opts(cfg, rounds=3, population=8, screen_cases=3, family_slots=True)
    first = run_training(paths, cfg, TrainOptions(**(o.__dict__ | {"rounds": 2})), run_id="rs", log=quiet)
    assert committed_rounds(first.run_dir) == [1, 2]
    # a resume uses the stored plan (screen sample and family slots included)
    run_json = first.run_dir / "run.json"
    meta = json.loads(run_json.read_text())
    meta["plan"]["rounds"] = 3
    run_json.write_text(json.dumps(meta))
    (first.run_dir / "summary.json").unlink()
    done = run_training(paths, cfg, None, run_id="rs", resume=True, log=quiet)
    assert committed_rounds(done.run_dir) == [1, 2, 3]
    assert "screen" in load_round(done.run_dir, 3)["round"]
    assert sum(p["role"] == "family_slot" for p in load_round(done.run_dir, 3)["population"]) == len(AgentFamily)


def test_family_slots_keep_one_mutant_of_each_family_and_leave_the_owner_children_alone(lab):
    cfg, paths, _ = lab
    base = run_training(paths, cfg, opts(cfg, population=10), run_id="nofs", log=quiet)
    fs = run_training(paths, cfg, opts(cfg, population=10, family_slots=True), run_id="fs", log=quiet)
    for r in (2, 3):
        pop = load_round(fs.run_dir, r)["population"]
        slots = [p for p in pop if p["role"] == "family_slot"]
        assert [p["genome"]["family"] for p in slots] == [f.value for f in AgentFamily]
        assert all(p["lineage"]["operator"] == "mutation" and p["lineage"]["slot"] == "family" for p in slots)
        assert len(pop) == 10 and sum(p["role"] == "child" for p in pop) == 10 - 2 - len(AgentFamily)
        # each slot's parent is its family's best fully scored agent so far
        for p in slots:
            parent = p["lineage"]["parents"][0]
            rows = [x for i in range(1, r) for x in load_round(fs.run_dir, i)["leaderboard"]["ranked"]
                    if x["family"] == p["genome"]["family"] and x.get("composite_exact") is not None]
            best = min(rows, key=lambda x: (-x["composite_exact"], x["genome_hash"]))
            assert parent == best["genome_hash"]
    # the option off: the owner's top-two rule and its children are exactly milestone 4's
    assert all(p["role"] in ("survivor", "child") for r in (2, 3) for p in load_round(base.run_dir, r)["population"])
    b2 = [p["lineage"]["genome_hash"] for p in load_round(base.run_dir, 2)["population"] if p["role"] == "child"]
    f2 = [p["lineage"]["genome_hash"] for p in load_round(fs.run_dir, 2)["population"] if p["role"] == "child"]
    assert f2[:2] == b2[:2]  # same stream (fewer owner children): their first mutations are unchanged by the slots
    with pytest.raises(ValueError, match="family-slots"):
        opts(cfg, population=5, family_slots=True).validate()


def test_a_stop_from_progress_cancels_the_cases_not_yet_started():
    import time

    from snowagent.lab.training.evaluate import _pool_map

    class Stop(Exception):
        pass

    def progress(done, total):
        raise Stop

    t0 = time.time()
    with pytest.raises(Stop):
        list(_pool_map(time.sleep, [0.5] * 40, 2, progress))
    assert time.time() - t0 < 5  # 40 cases of 0.5 s on 2 workers would take 10 s if the queue were drained
