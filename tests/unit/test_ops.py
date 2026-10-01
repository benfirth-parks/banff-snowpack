"""Periodic update pieces (ADR-037): MIN archive/parse, profile inbox, GFS day-1 fallback, live forecast store."""

import json
from pathlib import Path

import pandas as pd

FIX = Path(__file__).parents[1] / "fixtures"


def test_min_report_archived_once_per_version_and_parsed_without_usernames(tmp_path):
    from snowagent.ingest.min import archive_report, load_reports

    raw = (FIX / "min_report.json").read_bytes()
    url = "https://api.avalanche.ca/min/en/submissions/x"
    assert archive_report(raw, url, tmp_path)["status"] == "archived"
    assert archive_report(raw, url, tmp_path)["status"] == "unchanged"
    edited = json.loads(raw)
    edited["updatedDatetime"] = "2026-03-12T00:00:00Z"
    assert archive_report(json.dumps(edited).encode(), url, tmp_path)["status"] == "new_version"
    assert len((tmp_path / "manifest.jsonl").read_text().splitlines()) == 2
    (r,) = load_reports(tmp_path, {"bow_summit": (51.70946, -116.47950), "simpson": (50.98516, -115.98430)})
    assert r.hs_cm == 285 and r.test.depth_cm == 95 and r.test.crystal_types == ["facets"]
    assert r.elevation_m == 2200 and r.types == ["snowpack"]
    assert set(r.distance_km) == {"bow_summit"} and 13 < r.distance_km["bow_summit"] < 14
    assert "test-user" not in r.model_dump_json() and r.url.endswith(r.source_id)


def test_inbox_files_by_form_fields_dedups_and_keeps_unsupported(tmp_path):
    from snowagent.obs.inbox import process_inbox

    profiles = tmp_path / "profiles"
    inbox = profiles / "inbox"
    sub = inbox / "abc123"
    sub.mkdir(parents=True)
    (sub / "pit.pdf").write_bytes(b"%PDF-1.4 new pit")
    (sub / "submission.json").write_text(json.dumps({"submission_id": "abc123", "site": "simpson",
                                                     "observed_date": "2026-01-09", "notes": "N aspect"}))
    old = profiles / "2025-2026" / "Test profiles"
    old.mkdir(parents=True)
    (old / "2025-12-01_x.pdf").write_bytes(b"%PDF-1.4 same")
    (inbox / "copy.pdf").write_bytes(b"%PDF-1.4 same")
    (inbox / "notes.docx").write_bytes(b"doc")
    receipts = tmp_path / "received.jsonl"
    out = {r["original_name"]: r for r in process_inbox(inbox, profiles, receipts)}
    filed = profiles / "2025-2026" / "Study Plot profiles" / "Simpson" / "2026-01-09_pit.pdf"
    assert out["pit.pdf"]["status"] == "filed_needs_transcription" and filed.read_bytes() == b"%PDF-1.4 new pit"
    assert out["copy.pdf"]["status"] == "duplicate" and not (inbox / "copy.pdf").exists()
    assert out["notes.docx"]["status"] == "rejected" and (inbox / "notes.docx").exists()
    assert not sub.exists() and (tmp_path / "submissions" / "abc123.json").exists()
    assert len(receipts.read_text().splitlines()) == 3


def test_gfs_day1_fallback_uses_previous_run_only_where_a_run_is_missing(tmp_path, monkeypatch):
    import snowagent.weather.sources as src

    d0 = pd.Timestamp("2026-09-28", tz="UTC")
    calls = []

    def fake_hourly(df, point):
        run = pd.Timestamp(df["run"].iloc[0])
        calls.append(run)
        idx = pd.date_range(run, run + pd.Timedelta(hours=48), freq="h")
        g = pd.DataFrame({c: 1.0 for c in ("ta", "rh", "vw", "dw", "iswr", "ilwr", "psum")}, index=idx)
        g["ta"] = float(run.day)
        return g, 2000.0

    for d in (d0, d0 + pd.Timedelta(days=2)):  # run of 09-29 missing
        pd.DataFrame({"run": [d.isoformat()]}).to_csv(tmp_path / f"gfs_{d:%Y%m%d%H}.csv", index=False)
    monkeypatch.setattr(src, "gfs_hourly", fake_hourly)
    idx = pd.date_range(d0 + pd.Timedelta(hours=1), d0 + pd.Timedelta(days=3), freq="h")
    strict, _ = src.gfs_day1_series("p", idx, tmp_path)
    assert strict.loc["2026-09-29T12:00Z", "ta"] != strict.loc["2026-09-29T12:00Z", "ta"]  # NaN without fallback
    filled, _ = src.gfs_day1_series("p", idx, tmp_path, fallback_days=2)
    assert filled["ta"].notna().all() and filled.attrs["fallback_hours"] == 24
    assert filled.loc["2026-09-29T12:00Z", "ta"] == 28.0  # lead 36 h of the 09-28 run
    assert filled.loc["2026-09-30T12:00Z", "ta"] == 30.0


def test_live_forecast_stored_once_and_flagged_when_computed_late(tmp_path, monkeypatch):
    from snowagent.web import build

    rec = {"issue": "2026-09-20T00", "P": [], "forcing_hash": "abc"}
    first = build._issued_store("bow_summit", "2026-2027", rec, "run-1", tmp_path)
    assert first["computed_after_issue"] is True and first["initial_state_run_id"] == "run-1"
    again = build._issued_store("bow_summit", "2026-2027", {**rec, "forcing_hash": "zzz"}, "run-2", tmp_path)
    assert again["forcing_hash"] == "abc" and again["initial_state_run_id"] == "run-1"  # never rewritten
    assert build._issued_load("bow_summit", "2026-2027", pd.Timestamp("2026-09-20", tz="UTC"), tmp_path) == first
    assert build.current_season_year(pd.Timestamp("2026-08-31", tz="UTC")) == 2025
    assert build.current_season_year(pd.Timestamp("2026-09-01", tz="UTC")) == 2026
