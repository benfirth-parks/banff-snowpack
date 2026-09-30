"""Input contracts: explicit UTC times and units; ambiguity is rejected, never guessed."""

from __future__ import annotations

import json
from datetime import datetime

import pandas as pd
import pytest

from snowagent.contracts import Provenance, WeatherKind, WeatherMeta
from snowagent.errors import DataLeakage, InvalidInput, InvalidTime, InvalidUnits
from snowagent.weather.io import latest_forecast, load_weather

PROV = Provenance(source="test", synthetic=True)


def _meta(kind=WeatherKind.actuals, **kw) -> dict:
    base = dict(series_id="s1", kind=kind, source="test", lat=51.2, lon=-115.7, source_elevation_m=1500,
                timestamp_convention="end_of_interval", accumulation_interval_s=3600, wind_height_m=10,
                met_height_m=2, provenance=PROV.model_dump())
    if kind == WeatherKind.forecast:
        base.update(issue_time="2026-01-15T00:00:00Z", available_time="2026-01-15T04:00:00Z")
    else:
        base.update(availability_latency_s=3600)
    base.update(kw)
    return base


def _write(tmp_path, rows: list[dict], meta: dict | None = None, name="w.csv"):
    p = tmp_path / name
    pd.DataFrame(rows).to_csv(p, index=False)
    (tmp_path / (name + ".meta.json")).write_text(json.dumps(meta or _meta(), default=str))
    return p


def _rows(n=4, start="2026-01-01T01:00:00Z", **override):
    t0 = pd.Timestamp(start)
    out = []
    for i in range(n):
        r = {"valid_time_utc": (t0 + pd.Timedelta(hours=i)).strftime("%Y-%m-%dT%H:%M:%SZ"), "ta_c": -5.0,
             "rh_pct": 80.0, "vw_ms": 3.0, "dw_deg": 250, "iswr_wm2": 0.0, "ilwr_wm2": 250.0, "psum_mm": 0.5}
        r.update(override)
        out.append(r)
    return out


def test_naive_datetime_rejected_in_contract():
    with pytest.raises(ValueError, match="naive"):
        WeatherMeta.model_validate(_meta(WeatherKind.forecast, issue_time=datetime(2026, 1, 1)))


def test_forecast_requires_issue_and_available_time():
    m = _meta(WeatherKind.forecast)
    m.pop("available_time")
    with pytest.raises(ValueError, match="available_time"):
        WeatherMeta.model_validate(m)


def test_valid_series_converts_to_si(tmp_path):
    s = load_weather(_write(tmp_path, _rows()))
    assert s.data["ta"].iloc[0] == pytest.approx(268.15)
    assert s.data["rh"].iloc[0] == pytest.approx(0.8)
    assert s.data.index.tz is not None


@pytest.mark.parametrize("col", ["ta", "ta_f", "temp_c"])
def test_unknown_or_missing_unit_suffix_rejected(tmp_path, col):
    rows = _rows()
    for r in rows:
        r[col] = r.pop("ta_c")
    with pytest.raises((InvalidUnits, InvalidInput)):
        load_weather(_write(tmp_path, rows))


def test_duplicate_units_for_one_variable_rejected(tmp_path):
    rows = _rows()
    for r in rows:
        r["ta_k"] = r["ta_c"] + 273.15
    with pytest.raises(InvalidUnits, match="more than one unit"):
        load_weather(_write(tmp_path, rows))


def test_naive_timestamps_rejected(tmp_path):
    rows = _rows()
    for r in rows:
        r["valid_time_utc"] = r["valid_time_utc"].rstrip("Z")
    with pytest.raises(InvalidTime, match="explicit UTC"):
        load_weather(_write(tmp_path, rows))


def test_irregular_step_rejected(tmp_path):
    rows = _rows(5)
    del rows[2]
    with pytest.raises(InvalidTime):
        load_weather(_write(tmp_path, rows))


def test_implausible_values_rejected_as_unit_error(tmp_path):
    # Kelvin values written into a Celsius column
    with pytest.raises(InvalidInput, match="physical range"):
        load_weather(_write(tmp_path, _rows(ta_c=268.0)))


def test_missing_sidecar_rejected(tmp_path):
    p = tmp_path / "x.csv"
    pd.DataFrame(_rows()).to_csv(p, index=False)
    with pytest.raises(InvalidInput, match="sidecar"):
        load_weather(p)


def test_long_gap_rejected_short_gap_flagged(tmp_path):
    rows = _rows(10)
    rows[3]["ta_c"] = None
    s = load_weather(_write(tmp_path, rows))
    assert (s.qc["ta"] == "filled").sum() == 1
    for i in range(2, 8):
        rows[i]["ta_c"] = None
    with pytest.raises(InvalidInput, match="gap"):
        load_weather(_write(tmp_path, rows, name="g.csv"))


def test_actuals_availability_truncates_future_records(tmp_path):
    s = load_weather(_write(tmp_path, _rows(10)))
    sub = s.available_by(pd.Timestamp("2026-01-01T05:00:00Z"))
    # latency 1 h: records valid up to 04:00 are available at 05:00
    assert sub.data.index[-1] == pd.Timestamp("2026-01-01T04:00:00Z")


def test_forecast_not_available_at_issue_time_is_leakage(tmp_path):
    s = load_weather(_write(tmp_path, _rows(4, start="2026-01-15T01:00:00Z"), _meta(WeatherKind.forecast)))
    with pytest.raises(DataLeakage):
        s.available_by(pd.Timestamp("2026-01-15T03:00:00Z"))
    assert s.available_by(pd.Timestamp("2026-01-15T04:00:00Z")) is s


def test_latest_forecast_ignores_runs_available_after_issue(tmp_path):
    early = _write(tmp_path, _rows(4, start="2026-01-15T01:00:00Z"), _meta(WeatherKind.forecast, series_id="a"),
                   name="a.csv")
    late = _write(tmp_path, _rows(4, start="2026-01-15T13:00:00Z"),
                  _meta(WeatherKind.forecast, series_id="b", issue_time="2026-01-15T12:00:00Z",
                        available_time="2026-01-15T16:00:00Z"), name="b.csv")
    assert latest_forecast([early, late], pd.Timestamp("2026-01-15T06:00:00Z")).meta.series_id == "a"
    assert latest_forecast([early, late], pd.Timestamp("2026-01-15T17:00:00Z")).meta.series_id == "b"
