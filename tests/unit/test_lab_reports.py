"""Training run reports (ADR-082): the plain-language report from a small fake-engine run, its downloads and the
Reports page."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

REPO = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def trained(tmp_path_factory):
    from snowagent.lab.benchmark.builder import build_cases
    from snowagent.lab.competition.runner import EngineSpec
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths
    from snowagent.lab.training.loop import TrainOptions, run_training
    from tests.unit.lab_fixtures import write_multiseason_lab

    tmp = tmp_path_factory.mktemp("reports")
    cfg = load_lab_config(REPO / "config/lab.yaml")
    paths = LabPaths(tmp / "lab")
    write_multiseason_lab(paths.root, tmp / "checkout", cfg, ("2022-2023", "2023-2024"))
    build_cases(paths, cfg, tmp / "checkout", exclude_flagged=True)
    run_training(paths, cfg, TrainOptions.from_config(cfg, rounds=3, population=4, engine=EngineSpec(kind="fake")),
                 run_id="rep-train", log=lambda m: None)
    return paths, cfg


def test_the_report_is_plain_and_traceable(trained):
    from snowagent.lab.services.reports import training_report

    paths, cfg = trained
    rep = training_report(paths, "rep-train", cfg.genome, now=datetime(2026, 10, 6, 12, tzinfo=UTC))
    md = rep.to_markdown()
    for heading in ("## In short", "## What the score means", "## What got better", "## How long it took",
                    "## What to do next", "## Words used here", "## Reference"):
        assert heading in md
    assert "not an avalanche forecast" in md and "out of 100" in md
    assert "Proven on unseen winters: not yet" in md  # no promotion check exists
    assert "rep-train" in md and "Genome hash" in md and "Scoring version" in md  # traceability
    assert "Appendix" not in md  # the technical tables only on request
    assert "Whole run:" in md and "Last round (round 3):" in md and "an 8-hour night covers about" in md


def test_the_html_is_one_self_contained_file_and_the_appendix_is_optional(trained):
    from snowagent.lab.services.reports import training_report

    paths, cfg = trained
    rep = training_report(paths, "rep-train", cfg.genome, technical=True)
    doc = rep.to_html()
    assert doc.startswith("<!doctype html>") and "<svg" in doc and "<table>" in doc
    assert "<script" not in doc and "src=" not in doc and "href=" not in doc  # nothing to fetch: opens anywhere
    assert "Appendix: details for specialists" in doc and "not an avalanche forecast" in doc
    assert "Appendix: details for specialists" in rep.to_markdown()


def test_bad_choices_are_refused_and_copies_are_saved(trained):
    from snowagent.lab.services.reports import report_filename, save_report, training_report

    paths, cfg = trained
    with pytest.raises(ValueError, match="rank"):
        training_report(paths, "rep-train", cfg.genome, rank=99)
    with pytest.raises(ValueError, match="not committed"):
        training_report(paths, "rep-train", cfg.genome, round_no=9)
    rep = training_report(paths, "rep-train", cfg.genome, round_no=2)
    files = save_report(paths, rep, "rep-train", 2, 1)
    assert [f.name for f in files] == [report_filename("rep-train", 2, 1, "html"), "report-rep-train-r02-k1.md"]
    assert all(f.parent == paths.outputs / "reports" and f.stat().st_size > 1000 for f in files)


def test_settings_read_as_plain_sentences(trained):
    from snowagent.lab.services.reports import plain_change, plain_changes

    _, cfg = trained
    assert plain_change("sp_precip_mult_goat", 1.0, 1.2) == "Assumes 20% more snowfall at Goat's Eye than the gauge measured."
    assert plain_change("sp_wind_mult", 1.0, 0.79) == "Treats the wind as 21% weaker than measured."
    assert "70% → 96% sure" in plain_change("presence_confidence", 0.7, 0.958)
    out = plain_changes({"sp_hn_density": ["PARAMETERIZED", "FIXED"],
                         "sp_hn_density_parameterization": ["LEHNING_NEW", "NIED"],
                         "depth_spread_frac": [0.12, 0.08], "depth_spread_floor_m": [0.05, 0.10],
                         "sp_roughness_length_m": [0.002, 0.00187]}, cfg.genome)
    assert out[0].startswith("Uses a fixed density for new snow (100 kg/m³)")  # one sentence for the three genes
    assert "narrower range for total snow depth when the snow is deep, and a wider one" in " ".join(out)
    assert out[-1] == "Makes 1 other small adjustment."


def test_reports_page(trained, tmp_path, monkeypatch):
    pytest.importorskip("streamlit", reason="lab extra not installed (pip install -e '.[lab]')")
    from streamlit.testing.v1 import AppTest

    page = str(REPO / "lab_app/pages/7_Reports.py")
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(tmp_path / "empty"))
    at = AppTest.from_file(page, default_timeout=60).run()
    assert not at.exception and any("No training run yet" in str(i.value) for i in at.info)

    paths, _ = trained
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(paths.root))
    at = AppTest.from_file(page, default_timeout=60).run()
    assert not at.exception and {s.label: s.value for s in at.selectbox}["Training run"] == "rep-train"
    at.button[0].click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert len(at.get("download_button")) == 2
    assert (paths.outputs / "reports" / "report-rep-train-r03-k1.html").is_file()


def test_weak_layers_by_kind_from_rows_and_from_the_cache(trained):
    """ADR-091: the report breaks the weak-layer score down by kind; a run scored before the counts existed gets the
    same numbers from its cached predictions."""
    import pandas as pd

    from snowagent.lab.services import weak_layers
    from snowagent.lab.services.reports import _gather, training_report
    from snowagent.lab.training.loop import round_dir

    paths, cfg = trained
    md = training_report(paths, "rep-train", cfg.genome).to_markdown()
    assert "## Weak layers by kind" in md and "Surface hoar" in md and "false alarms" in md
    c = _gather(paths, "rep-train", None, 1)
    assert weak_layers.has_counts(c["sb"])
    hashes = [str(c["sa"]["genome_hash"].iloc[0]), c["best"]["genome_hash"]]
    got = weak_layers.from_cache(paths, cfg, "rep-train", hashes)
    assert got is not None
    for h, rows in zip(hashes, (c["sa"], c["sb"]), strict=True):
        assert weak_layers.counts(got[h].loc[rows.index.intersection(got[h].index)]) == weak_layers.counts(rows)
    # an older run: score rows without the counts; the report falls back to the cache only when given the config
    for r in (1, c["r"]):
        f = round_dir(c["run_dir"], r) / "scores.parquet"
        df = pd.read_parquet(f)
        df.drop(columns=weak_layers.COLS).to_parquet(f, index=False)
    assert "## Weak layers by kind" not in training_report(paths, "rep-train", cfg.genome).to_markdown()
    assert "## Weak layers by kind" in training_report(paths, "rep-train", cfg.genome, cfg=cfg).to_markdown()


def test_concern_by_class_counts_each_kind_once():
    from snowagent.lab.competition.scoring import Col, concern_by_class
    from snowagent.lab.schemas.profile import CriticalClass as K

    obs = [Col(0.1, 0.2, "SH", 1.0, K.surface_hoar), Col(0.5, 0.6, "FC", 2.0, K.facets),
           Col(0.9, 1.0, "DH", 2.0, K.depth_hoar)]
    pred = [Col(0.12, 0.2, "SH", 1.0, K.surface_hoar, 0.9), Col(0.5, 0.6, "FC", 2.0, K.facets, 0.4),
            Col(0.3, 0.4, "MF", 5.0, K.crust, 0.8)]
    c = concern_by_class(pred, obs)
    assert (c["wl_surface_hoar_observed"], c["wl_surface_hoar_found"]) == (1, 1)
    assert (c["wl_facets_forecast"], c["wl_facets_found"]) == (0, 0)  # below 0.5: not forecast
    assert (c["wl_depth_hoar_observed"], c["wl_depth_hoar_found"]) == (1, 0)
    assert (c["wl_crust_observed"], c["wl_crust_forecast"]) == (0, 1)
