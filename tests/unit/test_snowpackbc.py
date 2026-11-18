"""SnowpackBC as an outside blind-test competitor (ADR-112): daily snapshots saved unchanged, profiles read as
issued, scored beside standard SNOWPACK on the live season's pits, never from a snapshot saved after the case's
issue time."""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pandas as pd
import pytest

from snowagent.ops import snowpackbc as sb

LIVE = "2026-2027"
BOW = sb.POINTS[0]


def T(s: str) -> pd.Timestamp:
    return pd.Timestamp(s, tz="UTC")


def body(p: sb.Point, fetched: pd.Timestamp, hs_cm: float = 80.0, before_h: int = 24, after_h: int = 48) -> dict:
    """A profile answer as the site gives it: 3-hourly steps, layer tops in cm above ground, ground up."""
    times = pd.date_range(fetched - pd.Timedelta(hours=before_h), fetched + pd.Timedelta(hours=after_h), freq="3h")
    n = len(times)
    tops = [30.0, 70.0, hs_cm]
    return {"meta": {"station": p.location}, "time": [int(t.timestamp()) for t in times], "hs": [hs_cm] * n,
            "tops": [tops] * n,
            "params": {"grainshape": [["DH", "MFcr", "PP"]] * n, "hardness": [[2.0, 5.0, 1.0]] * n,
                       "density": [[250.0, 320.0, 90.0]] * n, "grainsize": [[3.0, 1.0, 0.5]] * n},
            "units": {"grainshape": "ICSSG"}}


class Resp:
    def __init__(self, d, status: int = 200) -> None:
        self.content, self.status = json.dumps(d).encode(), status

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise RuntimeError(f"HTTP {self.status}")


def test_fetch_saves_each_point_unchanged_once_per_six_hours(tmp_path):
    now = T("2026-11-18T13:05")
    asked = []

    def get(u):
        asked.append(u)
        p = next(p for p in sb.POINTS if f"location={p.location}&" in u and p.area in u)
        return Resp({"error": "boom"}, 500) if p.location == "Fidelity" else Resp(body(p, now))

    res = sb.fetch_snapshots(now, tmp_path, get)
    assert len(asked) == len(sb.POINTS) and all("days_before=3" in u and "inclination=0" in u for u in asked)
    assert len(res["saved"]) == len(sb.POINTS) - 1 and res["errors"][0].startswith("rogers_stn/Fidelity")
    assert res["warnings"][0]["level"] == "info"  # a comparison, never a failed daily step
    index = [json.loads(x) for x in (tmp_path / LIVE / "index.jsonl").read_text().splitlines()]
    rec = index[0]
    raw = gzip.decompress((tmp_path / rec["path"]).read_bytes())
    assert json.loads(raw) == body(BOW, now) and rec["bytes"] == len(raw) and len(rec["sha256"]) == 64
    assert rec["fetched_utc"] == "2026-11-18T13:05:00+00:00" and rec["last_utc"] == "2026-11-20T13:05:00+00:00"

    asked.clear()
    again = sb.fetch_snapshots(now + pd.Timedelta(hours=2), tmp_path, get)
    assert again["saved"] == [] and asked == [sb.url(sb.POINTS[2])]  # only the point that failed is asked again
    assert len(sb.fetch_snapshots(now + pd.Timedelta(hours=24), tmp_path, get)["saved"]) == len(sb.POINTS) - 1


def test_a_profile_for_another_location_is_refused(tmp_path):
    res = sb.fetch_snapshots(T("2026-11-18T13:05"), tmp_path, lambda u: Resp(body(sb.POINTS[1], T("2026-11-18"))),
                             points=(BOW,))
    assert res["saved"] == [] and "asked for 'Bow_Summit'" in res["errors"][0]


def test_profile_at_turns_heights_into_depths_and_holds_the_last_step():
    issued = T("2026-12-01T13:00")
    d = body(BOW, issued)
    r = sb.profile_at(d, T("2026-12-02T11:00"), issued)
    assert r.snow_depth_m == pytest.approx(0.8) and r.profile_lag_h == pytest.approx(1.0)
    assert [(ly.grain, round(ly.top_depth_m, 3), round(ly.bottom_depth_m, 3), ly.hardness_index)
            for ly in r.layers] == [("PP", 0.0, 0.1, 1.0), ("MFcr", 0.1, 0.5, 5.0), ("DH", 0.5, 0.8, 2.0)]
    assert r.notes["forecast"] is True and r.notes["issued_utc"] == issued.isoformat()
    far = sb.profile_at(d, T("2026-12-05T13:00"), issued)  # beyond its forecast: its last step, lag recorded
    assert far.profile_lag_h == pytest.approx(48.0)
    assert sb.profile_at(d, T("2026-11-20T00:00"), issued) is None


def test_issued_snapshot_is_the_newest_saved_by_the_issue_time():
    snaps = [(T("2026-12-01T13:05"), Path("a")), (T("2026-12-02T13:05"), Path("b"))]
    assert sb.issued_snapshot(snaps, T("2026-12-02T13:04"))[1] == Path("a")
    assert sb.issued_snapshot(snaps, T("2026-12-02T13:05"))[1] == Path("b")
    assert sb.issued_snapshot(snaps, T("2026-12-01T00:00")) is None
    assert sb.issued_snapshot(snaps, T("2026-12-06T00:00")) is None  # older than MAX_AGE_H: our gap, not scored


# ------------------------------------------------------------------------------------------- in the blind test


@pytest.fixture(scope="module")
def live_lab(tmp_path_factory):
    pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")
    from snowagent.lab.settings import load_lab_config
    from tests.unit.lab_fixtures import write_multiseason_lab

    tmp = tmp_path_factory.mktemp("outside")
    cfg = load_lab_config()
    ids = write_multiseason_lab(tmp / "lab", tmp / "checkout", cfg, ("2024-2025", "2025-2026", LIVE))
    observed = tmp / "observed.jsonl"
    observed.write_text("".join(json.dumps({"profile_id": i, "site_key": "bow_summit",
                                            "obs_time_utc": f"{i[:10]}T19:00:00+00:00"}) + "\n" for i in ids))
    archive = tmp / "snowpackbc"
    for day in pd.date_range("2026-11-25T13:05", "2027-02-10T13:05", freq="1D", tz="UTC"):
        for p in sb.POINTS[:4:3]:  # Bow Summit station and its grid point
            rel = Path(LIVE) / p.area / p.location / f"{day:%Y%m%dT%H%MZ}.json.gz"
            (archive / rel).parent.mkdir(parents=True, exist_ok=True)
            (archive / rel).write_bytes(gzip.compress(json.dumps(body(p, day)).encode()))
            with (archive / LIVE / "index.jsonl").open("a") as f:
                f.write(json.dumps({"fetched_utc": day.isoformat(), "area": p.area, "location": p.location,
                                    "path": rel.as_posix()}) + "\n")
    return tmp, observed, archive


def _entry() -> bytes:
    from snowagent.lab.genome import default_genome

    g = json.loads(default_genome("snowpack").model_dump_json())
    g["genes"]["sp_wind_mult"] = 0.8
    return json.dumps({"name": "Omar Balmoral", "genome": g, "source": {"run_id": "r"}, "season": LIVE,
                       "frozen_utc": "2026-11-01T00:00:00+00:00", "git_commit": "abc", "code_hash": "h",
                       "scoring_version": "lab-scoring-3"}).encode()


def test_snowpackbc_is_scored_as_issued_beside_standard_snowpack(live_lab, tmp_path):
    from snowagent.lab.competition.runner import EngineSpec
    from snowagent.lab.competition.scoring import SCORING_VERSION
    from snowagent.ops import blind_test as bt

    lab, observed, archive = live_lab
    kw = dict(observed=observed, archive=tmp_path / "archive", lab_root=lab / "lab", repo=lab / "checkout",
              engine=EngineSpec(kind="fake"), publish=False, do_import=False, outside_archive=archive)
    res = bt.run_blind_test(2026, tmp_path / "web", files={"e.json": _entry()}, **kw)
    assert res["scored"] > 0
    store = bt.load_store(tmp_path / "archive" / LIVE / "scores.jsonl")
    out = store[store["agent_id"].isin(["snowpackbc_stations", "snowpackbc_grid"])]
    assert len(out) and set(out["scoring_version"]) == {SCORING_VERSION} and set(out["site_code"]) == {"BOW"}
    for x in out.to_dict("records"):  # never a snapshot saved after the case's issue time
        issue = bt._utc(x["pit_utc"] if x["case_type"] == "next_pit" else x["as_of_utc"])
        assert bt._utc(x["issued_utc"]) <= issue and x["status"] == "ok"
    std = store[(store["agent_id"] == "standard") & (store["scoring_version"] == SCORING_VERSION)]
    assert set(out["case_id"]) <= set(std["case_id"])  # the yardstick under the same version, same cases

    summary = json.loads((tmp_path / "web" / "blind_test.json").read_text())
    assert [e["name"] for e in summary["entries"]] == ["Omar Balmoral"]  # not one of the frozen entries
    by = {e["id"]: e for e in summary["outside"]}
    s = by["snowpackbc_stations"]
    assert s["cases"] > 0 and s["standard_composite"] is not None and "BOW" in s["by_plot"]
    assert s["from_utc"] == "2026-11-25T13:05:00+00:00"

    again = bt.run_blind_test(2026, tmp_path / "web", files={"e.json": _entry()}, **kw)
    assert again.get("scored", 0) == 0  # nothing new: no second scoring, rows never rewritten


def test_outside_line():
    from snowagent.lab.services.blind_test import outside_line

    assert outside_line({}) == "no pit scored yet"
    assert outside_line({"cases": 1, "composite": 0.6, "standard_composite": 0.5, "won": 1}).startswith(
        "1 pit case so far: 0.600 against 0.500")
