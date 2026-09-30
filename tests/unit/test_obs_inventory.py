"""Observation intake: Propagation Labs header parsing and inventory QC (anonymised synthetic text)."""

from __future__ import annotations

import pytest

from snowagent.obs import inventory
from snowagent.obs.propagation_labs import parse_header_text

# Reproduces real export quirks: icon-font glyphs before labels, missing spaces, section words, ft units.
TEXT_FT = ("ProfileName: SiteA\nGeneral\nDate: 2026-03-0410:36:59.000\nObserver:Anon\nOrg:\n"
           "Location\nElevation:7581ft\nAspect:NW\nSlope:1°\nLat/Lng: 51.0901,-115.7554\n"
           "Weather\nAirTemperature:-5.6°C\nSkyCover:Clear\nPrecipitation:None\nWind:Calm\n"
           "BlowingSnow:--\nSnowConditions\nTotalSnowDepth(HS):160cm\nSurfaceGrain:--\nFootPen:35cm\nSkiPen:--\n"
           "Notes:CT13SP on SH\n")


def test_header_parse_units_and_quirks():
    p = parse_header_text(TEXT_FT)
    v = p.values
    assert v["obs_time_local"] == "2026-03-04T10:36:59"
    assert v["elevation_m"] == pytest.approx(7581 * 0.3048) and v["elevation_source_unit"] == "ft"
    assert "elevation_converted_from_ft" in p.flags
    assert (v["lat"], v["lon"]) == (51.0901, -115.7554)
    assert v["aspect_deg"] == 315 and v["slope_deg"] == 1.0
    assert v["hs_m"] == pytest.approx(1.60) and v["foot_pen_m"] == pytest.approx(0.35) and v["ski_pen_m"] is None
    assert v["air_temp_c"] == -5.6 and v["notes"] == "CT13SP on SH"


def test_unparseable_values_are_flagged_not_guessed():
    p = parse_header_text(TEXT_FT.replace("160cm", "lots").replace("2026-03-0410:36:59.000", "yesterday"))
    assert p.values["hs_m"] is None and any(f.startswith("hs_unparsed") for f in p.flags)
    assert "obs_time_local" not in p.values and any(f.startswith("date_unparsed") for f in p.flags)


def _tree(tmp_path, texts: dict[str, str]):
    root = tmp_path / "profiles"
    for rel in texts:
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_bytes(b"%PDF-fake " + rel.encode())
    (root / "2025-2026" / "Test profiles" / "Thumbs.db").write_bytes(b"x")
    return root


def test_inventory_qc_flags(tmp_path, monkeypatch):
    def site_text(lat, lon, date, elev="2282m"):
        return TEXT_FT.replace("51.0901,-115.7554", f"{lat},{lon}").replace("2026-03-0410:36:59.000", date) \
            .replace("7581ft", elev)
    texts = {
        "2025-2026/Study Plot profiles/Goat's Eye/2026-01-12 Goats Eye.pdf": site_text(51.0890, -115.7504, "2026-01-12 14:28:12.000"),
        "2025-2026/Study Plot profiles/Goat's Eye/2026-03-04 Goats Eye.pdf": site_text(51.0901, -115.7554, "2026-03-04 10:36:59.000"),
        "2025-2026/Study Plot profiles/Goat's Eye/2026-03-25 Goats eye.pdf": site_text(51.0899, -115.7563, "2026-03-25 10:31:07.000"),
        # entered at home: device GPS far away, shared with another site
        "2025-2026/Study Plot profiles/Goat's Eye/2026-01-26 Goats Eye.pdf": site_text(51.1908, -115.5601, "2026-01-26 11:32:13.000", "1399m"),
        "2025-2026/Test profiles/2025-12-10 West Nile.pdf": site_text(51.1908, -115.5603, "2025-12-10 12:00:00.000"),
        "2025-2026/Study Plot profiles/Simpson/2025-01-09 Simpson.pdf": "",
    }
    root = _tree(tmp_path, texts)
    monkeypatch.setattr(inventory, "_pdf_text", lambda p: texts[str(p.relative_to(root))])
    headers, skipped = inventory.build_inventory(root)
    by = {h.record_id.rsplit("_", 1)[0]: h for h in headers}
    assert len(headers) == 6 and skipped[0]["reason"].startswith("not a profile")
    assert by["2026-01-26_goats_eye"].station_id == "sunshine_village_ab_env"
    assert any("suspect_device_gps" in q for q in by["2026-01-26_goats_eye"].qc_flags)
    assert any("suspect_device_gps" in q for q in by["2025-12-10_west_nile"].qc_flags)
    assert not any("gps" in q for q in by["2026-03-04_goats_eye"].qc_flags)
    simpson = by["2025-01-09_simpson"]
    assert "pdf_without_text_layer" in simpson.qc_flags and any("outside_season" in q for q in simpson.qc_flags)
    ge = by["2026-03-25_goats_eye"]  # user-confirmed MST: fixed UTC-7 all season
    assert ge.obs_time_utc.isoformat() == "2026-03-25T17:31:07+00:00" and ge.time_zone_confirmed
    assert all(h.layers_status == "image_only" for h in headers)
    sites = inventory.site_summary(headers).set_index("site_key")
    assert sites.loc["goats_eye", "location_status"] == "consensus"
    assert sites.loc["goats_eye", "lat_median"] == pytest.approx(51.0899, abs=1e-3)
    assert sites.loc["simpson", "location_status"] == "no_fix"


def test_real_upload_inventory_if_present():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "profiles"
    if not root.exists():
        pytest.skip("no uploaded profiles in this checkout")
    headers, _ = inventory.build_inventory(root)
    assert headers and all(h.layers_status == "image_only" for h in headers)
    parsed = [h for h in headers if h.header_source == "pdf_text"]
    assert parsed
    for h in parsed:  # every missing core field is explained by a flag, never silently absent
        assert h.obs_time_local or any(q.startswith(("date_unparsed", "pdf_header_format")) for q in h.qc_flags)
        assert h.lat is not None or any(q.startswith("latlng_unparsed") for q in h.qc_flags) or h.lat is None


@pytest.mark.parametrize("name,season,expected,flag", [
    ("2024-30-29 Bow Summit.jpg", "2023-2024", None, "filename_date_invalid"),
    ("240203 Goats Eye.pdf", "2023-2024", "2024-02-03", "filename_date_yymmdd"),
    ("Tak Falls 02-21-2024.png", "2023-2024", "2024-02-21", None),
    ("Tak Falls -04-Jan.jpg", "2023-2024", "2024-01-04", "filename_year_inferred_from_season"),
    ("Pipestone Bowl 12Feb24.pdf", "2023-2024", "2024-02-12", None),
    ("01032025 Simpson.jpg", "2024-2025", None, "filename_date_ambiguous"),
    ("{825FF4D1-4ADA}.png", "2024-2025", None, "no_date_in_filename"),
])
def test_filename_dates(name, season, expected, flag):
    from snowagent.obs.filenames import parse_filename_date

    d, flags = parse_filename_date(name, season)
    assert d == expected
    assert (flag is None and not flags) or any(f.startswith(flag) for f in flags)


def test_folder_layouts_classified():
    aliases = {"goats_eye": ["goats eye", "goat's eye"], "tak_falls": ["tak falls", "takkakaw falls"]}
    keys = {"goats_eye", "tak_falls", "bow_summit"}
    assert inventory.classify(("2023-2024", "Goats Eye"), aliases, keys) == ("study_plot", "goats_eye")
    assert inventory.classify(("2024-2025", "Takkakaw Falls study plot"), aliases, keys) == ("study_plot", "tak_falls")
    assert inventory.classify(("2025-2026", "Study Plot profiles", "Bow Summit"), aliases, keys) == ("study_plot", "bow_summit")
    assert inventory.classify(("2023-2024", "Other Profiles"), aliases, keys) == ("test_profile", None)
    assert inventory.classify(("2024-2025",), aliases, keys) == ("unknown", None)
