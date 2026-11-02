"""New-site plumbing (ADR-108): Jasper logger conversion, sign-lost temperatures, the Avalanche Canada weather API,
the new-sites plot file, the separate ERA5 box, and the lab's code hash staying unchanged."""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from snowagent.ingest import era5
from snowagent.ingest.fts360 import load_station, parse_frame
from snowagent.newsites import avcan_weather, era5_box, jasper

REPO = Path(__file__).resolve().parents[2]


def _jasper_raw(temps, start="2022-01-01T00:00:00Z", minute=0):
    t = pd.date_range(start, periods=len(temps), freq="h", tz="UTC") + pd.Timedelta(minutes=minute)
    return pd.DataFrame({"Date": t.strftime("%Y-%m-%dT%H:%M:%SZ"), "ATCAvg": [str(v) for v in temps],
                         "RHAvg": "80", "AvgWS": "7.2", "PeakWS": "18", "AvgWD": "250", "SD": "120",
                         "Precip": [f"{100 + 0.5 * i:.2f}" for i in range(len(temps))], "TS0": "-1"})


def _parsed(raw):
    out, note = jasper.to_banff_frame(raw)
    return parse_frame(out).set_index("time_utc"), note


def test_jasper_logger_columns_parse_to_si():
    d, _ = _parsed(_jasper_raw([-5.3, -6.1, -7.4, -8.2] * 24))
    assert abs(d["ta_k"].iloc[0] - (273.15 - 5.3)) < 1e-9
    assert d["rh_frac"].iloc[0] == 0.8
    assert abs(d["vw_ms"].iloc[0] - 2.0) < 1e-9  # 7.2 km/h
    assert abs(d["vw_max_ms"].iloc[0] - 5.0) < 1e-9
    assert d["dw_deg"].iloc[0] == 250
    assert d["hs_m"].iloc[0] == 1.2
    assert d["psum_1h_mm"].iloc[1] == 0.5  # cumulative "Precip" -> hourly increments
    assert (d["ta_k_qc"] == "ok").all()


def test_sign_lost_spell_is_left_out_and_normal_days_kept():
    rng = np.random.default_rng(1)
    normal = list(np.round(rng.normal(-8, 3, 24 * 5), 1))  # decimals, mostly negative
    lost = [abs(int(v)) for v in rng.normal(-8, 3, 24 * 5)]  # whole numbers, sign dropped
    d, note = _parsed(_jasper_raw(normal + lost))
    assert d["ta_k"].iloc[: 24 * 4].notna().all()
    assert d["ta_k"].iloc[24 * 5 + 1:].isna().all()
    assert note["temperature_sign_lost_hours"] >= 24 * 5 - 1
    assert d["hs_m"].notna().all()  # other variables cannot lose a sign and are kept


def test_sign_lost_ignores_summer_records_with_decimals():
    warm = pd.Series(np.round(np.linspace(5.1, 20.3, 24 * 4), 1),
                     index=pd.date_range("2022-07-01", periods=24 * 4, freq="h", tz="UTC"))
    assert not jasper.sign_lost(warm).any()


def test_readings_at_minute_58_are_taken_as_the_hour():
    d, note = _parsed(_jasper_raw([-3.5] * 30, start="2018-11-20T17:00:00Z", minute=58))
    assert note["snapped_from_minute_55_59"] == 30
    assert d.index[0] == pd.Timestamp("2018-11-20T18:00:00Z")
    assert d["ta_k"].notna().all()


def test_avcan_rows_convert_and_load_like_fts(tmp_path):
    rows = [{"stationId": 93, "measurementDateTime": f"2024-01-10T{h:02d}:00:00Z", "hourlyPrecip": 0.5,
             "snowHeight": 150 + h, "airTempAvg": -8.5, "relativeHumidity": -8} for h in range(5)]
    raw = tmp_path / "raw" / "fidelity"
    raw.mkdir(parents=True)
    (raw / "fidelity_2024-01.json").write_text(json.dumps(rows))
    summary = avcan_weather.convert(tmp_path / "raw", tmp_path / "stations")
    assert summary["fidelity"]["rh_left_out"] == 5  # humidity repeating the temperature is not a humidity
    d = load_station("fidelity", fts_raw=tmp_path / "stations", byk_interim=tmp_path / "none",
                     dashboard_interim=tmp_path / "none")
    assert len(d) == 5
    assert abs(d["ta_k"].iloc[0] - 264.65) < 1e-9
    assert d["hs_m"].iloc[2] == 1.52
    assert d["psum_1h_mm"].iloc[0] == 0.5 and d["psum_1h_mm_qc"].iloc[0] == "ok"
    assert "rh_frac" not in d or d["rh_frac"].isna().all()


class _Reply:
    status_code, ok = 200, True

    def __init__(self, rows):
        self.content = json.dumps(rows).encode()

    def json(self):
        return json.loads(self.content)


def test_avcan_fetch_never_shortens_a_month(tmp_path):
    month = pd.Timestamp.now(tz="UTC").normalize().replace(day=1)  # an open month is fetched again
    dest = tmp_path / "fidelity" / f"fidelity_{month:%Y-%m}.json"
    dest.parent.mkdir(parents=True)
    dest.write_text(json.dumps([{"measurementDateTime": "2024-01-01T00:00:00Z"}] * 3))
    recs = avcan_weather.fetch_station("fidelity", 93, str(month), str(month + pd.Timedelta(hours=5)), tmp_path,
                                       get=lambda *a, **k: _Reply([]), sleep=lambda s: None)
    assert "warning" in recs[0]
    assert len(json.loads(dest.read_text())) == 3


def test_avcan_archive_round_trip(tmp_path):
    raw, arc = tmp_path / "raw", tmp_path / "archive"
    (raw / "fidelity").mkdir(parents=True)
    (raw / "fidelity" / "fidelity_2024-01.json").write_text(json.dumps([{"a": 1}]))
    assert avcan_weather.sync_archive(raw, arc) == {"restored": 0, "archived": 1}
    fresh = tmp_path / "fresh"
    assert avcan_weather.sync_archive(fresh, arc) == {"restored": 1, "archived": 0}
    assert json.loads((fresh / "fidelity" / "fidelity_2024-01.json").read_text()) == [{"a": 1}]


def test_new_sites_config_is_separate_and_complete():
    new = yaml.safe_load((REPO / "config/new_sites.yaml").read_text())
    banff = yaml.safe_load((REPO / "config/plot_forcing.yaml").read_text())
    assert set(new["plots"]) == {"parker_ridge", "fidelity"}
    assert not set(new["plots"]) & set(banff["plots"])  # the lab and the site never see them
    assert new["era5_dir"] == era5_box.ERA5_DIR
    lo_lat, hi_lat, lo_lon, hi_lon = era5_box.NORTH_WEST
    for p in new["plots"].values():
        assert lo_lat < p["lat"] < hi_lat and lo_lon < p["lon"] < hi_lon
        for var in ("ta", "rh", "psum"):
            for st in p[var]:
                assert st in new["station_elevation_m"]
    sources = yaml.safe_load((REPO / "config/external_sources.yaml").read_text())
    assert "big_bend" not in sources["fts360"]["stations"]  # the daily update's list is unchanged
    assert {"big_bend", "tangle_ridge"} <= set(sources["fts360_new_sites"]["stations"])
    assert "fidelity" in sources["avcan_weather"]["stations"]


def test_new_sites_box_swap_leaves_banff_box_alone():
    assert era5.BOX == (50.5, 52.0, -117.0, -115.25)

    class H(dict):
        pass

    h = H(latitude=np.arange(53.0, 50.0, -0.25), longitude=np.arange(240.0, 246.0, 0.25))
    orig = era5._box_index
    try:
        era5_box._use_box(era5_box.NORTH_WEST)
        _, _, la, lo = era5._box_index(h)
        assert la.min() >= 50.9 and la.max() <= 52.5 and lo.min() >= -118.1 and lo.max() <= -116.6
        _, _, la0, lo0 = orig(h)
        assert la0.min() >= 50.5 and lo0.min() >= -117.0  # the module's own lookup is unchanged
    finally:
        era5._box_index = orig


def test_newsites_is_outside_the_lab_code_hash():
    """Building the new sites must not invalidate the Banff training cache: `newsites/` is excluded from the hash,
    and no module inside the hash imports it (if one ever does, it must come back into the hash)."""
    from snowagent.lab.training import cache as cache_mod

    assert "newsites/" in cache_mod.CODE_EXCLUDE
    imports = re.compile(r"^\s*(from|import)\s+(snowagent\.newsites\b|\.+newsites\b)", re.M)
    hashed = [f for f in sorted(cache_mod.SRC.rglob("*.py"))
              if not (r := f.relative_to(cache_mod.SRC).as_posix()).startswith(cache_mod.CODE_EXCLUDE)
              and r not in cache_mod.CODE_EXCLUDE and "__pycache__" not in r]
    assert hashed and not [f for f in hashed if imports.search(f.read_text())]
