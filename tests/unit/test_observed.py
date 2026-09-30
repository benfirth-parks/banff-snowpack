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
    assert [o["duplicate_of"] for o in (a, b, c)].count(None) == 2
