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


def test_printed_date_differing_from_filename_date_is_flagged_and_used():
    """The printed date stays the observation date; a different filename date is flagged (ADR-049)."""
    inv = {"site_key": "goats_eye", "filename_date": "2026-01-11"}
    o = to_observed(_t(), inv, "Etc/GMT+7")
    assert "printed_date_2026-01-12_differs_from_filename_2026-01-11" in o["flags"]
    assert o["obs_time_utc"].startswith("2026-01-12")
    assert not any(f.startswith("printed_date_") for f in to_observed(_t(), inv | {"filename_date": "2026-01-12"},
                                                                     "Etc/GMT+7")["flags"])
    t = _t()
    t.header.date_local = None
    flags = to_observed(t, inv, "Etc/GMT+7")["flags"]
    assert "date_from_filename" in flags and not any(f.startswith("printed_date_") for f in flags)


CFG = {"study_plots": {"simpson": {"name": "Simpson"}, "bow_summit": {"name": "Bow Summit"},
                       "tak_falls": {"name": "Tak Falls"}, "vermilion": {"name": "Vermilion"}},
       "site_aliases": {"bow_summit": ["bow summit", "bow plot"], "tak_falls": ["tak falls", "takakkaw"],
                        "vermilion": ["vermilion", "vermillion"]},
       "printed_site_names": {"bow_summit": ["bow pass"]}}


@pytest.mark.parametrize("name,site,flag", [
    ("Simpson Study Plot", "simpson", None),
    ("260107 Bow Summit Snow Study Plot", "bow_summit", None),  # digits are not a place
    ("Bow Pass, Alberta", "bow_summit", None),  # printed_site_names
    ("Bow CSSummit", "bow_summit", None),  # close match
    ("Takakkaww", "tak_falls", None),
    ("Takfalls", "tak_falls", None),
    ("Study Plot", "simpson", None),  # names no place
    ("Wawa Test Profile", "simpson", "printed_site_name_not_folder_plot:simpson:Wawa Test Profile"),
    ('Below Bow Peak "West Nile" at treeline', "bow_summit",
     'printed_site_name_not_folder_plot:bow_summit:Below Bow Peak "West Nile" at treeline'),
    ("Vermillion  Plot", "simpson", "printed_site_name_is_other_plot:simpson->vermilion:Vermillion Plot"),
    (None, "simpson", None), ("Wawa", None, None),
])
def test_printed_site_name_flagged_only_when_it_names_another_place(name, site, flag):
    from snowagent.obs.site_names import plot_names, printed_site_flag

    assert printed_site_flag(name, site, plot_names(CFG)) == flag


def test_printed_site_name_recorded_and_flagged_by_to_observed():
    from snowagent.obs.site_names import plot_names

    t = _t()
    t.header.site_name_as_written = "Wawa Test Profile"
    inv = {"site_key": "simpson", "filename_date": "2026-01-12"}
    o = to_observed(t, inv, "Etc/GMT+7", plot_names(CFG))
    assert o["site_name_as_written"] == "Wawa Test Profile" and o["site_key"] == "simpson"  # not reassigned
    assert "printed_site_name_not_folder_plot:simpson:Wawa Test Profile" in o["flags"]
    assert not any(f.startswith("printed_site") for f in to_observed(t, inv, "Etc/GMT+7")["flags"])  # no names


def test_printed_names_of_the_plots_in_the_data_are_accepted_by_the_config():
    """Printed names seen in the transcriptions that name the folder's plot stay unflagged with
    config/observations.yaml; the clearly different places of the 2026-10-03 review are flagged."""
    import yaml

    from snowagent.obs.inventory import DEFAULT_CONFIG
    from snowagent.obs.site_names import plot_names, printed_site_flag

    names = plot_names(yaml.safe_load(DEFAULT_CONFIG.read_text()))
    same = {"goats_eye": ["GE Shot Plot", "SSV study plot", "Sunshine Study Ploy", "Goats Study Plot", "Shotplot",
                          "Sunshine Village - Goat's Eye Plot", "Goat's Eye - SSV"],
            "bow_summit": ["Bow Pass, Alberta", "Bow Summit Stidy Plot", "Bow Summit Wx Site", "Bow CSSummit"],
            "tak_falls": ["Tak Plot", "Tack Falls Moraine", "Takakaw Fall", "Takkakkaw Plot", "Tak Falks SP",
                          "Tak Falls Plot, British Columbia"],
            "simpson": ["Simpson Lower - Study plot", "SImpson Study Plot"], "vermilion": ["Vermillion Plot"]}
    for site, printed in same.items():
        assert [n for n in printed if printed_site_flag(n, site, names)] == [], site
    other = {"goats_eye": ["Brewster Rock, Alberta"], "simpson": ["Wawa Test Profile"],
             "bow_summit": ["National Geographics", "Observation Glades TL", 'Below Bow Peak "West Nile" at treeline']}
    for site, printed in other.items():
        assert all(printed_site_flag(n, site, names) for n in printed), site


def _transcribed_file(tmp_path, rel: str, data: bytes, **header) -> tuple[Path, Path]:
    """One profile file under ``tmp_path/profiles`` and a transcription of it (BASE with ``header`` fields)."""
    import hashlib
    import json

    profiles, transcriptions = tmp_path / "profiles", tmp_path / "transcriptions"
    f = profiles / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(data)
    d = copy.deepcopy(BASE) | {"source_file": str(f), "source_sha256": hashlib.sha256(data).hexdigest()}
    d["header"] |= header
    transcriptions.mkdir(exist_ok=True)
    (transcriptions / f"{f.stem}.json").write_text(json.dumps(d))
    return profiles, transcriptions


def test_build_observed_flags_printed_date_and_site_of_a_transcribed_pit(tmp_path):
    """The printed date and site name checks reach the observed set through build_observed (ADR-049)."""
    profiles, transcriptions = _transcribed_file(
        tmp_path, "2025-2026/Study Plot profiles/Simpson/2026-01-11 Simpson.pdf", b"%PDF-1.4 not a real pdf",
        site_name_as_written="Wawa Test Profile")
    obs, stats = build_observed(transcriptions, profiles)
    (o,) = obs
    assert stats["transcriptions"] == 1 and (o["site_key"], o["category"]) == ("simpson", "study_plot")
    assert o["site_name_as_written"] == "Wawa Test Profile"
    assert "printed_date_2026-01-12_differs_from_filename_2026-01-11" in o["flags"]
    assert "printed_site_name_not_folder_plot:simpson:Wawa Test Profile" in o["flags"]
