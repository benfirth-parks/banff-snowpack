"""Season lifecycle (ADR-054): rollover at the configured season start, the build time passed in, the finished
season completed once its ERA5 months are cached, and the as-issued forecasts kept through that rebuild."""

import json
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from tests.unit.test_ops import ROOT, _build_env, _era5_month

T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731
COLS = ["ta", "rh", "vw", "dw", "iswr", "ilwr", "psum"]


def test_season_start_and_rollover_follow_the_configured_start(monkeypatch):
    from snowagent.web import build

    assert build.season_start(2026) == T("2026-09-15")  # config/plot_forcing.yaml
    for day, y in (("2026-06-30", 2025), ("2026-07-01", 2025), ("2026-09-14T23:59", 2025), ("2026-09-15", 2026),
                   ("2027-01-10", 2026), ("2027-06-30", 2026), ("2027-07-01", 2026)):
        assert build.current_season_year(T(day)) == y, day
    assert build.current_season_year(pd.Timestamp("2026-09-14T23:59")) == 2025  # naive: UTC

    monkeypatch.setattr(build, "_cfg", lambda: {"season_start": "10-01", "season_end": "06-30"})
    assert build.season_start(2026) == T("2026-10-01")
    assert build.current_season_year(T("2026-09-20")) == 2025 and build.current_season_year(T("2026-10-01")) == 2026
    assert build.season_start(2026, {"season_start": "09-15"}) == T("2026-09-15")  # a config passed in wins

    def no_config():
        raise FileNotFoundError("config/plot_forcing.yaml")

    monkeypatch.setattr(build, "_cfg", no_config)  # outside the repository: the default start
    assert build.season_start(2026) == T("2026-09-15") and build.current_season_year(T("2026-09-15")) == 2026


def test_issued_store_takes_the_build_time(tmp_path):
    from snowagent.web import build

    now = T("2026-10-03T06:20:00")
    rec = build._issued_store("simpson", "2026-2027", {"issue": "2026-10-03T00", "P": []}, "run-1", tmp_path, now)
    assert rec["produced_utc"] == "2026-10-03T06:20:00+00:00" and rec["computed_after_issue"] is False
    late = build._issued_store("simpson", "2026-2027", {"issue": "2026-10-01T00", "P": []}, "run-1", tmp_path, now)
    assert late["computed_after_issue"] is True  # a gap filled two days later is not an as-issued forecast
    assert build._issued_load("simpson", "2026-2027", T("2026-10-03"), tmp_path) == rec
    assert build._issued_exists("simpson", "2026-2027", tmp_path) and not build._issued_exists("simpson", "2025-2026",
                                                                                               tmp_path)


def test_forecast_issues_show_the_stored_forecasts_whenever_the_season_was_live_once(tmp_path):
    from snowagent.web import build

    states = {t: Path(f"{t:%Y%m%d%H}.sno") for t in pd.date_range("2025-10-25", "2025-11-03", freq="D", tz="UTC")}
    states[T("2025-10-28T12:00")] = Path("noon.sno")  # not an issue time
    days = [t for t in sorted(states) if t.hour == 0]

    issues, stored, as_issued = build.forecast_issues(states, "station", "simpson", "2025-2026", tmp_path)
    assert (issues, stored, as_issued) == ([t for t in days if t.month == 11], {}, False)  # archived GFS months only
    issues, stored, as_issued = build.forecast_issues(states, "live", "simpson", "2025-2026", tmp_path)
    assert issues == days and as_issued and set(stored) == set(days) and not any(stored.values())

    kept = build._issued_store("simpson", "2025-2026", {"issue": "2025-10-27T00", "P": [], "forcing_hash": "a"},
                               "run-live", tmp_path, T("2025-10-27T06:00"))
    issues, stored, as_issued = build.forecast_issues(states, "station", "simpson", "2025-2026", tmp_path)
    assert as_issued and issues == days  # the season left live mode: every day, the stored forecast shown first
    assert stored[T("2025-10-27")] == kept and kept["initial_state_run_id"] == "run-live"
    assert [t for t in issues if stored.get(t) is None] == [t for t in days if t != T("2025-10-27")]  # computed
    assert build.forecast_issues(states, "station", "simpson", "2024-2025", tmp_path)[2] is False  # other season


def test_fetch_era5_requests_a_finished_season_only_to_june(tmp_path, monkeypatch):
    from snowagent.ingest import era5
    from snowagent.ops import update

    asked = []

    def extract(y, m, out_dir):
        asked.append(f"{y}-{m:02d}")
        _era5_month(out_dir / f"era5_box_{y}{m:02d}.npz")

    monkeypatch.setattr(update, "ERA5_DIR", tmp_path)
    monkeypatch.setattr(era5, "extract_month", extract)
    months = update.era5_season_months(2025)
    assert [f"{m:%Y-%m}" for m in months] == ["2025-09", "2025-10", "2025-11", "2025-12", "2026-01", "2026-02",
                                             "2026-03", "2026-04", "2026-05", "2026-06"]
    assert update.era5_months_missing(2025, tmp_path) == [f"{m:%Y-%m}" for m in months]
    res = update.fetch_era5(2025, T("2026-10-04T13:00"))  # the previous season, after the rollover
    assert asked == res["added"] == [f"{m:%Y-%m}" for m in months]  # September to June, nothing of the summer
    assert update.era5_months_missing(2025, tmp_path) == [] and res["warnings"] == []
    asked.clear()
    assert update.fetch_era5(2026, T("2026-10-04T13:00"))["added"] == asked == ["2026-09", "2026-10"]  # to now


def _season_file(web: Path, plot: str, season: str, mode: str) -> Path:
    f = web / plot / f"{season}.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps({"site": plot, "season": season, "mode": mode, "pits": [], "nowcast": []}))
    return f


def _era5_cache(d: Path, months) -> None:
    d.mkdir(parents=True, exist_ok=True)
    for m in months:
        _era5_month(d / f"era5_box_{m:%Y%m}.npz")


def test_finished_season_check_waits_for_the_era5_months(tmp_path):
    from snowagent.ops import update

    web, era = tmp_path / "web", tmp_path / "era5"
    _season_file(web, "goats_eye", "2025-2026", "live")
    _season_file(web, "simpson", "2025-2026", "station")
    _era5_cache(era, update.era5_season_months(2025)[:-1])  # June 2026 not on the mirror yet
    assert update.finished_season_check("goats_eye", 2025, web, era) == {
        "site": "goats_eye", "season": "2025-2026", "era5_missing": ["2026-06"]}
    assert update.finished_season_check("simpson", 2025, web, era) is None  # not live
    assert update.finished_season_check("bow_summit", 2025, web, era) is None  # no file
    assert update.finished_season_check("goats_eye", 2024, web, era) is None
    _era5_month(era / "era5_box_202606.npz")
    assert update.finished_season_check("goats_eye", 2025, web, era)["era5_missing"] == []


def test_build_completes_the_previous_season_once_its_era5_months_are_cached(tmp_path, monkeypatch):
    from snowagent.ops import update

    calls: list = []
    rebuilt_mode = {"mode": "station"}
    cut = {"level": "warning", "source": "forcing:goats_eye", "message": "Goat's Eye: weather stops at ...",
           "last_record_utc": None, "age_h": None}

    def season(plot, y, out, work, workers=1, now=None):
        calls.append((plot, y, now))
        if y == 2025:
            return {"site": plot, "season": "2025-2026", "mode": rebuilt_mode["mode"], "nowcast_profiles": 1150,
                    "forecast_issues": 181, "forecast_errors": 0, "pits": 9,
                    **({"warnings": [cut]} if rebuilt_mode["mode"] == "live" else {})}
        return {"site": plot}

    _build_env(tmp_path, monkeypatch, build_season=season)
    web, era, now = tmp_path / "web", tmp_path / "era5", T("2026-10-03T13:00")
    monkeypatch.setattr(update, "ERA5_DIR", era)
    _season_file(web, "goats_eye", "2025-2026", "live")
    _season_file(web, "bow_summit", "2025-2026", "live")
    _season_file(web, "simpson", "2025-2026", "station")  # completed already
    _era5_cache(era, update.era5_season_months(2025)[:-1])

    res = update.build(now, out_dir=web, work=tmp_path / "work")  # June 2026 missing: the live builds stay
    assert res["ok"] and [c[:2] for c in calls] == [(p, 2026) for p in ("goats_eye", "simpson", "bow_summit")]
    assert all(c[2] == now for c in calls)
    assert res["finished_seasons"] == [
        {"site": p, "season": "2025-2026", "rebuilt": False,
         "reason": "still the live build: ERA5 2026-06 not cached yet (published ~3 months after the month's end); "
                   "completed from the full forcing once it is"} for p in ("goats_eye", "bow_summit")]
    st = json.loads((web / "status.json").read_text())
    (w,) = [w for w in st["warnings"] if w["source"] == "season_final"]
    assert w["level"] == "info" and w["rebuilt"] is False and w["season"] == "2025-2026"
    assert w["message"].startswith("Season 2025-2026 (Goat's Eye, Bow Summit): still the live build: ERA5 2026-06")

    _era5_month(era / "era5_box_202606.npz")  # arrived: both finished seasons are completed, Simpson is left alone
    calls.clear()
    res = update.build(now, out_dir=web, work=tmp_path / "work")
    assert res["ok"] and [c[:2] for c in calls if c[1] == 2025] == [("goats_eye", 2025), ("bow_summit", 2025)]
    assert [(f["site"], f["rebuilt"], f["mode"], f["forecast_issues"]) for f in res["finished_seasons"]] == [
        ("goats_eye", True, "station", 181), ("bow_summit", True, "station", 181)]
    assert "no longer live, forecasts as stored when issued" in res["finished_seasons"][0]["reason"]
    (w,) = [w for w in json.loads((web / "status.json").read_text())["warnings"] if w["source"] == "season_final"]
    assert w["rebuilt"] is True and "completed from the full forcing" in w["message"]
    assert update.run_counts("build", res)["seasons_built"] == 3  # the live season's count is unchanged

    rebuilt_mode["mode"] = "live"  # the full forcing still has a hole: rebuilt, still live, its cut is a warning
    res = update.build(now, out_dir=web, work=tmp_path / "work")
    fs = res["finished_seasons"][0]
    assert fs["rebuilt"] is False and "still incomplete (mode live)" in fs["reason"] and fs["warnings"] == [cut]
    assert cut in res["warnings"]

    def broken(plot, y, out, work, workers=1, now=None):
        if y == 2025:
            raise RuntimeError("engine crashed")
        return {"site": plot}

    monkeypatch.setattr("snowagent.web.build.build_season", broken)  # a failed rebuild is a failed step
    res = update.build(now, out_dir=web, work=tmp_path / "work")
    assert res["ok"] is False and [f["step"] for f in res["failed_steps"]] == ["season_final:goats_eye",
                                                                              "season_final:bow_summit"]
    assert res["finished_seasons"][0] == {"site": "goats_eye", "season": "2025-2026", "rebuilt": False,
                                          "error": "RuntimeError: engine crashed",
                                          "reason": "the rebuild failed; the site keeps the live build"}
    st = json.loads((web / "status.json").read_text())
    assert "finished season not completed" in st["warnings"][0]["message"]
    assert not [w for w in st["warnings"] if w["source"] == "season_final"]  # the error entry says it


def test_write_index_lists_the_season_files_with_mode_and_live_block(tmp_path, monkeypatch):
    from snowagent.web import build

    monkeypatch.setattr(build, "_cfg", lambda: yaml.safe_load((ROOT / "config" / "plot_forcing.yaml").read_text()))
    d = tmp_path / "goats_eye"
    d.mkdir()
    (d / "2025-2026.json").write_text(json.dumps({"season": "2025-2026", "mode": "station", "pits": [1, 2]}))
    (d / "2025-2026_public.json").write_text("{}")
    live = {"generated_utc": "2026-10-03T13:00:00+00:00", "weather_through": "2026-10-03T12:00:00+00:00"}
    (d / "2026-2027.json").write_text(json.dumps({"season": "2026-2027", "mode": "live", "pits": [], "live": live}))
    (d / "2026-2027_forecasts.json").write_text("{}")
    idx = build.write_index(tmp_path, T("2026-10-03T13:05"))
    assert json.loads((tmp_path / "sites.json").read_text()) == idx
    assert idx["generated_utc"] == "2026-10-03T13:05:00+00:00" and [s["id"] for s in idx["sites"]] == list(build.SITES)
    ge = idx["sites"][0]
    assert ge["name"] == build.SITES["goats_eye"] and ge["elevation_m"] == 2280
    assert ge["seasons"] == [
        {"season": "2025-2026", "mode": "station", "file": "data/goats_eye/2025-2026.json", "forecasts": None,
         "public": "data/goats_eye/2025-2026_public.json", "pits": 2, "live": None},
        {"season": "2026-2027", "mode": "live", "file": "data/goats_eye/2026-2027.json",
         "forecasts": "data/goats_eye/2026-2027_forecasts.json", "public": None, "pits": 0, "live": live}]
    assert idx["sites"][1]["seasons"] == []  # no files for the other plots


def _fake_assemble(era5_gap_from: str | None, gfs_value: float = 2.0):
    """assemble() on a complete hourly table: ERA5 lacks the unmeasured variables from ``era5_gap_from`` on, the
    GFS day-1 composite has them (value ``gfs_value``)."""
    from snowagent.baseline import assemble as asm

    def fake(plot, start, end, era5_only=None, reanalysis="era5", gfs_fallback_days=0):
        idx = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="h")
        data = pd.DataFrame(1.0, index=idx, columns=COLS)
        if reanalysis == "era5" and era5_gap_from:
            data.loc[data.index >= era5_gap_from, ["vw", "dw", "iswr", "ilwr"]] = np.nan
        if reanalysis != "era5":
            data.loc[:, ["vw", "dw", "iswr", "ilwr"]] = gfs_value
        return asm.PlotForcing(plot, data, pd.DataFrame(reanalysis, index=idx, columns=COLS),
                               [f"reanalysis: {reanalysis}", "gfs_day1: 00 UTC runs" if reanalysis != "era5" else
                                "era5 nearest cell"])
    return fake


def test_season_forcing_completes_on_full_era5_and_splices_the_gfs_fill(monkeypatch):
    from snowagent.baseline import assemble as asm
    from snowagent.web import build

    monkeypatch.setattr(build, "_cfg", lambda: {"season_start": "09-15", "season_end": "06-30",
                                                "plots": {"goats_eye": {}}})
    monkeypatch.setattr(asm, "assemble", _fake_assemble(None))
    warnings: list = []
    pf, start, end, mode = build.season_forcing("goats_eye", 2025, T("2026-10-04T12:30"), warnings)
    assert (start, end, mode) == (T("2025-09-15"), T("2026-06-30"), "station")  # a finished season: whole
    assert pf.data.index[-1] == end and warnings == [] and not any(n.startswith(("live:", "cut:")) for n in pf.notes)
    assert build.season_forcing("goats_eye", 2026, T("2026-10-04T12:30"))[2] == T("2026-10-04T12:00")  # in season

    monkeypatch.setattr(asm, "assemble", _fake_assemble("2026-06-01T00:00Z"))  # June not published: GFS tail
    pf, start, end, mode = build.season_forcing("goats_eye", 2025, T("2026-10-04T12:30"), warnings)
    assert (end, mode) == (T("2026-06-30"), "live") and warnings == [] and pf.data.notna().all().all()
    assert pf.data.loc["2026-05-31T23:00Z", "vw"] == 1.0 and pf.data.loc["2026-06-01T00:00Z", "vw"] == 2.0
    assert pf.sources.loc["2026-05-31T23:00Z", "vw"] == "era5" and pf.sources.loc["2026-06-01T00:00Z", "vw"] == "gfs_day1"
    assert pf.sources.loc["2026-06-01T00:00Z", "ta"] == "gfs_day1"  # tail rows replaced whole (same station values)
    (note,) = [n for n in pf.notes if n.startswith("live:")]
    assert note.startswith("live: from 2026-06-01 00:00 UTC the fill is the GFS day-1 composite") and "gfs_day1:" in note


def test_public_reports_change_season_at_the_configured_start_reading_the_config_once(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from snowagent.ingest import min as min_
    from snowagent.web import build

    reads = []
    monkeypatch.setattr(build, "_cfg", lambda: reads.append(1) or {"season_start": "09-15"})
    reports = [SimpleNamespace(distance_km={"goats_eye": 3.0}, obs_time_utc=T(t))
               for t in ("2026-07-02T18:00", "2026-09-14T23:00", "2026-09-15T00:00", "2026-12-01T20:00")]
    monkeypatch.setattr(min_, "load_reports", lambda archive_dir: reports)
    monkeypatch.setattr(build, "_pub_compact", lambda r, km: f"{r.obs_time_utc:%Y-%m-%dT%H}")
    assert build.write_public(tmp_path, tmp_path / "min") == {"goats_eye": 4, "simpson": 0, "bow_summit": 0}
    rows = {f.name: json.loads(f.read_text())["reports"] for f in (tmp_path / "goats_eye").glob("*_public.json")}
    assert rows == {"2025-2026_public.json": ["2026-07-02T18", "2026-09-14T23"],
                    "2026-2027_public.json": ["2026-09-15T00", "2026-12-01T20"]}
    assert len(reads) == 1  # not once per report (the daily build has ~2100 of them)


def test_season_forcing_mode_does_not_depend_on_when_the_module_was_imported(monkeypatch):
    from snowagent.baseline import assemble as asm
    from snowagent.web import build

    monkeypatch.setattr(build, "_cfg", lambda: {"season_start": "09-15", "season_end": "06-30",
                                                "plots": {"goats_eye": {}, "bow_summit": {}}})
    monkeypatch.setattr(asm, "assemble", _fake_assemble(None))
    later = build._Y + 1  # a season after the one in progress at import (a build with a later ``now``)
    assert later not in build.STATION_SEASONS["goats_eye"]
    assert build.season_forcing("goats_eye", later, T(f"{later + 1}-01-10"))[3] == "station"
    assert build.season_forcing("bow_summit", 2016, T("2017-01-10"))[3] == "station"
