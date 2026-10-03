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


SNO = """SMET 1.1 ASCII
[HEADER]
station_id       = t
ProfileDate      = 2026-01-10T00:00:00
HS_Last          = 0.300000
nSoilLayerData   = 0
nSnowLayerData   = 3
ErosionLevel     = 2
fields           = timestamp Layer_Thick  T  Vol_Frac_I  Vol_Frac_W  Vol_Frac_V  Vol_Frac_S Rho_S Conduc_S HeatCapac_S  rg  rb  dd  sp  mk mass_hoar ne CDot metamo
[DATA]
2025-11-01T00:00:00 0.100000 268.0 0.30 0.0 0.70 0.0 0.0 0.000 0.0 0.8 0.4 0.0 0.06 1 0.0 1 0.0 0.0
2025-12-01T00:00:00 0.100000 265.0 0.25 0.0 0.75 0.0 0.0 0.000 0.0 0.5 0.2 0.0 0.30 0 0.0 1 0.0 0.0
2026-01-05T00:00:00 0.100000 262.0 0.10 0.0 0.90 0.0 0.0 0.000 0.0 0.15 0.05 0.8 0.5 0 0.0 1 0.0 0.0
"""


def test_scale_sno_scales_thickness_and_depth_only(tmp_path):
    from snowagent.learn.steer import _read_sno, scale_sno

    (tmp_path / "a.sno").write_text(SNO)
    hs = scale_sno(tmp_path / "a.sno", tmp_path / "b.sno", 1.5)
    assert abs(hs - 0.45) < 1e-9
    _h, rows = _read_sno(tmp_path / "b.sno")
    assert [float(r[1]) for r in rows] == [0.15, 0.15, 0.15]
    assert [r[3] for r in rows] == ["0.30", "0.25", "0.10"]  # ice fraction (density) unchanged
    assert "HS_Last          = 0.450000" in (tmp_path / "b.sno").read_text()


def test_pit_to_sno_takes_pit_layering_and_model_temperatures(tmp_path):
    from snowagent.learn.steer import _read_sno, pit_to_sno

    (tmp_path / "m.sno").write_text(SNO)
    pit = {"hs_cm": 40, "layers": [{"top_cm": 40, "bottom_cm": 30, "grain_form": "PP", "hardness_index": 1.0},
                                   {"top_cm": 30, "bottom_cm": 10, "grain_form": "FC", "hardness_index": 3.0},
                                   {"top_cm": 10, "bottom_cm": 0, "grain_form": None, "hardness_index": None}]}
    info = pit_to_sno(tmp_path / "m.sno", pit, tmp_path / "r.sno")
    assert info["hs_m"] == 0.4 and info["elements"] == 20 and info["grain_from_model"] == 5
    _h, rows = _read_sno(tmp_path / "r.sno")
    top, bottom = rows[-1], rows[0]
    assert float(top[13]) == 0.485 and abs(float(top[3]) - 95 / 917) < 1e-6  # PP sphericity, F density
    assert float(bottom[10]) == 0.8 and float(bottom[2]) == 268.0  # model grain and temperature kept at the base
    assert "nSnowLayerData   = 20" in (tmp_path / "r.sno").read_text()


def test_pit_to_sno_mass_target_keeps_depth(tmp_path):
    from snowagent.learn.steer import HARD_RHO_PITS, _read_sno, pit_to_sno, sno_swe

    (tmp_path / "m.sno").write_text(SNO)
    pit = {"hs_cm": 40, "layers": [{"top_cm": 40, "bottom_cm": 0, "grain_form": "FC", "hardness_index": 2.0}]}
    info = pit_to_sno(tmp_path / "m.sno", pit, tmp_path / "r.sno", hard_rho=HARD_RHO_PITS)
    assert abs(info["swe_mm"] - 0.4 * 220) < 0.5  # 4F -> 220 kg m-3 from the pits' table
    info = pit_to_sno(tmp_path / "m.sno", pit, tmp_path / "r2.sno", hard_rho=HARD_RHO_PITS, swe_target=100.0)
    _h, rows = _read_sno(tmp_path / "r2.sno")
    assert info["hs_m"] == 0.4 and abs(sno_swe(rows) - 100.0) < 0.5 and info["mass_factor"] > 1


def test_webcam_capture_stores_fresh_skips_stale_and_repeats(tmp_path):
    import io

    import pandas as pd
    from PIL import Image

    from snowagent.ingest.webcam import capture

    buf = io.BytesIO()
    Image.new("RGB", (1920, 1080), "white").save(buf, "JPEG")
    now = pd.Timestamp("2026-12-01T18:00", tz="UTC")
    lm = {"fresh": "Tue, 01 Dec 2026 17:30:00 GMT", "old": "Wed, 24 Jun 2026 16:34:55 GMT"}
    cfg = {"max_age_h": 48, "cams": {"stake": {"current": "fresh", "daylight": "old"}}}

    def get(url):
        return buf.getvalue(), {"last-modified": lm[url]}

    r = {x["kind"]: x for x in capture(now, tmp_path, cfg, get)}
    assert r["current"]["status"] == "stored" and r["daylight"]["status"] == "stale"
    assert r["current"]["stored_px"] == [1280, 720] and Path(r["current"]["path"]).exists()
    assert "2026-2027" in r["current"]["path"]
    again = {x["kind"]: x for x in capture(now, tmp_path, cfg, get)}
    assert again["current"]["status"] == "unchanged"


def _reply(content: bytes):
    from types import SimpleNamespace

    return SimpleNamespace(ok=True, status_code=200, content=content, url="https://fts360api.example/x",
                           text=content.decode())


def test_fts360_short_reply_never_replaces_a_fuller_month(tmp_path, monkeypatch):
    import snowagent.ingest.fts360 as fts

    a = (pd.Timestamp.now(tz="UTC") + pd.Timedelta(days=40)).normalize().replace(day=1)  # an open window
    start, end = f"{a:%Y-%m-%dT%H:%M:%S}", f"{a + pd.Timedelta(days=2):%Y-%m-%dT%H:%M:%S}"
    dest = tmp_path / "lookout" / f"lookout_{a:%Y-%m}.csv"
    dest.parent.mkdir()
    good = b"Date,TA\n2026-01-01T00:00Z,-5\n2026-01-01T01:00Z,-6\n2026-01-01T02:00Z,-7\n"
    dest.write_bytes(good)
    replies = iter([b"Date,TA\n", b"", good + b"2026-01-01T03:00Z,-8\n"])
    monkeypatch.setattr(fts.requests, "get", lambda *_a, **_k: _reply(next(replies)))
    for _ in range(2):  # header-only, then empty 200 replies: the month keeps its records
        (rec,) = fts.fetch_station(450, "lookout", "hex", start, end, tmp_path)
        assert dest.read_bytes() == good and "path" not in rec and rec["kept_existing"] == str(dest)
        assert "fewer than the 3" in rec["warning"]
    (rec,) = fts.fetch_station(450, "lookout", "hex", start, end, tmp_path)  # a fuller reply replaces it
    assert rec["path"] == str(dest) and rec["data_rows"] == 4 and "warning" not in rec
    assert fts.csv_data_rows(dest.read_bytes()) == 4
    log = [json.loads(x) for x in (tmp_path / "manifest.jsonl").read_text().splitlines()]
    assert [x["data_rows"] for x in log] == [0, 0, 4]  # every reply is logged, kept or not


def test_fts360_archive_sync_keeps_a_fuller_archived_month(tmp_path, monkeypatch):
    import gzip

    from snowagent.ops import update

    raw, arc = tmp_path / "raw", tmp_path / "archive"
    monkeypatch.setattr(update, "FTS_RAW", raw)
    monkeypatch.setattr(update, "FTS_ARCHIVE", arc)
    now = pd.Timestamp("2026-10-03T13:00", tz="UTC")
    f = raw / "lookout" / "lookout_2026-10.csv"
    f.parent.mkdir(parents=True)
    full = b"Date,TA\n2026-10-01T00:00Z,1\n2026-10-01T01:00Z,2\n"
    f.write_bytes(full)
    assert update.sync_fts360_archive(now) == {"archived": 1, "kept": []}
    f.write_bytes(b"Date,TA\n")  # header only: the archived month stays as it was
    res = update.sync_fts360_archive(now)
    assert res["archived"] == 0 and "lookout_2026-10.csv" in res["kept"][0]
    assert gzip.decompress((arc / "lookout" / "lookout_2026-10.csv.gz").read_bytes()) == full
    f.write_bytes(full + b"2026-10-01T02:00Z,3\n")
    assert update.sync_fts360_archive(now)["archived"] == 1
