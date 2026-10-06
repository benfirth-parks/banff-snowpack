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


def test_inbox_caaml_v5_xml_filed_exact_is_read_and_v6_is_reported(tmp_path):
    """A receipt saying filed_exact means the observed set reads the file (ADR-048)."""
    from snowagent.obs.inbox import process_inbox
    from snowagent.obs.observed import build_observed

    profiles = tmp_path / "profiles"
    inbox = profiles / "inbox"
    inbox.mkdir(parents=True)
    (inbox / "2026-01-09 Test slope.xml").write_bytes((FIX / "caaml_v5_min.xml").read_bytes())
    (inbox / "2026-01-09 snowscope.xml").write_bytes((FIX / "caaml_v6_min.xml").read_bytes())
    out = {r["original_name"]: r for r in process_inbox(inbox, profiles, tmp_path / "received.jsonl")}
    v5, v6 = out["2026-01-09 Test slope.xml"], out["2026-01-09 snowscope.xml"]
    assert (v5["status"], v5["format"]) == ("filed_exact", "caaml_v5")
    assert (v6["status"], v6["format"]) == ("filed_not_read", "caaml_other")
    obs, stats = build_observed(tmp_path / "no_transcriptions", profiles)
    (o,) = obs
    assert o["source_file"] == v5["filed_as"] and o["source_sha256"] == v5["sha256"] and len(o["layers"]) == 2
    assert stats["structured_not_read"] == 1 and stats["not_read"][0]["file"] == v6["filed_as"]


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
    # rollover at the configured season start (15 Sep): until then the season in progress is the one just ended
    assert build.current_season_year(pd.Timestamp("2026-09-14T23:00", tz="UTC")) == 2025
    assert build.current_season_year(pd.Timestamp("2026-09-15", tz="UTC")) == 2026
    assert build.current_season_year(pd.Timestamp("2026-07-01", tz="UTC")) == 2025
    assert build.current_season_year(pd.Timestamp("2026-06-30", tz="UTC")) == 2025


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


def test_fts360_archive_sync_refreshes_the_previous_month_on_every_day(tmp_path, monkeypatch):
    import gzip

    from snowagent.ops import update

    raw, arc = tmp_path / "raw", tmp_path / "archive"
    monkeypatch.setattr(update, "FTS_RAW", raw)
    monkeypatch.setattr(update, "FTS_ARCHIVE", arc)
    f = raw / "lookout" / "lookout_2026-09.csv"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"Date,TA\n2026-09-30T11:00Z,1\n2026-09-30T12:00Z,2\n")  # archived by the 30 Sep run
    assert update.sync_fts360_archive(pd.Timestamp("2026-09-30T13:00", tz="UTC"))["archived"] == 1
    grown = f.read_bytes() + b"2026-09-30T23:00Z,3\n"  # fetched on 1 Oct, but that run's archive sync failed
    f.write_bytes(grown)
    assert update.sync_fts360_archive(pd.Timestamp("2026-10-02T13:00", tz="UTC"))["archived"] == 1
    assert gzip.decompress((arc / "lookout" / "lookout_2026-09.csv.gz").read_bytes()) == grown
    f.write_bytes(grown + b"2026-09-30T23:30Z,4\n")  # two months on, September is a complete month: archived once
    assert update.sync_fts360_archive(pd.Timestamp("2026-11-02T13:00", tz="UTC"))["archived"] == 0
    assert [str(update.prev_month(pd.Timestamp(t, tz="UTC"))) for t in ("2026-10-01T00:00", "2026-10-31T23:00",
                                                                         "2027-01-15T13:00")] == \
        ["2026-09", "2026-09", "2026-12"]


def test_fts360_fetch_requests_the_previous_month_on_every_day(tmp_path, monkeypatch):
    import snowagent.ingest.fts360 as fts
    from snowagent.ops import update

    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "external_sources.yaml").write_text("fts360:\n  agency: 450\n  stations: {a: '1'}\n")
    starts = []
    monkeypatch.setattr(fts, "fetch_station", lambda ag, key, hx, start, end, raw: starts.append(start) or [])
    monkeypatch.setattr(update, "sync_fts360_archive", lambda now: {"archived": 0, "kept": []})
    for t in ("2026-10-01T13:00", "2026-10-02T13:00", "2026-10-31T13:00", "2027-01-15T13:00"):
        update.fetch_fts360(pd.Timestamp(t, tz="UTC"))
    assert starts == ["2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z", "2026-12-01T00:00:00Z"]


def _era5_month(path: Path, nan_hours: int = 0) -> None:
    import numpy as np

    t = pd.date_range("2026-09-01", periods=24, freq="h", tz="UTC")
    a = np.ones((24, 2, 2), dtype="float32")
    flux = a.copy()
    flux[:nan_hours, 0, 1] = np.nan
    np.savez_compressed(path, time_utc=t.astype("int64").to_numpy(), **{"2t": a, "mtpr": flux, "msdwswrf": a,
                                                                       "msdwlwrf": a})


def test_era5_fetch_tells_unpublished_months_from_errors(tmp_path, monkeypatch):
    from snowagent.ingest import era5
    from snowagent.ops import update

    class Http404(Exception):
        status = 404

    def unpublished(cause=None):
        exc = FileNotFoundError(f"{era5.BASE}/e5.oper.an.sfc/x.nc")
        exc.__cause__ = cause
        return exc

    outcome = {(2026, 10): unpublished(), (2026, 11): unpublished(Http404()),
               (2026, 12): unpublished(ConnectionError("connection reset")),  # fsspec wraps any failed request
               (2027, 1): OSError("HDF5: truncated file"), (2027, 3): unpublished()}

    def extract(y, m, out_dir):
        if (y, m) in outcome:
            raise outcome[(y, m)]
        _era5_month(out_dir / f"era5_box_{y}{m:02d}.npz")

    monkeypatch.setattr(update, "ERA5_DIR", tmp_path)
    monkeypatch.setattr(era5, "extract_month", extract)
    _era5_month(tmp_path / "era5_box_202609.npz", nan_hours=3)
    res = update.fetch_era5(2026, pd.Timestamp("2027-03-15T13:00", tz="UTC"))
    assert res["added"] == ["2027-02"]
    assert res["not_yet_available"] == ["2026-10", "2026-11", "2027-03"]
    assert [e[:7] for e in res["errors"]] == ["2026-12", "2027-01"]
    assert "ConnectionError: connection reset" in res["errors"][0] and "HDF5: truncated file" in res["errors"][1]
    msgs = [w["message"] for w in res["warnings"]]
    assert any("2026-09: 3 h without flux values" in m for m in msgs)  # detected, not re-fetched
    assert any("2026-10: still not on the mirror 134 days" in m for m in msgs)  # overdue
    assert not any("2026-11" in m or "2027-03" in m for m in msgs)  # within the mirror's usual delay
    assert sum("extraction failed" in m for m in msgs) == 2
    assert {w["level"] for w in res["warnings"]} == {"warning"}
    (tmp_path / "era5_box_202609.npz").write_bytes(b"not a zip")  # an unreadable month is reported, not fatal
    (w,) = update.fetch_era5(2026, pd.Timestamp("2026-09-20T13:00", tz="UTC"))["warnings"]
    assert "era5_box_202609.npz unreadable" in w["message"]


def test_era5_meanflux_listing_without_the_month_is_unpublished_but_a_failed_listing_is_not(monkeypatch):
    from types import SimpleNamespace

    import pytest
    import requests

    from snowagent.ingest import era5

    def listing(status):
        def raise_for_status():
            if status != 200:
                raise requests.HTTPError(f"{status} Service Unavailable")
        return lambda *_a, **_k: SimpleNamespace(text="<ListBucketResult></ListBucketResult>",
                                                 raise_for_status=raise_for_status)

    monkeypatch.setattr(requests, "get", listing(200))
    with pytest.raises(FileNotFoundError) as e:
        era5.read_mf("mtpr", 2027, 3)
    assert era5.is_unpublished(e.value)
    monkeypatch.setattr(requests, "get", listing(503))
    with pytest.raises(requests.HTTPError) as e:
        era5.read_mf("mtpr", 2027, 3)
    assert not era5.is_unpublished(e.value)


def _gfs_csv(path: Path, points=("a", "b"), max_lead: int = 72) -> None:
    rows = [{"run_utc": "x", "lead_h": h, "point": p} for h in range(0, max_lead + 1, 3) for p in points]
    pd.DataFrame(rows).to_csv(path, index=False)


def test_gfs_fetch_redoes_incomplete_runs_and_reports_runs_past_the_retry_window(tmp_path, monkeypatch):
    from snowagent.ingest import gfs_archive
    from snowagent.ops import update

    arc = tmp_path / "archive"
    arc.mkdir()
    for d in pd.date_range("2026-09-14", "2026-10-20"):
        if f"{d:%m-%d}" not in ("09-16", "10-05"):  # missing: one past the 21-day window, one inside it
            _gfs_csv(arc / f"gfs_{d:%Y%m%d}00.csv")
    _gfs_csv(arc / "gfs_2026092000.csv", points=("a",))  # an early partial extract, past the window
    _gfs_csv(arc / "gfs_2026101000.csv", max_lead=24)  # incomplete, inside the window
    (arc / "gfs_2026101100.csv").write_text("")  # unreadable counts as incomplete
    assert not gfs_archive.run_complete(arc / "gfs_2026101100.csv", ["a", "b"], 72)
    assert gfs_archive.run_complete(arc / "gfs_2026101200.csv", ["a", "b"], 72)
    assert not gfs_archive.run_complete(arc / "gfs_2026101200.csv", ["a", "b", "c"], 72)

    extracted, synced = [], []
    monkeypatch.setattr(update, "GFS_ARCHIVE", arc)
    monkeypatch.setattr(update, "GFS_INTERIM", tmp_path / "interim")
    monkeypatch.setattr(update, "_gfs_points", lambda: {"a": (51.0, -115.8), "b": (51.7, -116.5)})
    monkeypatch.setattr(gfs_archive, "extract_run", lambda run, leads, pts: (extracted.append(run) or [], []))
    monkeypatch.setattr(gfs_archive, "write_run", lambda rows, prov, out, run: None)
    monkeypatch.setattr(update.subprocess, "run", lambda *a, **k: synced.append(a))
    now = pd.Timestamp("2026-10-20T13:00", tz="UTC")
    res = update.fetch_gfs(pd.Timestamp("2026-09-15", tz="UTC"), now)
    assert [f"{r:%m-%d}" for r in extracted] == ["10-05", "10-10", "10-11"] and len(synced) == 1
    assert res["runs_added"] == ["2026-10-05", "2026-10-10", "2026-10-11"]
    assert res["incomplete_retried"] == ["2026-10-10", "2026-10-11"]
    assert res["permanently_missing"] == ["2026-09-16"] and res["permanently_incomplete"] == ["2026-09-20"]
    msgs = [w["message"] for w in res["warnings"] if w["level"] == "warning"]
    assert len(msgs) == 2 and all("past the 21-day retry window" in m for m in msgs)
    assert "2026-09-16" in msgs[0] and "2026-09-20" in msgs[1]


def test_live_season_cut_at_a_gfs_gap_is_a_warning(monkeypatch):
    import numpy as np

    from snowagent.baseline import assemble as asm
    from snowagent.web import build

    cols = ["ta", "rh", "vw", "dw", "iswr", "ilwr", "psum"]

    def fake_assemble(plot, start, end, era5_only=None, reanalysis="era5", gfs_fallback_days=0):
        idx = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="h")
        data = pd.DataFrame(1.0, index=idx, columns=cols)
        unmeasured = ["vw", "dw", "iswr", "ilwr"]
        if reanalysis == "era5":  # ERA5 not yet published from 1 October
            data.loc[data.index >= "2025-10-01T00:00Z", unmeasured] = np.nan
        else:  # GFS day-1: three days of runs missing, beyond the 2-day fallback
            data.loc["2025-10-05T01:00Z":"2025-10-08T00:00Z", unmeasured] = np.nan
        return asm.PlotForcing(plot, data, pd.DataFrame(reanalysis, index=idx, columns=cols), [])

    monkeypatch.setattr(asm, "assemble", fake_assemble)
    monkeypatch.setattr(build, "_cfg", lambda: {"season_start": "09-15", "season_end": "06-30",
                                                "plots": {"goats_eye": {}}})
    warnings: list = []
    pf, _start, end, mode = build.season_forcing("goats_eye", 2025, pd.Timestamp("2025-10-10T12:30", tz="UTC"),
                                                 warnings)
    assert mode == "live" and end == pd.Timestamp("2025-10-05T00:00", tz="UTC") and pf.data.index[-1] == end
    (w,) = warnings
    assert w["level"] == "warning" and w["source"] == "forcing:goats_eye" and w["variables"] == ["vw", "dw", "iswr", "ilwr"]
    assert w["gap_start_utc"].startswith("2025-10-05T01:00") and w["gap_end_utc"].startswith("2025-10-08T00:00")
    assert w["last_record_utc"].startswith("2025-10-05T00:00") and w["age_h"] == 132.5
    assert "weather stops at 2025-10-05 00:00 UTC" in w["message"] and "60 complete hours" in w["message"]
    assert any(n.startswith("cut: ") for n in pf.notes)


ROOT = Path(__file__).parents[2]
CFG = {"plots": {"goats_eye": {"ta": ["sunshine_village_ab_env", "lookout"], "rh": ["lookout"],
                               "psum": ["sunshine_village_ab_env"], "hs_check": ["sunshine_village_ab_env"]},
                 "simpson": {"ta": ["simpson_lower", "simpson_upper"], "rh": ["simpson_lower", "simpson_upper"],
                             "psum": ["sunshine_village_ab_env"], "hs_check": ["simpson_lower"]}},
       "seasonal_stations": {"lookout": {"off_months": [6, 7, 8, 9, 10]}}}


def test_stale_stations_and_gfs_are_flagged_with_their_role_and_seasonal_lookout_is_expected():
    from snowagent.ops import update

    t = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
    now = t("2026-10-03T13:00")
    last = {"sunshine_village_ab_env": t("2026-10-03T12:00"), "lookout": t("2026-06-23T19:00"),
            "simpson_lower": t("2026-10-02T06:00"), "simpson_upper": t("2026-10-03T12:00")}
    ws = {w["source"]: w for w in update.staleness_warnings(now, last, t("2026-10-03"), CFG)}
    assert set(ws) == {"station:lookout", "station:simpson_lower"}  # fresh stations and a 13 h old GFS run: none
    lo = ws["station:lookout"]
    assert lo["level"] == "info" and lo["seasonal"] and lo["last_record_utc"] == "2026-06-23T19:00:00+00:00"
    assert "seasonal station, off for the summer (expected)" in lo["message"]
    assert "Lookout supplies Goat's Eye humidity: GFS day-1 fill used instead." in lo["message"]
    assert "Lookout backs up Goat's Eye temperature: Sunshine Village AB station in use." in lo["message"]
    sl = ws["station:simpson_lower"]
    assert sl["level"] == "warning" and sl["age_h"] == 31.0 and not sl["seasonal"]
    assert "Simpson Lower supplies Simpson temperature and Simpson humidity: Simpson Upper used instead." in sl["message"]
    assert "Simpson snow-depth check: no measured snow depth to compare" in sl["message"]

    # a winter outage of the seasonal station is a warning; a station without records and a 3-day-old GFS too
    winter = update.staleness_warnings(t("2026-12-10T13:00"), {"lookout": t("2026-06-23T19:00"),
                                                               "sunshine_village_ab_env": None}, t("2026-12-07"), CFG)
    by = {w["source"]: w for w in winter}
    assert by["station:lookout"]["level"] == "warning" and "usually runs" in by["station:lookout"]["message"]
    assert "Goat's Eye temperature" in by["station:lookout"]["message"]  # Sunshine is out as well: fill used
    sv = by["station:sunshine_village_ab_env"]
    assert sv["level"] == "warning" and sv["age_h"] is None and "no records found" in sv["message"]
    assert by["gfs"]["level"] == "warning" and by["gfs"]["age_h"] == 85.0
    assert update.staleness_warnings(t("2026-12-10T13:00"), {}, None, CFG)[0]["source"] == "gfs"

    listed = {**CFG, "seasonal_stations": ["lookout"]}  # a bare list: expected in any month
    (w,) = update.staleness_warnings(t("2026-12-10T13:00"), {"lookout": t("2026-06-23")}, t("2026-12-10"), listed)
    assert w["level"] == "info"


def test_config_marks_lookout_seasonal_and_lists_every_plot_station():
    import yaml

    from snowagent.ops import update

    cfg = yaml.safe_load((ROOT / "config" / "plot_forcing.yaml").read_text())
    assert 10 in cfg["seasonal_stations"]["lookout"]["off_months"]
    assert set(update.plot_stations(cfg["plots"])) <= set(update.STATION_NAMES)
    assert "simpson_upper" in update.plot_stations(cfg["plots"])


def _build_env(tmp_path, monkeypatch, build_season=None, write_public=None) -> list:
    """update.build on stubs (no engine, no network): the config's plots, every station fresh but Lookout,
    a GFS archive up to yesterday and a forcing cut at Simpson. Returns the list of write_index calls."""
    import yaml

    import snowagent.ingest.fts360 as fts
    import snowagent.ingest.min as min_
    import snowagent.obs.inbox as inbox
    import snowagent.obs.observed as observed
    from snowagent.ops import update
    from snowagent.web import build as web

    last = {"lookout": "2026-06-23T19:00Z"}
    arc = tmp_path / "gfs"
    arc.mkdir()
    for d in pd.date_range("2026-09-14", "2026-10-02"):  # today's run not yet archived
        _gfs_csv(arc / f"gfs_{d:%Y%m%d}00.csv")
    cut = {"level": "warning", "source": "forcing:simpson", "message": "Simpson: weather stops at ...",
           "last_record_utc": None, "age_h": None}
    index_calls: list = []
    monkeypatch.setattr(web, "_cfg", lambda: yaml.safe_load((ROOT / "config" / "plot_forcing.yaml").read_text()))
    monkeypatch.setattr(web, "build_season", build_season or (lambda plot, y, out, work, workers=1, now=None:
                        {"site": plot, **({"warnings": [cut]} if plot == "simpson" else {})}))
    monkeypatch.setattr(web, "write_public", write_public or (lambda out: {}))
    monkeypatch.setattr(web, "write_index", lambda out, now=None: index_calls.append(out) or {})
    monkeypatch.setattr(observed, "build_observed", lambda a, b: ([], {"unique_observations": 0}))
    monkeypatch.setattr(observed, "write_observed", lambda obs, path: None)
    monkeypatch.setattr(min_, "ARCHIVE", tmp_path / "min")
    monkeypatch.setattr(min_, "latest_versions", lambda a: [])
    monkeypatch.setattr(inbox, "receipts_summary", lambda: [])
    monkeypatch.setattr(fts, "load_station", lambda key: pd.DataFrame(
        {"time_utc": [pd.Timestamp(last.get(key, "2026-10-03T12:00Z"))]}))
    monkeypatch.setattr(update, "GFS_ARCHIVE", arc)
    monkeypatch.setattr(update, "_gfs_points", lambda: {"a": (51.0, -115.8), "b": (51.7, -116.5)})
    return index_calls


def test_build_writes_sorted_warnings_and_all_plot_stations_to_status(tmp_path, monkeypatch):
    from snowagent.ops import update

    _build_env(tmp_path, monkeypatch)
    res = update.build(pd.Timestamp("2026-10-03T13:00", tz="UTC"), out_dir=tmp_path / "web", work=tmp_path / "work")
    st = json.loads((tmp_path / "web" / "status.json").read_text())
    assert res["ok"] and res["failed_steps"] == []
    assert st["warnings"] == res["warnings"] and st["stale_after_h"] == {"station": 24.0, "gfs": 48.0, "update": 36.0}
    assert [(w["level"], w["source"]) for w in st["warnings"]] == [
        ("warning", "forcing:simpson"), ("info", "station:lookout"), ("info", "gfs")]
    assert "2026-10-03" in st["warnings"][2]["message"]
    assert st["weather"]["Simpson Upper: last record"] == "2026-10-03T12:00:00+00:00"
    assert st["weather"]["Lookout: last record"] == "2026-06-23T19:00:00+00:00"


def test_build_lists_profile_files_not_read_in_status(tmp_path, monkeypatch):
    """A profile file kept but not read into the observed set (e.g. CAAML v6 committed to profiles/) reaches
    status.json as an info entry, not only the `obs profiles` output (ADR-048)."""
    import snowagent.obs.observed as observed
    from snowagent.ops import update

    _build_env(tmp_path, monkeypatch)
    nr = {"file": "profiles/2026-2027/Simpson/x.caaml", "sha256": "0" * 64, "format": "caaml_other",
          "reason": "CAAML other than v5 (e.g. CAAML v6 from SnowScope): no parser yet; kept, not read"}
    monkeypatch.setattr(observed, "build_observed", lambda a, b: ([], {"unique_observations": 7, "not_read": [nr]}))
    res = update.build(pd.Timestamp("2026-10-03T13:00", tz="UTC"), out_dir=tmp_path / "web", work=tmp_path / "work")
    st = json.loads((tmp_path / "web" / "status.json").read_text())
    (w,) = [w for w in st["warnings"] if w["source"] == "observed:not_read"]
    assert w["level"] == "info" and nr["file"] in w["message"] and "no parser yet" in w["message"]
    assert res["ok"] and res["observed"] == 7 and update.run_counts("build", res)["observed"] == 7


def test_fts360_fetch_contains_a_failing_station_and_stops_at_a_refused_credential(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import pytest

    import snowagent.ingest.fts360 as fts
    from snowagent.ops import update

    real_fetch = fts.fetch_station
    monkeypatch.chdir(tmp_path)  # relative config/ and data/ paths
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "external_sources.yaml").write_text(
        "fts360:\n  agency: 450\n  stations: {a: '1', b: '2', c: '3', d: '4', e: '5', f: '6'}\n")
    calls, synced = [], []

    def fetch_station(agency, key, hex_id, start, end, raw_dir):
        calls.append(key)
        if key == "b":
            raise ValueError("unexpected reply")
        if key == "c":  # the file system refuses this station's folder: not the credential
            raise PermissionError(13, "Permission denied", "data/raw/fts360/c/c_2026-10.csv")
        if key == "e":
            raise fts.CredentialRefused("FTS360 401: credential missing or not accepted")
        return [{"path": f"{key}.csv"}]

    monkeypatch.setattr(fts, "fetch_station", fetch_station)
    monkeypatch.setattr(update, "sync_fts360_archive", lambda now: synced.append(now) or {"archived": 2, "kept": []})
    res = update.fetch_fts360(pd.Timestamp("2026-10-03T13:00", tz="UTC"))
    assert calls == ["a", "b", "c", "d", "e"] and res["skipped"] == ["f"]  # one 401 stands for every station
    assert res["a"]["files"] == 1 and res["d"]["files"] == 1 and res["b"]["files"] == 0 and res["c"]["files"] == 0
    assert res["failed_steps"] == [{"step": "fts360:b", "error": "ValueError: unexpected reply"},
                                   {"step": "fts360:c", "error": "PermissionError: [Errno 13] Permission denied: "
                                                                 "'data/raw/fts360/c/c_2026-10.csv'"},
                                   {"step": "fts360", "error": "CredentialRefused: FTS360 401: credential missing or "
                                                               "not accepted"}]
    assert issubclass(fts.CredentialRefused, PermissionError)
    refused = SimpleNamespace(ok=False, status_code=401, url="u", text="", content=b"")
    monkeypatch.setattr(fts.requests, "get", lambda *_a, **_k: refused)
    with pytest.raises(fts.CredentialRefused, match="FTS360 401"):  # what the real fetch_station raises
        real_fetch(450, "a", "1", "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z", tmp_path / "raw")
    assert len(synced) == 1 and res["archived_files"] == 2  # what was fetched is still archived

    def broken_sync(now):
        raise OSError("No space left on device")

    monkeypatch.setattr(update, "sync_fts360_archive", broken_sync)
    res = update.fetch_fts360(pd.Timestamp("2026-10-03T13:00", tz="UTC"))
    assert res["archived_files"] is None and res["failed_steps"][-1]["step"] == "fts360:archive"


def test_fts360_failed_requests_are_warnings_and_a_station_with_none_answered_fails_the_run(tmp_path, monkeypatch):
    import snowagent.ingest.fts360 as fts
    from snowagent.ops import update

    monkeypatch.chdir(tmp_path)
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "external_sources.yaml").write_text(
        "fts360:\n  agency: 450\n  stations: {a: '1', b: '2', c: '3', lookout: '4'}\n")
    (tmp_path / "config" / "plot_forcing.yaml").write_text(
        "plots: {}\nseasonal_stations:\n  lookout:\n    off_months: [6, 7, 8, 9, 10]\n")
    sep, octo = ["2026-09-01T00:00:00.000Z", "2026-10-01T00:00:00.000Z"], ["2026-10-01T00:00:00.000Z", "now"]

    def err(window, code, text):  # the record fetch_station writes for a failed request (after its retries)
        return {"url": "u", "status_code": code, "station": "x", "window": window, "error": text}

    replies = {"a": [{"path": "a_2026-09.csv", "status": "exists"}, err(octo, 503, "Service\n Unavailable")],
               "b": [err(sep, None, "ConnectionError: connection reset"), {"path": "b_2026-10.csv"}],
               "c": [{"path": "c_2026-09.csv", "status": "exists"}, {"path": "c_2026-10.csv"}],
               "lookout": [err(sep, 503, ""), err(octo, 503, "")]}
    monkeypatch.setattr(fts, "fetch_station", lambda ag, key, hx, start, end, raw: replies[key])
    monkeypatch.setattr(update, "sync_fts360_archive", lambda now: {"archived": 1, "kept": []})
    now = pd.Timestamp("2026-10-03T13:00", tz="UTC")
    res = update.fetch_fts360(now)
    assert res["failed_steps"] == [{"step": "fts360:a",  # its only request failed
                                    "error": "RuntimeError: every request failed: 2026-10: HTTP 503: Service "
                                             "Unavailable"}]
    (wb, wl) = res["warnings"]
    assert (wb["level"], wb["source"]) == ("warning", "fts360:b") and wb["message"].startswith(
        "FTS360 b 2026-09: ConnectionError: connection reset; records of that month not collected this run")
    assert (wl["level"], wl["source"], wl["seasonal"]) == ("info", "fts360:lookout", True)  # off: never a failure
    assert "2026-09: HTTP 503: (empty reply); 2026-10: HTTP 503: (empty reply)" in wl["message"]
    assert "off for the summer (expected)" in wl["message"]
    assert res["b"]["files"] == 1 and res["a"]["errors"] == ["Service\n Unavailable"]

    step = update.Steps()  # as in update.fetch: the failed station makes the run exit 2
    step("fts360", update.fetch_fts360, now)
    assert [f["step"] for f in step.failed] == ["fts360:a"] and update.exit_code({"failed_steps": step.failed}) == 2
    replies["lookout"] = replies["a"]  # outside its off months (here November) Lookout fails like any station
    assert [f["step"] for f in update.fetch_fts360(pd.Timestamp("2026-11-03T13:00", tz="UTC"))["failed_steps"]] == \
        ["fts360:a", "fts360:lookout"]


def test_gfs_archive_sync_failure_keeps_the_fetch_output(tmp_path, monkeypatch):
    import subprocess

    from snowagent.ingest import gfs_archive
    from snowagent.ops import update

    arc = tmp_path / "archive"
    arc.mkdir()
    for d in pd.date_range("2026-09-14", "2026-10-02"):
        if f"{d:%m-%d}" != "10-01":
            _gfs_csv(arc / f"gfs_{d:%Y%m%d}00.csv")

    def sync(*a, **k):
        raise subprocess.CalledProcessError(1, a[0], stderr=b"cp: cannot create regular file: Permission denied\n")

    monkeypatch.setattr(update, "GFS_ARCHIVE", arc)
    monkeypatch.setattr(update, "GFS_INTERIM", tmp_path / "interim")
    monkeypatch.setattr(update, "_gfs_points", lambda: {"a": (51.0, -115.8), "b": (51.7, -116.5)})
    monkeypatch.setattr(gfs_archive, "extract_run", lambda run, leads, pts: ([], []))
    monkeypatch.setattr(gfs_archive, "write_run", lambda rows, prov, out, run: None)
    monkeypatch.setattr(update.subprocess, "run", sync)
    res = update.fetch_gfs(pd.Timestamp("2026-09-15", tz="UTC"), pd.Timestamp("2026-10-02T13:00", tz="UTC"))
    assert res["runs_added"] == ["2026-10-01"] and res["archive_synced"] is False
    (f,) = res["failed_steps"]
    assert f["step"] == "gfs:archive_sync" and f["error"].startswith("CalledProcessError: Command")
    assert "stderr: cp: cannot create regular file: Permission denied" in f["error"]


def test_fetch_goes_on_after_a_failed_source_and_lists_every_failure(tmp_path, monkeypatch):
    import requests

    from snowagent.ops import update

    def raises(exc):
        def f(*a, **k):
            raise exc
        return f

    era5_w = update.warning("warning", "era5", "ERA5 2026-09: 3 h without flux values")
    era5_calls = []
    monkeypatch.setattr(update, "ERA5_DIR", tmp_path)  # empty: the previous season's months are missing too
    monkeypatch.setattr(update, "fetch_fts360", lambda now: {
        "lookout": {"files": 0, "errors": ["PermissionError: FTS360 401"]}, "skipped": ["whymper"], "warnings": [],
        "failed_steps": [{"step": "fts360", "error": "PermissionError: FTS360 401"}]})
    monkeypatch.setattr(update, "fetch_gfs", raises(RuntimeError("NOMADS index unavailable")))
    monkeypatch.setattr(update, "fetch_era5", lambda y, now: era5_calls.append(y) or {
        "added": [] if y == 2026 else ["2026-06"], "warnings": [era5_w] if y == 2026 else []})
    monkeypatch.setattr(update, "fetch_min", raises(requests.HTTPError("503 Server Error")))
    monkeypatch.setattr(update, "fetch_inbox", lambda: [])
    monkeypatch.setattr(update, "fetch_webcams", lambda now: [{"cam": "stake", "status": "stored"}])
    res = update.fetch(pd.Timestamp("2026-10-03T13:00", tz="UTC"))
    assert res["ok"] is False and res["gfs"] is None and res["min"] is None
    assert res["era5"]["added"] == [] and res["inbox"] == [] and res["webcams"][0]["status"] == "stored"
    assert era5_calls == [2026, 2025] and res["era5_previous"]["added"] == ["2026-06"]  # finished season (ADR-054)
    assert update.run_counts("fetch", res)["era5_months_added"] == 1
    assert res["failed_steps"] == [{"step": "fts360", "error": "PermissionError: FTS360 401"},
                                   {"step": "gfs", "error": "RuntimeError: NOMADS index unavailable"},
                                   {"step": "min", "error": "HTTPError: 503 Server Error"}]
    assert "failed_steps" not in res["fts360"]  # moved to the top level
    assert [(w["level"], w["source"]) for w in res["warnings"]] == [
        ("error", "update:fts360"), ("error", "update:gfs"), ("error", "update:min"), ("warning", "era5")]
    assert "no new GFS runs archived" in res["warnings"][1]["message"]

    for m in update.era5_season_months(2025):  # every month of the previous season cached: not requested again
        (tmp_path / f"era5_box_{m:%Y%m}.npz").write_bytes(b"")
    era5_calls.clear()
    res = update.fetch(pd.Timestamp("2026-10-03T13:00", tz="UTC"))
    assert era5_calls == [2026] and "era5_previous" not in res

    def unreadable(y, era5_dir=None):
        raise PermissionError("data/interim/era5")

    monkeypatch.setattr(update, "era5_months_missing", unreadable)  # the cache check is a step of its own
    era5_calls.clear()
    res = update.fetch(pd.Timestamp("2026-10-03T13:00", tz="UTC"))
    assert era5_calls == [2026] and "era5_previous" not in res and res["webcams"][0]["status"] == "stored"
    assert {"step": "era5:previous", "error": "PermissionError: data/interim/era5"} in res["failed_steps"]


def test_build_writes_index_and_status_when_a_plot_or_a_check_fails(tmp_path, monkeypatch):
    from snowagent.ops import update
    from snowagent.web.build import SITES

    def season(plot, y, out, work, workers=1, now=None):
        if plot == "goats_eye":
            raise RuntimeError("engine crashed")
        return {"site": plot}

    def public(out):
        raise OSError("No space left on device")

    index_calls = _build_env(tmp_path, monkeypatch, build_season=season, write_public=public)
    now = pd.Timestamp("2026-10-03T13:00", tz="UTC")
    res = update.build(now, out_dir=tmp_path / "web", work=tmp_path / "work")
    assert res["ok"] is False and [f["step"] for f in res["failed_steps"]] == ["season:goats_eye", "public"]
    assert [s["site"] for s in res["seasons"]] == list(SITES) and len(index_calls) == 1  # every plot tried
    assert res["seasons"][0] == {"site": "goats_eye", "season": "2026-2027", "error": "RuntimeError: engine crashed"}
    st = json.loads((tmp_path / "web" / "status.json").read_text())
    assert [(w["level"], w["source"]) for w in st["warnings"][:2]] == [("error", "update:season:goats_eye"),
                                                                      ("error", "update:public")]
    assert "engine crashed" in st["warnings"][0]["message"] and "live season not rebuilt" in st["warnings"][0]["message"]
    assert st["weather"]["Lookout: last record"] == "2026-06-23T19:00:00+00:00"  # later steps still ran

    def broken(now):
        raise KeyError("plots")

    monkeypatch.setattr(update, "_station_status", broken)
    res = update.build(now, out_dir=tmp_path / "web", work=tmp_path / "work")
    st = json.loads((tmp_path / "web" / "status.json").read_text())
    assert st["weather"] == {} and "update:station_status" in [w["source"] for w in st["warnings"]]
    assert st["min"] == {"reports": 0, "last_scan_utc": None}


def test_update_cli_prints_the_whole_result_and_exits_2_when_a_step_failed(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from snowagent.cli import app
    from snowagent.ops import update

    monkeypatch.setattr(update, "RUN_LOG", tmp_path / "runs.jsonl")
    monkeypatch.setattr(update, "LOCK_FILE", tmp_path / "update.lock")
    failed = {"ok": False, "min": None, "failed_steps": [{"step": "min", "error": "HTTPError: 503"}], "warnings": []}
    monkeypatch.setattr(update, "fetch", lambda: failed)
    r = CliRunner().invoke(app, ["update", "fetch"])
    assert r.exit_code == update.EXIT_FAILED == 2 and json.loads(r.stdout) == failed
    monkeypatch.setattr(update, "build", lambda workers, out_dir: {"ok": True, "failed_steps": [], "seasons": []})
    r = CliRunner().invoke(app, ["update", "build"])
    assert r.exit_code == 0 and json.loads(r.stdout)["ok"] is True
    assert update.exit_code({"failed_steps": []}) == 0 and update.exit_code({}) == 0
    assert [json.loads(x)["exit_code"] for x in (tmp_path / "runs.jsonl").read_text().splitlines()] == [2, 0]


def test_run_log_merges_by_union_so_two_branches_that_appended_do_not_conflict():
    from snowagent.ops import update

    rules = [ln.split() for ln in (ROOT / ".gitattributes").read_text().splitlines() if not ln.startswith("#")]
    assert [update.RUN_LOG.as_posix(), "merge=union"] in rules


def test_run_log_gets_one_line_per_run_with_failures_counts_and_crashes(tmp_path, monkeypatch):
    import pytest

    from snowagent.ops import update

    monkeypatch.setattr(update, "LOCK_FILE", tmp_path / "update.lock")
    log = tmp_path / "ops" / "runs.jsonl"
    fetched = {"fts360": {"lookout": {"files": 2, "errors": []}, "bow_summit": {"files": 1, "errors": []},
                          "archived_files": 3, "warnings": []},
               "gfs": None, "era5": {"added": ["2026-06"]}, "min": {"archived": 4, "errors": 0}, "inbox": [],
               "webcams": [{"status": "stored"}, {"status": "stale"}],
               "warnings": [update.warning("error", "update:gfs", "x"), update.warning("info", "gfs", "y")],
               "failed_steps": [{"step": "gfs", "error": "RuntimeError: " + "z" * 400}]}
    res, code = update.run_command("fetch", lambda: fetched, log)
    assert res is fetched and code == 2
    built = {"seasons": [{"site": "goats_eye", "error": "RuntimeError: x"}, {"site": "simpson"}], "observed": 5,
             "public": {"goats_eye": 3, "simpson": 1}, "warnings": [], "failed_steps": []}
    assert update.run_command("build", lambda: built, log)[1] == 0

    def crash():
        raise MemoryError("out of memory")

    with pytest.raises(MemoryError):
        update.run_command("build", crash, log)
    f, b, c = (json.loads(x) for x in log.read_text().splitlines())
    assert f["command"] == "fetch" and f["ok"] is False and f["exit_code"] == 2 and f["time_utc"].endswith("+00:00")
    assert f["failed_steps"][0]["step"] == "gfs" and len(f["failed_steps"][0]["error"]) == 200
    assert f["counts"] == {"fts360_files": 3, "gfs_runs_added": None, "era5_months_added": 1, "min_archived": 4,
                           "inbox_items": 0, "webcam_images_stored": 1}
    assert f["warnings"] == {"error": 1, "warning": 0, "info": 1}
    assert b["ok"] is True and b["counts"] == {"seasons_built": 1, "seasons_failed": 1, "observed": 5,
                                               "public_reports": 4}
    assert c["exit_code"] == 1 and c["failed_steps"] == [{"step": "build", "error": "MemoryError: out of memory"}]
    assert max(len(x) for x in log.read_text().splitlines()) < 600  # small: a few hundred bytes per run

    (tmp_path / "blocked").write_text("a file, not a folder")  # an unwritable log is a failed step, not a crash
    res, code = update.run_command("build", lambda: {**built, "failed_steps": []}, tmp_path / "blocked" / "r.jsonl")
    assert code == 2 and res["ok"] is False and res["failed_steps"][0]["step"] == "run_log"
    assert res["warnings"][0]["source"] == "update:run_log"


def _lock_file(path: Path, hours_ago: float, pid: int | None = None, host: str | None = None, cmd: str = "build"):
    import os
    import socket

    t = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=hours_ago)
    path.write_text(json.dumps({"pid": os.getpid() if pid is None else pid, "host": host or socket.gethostname(),
                                "command": cmd, "started_utc": t.isoformat(timespec="seconds")}))


def test_update_lock_refuses_a_second_run_and_takes_over_only_a_stale_lock(tmp_path, monkeypatch):
    import os

    import pytest

    from snowagent.ops import update

    p = tmp_path / "data" / "update.lock"
    with update.update_lock("fetch", p) as info:
        assert json.loads(p.read_text())["pid"] == os.getpid() == info["pid"] and "took_over" not in info
        with pytest.raises(update.UpdateLocked, match="another update run holds .*fetch started"):
            with update.update_lock("build", p):
                pass
        assert p.exists()  # the refused run leaves the holder's lock alone
    assert not p.exists()  # released

    _lock_file(p, hours_ago=4)  # a live process, but older than 3 h
    with update.update_lock("fetch", p) as info:
        assert "stale after 3 h" in info["took_over"]["reason"] and info["took_over"]["command"] == "build"
        assert json.loads(p.read_text())["command"] == "fetch"
    _lock_file(p, hours_ago=1)
    monkeypatch.setattr(update, "_pid_alive", lambda pid: False)  # its process is gone on this host
    with update.update_lock("fetch", p) as info:
        assert info["took_over"]["reason"] == f"process {os.getpid()} is no longer running"
    _lock_file(p, hours_ago=1, host="another-host")  # a pid on another host cannot be checked: wait for 3 h
    with pytest.raises(update.UpdateLocked):
        with update.update_lock("fetch", p):
            pass

    p.write_text("")  # half-written: its age is the file's modification time
    with pytest.raises(update.UpdateLocked):
        with update.update_lock("fetch", p):
            pass
    old = (pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=5)).timestamp()
    os.utime(p, (old, old))
    with update.update_lock("fetch", p) as info:
        assert "took_over" in info
        _lock_file(p, hours_ago=0, pid=1, cmd="build")  # someone else's lock by now: not deleted on exit
    assert json.loads(p.read_text())["pid"] == 1


def test_two_runs_taking_over_the_same_stale_lock_never_both_hold_it(tmp_path, monkeypatch):
    import threading

    from snowagent.ops import update

    p = tmp_path / "update.lock"
    _lock_file(p, hours_ago=4)  # left by a run that did not finish
    real_holder = update._lock_holder
    b_has_read, a_waited, release_b = threading.Event(), threading.Event(), threading.Event()
    got: dict = {}

    def holder(path):  # run B stops right after reading the stale holder, before it takes the lock over
        held = real_holder(path)
        if threading.current_thread().name == "B" and not b_has_read.is_set():
            b_has_read.set()
            a_waited.wait(timeout=5)
        return held

    def run(name, command):
        try:
            with update.update_lock(command, p) as info:
                got[name] = info
                if name == "B":
                    release_b.wait(timeout=5)
        except update.UpdateLocked as exc:
            got[name] = exc

    monkeypatch.setattr(update, "_lock_holder", holder)
    b = threading.Thread(target=run, args=("B", "fetch"), name="B", daemon=True)
    b.start()
    assert b_has_read.wait(timeout=5)
    a = threading.Thread(target=run, args=("A", "build"), name="A", daemon=True)  # reads the same stale lock
    a.start()
    a.join(timeout=0.5)
    assert a.is_alive()  # A waits while B is between reading the stale lock and taking it over
    a_waited.set()
    a.join(timeout=5)
    assert isinstance(got["A"], update.UpdateLocked) and "fetch started" in str(got["A"])  # sees B's fresh lock
    assert got["B"]["took_over"]["command"] == "build" and json.loads(p.read_text())["command"] == "fetch"
    release_b.set()
    b.join(timeout=5)
    assert not p.exists()


def test_a_second_concurrent_update_exits_3_and_does_nothing(tmp_path, monkeypatch):
    import os
    import subprocess
    import sys

    from typer.testing import CliRunner

    from snowagent.cli import app
    from snowagent.ops import update

    lock, log = tmp_path / "update.lock", tmp_path / "runs.jsonl"
    monkeypatch.setattr(update, "LOCK_FILE", lock)
    monkeypatch.setattr(update, "RUN_LOG", log)
    ran = []
    monkeypatch.setattr(update, "fetch", lambda: ran.append(1) or {"failed_steps": []})
    holder = subprocess.Popen(  # another process in the middle of an update
        [sys.executable, "-c", "import sys, time; from snowagent.ops.update import update_lock\n"
         f"with update_lock('build', {str(lock)!r}):\n    print('held', flush=True); time.sleep(60)"],
        stdout=subprocess.PIPE, text=True, env={**os.environ, "PYTHONPATH": str(ROOT / "src")})
    try:
        assert holder.stdout.readline().strip() == "held"
        r = CliRunner().invoke(app, ["update", "fetch"])
        assert r.exit_code == update.EXIT_LOCKED == 3 and not ran
        out = json.loads(r.stdout)
        assert out["locked"] is True and out["failed_steps"][0]["step"] == "lock"
        assert f"pid {holder.pid}" in r.stderr and "this run did nothing" in r.stderr
        (rec,) = [json.loads(x) for x in log.read_text().splitlines()]
        assert rec["exit_code"] == 3 and rec["failed_steps"][0]["step"] == "lock"
    finally:
        holder.kill()  # killed: no clean release, the lock file stays behind
        holder.wait()
        holder.stdout.close()
    assert json.loads(lock.read_text())["pid"] == holder.pid
    r = CliRunner().invoke(app, ["update", "fetch"])  # its process is gone: taken over at once
    assert r.exit_code == 0 and ran == [1] and not lock.exists()
    assert "no longer running" in json.loads(r.stdout)["warnings"][0]["message"]


def test_step_errors_never_carry_the_fts360_token(monkeypatch):
    from snowagent.ops import update

    monkeypatch.setenv("FTS360_TOKEN", "s3cr3t-token-value")
    f = update.failure("fts360", ValueError("bad header Authorization: Bearer s3cr3t-token-value"))
    assert "s3cr3t" not in f["error"] and f["error"] == "ValueError: bad header Authorization: Bearer ***"


def test_blind_test_without_the_lab_extra_is_silent_until_an_agent_is_frozen(tmp_path, monkeypatch):
    import builtins

    from snowagent.ops import blind_test as bt

    real = builtins.__import__

    def no_pyarrow(name, *a, **k):
        if name == "pyarrow":
            raise ImportError("no pyarrow")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", no_pyarrow)
    assert bt.run_blind_test(2026, tmp_path, files={})["warnings"] == []
    res = bt.run_blind_test(2026, tmp_path, files={"a.json": b"{}"})
    assert res["entries"] == 0 and "lab extra" in res["warnings"][0]["message"]
