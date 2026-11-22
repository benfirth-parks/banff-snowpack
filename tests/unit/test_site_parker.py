"""Parker Ridge on the public site (ADR-139): its recipe, data folders, forcing, daily fetch and deploy check."""

import subprocess
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

REPO = Path(__file__).resolve().parents[2]
COLS = ["ta", "rh", "vw", "dw", "iswr", "ilwr", "psum"]
T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731


def test_parker_ridge_is_a_site_plot_read_from_the_new_sites_file(monkeypatch):
    from snowagent.web import build

    monkeypatch.chdir(REPO)
    new = yaml.safe_load((REPO / "config" / "new_sites.yaml").read_text())
    assert set(new["site"]["plots"]) == set(build.NEW_SITE_PLOTS) <= set(build.SITES)
    assert build.SITES["parker_ridge"] == new["site"]["plots"]["parker_ridge"]
    # plot_forcing.yaml (the lab's Banff forcing) does not change: the plot lives in its own file
    assert "parker_ridge" not in yaml.safe_load((REPO / "config" / "plot_forcing.yaml").read_text())["plots"]
    assert build._cfg()["plots"]["parker_ridge"]["elevation_m"] == 2010  # every site plot, for _cfg()["plots"][x]
    cfg = build._plot_cfg("parker_ridge")
    dirs = build._dirs(cfg)
    assert dirs["stations"] == Path("data/interim/new_sites/stations") and dirs["era5"].name == "era5_north_west"
    assert dirs["gfs"] == Path("archive/forecasts/new_sites/gfs") and dirs["hrdps"].parts[-2:] == ("new_sites", "hrdps")
    assert build._dirs(build._plot_cfg("goats_eye")) == {"stations": Path("data/raw/fts360"),
                                                         "era5": Path("data/interim/era5"), "gfs": None, "hrdps": None}
    assert build.STATION_SEASONS["parker_ridge"].start == cfg["plots"]["parker_ridge"]["station_seasons_from"] == 2018
    from snowagent.ops.site_agents import PLOT_CODES

    assert PLOT_CODES["parker_ridge"] == "PARK"  # the lab's Jasper test set code: the agents' genes read it


def _fake_assemble(calls: list):
    from snowagent.baseline import assemble as asm

    def fake(plot, start, end, era5_only=None, reanalysis="era5", gfs_fallback_days=0, **kw):
        calls.append({"reanalysis": reanalysis, "era5_only": era5_only, **kw})
        idx = pd.date_range(pd.Timestamp(start), pd.Timestamp(end), freq="h")
        data = pd.DataFrame(1.0, index=idx, columns=COLS)
        if reanalysis == "era5":  # ERA5 not published for the last months: the GFS day-1 fill takes over
            data.loc[data.index >= "2026-10-01", ["vw", "dw", "iswr", "ilwr"]] = np.nan
        return asm.PlotForcing(plot, data, pd.DataFrame(reanalysis, index=idx, columns=COLS), [f"reanalysis: {reanalysis}"])
    return fake


def test_parker_forcing_reads_its_own_stations_era5_box_and_gfs_folder(monkeypatch):
    from snowagent.baseline import assemble as asm
    from snowagent.web import build

    monkeypatch.chdir(REPO)
    calls: list = []
    monkeypatch.setattr(asm, "assemble", _fake_assemble(calls))
    pf, start, end, mode = build.season_forcing("parker_ridge", 2026, T("2026-11-22T13:30"))
    assert mode == "live" and end == T("2026-11-22T13:00") and pf.data.notna().all().all()
    era5, gfs = calls
    for c in (era5, gfs):
        assert c["cfg_path"] == build.NEW_SITES and c["fts_raw"] == Path("data/interim/new_sites/stations")
        assert c["era5_dir"] == Path("data/interim/era5_north_west")
    assert gfs["reanalysis"] == "gfs_day1" and gfs["gfs_dir"] == Path("archive/forecasts/new_sites/gfs")
    calls.clear()
    pf, *_r, mode = build.season_forcing("parker_ridge", 2005, T("2026-11-22T13:30"))  # before Big Bend: ERA5 alone
    assert mode == "era5" and calls[0]["era5_only"] == {"method": "constant", "ta_offset_k": 0.0, "psum_ratio": 1.0}


def test_the_daily_fetch_gets_parker_inputs_into_their_own_folders(tmp_path, monkeypatch):
    from snowagent.newsites import jasper
    from snowagent.ops import update

    monkeypatch.chdir(REPO)
    got: dict = {}
    monkeypatch.setattr(jasper, "update_stations", lambda cfg, keys, start, end, raw, archive, dest: got.update(
        fts=(keys, start, dest)) or {"errors": {"big_bend": [], "tangle_ridge": ["HTTP 500"]}})
    monkeypatch.setattr(update, "fetch_gfs", lambda start, now, **k: got.update(gfs=k) or {"warnings": []})
    monkeypatch.setattr(update, "fetch_hrdps", lambda start, now, **k: got.update(hrdps=k) or {"warnings": []})
    monkeypatch.setattr(update, "fetch_era5", lambda y, now, d: got.setdefault("era5", []).append((y, d)) or {
        "warnings": []})
    monkeypatch.setattr(update, "era5_months_missing", lambda y, d=None: ["2026-06"])
    res = update.fetch_new_sites(T("2026-11-22T13:00"))
    assert got["fts"] == (["big_bend", "tangle_ridge"], "2026-10-01T00:00:00Z", Path("data/interim/new_sites/stations"))
    assert got["gfs"]["points"] == {"parker_ridge_plot": (52.19587, -117.10408)}
    assert got["gfs"]["archive"] == Path("archive/forecasts/new_sites/gfs") and got["gfs"]["sync_args"] == ("new_sites",)
    assert got["gfs"]["lookback_days"] >= 68  # the whole live season: the AWS archive keeps every GFS run
    assert got["hrdps"]["interim"] == update.NEW_SITE_HRDPS_INTERIM
    assert [y for y, _d in got["era5"]] == [2026, 2025] and got["era5"][0][1].name == "era5_north_west"
    assert res["failed_steps"] == [] and [w["source"] for w in res["warnings"]] == ["fts360:tangle_ridge"]


def test_deploy_accepts_a_site_without_parker_until_it_is_built(tmp_path):
    import json

    from snowagent.ops.deploy import check_deploy

    web = tmp_path / "web"
    (web / "data").mkdir(parents=True)
    for name in ("index.html", "app.js", "styles.css"):
        (web / name).write_text("x")
    seasons = [{"season": "2026-2027", "mode": "live", "file": f"data/{p}/2026-2027.json"}
               for p in ("goats_eye", "simpson", "bow_summit")]
    idx = {"sites": [{"id": s["file"].split("/")[1], "seasons": [s]} for s in seasons]}
    for s in seasons:
        (web / s["file"]).parent.mkdir(parents=True, exist_ok=True)
        (web / s["file"]).write_text("{}")
    (web / "data" / "sites.json").write_text(json.dumps(idx))
    p = check_deploy(web, None, now=T("2026-11-22T13:00"), lock=tmp_path / "lock", no_reference=True)["problems"]
    assert not any("parker_ridge" in x for x in p)


def test_the_archive_sync_keeps_new_site_extracts_apart(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "sync_forecast_archive.sh").write_bytes((REPO / "scripts" / "sync_forecast_archive.sh").read_bytes())
    src = tmp_path / "data/interim/forecasts/new_sites/gfs"
    src.mkdir(parents=True)
    (src / "gfs_2026112200.csv").write_text("run_utc,lead_h,point\n")
    (src / "gfs_2026112200.provenance.json").write_text("{}")
    subprocess.run(["bash", "scripts/sync_forecast_archive.sh", "gfs", "new_sites"], cwd=tmp_path, check=True,
                   capture_output=True)
    assert (tmp_path / "archive/forecasts/new_sites/gfs/gfs_2026112200.csv").exists()
    assert not (tmp_path / "archive/forecasts/gfs").exists()


def test_an_unpublished_north_west_month_is_not_yet_available_not_a_failure(tmp_path, monkeypatch):
    from snowagent.newsites import era5_box
    from snowagent.ops import update

    def unpublished(year, month, out_dir):
        raise FileNotFoundError("e5.oper.an.sfc.128_167_2t.ll025sc.2026110100_2026113023.nc")

    monkeypatch.setattr(era5_box, "extract_month", unpublished)
    assert update._new_site_era5_month(2026, 11, str(tmp_path)) is None  # classified in the worker process

    def broken(year, month, out_dir):
        raise FileNotFoundError("lost") from ConnectionError("reset")

    monkeypatch.setattr(era5_box, "extract_month", broken)
    try:
        update._new_site_era5_month(2026, 11, str(tmp_path))
    except FileNotFoundError:
        pass
    else:
        raise AssertionError("a failed download must still be reported")
    # fetch_era5 runs the worker in its own process: the monkeypatch reaches it through fork on Linux
    monkeypatch.setattr(era5_box, "extract_month", unpublished)
    res = update.fetch_era5(2026, T("2026-11-22T13:00"), era5_dir=tmp_path / "era5_north_west")
    assert res["errors"] == [] and res["not_yet_available"] == ["2026-09", "2026-10", "2026-11"]
    assert res["warnings"] == []
