"""The group check (ADR-095): pooling agents' predictions into a consensus profile with disagreement measures, the
fairness rule for members, the check on a training run's locked winters, its report and the Reports page."""

from __future__ import annotations

import json
import math
from pathlib import Path

import pandas as pd
import pytest

from snowagent.lab.competition import scoring
from snowagent.lab.schemas.benchmark import TargetScope
from snowagent.lab.schemas.profile import CriticalClass
from tests.unit.test_lab_scoring import _pred, _truth

REPO = Path(__file__).resolve().parents[2]
OT, SH, FC = CriticalClass.other, CriticalClass.surface_hoar, CriticalClass.facets


def _member(sh_at: float | None, hs: float = 1.0, facets: bool = False):
    """Rounded snow, 1F over P, with a surface hoar layer at ``sh_at`` (relative depth) and optional basal facets."""
    layers = []
    top = 0.0
    if sh_at is not None:
        layers += [(0.0, sh_at, "RG", "1F", OT, 0.9), (sh_at, sh_at + 0.02, "SH", "F", SH, 0.9)]
        top = sh_at + 0.02
    bottom = 0.8 if facets else 1.0
    layers.append((top, bottom, "RG", "P", OT, 0.9))
    if facets:
        layers.append((0.8, 1.0, "FC", "4F", FC, 0.9))
    return _pred([(t * hs, b * hs, g, h, c, p) for t, b, g, h, c, p in layers], hs=hs)


def test_a_weak_layer_most_agents_forecast_is_in_the_consensus_and_a_lone_one_is_not_forecast():
    from snowagent.lab.services.group import consensus

    preds = [_member(0.40), _member(0.42), _member(0.41), _member(None, facets=True), _member(None)]
    view = consensus(preds)
    sh = [g for g in view.layers if g.kind == "surface_hoar"]
    fc = [g for g in view.layers if g.kind == "facets"]
    assert len(sh) == 1 and sh[0].support == pytest.approx(0.6) and len(sh[0].members) == 3
    assert len(fc) == 1 and fc[0].support == pytest.approx(0.2)  # one agent of five: below the floor
    layers = view.prediction.layers
    sh_layers = [ly for ly in layers if ly.critical_class == SH]
    assert len(sh_layers) == 1 and sh_layers[0].probability_present == pytest.approx(0.6)
    assert not any(ly.critical_class == FC and ly.probability_present >= 0.5 for ly in layers)
    assert view.stats["weak_layers_agreed"] == 0 and view.stats["weak_layers_split"] == 1  # 3 of 5 is split
    # the consensus is a valid prediction the scorer accepts: the agreed layer is found in a pit that has it
    truth = _truth([(0.0, 0.41, "RG", 3.0, OT), (0.41, 0.43, "SH", 1.0, SH), (0.43, 1.0, "RG", 4.0, OT)])
    s = scoring.score_case(view.prediction, truth, TargetScope.full_profile)
    assert s["critical_layers"] == 1.0


def test_split_agents_show_as_disagreement_and_depth_spread():
    from snowagent.lab.services.group import consensus

    agree = consensus([_member(0.4, hs=1.0), _member(0.4, hs=1.02), _member(0.4, hs=0.98)])
    split = consensus([_member(0.4, hs=0.7), _member(None, hs=1.0), _member(0.4, hs=1.3), _member(None, hs=1.1)])
    assert agree.stats["depth_spread_m"] < 0.05 < split.stats["depth_spread_m"]
    assert agree.stats["structure_disagreement"] < split.stats["structure_disagreement"]
    assert split.stats["weak_layers_split"] == 1 and agree.stats["weak_layers_split"] == 0
    assert split.prediction.bulk_state.snow_depth_m.p50 == pytest.approx(1.05)


def test_silent_agents_count_as_doubt_and_too_few_answers_give_no_consensus():
    from snowagent.lab.services.group import consensus

    gone = _pred([], status="insufficient")
    view = consensus([_member(0.4), _member(0.4), gone, gone])
    assert view.stats["answered"] == 2 and view.layers[0].support == pytest.approx(0.5)
    assert consensus([_member(0.4), gone, gone]).prediction.status == "insufficient_data"


def test_the_verdict_rules():
    from snowagent.lab.services.group import _rises, _useful

    def thirds(lo, mid, hi, col="abs_error_m"):
        return [{col: lo}, {col: mid}, {col: hi}]

    assert _useful(thirds(0.05, 0.08, 0.15), "abs_error_m", True) == "yes"
    assert _useful(thirds(0.05, 0.20, 0.10), "abs_error_m", True) == "weak"
    assert _useful(thirds(0.10, 0.10, 0.11), "abs_error_m", True) == "no"
    assert _useful(thirds(0.7, 0.65, 0.5, "layer_structure"), "layer_structure", False) == "yes"
    assert _useful([], "x", True) == "too few cases"
    few = {"layers": 10, "share": 0.2}
    assert _rises([few, {}, {"layers": 10, "share": 0.5}]) == "yes"
    assert _rises([few, {}, {"layers": 10, "share": 0.25}]) == "no"
    assert _rises([few, {}, {"layers": 3, "share": 0.9}]) == "too few"


# --------------------------------------------------------------------------------------------- the check


SEASONS = ("2020-2021", "2021-2022", "2022-2023", "2023-2024")


@pytest.fixture(scope="module")
def lab(tmp_path_factory):
    pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")
    from snowagent.lab.benchmark.builder import build_cases
    from snowagent.lab.competition.runner import EngineSpec
    from snowagent.lab.settings import load_lab_config
    from snowagent.lab.storage.paths import LabPaths
    from snowagent.lab.training.loop import TrainOptions, run_training
    from tests.unit.lab_fixtures import write_multiseason_lab

    tmp = tmp_path_factory.mktemp("group")
    cfg = load_lab_config(REPO / "config/lab.yaml")
    paths = LabPaths(tmp / "lab")
    write_multiseason_lab(paths.root, tmp / "checkout", cfg, SEASONS)
    build_cases(paths, cfg, tmp / "checkout", exclude_flagged=True)
    fake = EngineSpec(kind="fake")
    run_training(paths, cfg, TrainOptions.from_config(cfg, rounds=2, population=4, engine=fake, locked_seasons=1),
                 run_id="clean", log=lambda m: None)
    run_training(paths, cfg, TrainOptions.from_config(cfg, rounds=2, population=4, engine=fake, locked_seasons=0),
                 run_id="saw-all", log=lambda m: None)
    return paths, cfg


def test_only_agents_that_never_saw_the_test_winters_may_join(lab):
    from snowagent.lab.services.group import clean_runs, default_members, resolve_member

    paths, cfg = lab
    assert clean_runs(paths, ["2023-2024"]) == ["clean"]
    keys = default_members(paths, ["2023-2024"])
    assert keys[:2] == ["standard", "kind:hybrid"] and "clean/2/1" in keys and not any("saw-all" in k for k in keys)
    assert sum(k.startswith("nudge:") for k in keys) == 4
    with pytest.raises(ValueError, match="learned from some of the test winters"):
        resolve_member(paths, "saw-all/2/1", ["2023-2024"], cfg.genome)
    more = resolve_member(paths, "nudge:more-snow", ["2023-2024"], cfg.genome)
    assert more.genome.genes["sp_precip_mult_goat"] == 1.15 and more.genome.family.value == "snowpack"


def test_the_group_check_scores_the_consensus_on_the_locked_winters_and_writes_a_report(lab):
    from snowagent.lab.services.group import list_group_checks, load_group_check, run_group_check
    from snowagent.lab.services.group_report import group_report

    paths, cfg = lab
    res = run_group_check(paths, cfg, "clean", workers=1, log=lambda m: None)
    assert res["seasons"] == ["2023-2024"] and res["cases"] > 0
    assert len(res["members"]) >= 5 and {m["group"] for m in res["members"]} >= {"standard", "weather nudge"}
    assert res["standard"]["name"] == "standard SNOWPACK" and res["vs_standard"] in ("better", "worse",
                                                                                    "about the same")
    assert json.loads((paths.outputs / "group_checks" / res["check_id"] / "result.json").read_text())
    assert list_group_checks(paths) == [res["check_id"]]
    loaded = load_group_check(paths, res["check_id"])
    cases = loaded["cases_df"]
    assert set(cases["season"]) == {"2023-2024"} and cases["composite"].notna().all()
    assert {"depth_spread_m", "structure_disagreement", "weak_layers_split"} <= set(cases.columns)
    assert isinstance(loaded["members_df"], pd.DataFrame) and len(loaded["members_df"]) == \
        len(cases) * len(res["members"])
    # a second check reuses every saved prediction
    again = run_group_check(paths, cfg, "clean", workers=1, log=lambda m: None)
    assert again["evaluation"]["cache_misses"] == 0
    rep = group_report(loaded)
    md, html = rep.to_markdown(), rep.to_html()
    for text in ("Who was in the group", "Is the group better than one agent", "disagree",
                 "weak layers most agents agree on"):
        assert text in md
    assert "standard SNOWPACK" in md and "<html" in html.lower()
    assert not any(isinstance(v, float) and math.isnan(v) for v in json.loads(
        (paths.outputs / "group_checks" / res["check_id"] / "result.json").read_text()).values())


def test_the_command_line_and_the_reports_page(lab, tmp_path, monkeypatch):
    pytest.importorskip("streamlit", reason="lab extra not installed (pip install -e '.[lab]')")
    from streamlit.testing.v1 import AppTest
    from typer.testing import CliRunner

    from snowagent.lab.cli import lab_app

    paths, _ = lab
    out = CliRunner().invoke(lab_app, ["group-check", "--test-run", "clean", "--member", "standard", "--member",
                                       "nudge:warmer", "--member", "nudge:colder", "--check-id", "cli-check",
                                       "--data-root", str(paths.root), "--config", str(REPO / "config/lab.yaml")])
    assert out.exit_code == 0, out.output
    assert "Group check cli-check" in out.output and (paths.outputs / "reports" / "report-cli-check.html").is_file()
    bad = CliRunner().invoke(lab_app, ["group-check", "--test-run", "clean", "--member", "saw-all/2/1",
                                       "--data-root", str(paths.root), "--config", str(REPO / "config/lab.yaml")])
    assert bad.exit_code == 2 and "learned from some of the test winters" in bad.output

    page = str(REPO / "lab_app/pages/7_Reports.py")
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(paths.root))
    at = AppTest.from_file(page, default_timeout=60).run()
    assert not at.exception
    at.radio[0].set_value("The agents as a group").run()
    assert not at.exception, [e.value for e in at.exception]
    picked = {m.label: m.value for m in at.multiselect}["Agents in the group"]
    assert "standard" in picked and "clean/2/1" in picked and not any("saw-all" in k for k in picked)
    assert len(at.get("download_button")) == 2
