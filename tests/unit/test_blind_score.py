"""Blind-test scoring (ADR-090): frozen agents scored on the live season's pits observed after their freeze, beside
standard SNOWPACK, under their recorded scoring version; append-only store; nothing done when nothing is new."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

LIVE = "2026-2027"


def _entry(name: str, frozen: str, version: str = "lab-scoring-3", **genes) -> bytes:
    from snowagent.lab.genome import default_genome

    g = json.loads(default_genome("snowpack").model_dump_json())
    g["genes"].update(genes)
    return json.dumps({"name": name, "genome": g, "source": {"run_id": "r"}, "season": LIVE, "frozen_utc": frozen,
                       "git_commit": "abc", "code_hash": "h", "scoring_version": version}).encode()


@pytest.fixture(scope="module")
def live_lab(tmp_path_factory):
    """Two past seasons and the live one (2026-27, in no configured split), three Bow Summit pits each."""
    from snowagent.lab.settings import load_lab_config
    from tests.unit.lab_fixtures import write_multiseason_lab

    tmp = tmp_path_factory.mktemp("blind")
    cfg = load_lab_config()
    assert LIVE not in cfg.splits.all_seasons
    ids = write_multiseason_lab(tmp / "lab", tmp / "checkout", cfg, ("2024-2025", "2025-2026", LIVE))
    live = [i for i in ids if i.startswith(("2026-12", "2027-01"))]
    observed = tmp / "observed.jsonl"
    observed.write_text("".join(json.dumps({"profile_id": i, "site_key": "bow_summit",
                                            "obs_time_utc": f"{i[:10]}T19:00:00+00:00"}) + "\n"
                                for i in ids))
    return tmp, live, observed


def test_season_pits_reads_the_live_season_only(live_lab):
    from snowagent.ops.blind_test import season_pits

    _tmp, live, observed = live_lab
    pits = season_pits(LIVE, observed)
    assert [p["id"] for p in pits] == live and all(p["plot"] == "bow_summit" for p in pits)


def test_frozen_agents_are_scored_only_on_pits_after_their_freeze(live_lab, tmp_path):
    from snowagent.lab.competition.runner import EngineSpec
    from snowagent.ops import blind_test as bt

    lab, live, observed = live_lab
    files = {"early.json": _entry("Omar Balmoral", "2026-11-01T00:00:00+00:00", sp_wind_mult=0.8),
             "late.json": _entry("Paulie Windsor", "2026-12-10T00:00:00+00:00", sp_wind_mult=0.9),
             "old.json": _entry("Bunk Greene", "2026-11-02T00:00:00+00:00", "lab-scoring-2", sp_wind_mult=0.85),
             "junk.json": b"{not json"}
    kw = dict(observed=observed, archive=tmp_path / "archive", lab_root=lab / "lab", repo=lab / "checkout",
              engine=EngineSpec(kind="fake"), publish=False, do_import=False)
    res = bt.run_blind_test(2026, tmp_path / "web", files=files, **kw)
    assert res["entries"] == 3 and res["pits"] == 3 and res["scored"] > 0
    assert any("junk.json" in w["message"] for w in res["warnings"])

    store = bt.load_store(tmp_path / "archive" / LIVE / "scores.jsonl")
    late = store[store["name"] == "Paulie Windsor"]
    assert len(late) and (late["pit_utc"].map(bt._utc) > bt._utc("2026-12-10T00:00:00+00:00")).all()
    assert set(store[store["name"] == "Bunk Greene"]["scoring_version"]) == {"lab-scoring-2"}
    std = store[store["agent_id"] == "standard"]
    assert set(std["scoring_version"]) == {"lab-scoring-2", "lab-scoring-3"}  # the yardstick under each version

    summary = json.loads((tmp_path / "web" / "blind_test.json").read_text())
    by = {e["name"]: e for e in summary["entries"]}
    assert by["Omar Balmoral"]["cases"] > by["Paulie Windsor"]["cases"] > 0
    e = by["Omar Balmoral"]
    assert e["standard_composite"] is not None and e["difference"] == pytest.approx(
        e["composite"] - e["standard_composite"], abs=1e-4)
    assert e["won"] + e["lost"] <= e["cases"] and "BOW" in e["by_plot"]
    assert "not an avalanche forecast" in summary["label"]

    # nothing new: no import, no scoring, the store unchanged (a score once written is never rewritten)
    before = (tmp_path / "archive" / LIVE / "scores.jsonl").read_text()
    again = bt.run_blind_test(2026, tmp_path / "web", files=files, **kw)
    assert again["scored"] == 0 and (tmp_path / "archive" / LIVE / "scores.jsonl").read_text() == before


def test_no_entries_or_no_pits_after_the_freeze_does_nothing(live_lab, tmp_path):
    from snowagent.ops import blind_test as bt

    lab, _live, observed = live_lab
    kw = dict(observed=observed, archive=tmp_path / "archive", lab_root=tmp_path / "unused", repo=lab / "checkout",
              publish=False, do_import=False)
    res = bt.run_blind_test(2026, tmp_path / "web", files={}, **kw)
    assert res["entries"] == 0 and json.loads((tmp_path / "web" / "blind_test.json").read_text())["entries"] == []
    res = bt.run_blind_test(2026, tmp_path / "web",
                            files={"a.json": _entry("Omar Balmoral", "2027-03-01T00:00:00+00:00")}, **kw)
    assert res["entries"] == 1 and res["pits"] == 0 and not Path(tmp_path / "unused").exists()
    s = json.loads((tmp_path / "web" / "blind_test.json").read_text())
    assert s["entries"][0]["cases"] == 0 and s["entries"][0]["composite"] is None


def test_the_store_is_append_only(tmp_path):
    from snowagent.ops.blind_test import append_store, load_store

    f = tmp_path / "s.jsonl"
    row = {"case_id": "c", "agent_id": "a", "scoring_version": "v", "composite": 0.5}
    assert append_store(f, [row]) == 1
    assert append_store(f, [row | {"composite": 0.9}]) == 0  # the same key is never rewritten
    assert load_store(f)["composite"].tolist() == [0.5]


def test_results_reach_the_lab_app_through_the_branch(tmp_path):
    import subprocess

    from snowagent.lab.services import blind_test as app
    from snowagent.ops import blind_test as bt

    def git(cwd, *a):
        return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    origin, work = tmp_path / "origin.git", tmp_path / "work"
    git(tmp_path, "init", "--quiet", "--bare", str(origin))
    git(tmp_path, "init", "--quiet", "-b", "main", str(work))
    git(work, "config", "user.email", "ben@example.org")
    git(work, "config", "user.name", "Ben")
    (work / "README.md").write_text("hi\n")
    git(work, "add", "README.md")
    git(work, "commit", "--quiet", "-m", "init")
    git(work, "remote", "add", "origin", str(origin))
    git(work, "push", "--quiet", "-u", "origin", "main")
    assert bt.RESULTS_FOLDER == app.RESULTS_FOLDER
    assert app.results(work, LIVE) is None
    summary = {"label": bt.LABEL, "season": LIVE, "generated_utc": "2026-12-02T13:00:00+00:00",
               "entries": [{"id": "x", "name": "Omar Balmoral", "cases": 2, "composite": 0.55,
                            "standard_composite": 0.52, "won": 2, "lost": 0}]}
    bt._publish(work, LIVE, summary)
    got = app.results(work, LIVE)
    assert got == summary and git(work, "status", "--porcelain") == ""
    assert app.result_line(got["entries"][0]) == ("2 pit cases so far: 0.550 against 0.520 for standard SNOWPACK "
                                                  "(+0.030), better on 2, worse on 0")
    assert app.result_line({}) == "no pit dug since its freeze has been scored yet"
