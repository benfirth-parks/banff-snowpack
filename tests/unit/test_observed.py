"""Transcription -> observed profile conversion (no filling, explicit flags, duplicate detection)."""

from __future__ import annotations

import copy
from pathlib import Path

import pytest

from snowagent.obs.observed import build_observed, hardness_index, mark_observation_duplicates, to_observed
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


def test_printed_utm_converted():
    from snowagent.obs.observed import utm_text_to_latlon

    lat, lon = utm_text_to_latlon("Co-ord: 11U 587149W 5660594N")
    assert lat == pytest.approx(51.09, abs=0.02) and lon == pytest.approx(-115.76, abs=0.02)
    assert utm_text_to_latlon("no coordinates here") is None


def test_depth_chart_temperatures_and_tests_converted_like_layers():
    layers = [{"top_cm": 0, "bottom_cm": 10, "grain_form": "PP"}, {"top_cm": 10, "bottom_cm": 60, "grain_form": "RG"}]
    t = _t(height_reference="depth_from_surface", layers=layers, temperatures=[{"height_cm": 20, "t_c": -3.0}],
           tests=[{"raw": "CT12", "height_cm": 10}])
    t.header.hs_cm = 60
    o = to_observed(t, None, "Etc/GMT+7")
    assert o["temperatures"][0]["height_cm"] == 40 and o["tests"][0]["height_cm"] == 50


def _avanet(hs, pit):
    layers = [{"top_cm": 130, "bottom_cm": 30, "grain_form": "RG"}, {"top_cm": 30, "bottom_cm": 0, "grain_form": "DH"}]
    t = _t(source_format="avanet", layers=layers, tests=[{"raw": "CT22", "height_cm": 30}])
    t.header.hs_cm, t.header.profile_depth_cm = hs, pit
    return to_observed(t, None, "Etc/GMT+7")


def test_avanet_axis_from_pit_bottom():
    o = _avanet(165, 130)  # axis "130 SURFACE", snowpack 165 cm: pit bottom is 35 cm above ground
    assert [(ly["top_cm"], ly["bottom_cm"]) for ly in o["layers"]] == [(165, 65), (65, 35)]
    assert o["tests"][0]["height_cm"] == 65 and any(f.startswith("avanet_axis_from_pit_bottom") for f in o["flags"])
    full = _avanet(130, 130)  # pit to the ground: unchanged
    assert full["layers"][-1]["bottom_cm"] == 0
    no_hs = _avanet(None, None)  # ground unknown: depths below the surface
    assert no_hs["height_reference"] == "depth_from_surface"
    assert [(ly["top_cm"], ly["bottom_cm"]) for ly in no_hs["layers"]] == [(0, 100), (100, 130)]


def test_descending_size_range_marked_uncertain():
    t = _t(layers=[{"top_cm": 50, "bottom_cm": 0, "grain_form": "FC", "grain_size_mm": [1.0, 0.5]}])
    o = to_observed(t, None, "Etc/GMT+7")
    assert "grain_size_mm" in o["layers"][0]["uncertain_fields"]


FIX = Path(__file__).parents[1] / "fixtures"


def test_structured_reader_classifies_xml_by_content(tmp_path):
    """CAAML v5 is read as .xml or .caaml (ADR-048); other XML is listed as not read, never skipped."""
    v5 = (FIX / "caaml_v5_min.xml").read_bytes()
    folder = tmp_path / "2025-2026" / "Test profiles"
    folder.mkdir(parents=True)
    (folder / "2026-01-09_pit.xml").write_bytes(v5)
    (folder / "2026-01-09_pit_copy.caaml").write_bytes(v5)  # same bytes: one observation
    (folder / "2026-01-10_pit.caaml").write_bytes(v5.replace(b"2026-01-09T11:30", b"2026-01-10T11:30"))
    (folder / "2026-01-09_snowscope.xml").write_bytes((FIX / "caaml_v6_min.xml").read_bytes())
    gpx = b'<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1"/>'
    (folder / "track.xml").write_bytes(gpx)
    obs, stats = build_observed(tmp_path / "no_transcriptions", tmp_path)
    assert (stats["structured_files"], stats["structured_identical_files"]) == (5, 1)
    assert (stats["structured_parsed"], stats["structured_errors"], stats["structured_not_read"]) == (2, 0, 2)
    by_name = {Path(o["source_file"]).name: o for o in obs}
    assert set(by_name) == {"2026-01-09_pit.xml", "2026-01-10_pit.caaml"}
    xml = by_name["2026-01-09_pit.xml"]
    assert xml["provenance"]["method"] == "structured:caaml_v5" and not xml["unusable"]
    assert xml["obs_time_utc"] == "2026-01-09T18:30:00+00:00"
    assert [(ly["top_cm"], ly["bottom_cm"], ly["grain_form"]) for ly in xml["layers"]] == [
        (120, 100, "PP"), (100, 0, "RG")]
    nr = {Path(x["file"]).name: x for x in stats["not_read"]}
    assert {k: v["format"] for k, v in nr.items()} == {"2026-01-09_snowscope.xml": "caaml_other",
                                                        "track.xml": "xml_unknown"}
    assert "v6" in nr["2026-01-09_snowscope.xml"]["reason"]
    assert all(len(x["sha256"]) == 64 for x in nr.values())


def test_xml_kind_shared_by_inbox_and_reader():
    from snowagent.obs.caaml import is_caaml_v5, xml_kind

    assert xml_kind((FIX / "caaml_v5_min.xml").read_bytes()) == "caaml_v5"
    assert xml_kind((FIX / "caaml_v6_min.xml").read_bytes()) == "caaml_other"
    assert xml_kind(b"<?xml version='1.0'?><kml/>") == "xml_unknown"
    assert is_caaml_v5((FIX / "caaml_v5_min.xml").read_bytes())
