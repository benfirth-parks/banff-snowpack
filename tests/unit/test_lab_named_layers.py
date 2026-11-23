"""Named-layer check (ADR-140): observers' dated layer names are read from the withheld pits only, parsed into a
formation day and kind, and each competition agent is scored on having a layer of that kind there, against chance,
split by whether the layer formed after the last pit the agent saw."""

from __future__ import annotations

import json
from datetime import date

import pandas as pd
import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.lab.benchmark import package as pkg  # noqa: E402
from snowagent.lab.services import named_layers as nl  # noqa: E402
from snowagent.lab.storage.paths import LabPaths  # noqa: E402
from snowagent.lab.ui.pages import PAGES  # noqa: E402

REPO = __import__("pathlib").Path(__file__).resolve().parents[2]


@pytest.mark.parametrize("tag,grain,month,day,kind", [
    ("Nov 5 crust complex", None, 11, 5, "crust"),
    ("April 10th suncrust", "MFcr", 4, 10, "sun_crust"),
    ("Oct 20 rain crust", None, 10, 20, "rain_crust"),
    ("121106 RC", None, 11, 6, "rain_crust"),
    ("Dec 15 Cr", None, 12, 15, "crust"),
    ("Nov crust?", "FC", 11, None, "crust"),
    ("January drought facets", "FC", 1, None, "facets"),
    ("Jan 24", "SH", 1, 24, "surface_hoar"),
    ("Jan 7 interface", "RG", 1, 7, "other"),
    ("xmas facets", None, 12, 25, "facets"),
    ("1017", "DH", 10, 17, "facets"),
    ("11/05", None, 11, 5, "other"),
])
def test_tags_are_read(tag, grain, month, day, kind):
    t = nl.parse_tag(tag, grain)
    assert (t.month, t.day, t.kind) == (month, day, kind)


@pytest.mark.parametrize("tag", [None, "", "Problematic layer", "2nd atmospheric river", "Old Rain Crust"])
def test_tags_without_a_month_are_not_named_layers(tag):
    assert nl.parse_tag(tag) is None


def test_formation_date_is_in_the_pits_winter():
    assert nl.formation_date(nl.parse_tag("Nov 5 crust"), date(2021, 1, 10)) == (date(2020, 11, 5), True)
    assert nl.formation_date(nl.parse_tag("Jan 7"), date(2021, 3, 2)) == (date(2021, 1, 7), True)
    assert nl.formation_date(nl.parse_tag("Feb 30"), date(2021, 3, 2)) == (date(2021, 2, 28), True)
    assert nl.formation_date(nl.parse_tag("Dec crust"), date(2021, 1, 2)) == (date(2020, 12, 1), False)


def test_since_last_pit():
    assert nl.since_last_pit(date(2021, 1, 20), True, date(2021, 1, 10)) == "new"
    assert nl.since_last_pit(date(2021, 1, 5), True, date(2021, 1, 10)) == "carried"
    assert nl.since_last_pit(date(2021, 1, 1), False, date(2021, 1, 10)) == "unclear"
    assert nl.since_last_pit(date(2020, 12, 1), False, date(2021, 1, 10)) == "carried"
    assert nl.since_last_pit(date(2020, 12, 1), True, None) == "new"


def test_kinds_match_their_grain_forms():
    assert nl.matches("crust", None, ["MFcr"]) and nl.matches("sun_crust", None, ["IF"])
    assert not nl.matches("crust", None, ["FC"]) and nl.matches("crust", "FC", ["FC"])  # a crust faceted out
    assert nl.matches("facets", None, ["DH"]) and nl.matches("surface_hoar", None, ["SH"])
    assert nl.matches("other", "RG", ["RG"]) and not nl.matches("other", None, ["RG"])
    assert not nl.matches("crust", None, [])


def test_hit_and_chance():
    hit, chance = nl.hit_and_chance([(0.40, 0.42)], 0.45, 0.47)
    assert hit and 0 < chance < 0.5
    miss, c2 = nl.hit_and_chance([(0.10, 0.12)], 0.60, 0.62)
    assert not miss and 0 < c2 < 0.5
    assert nl.hit_and_chance([], 0.5, 0.6) == (False, 0.0)
    whole, c = nl.hit_and_chance([(0.0, 1.0)], 0.5, 0.6)
    assert whole and c == 1.0


def _pred(layers, hs=1.0):
    return {"bulk_state": {"snow_depth_m": {"p10": hs, "p50": hs, "p90": hs}},
            "layers": [{"top_depth_m": {"p50": t}, "bottom_depth_m": {"p50": b}, "grain_form": [g],
                        "probability_present": p} for t, b, g, p in layers]}


def test_forecast_layers_below_the_presence_threshold_do_not_count():
    named = [{"tag": "Jan 24 crust", "kind": "crust", "formed": "2021-01-24", "day_known": True, "grain": "MFcr",
              "top": 0.30, "bottom": 0.32}]
    sure = nl.score_case(_pred([(0.3, 0.32, "MFcr", 0.9)]), named, date(2021, 1, 10))
    unsure = nl.score_case(_pred([(0.3, 0.32, "MFcr", 0.3)]), named, date(2021, 1, 10))
    assert sure[0]["hit"] and not unsure[0]["hit"]
    assert sure[0]["since_last_pit"] == "new" and sure[0]["month"] == 1


def _lab(tmp_path):
    """A lab with two pits (the earlier one visible, the later withheld and named) and one competition run."""
    paths = LabPaths(tmp_path / "lab")
    paths.profiles.parent.mkdir(parents=True)
    pd.DataFrame({"profile_id": ["p1", "p2"], "observed_at": pd.to_datetime(["2021-01-10T19:00Z", "2021-02-01T19:00Z"]),
                  "snow_depth_m": [1.0, 1.2], "site_code": ["BOW", "BOW"], "plot_id": ["BOW", "BOW"]}
                 ).to_parquet(paths.profiles)
    pd.DataFrame({"profile_id": ["p2", "p2", "p2"], "top_depth_m": [0.30, 0.80, 0.0],
                  "bottom_depth_m": [0.33, 0.84, 0.1], "grain_primary": ["MFcr", "FC", "PP"],
                  "hardness": ["K", "4F", "F"], "date_tag": ["Jan 24 crust", "Dec 7", None]}).to_parquet(paths.layers)
    run = paths.outputs / "competitions" / "comp-1"
    (run / "cases").mkdir(parents=True)
    good = _pred([(0.25, 0.28, "MFcr", 0.9), (0.66, 0.70, "FC", 0.9)], hs=1.2)
    poor = _pred([(0.0, 1.2, "RG", 0.9)], hs=1.2)
    case = {"case_id": "c1", "season": "2020-2021", "truth_used": True, "target_profile_id": "p2",
            "visible_profile_ids": ["p1"],
            "rows": [{"agent_id": "a-good", "label": "good", "status": "ok", "site_code": "BOW"},
                     {"agent_id": "a-poor", "label": "poor", "status": "ok", "site_code": "BOW"}],
            "predictions": {"a-good": good, "a-poor": poor}}
    (run / "cases" / "c1.json").write_text(json.dumps(case))
    (run / "leaderboard.json").write_text("{}")
    return paths


def test_a_competition_run_is_checked(tmp_path):
    paths = _lab(tmp_path)
    res = nl.run_named_layer_check(paths, "comp-1")
    assert res["named_layers"] == 2 and res["pits"] == 1
    good, poor = res["agents"]["a-good"], res["agents"]["a-poor"]
    assert good["subsets"]["all"]["hit"] == 1.0 and poor["subsets"]["all"]["hit"] == 0.0
    assert good["by_since"]["new"]["n"] == 1 and good["by_since"]["carried"]["n"] == 1  # Jan 24 new, Dec 7 carried
    assert good["by_kind"]["crust"]["n"] == 1 and good["by_kind"]["facets"]["n"] == 1
    out = paths.outputs / "named_layer_checks" / "comp-1"
    assert (out / "report.md").read_text().startswith("# Named-layer check")
    assert json.loads((out / "result.json").read_text())["check_version"] == nl.CHECK_VERSION


def test_the_named_layer_table_lists_every_named_layer(tmp_path):
    t = nl.named_layer_table(_lab(tmp_path))
    assert list(t["tag"]) == ["Jan 24 crust", "Dec 7"] and set(t["season"]) == {"2020-2021"}
    assert list(t["kind"]) == ["crust", "facets"]


def test_tags_never_reach_an_agent():
    assert {"date_tag", "comment", "notes"} <= pkg.FORBIDDEN_FIELDS
    assert (REPO / "lab_app/pages" / PAGES["named"][0]).is_file()
