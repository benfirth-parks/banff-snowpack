"""The old Snowpack Translator address (web/pit/) is a "moved" page: the Translator lives in its own repository
and site (benfirth-parks/snowpack-translator, 2026-11-14), and its only link to this app is the profiles it uploads."""

from __future__ import annotations

from pathlib import Path

from snowagent.ops.deploy import STATIC_FILES

PIT = Path(__file__).resolve().parents[2] / "web" / "pit"
NEW = "https://snowpack-translator.netlify.app/"


def test_both_old_pages_send_people_to_the_new_address_and_carry_spoken_pits():
    for name in ("index.html", "voice.html"):
        page = (PIT / name).read_text()
        assert NEW in page and "snowpack-voice-v1" in page and "#import=" in page
    assert not (PIT / "js").exists()


def test_offline_copy_is_the_moved_page_and_the_deploy_check_requires_it():
    sw = (PIT / "sw.js").read_text()
    assert 'VERSION = "pit-moved"' in sw and "./voice.html" in sw and "js/" not in sw
    assert {f for f in STATIC_FILES if f.startswith("pit/")} == {
        "pit/index.html", "pit/voice.html", "pit/sw.js", "pit/manifest.webmanifest"}
    assert all((PIT.parent / f).exists() for f in STATIC_FILES if f.startswith("pit/"))
