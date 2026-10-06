"""The Arena's event feed (ADR-078): the writer, the reader, the rebuild from a run's files, and that the feed
changes no result and no cache key."""

from __future__ import annotations

import json
import shutil

import pandas as pd
import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.lab import events  # noqa: E402
from snowagent.lab.benchmark import builder  # noqa: E402
from snowagent.lab.competition.runner import EngineSpec, run_competition  # noqa: E402
from snowagent.lab.genome import default_genomes  # noqa: E402
from snowagent.lab.services.arena import (  # noqa: E402
    load_feed,
    race_table,
    rebuild_competition,
    rebuild_training,
    rounds_of,
    scored_frame,
)
from snowagent.lab.settings import load_lab_config  # noqa: E402
from snowagent.lab.storage.paths import LabPaths  # noqa: E402
from snowagent.lab.training.cache import CODE_EXCLUDE  # noqa: E402
from snowagent.lab.training.loop import TrainOptions, load_round, run_training  # noqa: E402
from tests.unit.lab_fixtures import write_multiseason_lab  # noqa: E402
from tests.unit.test_lab_benchmark import CONFIG  # noqa: E402

VOLATILE = ("runtime_s", "engine_s")  # wall-clock times differ between any two runs


def test_emit_and_read_whole_lines_only(tmp_path, monkeypatch):
    events.emit(tmp_path, "case_scored", case_id="A", composite=0.5, bad=float("nan"))
    events.emit(tmp_path, "round_committed", round=1)
    with open(tmp_path / events.EVENTS_FILE, "a") as fh:
        fh.write('{"ev": "half-writ')  # a line still being written
    got, off = events.read_events(tmp_path)
    assert [e["ev"] for e in got] == ["case_scored", "round_committed"] and got[0]["bad"] is None
    with open(tmp_path / events.EVENTS_FILE, "a") as fh:
        fh.write('ten"}\n')
    more, off2 = events.read_events(tmp_path, off)
    assert [e["ev"] for e in more] == ["half-written"] and off2 > off
    assert events.read_events(tmp_path, off2) == ([], off2)
    events.emit(tmp_path / "missing" / "dir", "x")  # never raises
    monkeypatch.setenv(events.ENV, "0")
    events.emit(tmp_path, "ignored")
    assert len(events.read_events(tmp_path)[0]) == 3
    assert events.pit_time("BOW_20151126T1915Z_H72") == "2015-11-26T19:15Z" and events.pit_time("x") is None


def test_the_feed_is_outside_the_cache_code_hash():
    assert "lab/events.py" in CODE_EXCLUDE  # editing the feed never invalidates cached predictions


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("events")
    cfg = load_lab_config(CONFIG)
    paths = LabPaths(root / "lab")
    write_multiseason_lab(paths.root, root / "checkout", cfg, ("2022-2023", "2023-2024"))
    builder.build_cases(paths, cfg, root / "checkout", exclude_flagged=True)
    return cfg, root


def _copy(built, tmp_path, name):
    cfg, root = built
    shutil.copytree(root / "lab", tmp_path / name)
    return cfg, LabPaths(tmp_path / name)


def test_competition_results_identical_with_and_without_the_feed(built, tmp_path, monkeypatch):
    out = {}
    for flag in ("1", "0"):
        cfg, paths = _copy(built, tmp_path, f"lab{flag}")
        monkeypatch.setenv(events.ENV, flag)
        run_dir = paths.outputs / "competitions" / "c"
        feed = events.CompetitionFeed(run_dir, [{"agent_id": g.agent_id} for g in default_genomes()])
        res = run_competition(paths, cfg, default_genomes(), engine=EngineSpec(kind="fake"), run_id="c",
                              workers=2, progress=feed.progress)
        feed.finish()
        out[flag] = (res.scores.drop(columns=list(VOLATILE), errors="ignore"), res.leaderboard["overall"], run_dir)
    pd.testing.assert_frame_equal(out["1"][0], out["0"][0])
    strip = [{k: v for k, v in r.items() if not k.startswith("runtime")} for r in out["1"][1]]
    assert strip == [{k: v for k, v in r.items() if not k.startswith("runtime")} for r in out["0"][1]]
    assert not (out["0"][2] / events.EVENTS_FILE).exists()
    got, _ = events.read_events(out["1"][2])
    scored = [e for e in got if e["ev"] == "case_scored"]
    assert got[0]["ev"] == "run_started" and got[-1]["ev"] == "run_finished"
    assert len(scored) == len(out["1"][0])  # one per agent and case
    df = out["1"][0].set_index(["case_id", "agent_id"])
    for e in scored:
        c = df.loc[(e["case_id"], e["agent_id"]), "composite"]
        assert (e["composite"] is None and pd.isna(c)) or e["composite"] == pytest.approx(c)
    # the replay of the same run rebuilt from its case records holds the same results
    rebuilt = {(e["case_id"], e["agent_id"]): e["composite"] for e in rebuild_competition(out["1"][2])
               if e["ev"] == "case_scored"}
    assert rebuilt == {(e["case_id"], e["agent_id"]): e["composite"] for e in scored}


def test_training_results_and_cache_keys_identical_with_and_without_the_feed(built, tmp_path, monkeypatch):
    out = {}
    for flag in ("1", "0"):
        cfg, paths = _copy(built, tmp_path, f"lab{flag}")
        monkeypatch.setenv(events.ENV, flag)
        res = run_training(paths, cfg, TrainOptions.from_config(cfg, rounds=2, population=4,
                                                                engine=EngineSpec(kind="fake")),
                           run_id="t", workers=2, log=lambda m: None)
        keys = sorted(p.name for p in (paths.outputs / "cache").rglob("*.json"))
        boards = [load_round(res.run_dir, r)["leaderboard"]["ranked"] for r in (1, 2)]
        out[flag] = (keys, boards, res.run_dir, paths)
    assert out["1"][0] == out["0"][0]  # the same cache entries under the same keys
    assert [[(x["agent_id"], x["composite"]) for x in b] for b in out["1"][1]] == \
        [[(x["agent_id"], x["composite"]) for x in b] for b in out["0"][1]]
    assert not (out["0"][2] / events.EVENTS_FILE).exists()
    feed = load_feed(out["1"][3], "training", "t")
    kinds = [e["ev"] for e in feed.events]
    for k in ("run_started", "round_started", "case_started", "case_scored", "round_committed", "run_finished"):
        assert k in kinds, k
    df = scored_frame(feed)
    assert set(df["round"]) == {1, 2} and df["key"].notna().all()
    r1 = df[df["round"] == 1]
    board = race_table(r1)
    ranked = load_round(out["1"][2], 1)["leaderboard"]["ranked"]
    assert board["agent_id"].nunique() == len(ranked)
    # the race's mean case composite is the leaderboard's case composite
    want = {x["agent_id"]: x.get("case_composite") for x in ranked}
    for row in board.to_dict("records"):
        if want.get(row["agent_id"]) is not None:
            assert row["mean"] == pytest.approx(want[row["agent_id"]], abs=1e-4)
    rounds = rounds_of(feed)
    assert rounds[2]["agents"] and {a["role"] for a in rounds[2]["agents"]} >= {"survivor", "child"}
    assert rounds[2]["committed"]["best"]["agent_id"] == load_round(out["1"][2], 2)["round"]["best"]["agent_id"]
    # rebuilt from the round files: the same results
    live = {(e["round"], e["case_id"], e["agent_id"]): e["composite"] for e in feed.events if e["ev"] == "case_scored"}
    rebuilt = {(e["round"], e["case_id"], e["agent_id"]): e["composite"] for e in rebuild_training(out["1"][2])
               if e["ev"] == "case_scored"}
    assert rebuilt.keys() == live.keys()
    assert all(rebuilt[k] == pytest.approx(v) or (v is None and rebuilt[k] is None) for k, v in live.items())
    json.dumps(feed.events)  # plain JSON throughout
