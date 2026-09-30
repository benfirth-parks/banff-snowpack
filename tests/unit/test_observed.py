"""Transcription -> observed profile conversion (no filling, explicit flags, duplicate detection)."""

from __future__ import annotations

import copy

import pytest

from snowagent.obs.observed import hardness_index, mark_observation_duplicates, to_observed
from snowagent.obs.transcription import validate_transcription
from tests.unit.test_transcription import BASE


def _t(**over):
    d = copy.deepcopy(BASE)
    d.update(over)
    t, errors, _ = validate_transcription(d)
    assert not errors
    return t


def test_hardness_index_steps():
    assert hardness_index("F") == 1 and hardness_index("I") == 6
    assert hardness_index("4F+") == pytest.approx(2 + 1 / 3) and hardness_index("P-") == pytest.approx(4 - 1 / 3)
    assert hardness_index("4F-1F") is None and hardness_index(None) is None


def test_mst_conversion_and_date_only_flag():
    t = _t()
    t.header.time_local = "14:28"
    o = to_observed(t, None, "Etc/GMT+7")
    assert o["obs_time_utc"] == "2026-01-12T21:28:00+00:00" and o["time_zone"] == "Etc/GMT+7"
    t.header.time_local = None
    assert "time_unknown_date_only" in to_observed(t, None, "Etc/GMT+7")["flags"]


def test_depth_chart_converted_only_with_hs():
    layers = [{"top_cm": 0, "bottom_cm": 10, "grain_form": "PP"}, {"top_cm": 10, "bottom_cm": 60, "grain_form": "RG"}]
    t = _t(height_reference="depth_from_surface", layers=layers)
    o = to_observed(t, None, "Etc/GMT+7")
    assert o["height_reference"] == "height_above_ground"
    assert (o["layers"][0]["top_cm"], o["layers"][0]["bottom_cm"]) == (100, 90)
    t.header.hs_cm = None
    o = to_observed(t, None, "Etc/GMT+7")
    assert o["height_reference"] == "depth_from_surface"
    assert "depth_chart_without_hs_heights_are_depths" in o["flags"] and o["layers"][0]["top_cm"] == 0


def test_same_pit_exported_twice_is_one_observation():
    a = to_observed(_t(), {"site_key": "goats_eye"}, "Etc/GMT+7")
    b = to_observed(_t(record_id="r2", source_file="profiles/x.jpg"), {"site_key": "goats_eye"}, "Etc/GMT+7")
    c = to_observed(_t(record_id="r3"), {"site_key": "bow_summit"}, "Etc/GMT+7")
    mark_observation_duplicates([a, b, c])
    # a/b: same site, same layers -> one observation. c: identical layers at another plot = copied file:
    # merged but flagged so the site assignment can be checked.
    assert {a["duplicate_of"], b["duplicate_of"]} & {a["profile_id"], b["profile_id"]}
    assert any(f.startswith("identical_profile_filed_under_sites") for f in a["flags"] + c["flags"])


def test_undug_part_trimmed_or_flagged():
    from snowagent.obs.observed import trim_unobserved

    layers = [{"top_cm": 100, "bottom_cm": 60}, {"top_cm": 60, "bottom_cm": 20}, {"top_cm": 20, "bottom_cm": 0}]
    kept, flags = trim_unobserved(layers, 100, 70, "")
    assert [(ly["top_cm"], ly["bottom_cm"]) for ly in kept] == [(100, 60), (60, 30)]
    assert "layer_below_pit_bottom_removed" in flags and "layer_clipped_at_pit_bottom" in flags
    kept, flags = trim_unobserved(layers, 100, None, "60-0cm: Didn't dig to ground")
    assert kept == layers and flags == ["pit_did_not_reach_ground_bottom_unknown"]


def test_same_pit_from_two_apps_detected():
    a = to_observed(_t(), {"site_key": None}, "Etc/GMT+7")
    b = copy.deepcopy(a)
    b["profile_id"], b["source_file"] = "other", "profiles/other.pdf"
    b["layers"][2]["grain_form"] = "DH"  # different call at the base, same geometry
    c = copy.deepcopy(a)
    c["profile_id"] = "different_pit"
    c["layers"][0]["bottom_cm"], c["layers"][1]["top_cm"] = 80, 80
    c["layers"][1]["bottom_cm"], c["layers"][2]["top_cm"] = 70, 70
    mark_observation_duplicates([a, b])
    assert b["duplicate_of"] == a["profile_id"] or a["duplicate_of"] == b["profile_id"]
    c2 = copy.deepcopy(c)
    a2 = to_observed(_t(), {"site_key": None}, "Etc/GMT+7")
    mark_observation_duplicates([a2, c2])
    assert a2["duplicate_of"] is None and c2["duplicate_of"] is None


def test_different_plots_with_similar_layers_are_not_merged():
    a = to_observed(_t(), {"site_key": "vermilion"}, "Etc/GMT+7")
    b = copy.deepcopy(a)
    b.update(profile_id="bow", site_key="bow_summit")
    b["layers"][2]["grain_form"] = "DH"
    mark_observation_duplicates([a, b])
    assert a["duplicate_of"] is None and b["duplicate_of"] is None
