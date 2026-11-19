"""Observer names and e-mail addresses stay out of the repository's text (ADR-120)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from snowagent.obs.redact import REMOVED, observer_text_found, scrub_record, strip_observer_text
from snowagent.obs.transcribe_cli import scrub_file, validate_files
from snowagent.obs.transcription import load_all, validate_transcription

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(("text", "expected"), [
    ("Org: PC. Observer: Pat Example. Snowpit depth 70 cm.", f"Org: PC. Observer: {REMOVED}. Snowpit depth 70 cm."),
    ("Observer: pat.example@pc.gc.ca. Blowing snow: None.", f"Observer: {REMOVED}. Blowing snow: None."),
    ("Obs: AB CD; HS 120", f"Obs: {REMOVED}; HS 120"),
    ("Observers: AB and CD'. Wind: calm", f"Observers: {REMOVED}. Wind: calm"),
    ("Header 'Tak Falls'; observer 'Pat Example'. Printed date", f"Header 'Tak Falls'; observer '{REMOVED}'. Printed date"),
    ("Name printed in the org position: Pat Example. Co-ord printed", f"Name printed in the org position: {REMOVED}. Co-ord printed"),
    ("Organization: Parks. .example@pc.gc.ca. Snowpit depth 99 cm", f"Organization: Parks. {REMOVED}. Snowpit depth 99 cm"),
])
def test_strip_observer_text(text, expected):
    assert strip_observer_text(text) == expected
    assert strip_observer_text(expected) == expected  # idempotent


@pytest.mark.parametrize("text", [
    "Observer: --. Date: --.", "Observer: '--'. Aspect: '--'.", "Observer: Snow Safety. Date printed '--'.",
    "Observer/org field: 'Banff Visitor Safety'. Region 'Rockies BC'.", "Printed 'Parker SP', 'Jasper Visitor Safety'",
    "CT13 SP @ 87 cm x2 = observed twice", "CTV @63.5cm on FC sz 1.0", "Obs glade test pit", None, "",
])
def test_unit_names_blanks_and_snow_text_are_kept(text):
    assert strip_observer_text(text) == text


def _record(notes: str) -> dict:
    return {"schema_version": "transcription-1", "record_id": "2020-01-01_x_abc123", "source_file": "x.pdf",
            "source_sha256": "0" * 64, "source_format": "other",
            "transcriber": {"method": "vision_model", "agent": "test", "reviewed": False, "reviewer": None},
            "readable": True, "is_snow_profile": True, "header": {"hs_cm": 100.0, "notes": notes},
            "layers": [{"top_cm": 100.0, "bottom_cm": 0.0, "grain_form": "RG", "hardness": "1F",
                        "comment": "Observer: Pat Example"}],
            "tests": [{"raw": "CT12 SP @ 50", "comment": "observed twice"}], "confidence": "high"}


def test_scrub_record_touches_only_free_text():
    rec = _record("Observer: Pat Example. HS 100 cm.")
    clean, changed = scrub_record(rec)
    assert changed == 2
    assert clean["header"]["notes"] == f"Observer: {REMOVED}. HS 100 cm."
    assert clean["layers"][0]["comment"] == f"Observer: {REMOVED}"
    assert clean["layers"][0]["hardness"] == "1F" and clean["tests"] == rec["tests"]
    assert observer_text_found(clean) == []
    _, _, flags = validate_transcription(rec)
    assert "observer_text_in:notes" in flags and "observer_text_in:comment" in flags


def test_validate_removes_observer_text_in_place(tmp_path, capsys):
    p = tmp_path / "rec.json"
    p.write_text(json.dumps(_record("Observer: pat@example.org. HS 100 cm.")))
    assert validate_files([p]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["observer_text_removed"] == 2 and not any(f.startswith("observer_text_in") for f in out["flags"])
    assert "example" not in p.read_text().lower().replace("[name removed]", "")
    assert scrub_file(p) == 0  # second pass changes nothing


def test_repository_transcriptions_carry_no_observer_text():
    """Guard for every committed transcription (the repository is public)."""
    for sub in ("transcriptions", "transcription_qa"):
        for path, data in load_all(ROOT / "observations" / sub):
            assert observer_text_found(data) == [], path
