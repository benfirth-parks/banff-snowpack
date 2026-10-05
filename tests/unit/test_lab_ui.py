"""Lab UI smoke test: every page runs without an exception, with no lab data and with imported synthetic data."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

pytest.importorskip("streamlit", reason="lab extra not installed (pip install -e '.[lab]')")
pytest.importorskip("plotly", reason="lab extra not installed (pip install -e '.[lab]')")
pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")
from streamlit.testing.v1 import AppTest  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
PAGES = [REPO / "lab_app/Home.py", REPO / "lab_app/pages/1_Data_Explorer.py",
         REPO / "lab_app/pages/2_Benchmark_Cases.py", REPO / "lab_app/pages/3_Leaderboard.py"]
FIX = REPO / "tests/fixtures/lab"


def _run(page: Path) -> AppTest:
    at = AppTest.from_file(str(page), default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _text(at: AppTest) -> str:
    return " ".join(str(x.value) for kind in ("markdown", "warning", "info", "caption") for x in getattr(at, kind))


@pytest.mark.parametrize("page", PAGES, ids=lambda p: p.name)
def test_pages_run_with_no_data(page, tmp_path, monkeypatch):
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(tmp_path / "empty"))
    at = _run(page)
    text = _text(at)
    assert "not an avalanche forecast" in text and "snowagent lab import" in text


def test_pages_run_with_imported_data(tmp_path, monkeypatch):
    from snowagent.lab.services.data import import_data
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths

    src = tmp_path / "checkout"
    shutil.copytree(FIX / "fts360", src / "data/raw/fts360")
    (src / "data/interim/obs").mkdir(parents=True)
    shutil.copy(FIX / "observed_profiles.jsonl", src / "data/interim/obs/observed_profiles.jsonl")
    import_data(src, LabPaths(tmp_path / "lab"), load_lab_config(REPO / "config/lab.yaml"))
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(tmp_path / "lab"))

    home = _run(PAGES[0])
    assert any(str(m.value) == "1" for m in home.metric)  # one unique usable pit at Bow Summit
    assert "No station weather imported for this site." in _text(home)  # Goat's Eye and Simpson have none
    explorer = _run(PAGES[1])
    sel = {s.label: s for s in explorer.selectbox}
    assert sel["Profile"].value == "2024-01-10_bow_summit_syn001"
    assert len(explorer.get("plotly_chart")) == 2  # profile plot and weather small multiples
    sel["Site"].set_value("SIMP").run()  # site without weather: empty states, no crash
    assert not explorer.exception
    assert "No station weather at Simpson" in _text(explorer)


def test_benchmark_page_with_built_cases(tmp_path, monkeypatch):
    from snowagent.lab.benchmark.builder import build_cases
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths
    from tests.unit.lab_fixtures import write_synthetic_lab

    cfg = load_lab_config(REPO / "config/lab.yaml")
    paths = LabPaths(tmp_path / "lab")
    write_synthetic_lab(paths.root, tmp_path / "checkout", cfg)
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(paths.root))
    page = REPO / "lab_app/pages/2_Benchmark_Cases.py"
    assert "No cases built yet" in _text(_run(page))

    rep = build_cases(paths, cfg, tmp_path / "checkout", exclude_flagged=True)
    at = _run(page)
    sel = {s.label: s for s in at.selectbox}
    assert sel["Case set"].value == "all" and sel["Split"].value == "training"
    assert any(m.label == "cases" and str(m.value) == str(rep["case_counts"]["BOW"]["training"]["forecast_h72"]) for m in at.metric)
    assert at.get("plotly_chart")  # visible weather and the latest visible pit
    at.toggle[0].set_value(True).run()  # training truth may be shown
    assert not at.exception
    sel = {s.label: s for s in at.selectbox}
    sel["Case type"].set_value("next_pit").run()
    assert not at.exception
    [b for b in at.button if b.label == "Re-run the leakage checks"][0].click().run()
    assert not at.exception and any("pass" in str(s.value) for s in at.success)


def test_leaderboard_page_with_a_competition_run(tmp_path, monkeypatch):
    from snowagent.lab.benchmark.builder import build_cases
    from snowagent.lab.competition.runner import EngineSpec, run_competition
    from snowagent.lab.genome import default_genomes
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths
    from tests.unit.lab_fixtures import write_synthetic_lab

    cfg = load_lab_config(REPO / "config/lab.yaml")
    paths = LabPaths(tmp_path / "lab")
    write_synthetic_lab(paths.root, tmp_path / "checkout", cfg)
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(paths.root))
    page = REPO / "lab_app/pages/3_Leaderboard.py"
    build_cases(paths, cfg, tmp_path / "checkout", exclude_flagged=True)
    assert "No competition run yet" in _text(_run(page))

    run_competition(paths, cfg, default_genomes(), engine=EngineSpec(kind="fake"), run_id="ui-run")
    at = _run(page)
    assert len(at.dataframe) >= 1 and "7 cases" in " ".join(str(h.value) for h in at.subheader)
    assert len(at.get("plotly_chart")) == 2  # the prediction beside the observed pit
    multi = {m.label: m for m in at.multiselect}
    multi["Forecast source"].set_value(["archived_gfs"]).run()
    assert not at.exception and "1 cases" in " ".join(str(h.value) for h in at.subheader)
    sel = {s.label: s for s in at.selectbox}
    sel["Agent"].set_value("analogue-default").run()  # an insufficient answer: shown as text, no crash
    assert not at.exception


def test_training_page_without_and_with_runs(tmp_path, monkeypatch):
    from snowagent.lab.benchmark.builder import build_cases
    from snowagent.lab.competition.runner import EngineSpec
    from snowagent.lab.services import training as svc
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths
    from snowagent.lab.training.loop import TrainOptions, run_training
    from tests.unit.lab_fixtures import write_multiseason_lab

    page = REPO / "lab_app/pages/4_Training.py"
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(tmp_path / "empty"))
    text = _text(_run(page))
    assert "not an avalanche forecast" in text and "No training run yet" in text and "build-cases" in text

    cfg = load_lab_config(REPO / "config/lab.yaml")
    paths = LabPaths(tmp_path / "lab")
    write_multiseason_lab(paths.root, tmp_path / "checkout", cfg, ("2022-2023", "2023-2024"))
    build_cases(paths, cfg, tmp_path / "checkout", exclude_flagged=True)
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(paths.root))
    launched = []

    class FakePopen:
        def __init__(self, cmd, **kw):
            launched.append((cmd, kw))
            self.pid = 4242

    monkeypatch.setattr(svc, "_launch", FakePopen)
    at = _run(page)
    assert "No training run yet" in _text(at)
    at.button[0].click().run()  # the form's Start training button
    assert not at.exception and launched
    cmd, kw = launched[0]
    assert cmd[1:5] == ["-m", "snowagent.cli", "lab", "train"] and kw["start_new_session"]  # detached process
    assert cmd[cmd.index("--rounds") + 1] == "10" and cmd[cmd.index("--data-root") + 1] == str(paths.root.resolve())
    assert any("Started training run" in str(s.value) for s in at.success)

    run_training(paths, cfg, TrainOptions.from_config(cfg, rounds=3, population=4, engine=EngineSpec(kind="fake")),
                 run_id="ui-train", log=lambda m: None)
    at = _run(page)
    sel = {s.label: s for s in at.selectbox}
    sel["Training run"].set_value("ui-train").run()
    assert not at.exception
    text = _text(at)
    assert "warning signal only" in text and "No promotion check yet" in text and "check-loso" in text
    assert len(at.get("plotly_chart")) == 2  # best composite per round, gap with flags
    assert len(at.dataframe) >= 2 and len(at.code) == 1  # leaderboard and lineage
    assert any(m.label == "State" and m.value == "finished" for m in at.metric)

    from snowagent.lab.training.loso import check_loso

    check_loso(paths, cfg, "ui-train/3/1", None, source=tmp_path / "checkout", log=lambda m: None,
               check_id="ui-check")
    at = _run(page)
    {s.label: s for s in at.selectbox}["Training run"].set_value("ui-train").run()
    assert not at.exception
    assert any("pooled held-out composite" in str(x.value) for x in [*at.success, *at.error])
