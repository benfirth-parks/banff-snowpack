"""HRDPS point archive and its daily fetch (ADR-115)."""

from datetime import datetime

import pandas as pd

from snowagent.ops import hrdps, update


def _rows(run="2026-11-01T00:00:00+00:00", points=("a", "b"), max_lead=48, temp=270.0):
    return [{"run_utc": run, "lead_h": lead, "point": p, "tmp2m_k": temp, "apcp_kgm2": 0.1 * lead,
             "model_elev_m": 2000.0 if lead == 0 else None} for lead in range(max_lead + 1) for p in points]


def test_file_url_follows_the_datamart_layout():
    u = hrdps.file_url("https://hpfx.collab.science.gc.ca", datetime(2026, 11, 17), 7, "TMP_AGL-2m")
    assert u == ("https://hpfx.collab.science.gc.ca/20261117/WXO-DD/model_hrdps/continental/2.5km/00/007/"
                 "20261117T00Z_MSC_HRDPS_TMP_AGL-2m_RLatLon0.0225_PT007H.grib2")


def test_run_complete_needs_every_point_every_lead_and_values(tmp_path):
    run = datetime(2026, 11, 1)
    f = hrdps.write_run(_rows(), [{"url": "u", "sha256": "s"}], tmp_path, run)
    assert f.name == "hrdps_2026110100.csv" and (tmp_path / "hrdps_2026110100.provenance.json").exists()
    assert hrdps.run_complete(f, ["a", "b"])
    assert not hrdps.run_complete(f, ["a", "b", "c"])  # a point missing
    hrdps.write_run(_rows(max_lead=30), [], tmp_path, run)
    assert not hrdps.run_complete(f, ["a"])  # short run (still being published)
    rows = _rows()
    rows[5]["tmp2m_k"] = None
    hrdps.write_run(rows, [], tmp_path, run)
    assert not hrdps.run_complete(f, ["a"])  # a file was missing
    assert not hrdps.run_complete(tmp_path / "nope.csv", ["a"])


def test_fetch_retries_inside_the_datamart_window_and_reports_older_gaps(tmp_path, monkeypatch):
    monkeypatch.setattr(update, "HRDPS_ARCHIVE", tmp_path / "archive")
    monkeypatch.setattr(update, "HRDPS_INTERIM", tmp_path / "interim")
    monkeypatch.setattr(update, "_gfs_points", lambda: {"a": (51.0, -116.0)})
    for d in pd.date_range("2026-09-14", "2026-10-31", freq="D"):  # archived except the gaps below
        if d.strftime("%m-%d") not in ("09-15", "10-25", "10-30"):
            hrdps.write_run(_rows(run=d.isoformat(), points=("a",)), [], tmp_path / "archive", d.to_pydatetime())
    extracted, synced = [], []

    def extract(run, pts):
        extracted.append(run)
        short = run.day == 30  # still being published
        return _rows(run=run.isoformat(), points=("a",), max_lead=20 if short else 48), []

    monkeypatch.setattr(hrdps, "extract_run", extract)
    monkeypatch.setattr(update.subprocess, "run", lambda *a, **k: synced.append(a))
    res = update.fetch_hrdps(pd.Timestamp("2026-09-15", tz="UTC"), pd.Timestamp("2026-11-01T13:00", tz="UTC"))
    assert [f"{r:%m-%d}" for r in extracted] == ["10-25", "10-30", "11-01"] and len(synced) == 1
    assert res["runs_added"] == ["2026-10-25", "2026-11-01"] and res["failed"] == ["2026-10-30"]
    assert res["permanently_missing"] == ["2026-09-15"]
    assert [w["level"] for w in res["warnings"]] == ["info", "warning"]
    assert "no longer on the MSC Datamart" in res["warnings"][1]["message"]
