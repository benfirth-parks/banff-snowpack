"""`snowagent lab init` / `lab import`: Parquet tables, run registry, raw inputs never changed, lazy lab deps."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from snowagent.lab.ingest.profiles import profile_from_observed
from snowagent.lab.settings import load_lab_config
from snowagent.lab.storage.paths import LabPaths

REPO = Path(__file__).resolve().parents[2]
FIX = REPO / "tests/fixtures/lab"


@pytest.fixture()
def source(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    shutil.copytree(FIX / "fts360", root / "data/raw/fts360")
    (root / "data/interim/obs").mkdir(parents=True)
    shutil.copy(FIX / "observed_profiles.jsonl", root / "data/interim/obs/observed_profiles.jsonl")
    return root


def _snapshot(root: Path) -> dict[str, tuple[str, int, int]]:
    return {str(p.relative_to(root)): (hashlib.sha256(p.read_bytes()).hexdigest(), p.stat().st_mtime_ns,
                                       p.stat().st_mode)
            for p in sorted(root.rglob("*")) if p.is_file()}


def test_lab_extra_not_imported_by_core_cli():
    """The daily run imports snowagent.cli; with the lab packages unavailable it must still import and run."""
    code = (
        "import sys, importlib.abc\n"
        "class Block(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path=None, target=None):\n"
        "        if name.split('.')[0] in {'streamlit', 'plotly', 'pyarrow', 'sklearn'}:\n"
        "            raise ImportError('blocked: ' + name)\n"
        "sys.meta_path.insert(0, Block())\n"
        "import snowagent.cli, snowagent.ops.update, snowagent.web.build, snowagent.lab.cli\n"
        "from typer.testing import CliRunner\n"
        "r = CliRunner().invoke(snowagent.cli.app, ['lab', '--help'])\n"
        "assert r.exit_code == 0, r.output\n"
        "print(sorted(m for m in sys.modules if m.split('.')[0] in {'streamlit', 'plotly', 'pyarrow', 'sklearn'}))\n")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "[]"


def test_import_writes_tables_and_registry_without_touching_inputs(source, tmp_path):
    pytest.importorskip("pyarrow")
    from snowagent.lab.services.data import (
        coverage,
        data_status,
        import_data,
        latest_runs,
        load_profile,
        load_weather,
    )

    cfg = load_lab_config(REPO / "config/lab.yaml")
    paths = LabPaths(tmp_path / "lab")
    before = _snapshot(source)
    report = import_data(source, paths, cfg)
    assert _snapshot(source) == before  # raw files never changed (content, time, mode)
    assert all(data_status(paths).values())
    bow = report["sites"]["BOW"]
    assert (bow["profiles"], bow["profiles_unique_usable"], bow["profiles_duplicates"]) == (2, 1, 1)
    assert bow["layers"] == 7 and bow["layers_of_concern"] == 4 and bow["stability_tests"] == 1
    assert bow["weather_hours"] == 48 and report["sites"]["SIMP"]["weather_hours"] == 0
    assert report["sites"]["SIMP"]["profiles_on_review_list"] == 1
    assert report["profiles_skipped"] == {"not_a_lab_site:tak_falls": 1, "not_a_lab_site:unknown": 1,
                                          "no_observation_time": 1}
    assert report["inputs"] == 3

    # registry: one write-once manifest with provenance
    runs = latest_runs(paths)
    assert len(runs) == 1
    m = runs[0]
    assert m.run_id == report["run_id"] and m.kind == "data_import" and m.config_hash == cfg.config_hash()
    assert len(m.data_hash) == 64 and len(m.inputs) == 3 and m.software_version
    assert "2024-01-24_goats_eye_syn003" in m.profile_ids_used and m.counts["BOW_weather_hours"] == 48
    assert json.loads((paths.manifests / f"{m.run_id}.json").read_text())["run_id"] == m.run_id
    with sqlite3.connect(paths.registry) as con:
        assert con.execute("SELECT count(*) FROM run_manifest").fetchone()[0] == 1

    # the stored profile is the converted profile
    rec = json.loads((FIX / "observed_profiles.jsonl").read_text().splitlines()[0])
    assert load_profile(paths, rec["profile_id"]) == profile_from_observed(rec, "BOW")
    w = load_weather(paths, "BOW")
    assert len(w) == 48 and str(w["observed_at"].dt.tz) == "UTC"
    cov = coverage(paths, cfg)
    assert cov["profiles"].set_index("site_code").loc["BOW", "season"] == "2023-2024"
    assert cov["weather"].iloc[0]["hours"] == 48

    # a second import adds a second manifest and the same data hash
    again = import_data(source, paths, cfg)
    assert again["data_hash"] == report["data_hash"] and len(latest_runs(paths)) == 2


def test_import_refuses_to_write_into_inputs(source):
    from snowagent.lab.services.data import LabImportError, import_data

    with pytest.raises(LabImportError, match="read-only input"):
        import_data(source, LabPaths(source / "data/raw/lab"), load_lab_config(REPO / "config/lab.yaml"))


def test_empty_data_root_reads_as_empty(tmp_path):
    from snowagent.lab.services.data import coverage, data_status, latest_runs, load_profiles, load_weather

    paths = LabPaths(tmp_path / "nothing")
    cfg = load_lab_config(REPO / "config/lab.yaml")
    assert not any(data_status(paths).values())
    assert load_profiles(paths).empty and load_weather(paths, "BOW").empty and latest_runs(paths) == []
    cov = coverage(paths, cfg)
    assert cov["profiles"].empty and cov["weather"].empty


def test_cli_init_and_import(source, tmp_path):
    pytest.importorskip("pyarrow")
    from snowagent.cli import app

    runner = CliRunner()
    root = tmp_path / "lab"
    cwd = os.getcwd()
    os.chdir(REPO)
    try:
        r = runner.invoke(app, ["lab", "init", "--data-root", str(root)])
        assert r.exit_code == 0, r.output
        out = json.loads(r.output)
        assert out["sites"]["SIMP"]["plot"] == "simpson" and out["split_mode"] == "all"  # ADR-059
        assert (root / "registry.sqlite").exists() and (root / "benchmark/sealed_test").is_dir()
        r = runner.invoke(app, ["lab", "import", "--source", str(source), "--data-root", str(root), "--only",
                                "profiles"])
        assert r.exit_code == 0, r.output
        rep = json.loads(r.output)
        assert rep["sites"]["GOAT"]["profiles"] == 1 and "weather_hours" not in rep["sites"]["BOW"]
        r = runner.invoke(app, ["lab", "import", "--source", str(tmp_path / "missing"), "--data-root", str(root)])
        assert r.exit_code == 2 and "snowagent obs profiles" in r.output
        r = runner.invoke(app, ["lab", "coverage", "--data-root", str(root)])
        assert r.exit_code == 0 and json.loads(r.output)["weather"] == []
    finally:
        os.chdir(cwd)
