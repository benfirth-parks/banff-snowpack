"""Transcription schema/consistency validation (never edits content; flags or rejects)."""

from __future__ import annotations

import copy

from snowagent.obs.transcription import validate_transcription

BASE = {
    "schema_version": "transcription-1", "record_id": "r1", "source_file": "profiles/x.pdf", "source_sha256": "0" * 64,
    "source_format": "propagation_labs",
    "transcriber": {"method": "vision_model", "agent": "test", "reviewed": False, "reviewer": None},
    "readable": True, "unreadable_reason": None, "is_snow_profile": True,
    "header": {"date_local": "2026-01-12", "hs_cm": 100},
    "height_reference": "height_above_ground",
    "layers": [
        {"top_cm": 100, "bottom_cm": 90, "grain_form": "PP", "grain_size_mm": [0.5], "hardness": "F-"},
        {"top_cm": 90, "bottom_cm": 89, "grain_form": "SH", "grain_size_mm": [4, 6], "hardness": "F",
         "date_tag": "Jan 3"},
        {"top_cm": 89, "bottom_cm": 0, "grain_form": "RG", "grain_form_2": "FC", "hardness": "1F",
         "hardness_bottom": "P", "moisture": "D"},
    ],
    "temperatures": [{"height_cm": 100, "t_c": -4.0}],
    "tests": [{"raw": "CT13SP", "type": "CT", "result": "CT13", "score": 13, "fracture_character": "SP",
               "height_cm": 89}],
    "confidence": "medium",
}


def test_valid_record_only_flags_unreviewed():
    t, errors, flags = validate_transcription(copy.deepcopy(BASE))
    assert t is not None and errors == [] and flags == ["unreviewed_transcription"]


def test_unknown_grain_code_and_hardness_rejected():
    d = copy.deepcopy(BASE)
    d["layers"][0]["grain_form"] = "XX"
    d["layers"][1]["hardness"] = "4F-1F"
    _, errors, _ = validate_transcription(d)
    assert any("grain form" in e for e in errors) and any("hardness" in e for e in errors)


def test_inverted_boundaries_rejected_gaps_and_hs_mismatch_flagged():
    d = copy.deepcopy(BASE)
    d["layers"][1]["top_cm"], d["layers"][1]["bottom_cm"] = 87, 86  # gap 90->87, overlap-free
    d["header"]["hs_cm"] = 110
    _, errors, flags = validate_transcription(d)
    assert errors == []
    assert any(f.startswith("gap") for f in flags) and any("differs from HS" in f for f in flags)
    d["layers"][2]["top_cm"], d["layers"][2]["bottom_cm"] = 0, 86
    _, errors, _ = validate_transcription(d)
    assert any("not above bottom" in e for e in errors)


def test_non_profile_cannot_carry_layers_and_schema_is_strict():
    d = copy.deepcopy(BASE)
    d["is_snow_profile"], d["readable"] = False, False
    _, errors, _ = validate_transcription(d)
    assert any("must not contain layers" in e for e in errors)
    d = copy.deepcopy(BASE)
    d["layers"][0]["made_up_field"] = 1
    t, errors, _ = validate_transcription(d)
    assert t is None and errors


def test_physically_impossible_temperature_rejected():
    d = copy.deepcopy(BASE)
    d["temperatures"].append({"height_cm": 50, "t_c": 3.0})
    _, errors, _ = validate_transcription(d)
    assert any("temperature" in e for e in errors)


def test_transparent_png_rendered_on_white(tmp_path):
    from PIL import Image

    from snowagent.obs.transcription import render_record

    src = tmp_path / "t.png"
    im = Image.new("RGBA", (40, 40), (0, 0, 0, 0))
    im.putpixel((5, 5), (0, 0, 0, 255))  # black "text" on a transparent background
    im.save(src)
    (out,) = render_record(src, tmp_path / "img", "t")
    r = Image.open(out).convert("RGB")
    assert r.getpixel((20, 20)) == (255, 255, 255) and r.getpixel((5, 5)) == (0, 0, 0)
