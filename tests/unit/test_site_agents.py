"""Experimental evolved agents on the public site (ADR-084): the strict reading of the site-agents branch, Send to
site / Remove through git plumbing (never touching the checkout), the daily build's files, and the TV names."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pandas as pd
import pytest

from snowagent.lab.genome import default_genome
from snowagent.lab.services.names import display, nickname
from snowagent.ops import site_agents as sa


def _genome(**genes) -> dict:
    d = json.loads(default_genome("snowpack").model_dump_json())
    d["genes"].update(genes)
    return d


def _file(name: str, added: str, **genes) -> bytes:
    return json.dumps({"name": name, "genome": _genome(**genes), "source": {"run_id": "r"},
                       "added_utc": added}).encode()


def test_names_are_stable_and_defaults_stay_plain():
    h = "a" * 64
    assert nickname(h, "r20-m05-snowpack") == nickname(h) and " " in nickname(h)
    assert nickname(h, "snowpack-default") == "standard SNOWPACK"
    assert nickname(h, "analogue-default") == "analogue-default"
    assert display(h, "r20-m05-snowpack") == f"{nickname(h)} (r20-m05-snowpack)"
    hashes = [hashlib.sha256(str(i).encode()).hexdigest() for i in range(300)]
    assert len({nickname(x) for x in hashes}) > 150  # varied, not one name for all


def test_the_branch_files_are_validated_strictly():
    files = {
        "a.json": _file("Omar Balmoral", "2026-10-06T10:00:00+00:00", sp_wind_mult=0.8),
        "b.json": _file("Paulie Windsor", "2026-10-06T11:00:00+00:00", sp_wind_mult=0.9),
        "c.json": _file("Bunk Greene", "2026-10-06T12:00:00+00:00", sp_wind_mult=0.95),
        "d.json": _file("Avon Corgi", "2026-10-06T13:00:00+00:00", sp_wind_mult=0.85),  # the fourth: not run
        "range.json": _file("Too Windy", "2026-10-06T09:00:00+00:00", sp_wind_mult=9.0),
        "name.json": _file("<script>", "2026-10-06T09:00:00+00:00"),
        "junk.json": b"{not json",
        "big.json": b" " * (sa.MAX_FILE_BYTES + 1),
        "extra.json": json.dumps({**json.loads(_file("X", "2026-10-06T09:00:00+00:00")), "cmd": "rm"}).encode(),
    }
    other = json.loads(_file("Wrong Family", "2026-10-06T09:00:00+00:00"))
    other["genome"] = json.loads(default_genome("analogue").model_dump_json())
    files["family.json"] = json.dumps(other).encode()
    agents, warnings = sa.parse_agents(files)
    assert [a.name for _, a in agents] == ["Omar Balmoral", "Paulie Windsor", "Bunk Greene"]
    skipped = " ".join(w["message"] for w in warnings)
    for f in ("range.json", "name.json", "junk.json", "big.json", "extra.json", "family.json"):
        assert f"site agent {f} skipped" in skipped
    assert "only the 3 oldest run" in skipped


def test_forcing_genes_apply_except_on_gfs_hours():
    idx = pd.date_range("2026-10-01", periods=4, freq="h", tz="UTC")
    pf = type("PF", (), {})()
    pf.data = pd.DataFrame({"psum": [1.0] * 4, "vw": [2.0] * 4}, index=idx)
    pf.sources = pd.DataFrame({"psum": ["station", "era5", "gfs:2026100100", "gfs"],
                               "vw": ["era5", "gfs", "station", "station"]}, index=idx)
    genes = _genome(sp_precip_mult_bow=1.2, sp_wind_mult=0.8)["genes"]
    data, phys = sa.agent_forcing(pf, genes, "bow_summit")
    assert phys.precip_mult == pytest.approx(1.2) and phys.wind_mult == pytest.approx(0.8)
    assert data["psum"].round(3).tolist() == [1.2, 1.2, 1.0, 1.0]
    assert data["vw"].round(3).tolist() == [1.6, 2.0, 1.6, 1.6]
    assert pf.data["psum"].tolist() == [1.0] * 4  # the site's own forcing is untouched


def test_the_daily_build_writes_the_files_and_never_fails_for_one_agent(tmp_path, monkeypatch):
    files = {"a.json": _file("Omar Balmoral", "2026-10-06T10:00:00+00:00", sp_wind_mult=0.8),
             "b.json": _file("Paulie Windsor", "2026-10-06T11:00:00+00:00", sp_wind_mult=0.9)}

    def fake_run(plot, y, aid, agent, work, now=None, spec=None):
        if agent.name == "Paulie Windsor" and plot == "simpson":
            raise RuntimeError("engine crashed")
        return {"id": aid, "name": agent.name, "nowcast": [], "daily": {"d0": "2026-10-01", "hs_model": []},
                "pits": []}

    monkeypatch.setattr(sa, "run_agent_season", fake_run)
    out = tmp_path / "data"
    stale = out / "goats_eye" / "2026-2027_agents.json"
    res = sa.build_agents(2026, out, tmp_path / "work", pd.Timestamp("2026-10-06T12:00Z"), files=files)
    assert len(res["agents"]) == 2 and set(res["plots"]) == {"bow_summit", "goats_eye", "simpson"}
    assert any("Paulie Windsor at" in w["message"] and "engine crashed" in w["message"] for w in res["warnings"])
    simp = json.loads((out / "simpson" / "2026-2027_agents.json").read_text())
    assert [a["name"] for a in simp["agents"]] == ["Omar Balmoral"] and "not validated" in simp["label"]
    index = json.loads((out / "agents.json").read_text())
    assert index["plots"]["bow_summit"] == {"2026-2027": "2026-2027_agents.json"}
    assert "not an avalanche forecast" in index["label"]

    assert stale.exists()
    res = sa.build_agents(2026, out, tmp_path / "work", files={})  # every agent removed: the site stops offering them
    assert res["agents"] == [] and not stale.exists()
    assert json.loads((out / "agents.json").read_text())["agents"] == []


# --------------------------------------------------------------------------------------------- send and remove


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture()
def checkout(tmp_path):
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "--quiet", "--bare", str(origin))
    work = tmp_path / "work"
    _git(tmp_path, "init", "--quiet", "-b", "main", str(work))
    _git(work, "config", "user.email", "ben@example.org")
    _git(work, "config", "user.name", "Ben")
    (work / "README.md").write_text("hi\n")
    _git(work, "add", "README.md")
    _git(work, "commit", "--quiet", "-m", "init")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "--quiet", "-u", "origin", "main")
    return work, origin


def test_send_and_remove_go_to_the_branch_only(checkout):
    from snowagent.lab.services.site_send import SendError, on_site, remove, send

    work, origin = checkout
    head = _git(work, "rev-parse", "HEAD")
    assert on_site(work) == []
    recs = [json.loads(_file(n, f"2026-10-06T1{i}:00:00+00:00", sp_wind_mult=w))
            for i, (n, w) in enumerate((("Omar Balmoral", 0.8), ("Paulie Windsor", 0.9), ("Bunk Greene", 0.95),
                                        ("Avon Corgi", 0.85)))]
    for rec in recs[:3]:
        send(work, rec)
    assert [a["name"] for a in on_site(work)] == ["Omar Balmoral", "Paulie Windsor", "Bunk Greene"]
    with pytest.raises(SendError, match="already has 3"):
        send(work, recs[3])
    names = _git(origin, "ls-tree", "--name-only", f"{sa.BRANCH}", f"{sa.FOLDER}/").split("\n")
    assert len(names) == 3 and all(n.endswith(".json") for n in names)

    first = on_site(work)[0]["id"]
    remove(work, first)
    assert [a["name"] for a in on_site(work)] == ["Paulie Windsor", "Bunk Greene"]
    with pytest.raises(SendError, match="not on the site"):
        remove(work, first)
    send(work, recs[3])
    assert len(on_site(work)) == 3

    # the checkout is never touched: same branch, same commit, nothing staged or changed
    assert _git(work, "rev-parse", "HEAD") == head and _git(work, "branch", "--show-current") == "main"
    assert _git(work, "status", "--porcelain") == ""
    # and the daily update reads exactly what was sent
    agents, warnings = sa.parse_agents(sa.branch_files(work))
    assert [a.name for _, a in agents] == ["Paulie Windsor", "Bunk Greene", "Avon Corgi"] and not warnings


def test_a_refused_push_says_how_to_sign_in(checkout, tmp_path):
    from snowagent.lab.services.site_send import SendError, send

    work, _ = checkout
    _git(work, "remote", "set-url", "origin", str(tmp_path / "missing.git"))
    with pytest.raises(SendError):
        send(work, json.loads(_file("Omar Balmoral", "2026-10-06T10:00:00+00:00")))


# --------------------------------------------------------------------------------------------- from a training run


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")
    from snowagent.lab.benchmark.builder import build_cases
    from snowagent.lab.competition.runner import EngineSpec
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths
    from snowagent.lab.training.loop import TrainOptions, run_training
    from tests.unit.lab_fixtures import write_multiseason_lab

    repo = Path(__file__).resolve().parents[2]
    tmp = tmp_path_factory.mktemp("site")
    cfg = load_lab_config(repo / "config/lab.yaml")
    paths = LabPaths(tmp / "lab")
    write_multiseason_lab(paths.root, tmp / "checkout", cfg, ("2022-2023", "2023-2024"))
    build_cases(paths, cfg, tmp / "checkout", exclude_flagged=True)
    run_training(paths, cfg, TrainOptions.from_config(cfg, rounds=2, population=4, engine=EngineSpec(kind="fake"),
                                                      initial=[default_genome("snowpack")], survivors=1),
                 run_id="site-train", log=lambda m: None)
    return paths


def test_the_record_and_the_training_page_send_and_remove(trained, checkout, monkeypatch):
    pytest.importorskip("streamlit", reason="lab extra not installed (pip install -e '.[lab]')")
    from streamlit.testing.v1 import AppTest

    from snowagent.lab.services.site_send import agent_record, on_site
    from snowagent.lab.training.loop import load_round, training_root

    paths, (work, _) = trained, checkout
    best = load_round(training_root(paths) / "site-train", 2)["leaderboard"]["ranked"][0]
    rec = agent_record(paths, "site-train", 2, best["agent_id"])
    assert rec["name"] == nickname(best["genome_hash"], best["label"])
    assert rec["source"]["run_id"] == "site-train" and rec["source"]["round"] == 2 and rec["source"]["rank"] == 1
    assert sa.SiteAgent.model_validate(rec).checked_genome().agent_id == best["agent_id"]

    page = str(Path(__file__).resolve().parents[2] / "lab_app/pages/4_Training.py")
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(paths.root))
    monkeypatch.setenv("SNOWAGENT_SITE_REPO", str(work))
    at = AppTest.from_file(page, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    send_btn = [b for b in at.button if b.label.startswith("Send ")]
    assert len(send_btn) == 1 and rec["name"] in send_btn[0].label
    send_btn[0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert [a["name"] for a in on_site(work)] == [rec["name"]]
    assert any("sent" in str(s.value) for s in at.success)
    [rm] = [b for b in at.button if b.label == "Remove"]
    rm.click().run()
    assert not at.exception and on_site(work) == []


# --------------------------------------------------------------------------------------------- blind test (ADR-086)


def test_freezing_for_the_blind_test_is_permanent_and_capped(trained, checkout, monkeypatch):
    from datetime import UTC, datetime

    from snowagent.lab.services import blind_test as bt
    from snowagent.lab.services.site_send import SendError, on_site
    from snowagent.lab.training.loop import load_round, training_root

    paths, (work, origin) = trained, checkout
    ranked = load_round(training_root(paths) / "site-train", 2)["leaderboard"]["ranked"]
    now = datetime(2026, 10, 6, 15, tzinfo=UTC)
    rec = bt.entry_record(paths, "site-train", 2, ranked[0]["agent_id"], work, now=now)
    assert rec["season"] == "2026-2027" and rec["frozen_utc"] == "2026-10-06T15:00:00+00:00"
    assert len(rec["code_hash"]) == 64 and rec["git_commit"] and rec["scoring_version"]
    bt.freeze(work, rec)
    with pytest.raises(SendError, match="already frozen"):
        bt.freeze(work, rec)
    got = bt.frozen(work, "2026-2027")
    assert [e["name"] for e in got] == [rec["name"]] and got[0]["run_id"] == "site-train"
    assert bt.frozen(work, "2027-2028") == [] and len(bt.frozen(work)) == 1
    assert on_site(work) == []  # the site's agents are a separate folder
    names = _git(origin, "ls-tree", "-r", "--name-only", sa.BRANCH).split("\n")
    assert names == [f"blind_test/2026-2027/{ranked[0]['agent_id']}.json"]

    monkeypatch.setattr(bt, "MAX_ENTRIES", 1)
    with pytest.raises(SendError, match="already has 1"):
        bt.freeze(work, bt.entry_record(paths, "site-train", 2, ranked[1]["agent_id"], work, now=now))

    bad = dict(rec, season="next winter")
    with pytest.raises(ValueError):
        bt.freeze(work, bad)
    entries, skipped = bt.parse_entries({"a.json": json.dumps(rec).encode(), "b.json": b"{",
                                         "c.json": json.dumps(dict(rec, extra=1)).encode()})
    assert len(entries) == 1 and len(skipped) == 2


def test_the_training_page_freezes_an_agent(trained, checkout, monkeypatch):
    pytest.importorskip("streamlit", reason="lab extra not installed (pip install -e '.[lab]')")
    from streamlit.testing.v1 import AppTest

    from snowagent.lab.services.blind_test import frozen

    paths, (work, _) = trained, checkout
    page = str(Path(__file__).resolve().parents[2] / "lab_app/pages/4_Training.py")
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(paths.root))
    monkeypatch.setenv("SNOWAGENT_SITE_REPO", str(work))
    at = AppTest.from_file(page, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    [btn] = [b for b in at.button if b.label.startswith("Freeze ")]
    btn.click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(frozen(work)) == 1 and any("frozen for the" in str(s.value) for s in at.success)
    assert not [b for b in at.button if b.label.startswith("Freeze ")]  # frozen once only
