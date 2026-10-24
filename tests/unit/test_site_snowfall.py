"""The site's SNOWPACK with snowfall from the snow depth sensor (ADR-102): the driven-then-precipitation run, the
daily states and depth joined over consecutive runs, and the profiles kept at the switch."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from snowagent.engine import column
from snowagent.engine import snowpack as sp
from snowagent.learn import steer
from snowagent.web import build

T = lambda s: pd.Timestamp(s, tz="UTC")  # noqa: E731


def _fake_engine(calls: list):
    def fake(eng, settings, run_dir, unit, forcing, end, *, initial_sno=None, snowfree_start=None):
        calls.append({"settings": settings, "hs": "HS" in forcing, "first": forcing.index[0],
                      "last": forcing.index[-1], "end": end, "init": initial_sno, "snowfree": snowfree_start,
                      "dir": run_dir})
        return type("O", (), {"sno": str(Path(run_dir) / "state.sno"), "run_dir": run_dir})()
    return fake


def test_run_span_drives_to_the_switch_then_runs_on_precipitation(tmp_path, monkeypatch):
    calls: list = []
    monkeypatch.setattr(column, "prepare_and_run", _fake_engine(calls))
    idx = pd.date_range("2026-11-01", "2026-11-05", freq="h", tz="UTC")
    smet = pd.DataFrame({"TA": 270.0}, index=idx)
    hs = pd.Series(0.4, index=idx)
    plain, driven = sp.EngineSettings(), sp.EngineSettings(calculation_step_min=7.5)
    t0, t1, sw = T("2026-11-01 06:00"), T("2026-11-05"), T("2026-11-03")

    segs = column.run_span(None, plain, tmp_path, None, smet, t0, t1)  # no sensor: one precipitation run
    assert [(a, b) for a, b, _o in segs] == [(t0, t1)] and calls[0]["snowfree"] == t0.to_pydatetime()
    assert calls[0]["settings"] is plain and not calls[0]["hs"] and calls[0]["first"] == T("2026-11-01")

    calls.clear()
    segs = column.run_span(None, plain, tmp_path, None, smet, t0, t1, hs=hs, switch=sw, driven_settings=driven)
    assert [(a, b) for a, b, _o in segs] == [(t0, sw), (sw, t1)]
    first, rest = calls
    assert first["settings"] is driven and first["hs"] and first["last"] == sw and first["end"] == sw.to_pydatetime()
    assert rest["settings"] is plain and not rest["hs"] and rest["init"] == tmp_path / "seg0" / "state.sno"
    assert rest["first"] == sw - pd.Timedelta(hours=6) and rest["last"] == t1

    calls.clear()  # a continuation after a pit update, from a state before the switch
    init = tmp_path / "init.sno"
    segs = column.run_span(None, plain, tmp_path, None, smet, T("2026-11-02"), t1, initial_sno=init, hs=hs,
                           switch=sw, driven_settings=driven)
    assert len(segs) == 2 and calls[0]["init"] == init and calls[0]["first"] == T("2026-11-01 18:00")

    calls.clear()  # wholly after the switch: precipitation; wholly before it: driven
    column.run_span(None, plain, tmp_path, None, smet, T("2026-11-04"), t1, initial_sno=init, hs=hs, switch=sw,
                    driven_settings=driven)
    column.run_span(None, plain, tmp_path, None, smet, t0, sw, hs=hs, switch=sw, driven_settings=driven)
    assert [c["settings"] is driven for c in calls] == [False, True] and [c["hs"] for c in calls] == [False, True]


def test_daily_states_and_depth_join_over_consecutive_runs(tmp_path, monkeypatch):
    def run(name, times, depth):
        d = tmp_path / name / "output"
        d.mkdir(parents=True)
        for t in times:
            (d / f"ST.sno{t:%Y%m%d%H%M}").write_text(name)
        met = tmp_path / name / "x.met"
        met.write_text(name)
        return type("O", (), {"run_dir": tmp_path / name, "met": met, "depth": depth})()

    a = run("a", [T("2026-11-01"), T("2026-11-02"), T("2026-11-03")], 10.0)
    b = run("b", [T("2026-11-03"), T("2026-11-04")], 20.0)
    idx = {"a": pd.date_range("2026-11-01", "2026-11-03", freq="h", tz="UTC"),
           "b": pd.date_range("2026-11-03", "2026-11-04", freq="h", tz="UTC")}
    monkeypatch.setattr(sp, "parse_met", lambda p: pd.DataFrame(
        {"Modelled snow depth (vertical)": 10.0 if Path(p).read_text() == "a" else 20.0}, index=idx[Path(p).read_text()]))
    segs = [(T("2026-11-01"), T("2026-11-03"), a), (T("2026-11-03"), T("2026-11-04"), b)]
    states = steer.span_backups(segs)
    assert sorted(states) == [T(f"2026-11-0{d}") for d in (1, 2, 3, 4)]
    assert states[T("2026-11-03")].read_text() == "b" and states[T("2026-11-02")].read_text() == "a"
    hs = steer.span_hs(segs)
    assert hs.index.is_unique and hs[T("2026-11-02")] == 10.0 and hs[T("2026-11-03")] == 20.0
    assert len(steer.span_backups(segs[:1])) == 3 and len(steer.span_hs(segs[:1])) == len(idx["a"])


def test_profiles_keep_the_switch_time_but_not_a_pit_update_time(monkeypatch):
    prof = {"a": ["2026-11-02T18", "2026-11-03T00"], "b": ["2026-11-03T06", "2026-11-04T00"],
            "c": ["2026-11-04T06", "2026-11-05T00"]}
    monkeypatch.setattr(build, "_profiles", lambda pro, slope, every, skipped: [{"t": t} for t in prof[pro]])
    seg = lambda a, b, name: (T(a), T(b), type("O", (), {"pro": name})())  # noqa: E731
    segs = [seg("2026-11-01", "2026-11-03", "a"), seg("2026-11-03", "2026-11-04", "b"),
            seg("2026-11-04", "2026-11-05", "c")]
    # 2026-11-03 is the switch: the state runs on, so its profile stays; 2026-11-04 is a pit update: left out
    got = [p["t"] for p in build._span_profiles(segs, None, [], switch=T("2026-11-03"))]
    assert got == ["2026-11-02T18", "2026-11-03T00", "2026-11-03T06", "2026-11-04T06", "2026-11-05T00"]
    assert [p["t"] for p in build._span_profiles(segs[:1], None, [])] == prof["a"]  # one run: unfiltered
