"""Stability check (ADR-127): reading the pits' tests, SNOWPACK's stability numbers and scoring them."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.lab.services.stability import (  # noqa: E402
    Break,
    add_lemons,
    eligible_cases,
    parse_test,
    pit_breaks,
    read_stability,
    report_lines,
    run_stability_check,
    score_pit,
    verdict,
)
from snowagent.lab.settings import load_lab_config  # noqa: E402
from snowagent.lab.storage.paths import LabPaths  # noqa: E402
from tests.unit.lab_fixtures import write_synthetic_lab  # noqa: E402

REPO = Path(__file__).resolve().parents[2]
PRO = REPO / "tests/fixtures/engine_sample_slope35.pro"


# --------------------------------------------------------------------------------------------- the pits' tests


def test_snowpro_compression_result_is_read_from_its_raw_text():
    b = parse_test({"raw": "Compression M  - Moderate 14 SC x2", "type": "CT", "result": None, "score": None,
                    "fracture_character": None, "depth_m": 0.42})
    assert b == Break(0.42, "CT", 14.0, True, True)
    hard = parse_test({"raw": "Compression H  - Hard 26", "type": "CT", "result": None, "depth_m": 0.8})
    assert hard.taps == 26.0 and hard.sudden is None and not hard.easy_sudden


def test_test_kinds_and_results():
    ct = {"type": "CT", "result": "CTE4", "score": 4.0, "fracture_character": "RP", "depth_m": 0.5}
    assert parse_test(ct) == Break(0.5, "CT", 4.0, False, False)  # easy but resistant
    assert parse_test(ct | {"fracture_character": "SP"}).easy_sudden
    assert parse_test(ct | {"result": "CTH25", "score": 25.0, "fracture_character": "SP"}).easy_sudden is False
    assert parse_test(ct | {"result": "CTN", "score": None}) is None  # no break
    assert parse_test(ct | {"result": "CTV", "score": None, "fracture_character": "SC"}).taps == 0.0
    assert parse_test(ct | {"depth_m": None}) is None  # no depth: cannot be placed
    assert parse_test({"type": "EXTENDEDCOLUMN", "result": "ECTP12", "depth_m": 0.6}).easy_sudden
    assert parse_test({"type": "ECT", "result": "ECTN20", "depth_m": 0.6}).sudden is False
    assert parse_test({"type": "ECT", "result": "ECTX", "depth_m": 0.6}) is None
    assert parse_test({"type": "PST", "result": "PST 35/100 End", "depth_m": 0.9}).easy_sudden
    assert parse_test({"type": None, "result": None, "raw": "010,S,M,", "depth_m": 0.1}) is None


def test_tests_at_one_depth_are_one_broken_layer():
    out = pit_breaks([{"type": "CT", "result": "CTM15", "score": 15, "fracture_character": "RP", "depth_m": 0.40},
                      {"type": "CT", "result": "CTE7", "score": 7, "fracture_character": "SC", "depth_m": 0.41},
                      {"type": "CT", "result": "CTH22", "score": 22, "depth_m": 0.90}])
    assert [round(b.depth_m, 2) for b in out] == [0.40, 0.90]
    assert out[0].easy_sudden and out[0].sudden


# --------------------------------------------------------------------------------------------- SNOWPACK's numbers


def test_stability_numbers_are_read_from_the_profile_file():
    s = read_stability(PRO, pd.Timestamp("2025-12-01T06:00:00Z"))
    assert s["profile_time"].startswith("2025-12-01T06:00")
    lay = s["layers"]
    assert lay[0]["top_m"] == 0.0 and lay[-1]["bottom_m"] == pytest.approx(s["snow_depth_m"])
    assert all(a["bottom_m"] == pytest.approx(b["top_m"]) for a, b in zip(lay, lay[1:], strict=False))
    assert lay[-1]["crack"] is None  # -999 in the file (the ground element)
    weakest = min((x for x in lay if x["sk38"] is not None), key=lambda x: x["sk38"])
    assert weakest["sk38"] == pytest.approx(0.1) and weakest["top_m"] == pytest.approx(0.6879)
    assert {x["grain_class"] for x in lay} >= {3}


def _layer(top, bottom, sk38=3.0, crack=0.8, rta=0.1, gs=0.5, gc=3, hard=3.0):
    return {"top_m": top, "bottom_m": bottom, "sk38": sk38, "crack": crack, "rta": rta, "grain_size_mm": gs,
            "grain_class": gc, "hardness": hard}


def test_lemons_count_the_six_signs():
    lay = [_layer(0.0, 0.3, hard=3.0, gs=0.5), _layer(0.3, 0.35, gc=4, gs=1.5, hard=1.0), _layer(0.35, 1.0, hard=4.0)]
    add_lemons(lay)
    assert lay[1]["lemons"] == 6.0  # facets, 1.5 mm, fist, size and hardness jumps, 30 cm deep
    assert lay[0]["lemons"] == 2.0  # only its size and hardness jumps with the facets below


# --------------------------------------------------------------------------------------------- scoring


def test_weakest_spot_at_the_break_is_a_hit_and_chance_is_the_share_near_a_break():
    model = {"snow_depth_m": 1.0, "layers": [_layer(0.0, 0.3), _layer(0.3, 0.35, sk38=0.8, crack=0.2, rta=0.9),
                                             _layer(0.35, 1.0, sk38=2.0, crack=0.5, rta=0.3)]}
    s = score_pit(model, [Break(0.33, "CT", 12, True, True)], pit_hs_m=1.0)
    r = s["all"]
    assert r["sk38"]["hit"] == 1.0 and r["crack"]["hit"] == 1.0 and r["rta"]["hit"] == 1.0
    # every layer reaches within 10 cm of the break: chance is the whole column here
    assert r["chance"] == pytest.approx(1.0)
    assert r["sk38"]["found"] == 1 and r["rta"]["found"] == 1 and r["crack"]["found"] == 1
    assert s["easy_sudden"]["breaks"] == 1


def test_a_miss_and_the_stretch_to_the_pits_depth():
    # the model is half as deep as the pit: its weak layer at 0.25 m sits at 0.5 m on the pit's scale
    model = {"snow_depth_m": 0.6, "layers": [_layer(0.0, 0.24), _layer(0.24, 0.26, sk38=0.5),
                                             _layer(0.26, 0.6, sk38=4.0)]}
    hit = score_pit(model, [Break(0.50, "CT", 10, None, False)], pit_hs_m=1.2)
    assert hit["stretch"] == 2.0 and hit["all"]["sk38"]["hit"] == 1.0 and "easy_sudden" not in hit
    miss = score_pit(model, [Break(0.95, "CT", 10, None, False)], pit_hs_m=1.2)
    assert miss["all"]["sk38"]["hit"] == 0.0 and 0 < miss["all"]["chance"] < 1


def test_verdict_from_the_winter_range():
    assert verdict({"range90": (0.02, 0.2)}) == "better than chance"
    assert verdict({"range90": (-0.1, 0.2)}) == "no better than chance"
    assert verdict({"range90": None}) == "too few winters to tell"


# --------------------------------------------------------------------------------------------- the check


@pytest.fixture()
def lab(tmp_path):
    from snowagent.lab.benchmark import builder

    cfg = load_lab_config(REPO / "config/lab.yaml")
    paths = LabPaths(tmp_path / "lab")
    source = tmp_path / "checkout"
    write_synthetic_lab(paths.root, source, cfg)
    builder.build_cases(paths, cfg, source, exclude_flagged=True)
    return cfg, paths


def test_check_runs_scores_resumes_and_reports(lab):
    cfg, paths = lab
    cases, locked = eligible_cases(paths)
    assert cases and locked == []  # the synthetic lab has too few winters to lock any
    assert all(c["breaks"][0]["kind"] == "ECT" for c in cases)  # the fixture's test: ECTN12 at 0.3 m
    calls = []

    def fake(case_dir: Path, genes: dict) -> dict:
        calls.append(case_dir.name)
        # the weak layer reaches the break at 0.3 m whatever the pit's depth it is stretched to (0.6 to 1.5 m)
        return {"snow_depth_m": 1.0, "layers": [_layer(0.0, 0.25), _layer(0.25, 0.5, sk38=0.7, crack=0.25, rta=0.9),
                                                _layer(0.5, 1.0, sk38=2.5, crack=0.7, rta=0.2)]}

    res = run_stability_check(paths, cfg, ["standard"], check_id="sc-test", log=lambda s: None, profiler=fake)
    assert len(calls) == len(cases) and res["failed_runs"] == 0
    summ = res["summary"]["standard"]["subsets"]["all"]
    assert summ["sk38"]["hit"] == 1.0 and summ["sk38"]["found_share"] == 1.0
    assert "easy_sudden" not in res["summary"]["standard"]["subsets"] or not res["summary"]["standard"]["subsets"][
        "easy_sudden"]
    cdir = paths.outputs / "stability_checks" / "sc-test"
    assert (cdir / "report.md").is_file() and json.loads((cdir / "plan.json").read_text())["cases"] == len(cases)
    assert "Every layer that broke" in "\n".join(report_lines(res))
    calls.clear()
    run_stability_check(paths, cfg, ["standard"], check_id="sc-test", log=lambda s: None, profiler=fake)
    assert calls == []  # every run kept from before


def test_cli_and_page_registration(lab, monkeypatch):
    from typer.testing import CliRunner

    from snowagent.lab.cli import lab_app
    from snowagent.lab.services.activity import WHAT
    from snowagent.lab.services.jobs import KINDS
    from snowagent.lab.ui.pages import MENU, PAGES

    assert "stability-check" in KINDS and WHAT["stability-check"][2] == "stability"
    assert (REPO / "lab_app/pages" / PAGES["stability"][0]).is_file()
    assert "stability" in MENU["Competitions and checks"]
    _cfg, paths = lab
    out = CliRunner().invoke(lab_app, ["stability-check", "--agent", "nonsense", "--data-root", str(paths.root),
                                       "--config", str(REPO / "config/lab.yaml")])
    assert out.exit_code == 2 and "standard" in out.output


def test_check_code_is_outside_the_prediction_hash():
    from snowagent.lab.training.cache import CODE_EXCLUDE

    assert "lab/services/stability.py".startswith(CODE_EXCLUDE)  # adding it re-runs no cached forecast


def test_engine_reads_the_numbers_of_the_profile_the_agents_read():
    from types import SimpleNamespace

    from snowagent.lab.services.stability import StabilityEngine

    eng = StabilityEngine()
    hs, layers, lag = eng._profile(SimpleNamespace(pro=str(PRO)), pd.Timestamp("2025-12-01T06:00:00Z"))
    assert eng.stability is not None and lag == 0.0
    assert eng.stability["snow_depth_m"] == pytest.approx(hs, abs=0.02)  # the same profile as the agents' layers


def test_the_page_offers_a_new_check_and_shows_a_finished_one(lab, tmp_path, monkeypatch):
    pytest.importorskip("streamlit", reason="lab extra not installed (pip install -e '.[lab]')")
    from streamlit.testing.v1 import AppTest

    page = str(REPO / "lab_app/pages/14_Stability_check.py")
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(tmp_path / "empty"))
    at = AppTest.from_file(page, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("No finished stability check yet" in str(i.value) for i in at.info)
    assert any(b.label == "Start the stability check" for b in at.button)

    cfg, paths = lab

    def fake(case_dir: Path, genes: dict) -> dict:
        return {"snow_depth_m": 1.0, "layers": [_layer(0.0, 0.25), _layer(0.25, 0.5, sk38=0.7), _layer(0.5, 1.0)]}

    run_stability_check(paths, cfg, ["standard"], check_id="sc-page", log=lambda s: None, profiler=fake)
    monkeypatch.setenv("SNOWAGENT_LAB_DATA_ROOT", str(paths.root))
    at = AppTest.from_file(page, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("hits" in str(m.value) for m in at.markdown)


def _fake_run(paths: LabPaths, run_id: str, created: str, genomes: list, case_set: str = "all") -> None:
    from snowagent.lab.training.loop import round_dir, training_root

    d = training_root(paths) / run_id
    rd = round_dir(d, 3)
    rd.mkdir(parents=True)
    (d / "run.json").write_text(json.dumps({"created_at": created, "plan": {"case_set": case_set}}))
    (rd / "round.json").write_text("{}")
    (rd / "population.json").write_text(json.dumps([{"genome": g.model_dump(mode="json")} for g in genomes]))
    (rd / "leaderboard.json").write_text(json.dumps({"ranked": [{"genome_hash": g.genome_hash} for g in genomes]}))


def test_agent_choices_offer_training_runs_once_each_and_leave_out_promotion_check_folds(tmp_path):
    from snowagent.lab.genome import default_genome, make_genome
    from snowagent.lab.schemas.genome import AgentFamily
    from snowagent.lab.services.stability import agent_choices

    cfg = load_lab_config(REPO / "config/lab.yaml")
    paths = LabPaths(tmp_path / "lab")
    std = default_genome(AgentFamily.snowpack, cfg.genome)
    evolved = make_genome(AgentFamily.snowpack, dict(std.genes) | {"presence_confidence": 0.61}, cfg.genome,
                          label="r12-x02-snowpack")
    fold_agent = make_genome(AgentFamily.snowpack, dict(std.genes) | {"presence_confidence": 0.62}, cfg.genome,
                             label="fold-agent")
    column = default_genome(AgentFamily.column, cfg.genome)
    _fake_run(paths, "training-main", "2026-11-19T00:17:33+00:00", [evolved, std, column])
    _fake_run(paths, "training-main-r13-k1-loso-reduced-3s", "2026-11-19T13:32:00+00:00", [fold_agent, std],
              case_set="loso_2020-2021")
    out = agent_choices(paths, cfg.genome)
    assert "training-main/3/1" in out  # the main run's top agent
    assert "training-main/3/2" not in out  # standard SNOWPACK again: offered once
    assert "training-main/3/3" not in out  # the column model has no stability numbers
    assert not any("loso" in k for k in out)  # the promotion check's fold
