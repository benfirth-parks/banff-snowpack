"""Pits awaiting the owner's review: the opt-in exclusion from steering and scoring (ADR-050)."""

from __future__ import annotations

import json

import pandas as pd
import pytest

START, END = pd.Timestamp("2021-09-15", tz="UTC"), pd.Timestamp("2022-06-30", tz="UTC")


def _pit(pid: str, t: str, *, site: str = "goats_eye", hs: float | None = 100.0, flags: tuple = (),
         location_qc: tuple = (), **kw) -> dict:
    return {"profile_id": pid, "site_key": site, "category": "study_plot", "obs_time_utc": t, "hs_cm": hs,
            "height_reference": "height_above_ground", "duplicate_of": None, "unusable": False,
            "layers": [{"top_cm": hs or 50.0, "bottom_cm": 0.0}], "flags": list(flags),
            "location_qc": list(location_qc), "provenance": {"method": "transcription:vision_model",
                                                             "confidence": "medium"},
            "source_file": f"profiles/2021-2022/Goat's Eye/{pid}.pdf", **kw}


PITS = [
    _pit("clean_a", "2021-12-01T18:00:00+00:00"),
    _pit("gps", "2021-12-10T18:00:00+00:00", location_qc=("location_17.6km_from_site_median",)),
    _pit("date", "2022-01-05T18:00:00+00:00", flags=("printed_date_2022-01-05_differs_from_filename_2021-01-05",)),
    _pit("place", "2022-01-20T18:00:00+00:00",
         flags=("time_unknown_date_only", "printed_site_name_not_folder_plot:goats_eye:Brewster Rock, Alberta")),
    _pit("clean_b", "2022-02-01T18:00:00+00:00", flags=("unreviewed_transcription",)),
    _pit("dup", "2022-02-02T18:00:00+00:00", location_qc=("location_2.0km_from_site_median",),
         duplicate_of="clean_b"),
    _pit("other_site", "2022-02-03T18:00:00+00:00", site="simpson", location_qc=("location_9km_from_site_median",)),
]


def _config(tmp_path, value) -> object:
    p = tmp_path / "observations.yaml"
    p.write_text("time_zone: Etc/GMT+7\n" + ("" if value is None else
                                             f"exclude_flagged_pits_from_steering_and_scoring: {value}\n"))
    return p


def test_review_reasons_are_location_qc_and_printed_date_or_site_flags():
    from snowagent.obs.observed import review_reasons

    by_id = {o["profile_id"]: o for o in PITS}
    assert review_reasons(by_id["clean_b"]) == []
    assert review_reasons(by_id["gps"]) == ["location_17.6km_from_site_median"]
    assert review_reasons(by_id["place"]) == ["printed_site_name_not_folder_plot:goats_eye:Brewster Rock, Alberta"]
    assert review_reasons({"profile_id": "x"}) == []


def test_switch_defaults_to_false_and_must_be_a_boolean(tmp_path):
    from snowagent.obs.inventory import DEFAULT_CONFIG
    from snowagent.obs.observed import exclude_flagged_pits

    assert exclude_flagged_pits() is False  # the repository's config: today's behaviour until the owner rules
    assert "exclude_flagged_pits_from_steering_and_scoring: false" in DEFAULT_CONFIG.read_text()
    assert exclude_flagged_pits(_config(tmp_path, None)) is False
    assert exclude_flagged_pits(_config(tmp_path, "true")) is True
    with pytest.raises(ValueError):
        exclude_flagged_pits(_config(tmp_path, '"no"'))


@pytest.mark.parametrize("setting", [False, True])
def test_scoring_pits_follow_the_switch_and_list_what_they_leave_out(tmp_path, monkeypatch, setting):
    import snowagent.obs.observed as observed
    from snowagent.baseline.evaluate import observed_at_plot

    jsonl = tmp_path / "observed_profiles.jsonl"
    jsonl.write_text("".join(json.dumps(o) + "\n" for o in PITS))
    monkeypatch.setattr(observed, "DEFAULT_CONFIG", _config(tmp_path, str(setting).lower()))
    excluded: list[dict] = []
    ids = [o["profile_id"] for o in observed_at_plot(jsonl, "goats_eye", START, END, excluded=excluded)]
    if setting:
        assert ids == ["clean_a", "clean_b"]
        assert [(x["profile_id"], len(x["reasons"])) for x in excluded] == [("gps", 1), ("date", 1), ("place", 1)]
    else:  # today's behaviour: every unique usable pit, nothing listed
        assert ids == ["clean_a", "gps", "date", "place", "clean_b"] and excluded == []


@pytest.mark.parametrize("setting", [False, True])
def test_plot_pits_records_pits_excluded_only_when_the_switch_leaves_pits_out(tmp_path, monkeypatch, setting):
    """The selection the site build and `snowagent baseline` use, with what they write as ``pits_excluded``."""
    import snowagent.obs.observed as observed
    from snowagent.baseline.evaluate import plot_pits

    jsonl = tmp_path / "observed_profiles.jsonl"
    jsonl.write_text("".join(json.dumps(o) + "\n" for o in PITS))
    monkeypatch.setattr(observed, "DEFAULT_CONFIG", _config(tmp_path, str(setting).lower()))
    pits, extra = plot_pits(jsonl, "goats_eye", START, END)
    if setting:
        assert [o["profile_id"] for o in pits] == ["clean_a", "clean_b"]
        assert list(extra) == ["pits_excluded"]
        assert [(x["profile_id"], x["reasons"]) for x in extra["pits_excluded"]] == [
            ("gps", ["location_17.6km_from_site_median"]),
            ("date", ["printed_date_2022-01-05_differs_from_filename_2021-01-05"]),
            ("place", ["printed_site_name_not_folder_plot:goats_eye:Brewster Rock, Alberta"])]
    else:  # today's outputs: every pit, nothing added
        assert [o["profile_id"] for o in pits] == ["clean_a", "gps", "date", "place", "clean_b"] and extra == {}
    # a window without flagged pits adds nothing either
    assert plot_pits(jsonl, "goats_eye", START, pd.Timestamp("2021-12-05", tz="UTC"))[1] == {}


def _legacy_update_times(pits, start, end):
    """The selection steered_run made inline before ADR-050."""
    from snowagent.learn.steer import _pit_hs

    times = {}
    for o in pits:
        if _pit_hs(o) is None:
            continue
        t = pd.Timestamp(o["obs_time_utc"]).ceil("D")
        if start < t < end:
            times[t] = o
    return times


@pytest.mark.parametrize("setting", [False, True])
def test_steering_pits_follow_the_switch(tmp_path, monkeypatch, setting):
    import snowagent.obs.observed as observed
    from snowagent.learn.steer import update_pits

    pits = [o for o in PITS if o["site_key"] == "goats_eye" and not o.get("duplicate_of")]
    pits += [_pit("no_hs", "2022-03-01T18:00:00+00:00", hs=None) | {"layers": []},
             _pit("same_day_later", "2021-12-01T20:00:00+00:00"),  # same update time as clean_a: the later wins
             _pit("after_end", "2022-07-02T18:00:00+00:00")]
    monkeypatch.setattr(observed, "DEFAULT_CONFIG", _config(tmp_path, str(setting).lower()))
    excluded: list[dict] = []
    times = update_pits(pits, START, END, excluded=excluded)
    ids = {t.date().isoformat(): o["profile_id"] for t, o in sorted(times.items())}
    if setting:
        assert ids == {"2021-12-02": "same_day_later", "2022-02-02": "clean_b"}
        assert [x["profile_id"] for x in excluded] == ["gps", "date", "place"]
    else:
        assert times == _legacy_update_times(pits, START, END) and excluded == []
        assert ids == {"2021-12-02": "same_day_later", "2021-12-11": "gps", "2022-01-06": "date",
                       "2022-01-21": "place", "2022-02-02": "clean_b"}
    assert update_pits(pits, START, END, exclude_flagged=False) == _legacy_update_times(pits, START, END)


def _review_fixture(tmp_path):
    """Observed records and built season files for the review list."""
    gx = "profiles/2021-2022/Goat's Eye"
    obs = [
        _pit("clean_a", "2021-12-01T18:00:00+00:00", source_file=f"{gx}/2021-12-01 Goats Eye.pdf"),
        _pit("gps", "2021-12-10T18:00:00+00:00", location_qc=("location_17.6km_from_site_median",),
             source_file=f"{gx}/2021-12-10 Goats Eye.pdf", site_name_as_written="Goats Eye Plot"),
        _pit("date", "2022-01-05T07:00:00+00:00", source_file=f"{gx}/2021-01-05 Goats Eye.pdf",
             flags=("printed_date_2022-01-05_differs_from_filename_2021-01-05",)),
        _pit("place", "2022-01-20T18:00:00+00:00", source_file=f"{gx}/2022-01-20 Brewster.pdf",
             flags=("printed_site_name_not_folder_plot:goats_eye:Brewster Rock, Alberta",),
             site_name_as_written="Brewster Rock, Alberta"),
        _pit("place_copy", "2022-01-20T18:00:00+00:00", category="test_profile", site=None,
             location_qc=("coordinates_shared_by_different_sites_suspect_device_gps",), duplicate_of="place",
             source_file="profiles/2021-2022/Test Profiles/2022-01-20 Brewster.pdf"),
        _pit("dup", "2022-02-02T18:00:00+00:00", location_qc=("location_2.0km_from_site_median",),
             duplicate_of="clean_a", source_file=f"{gx}/2022-02-02 Goats Eye.jpg"),
        _pit("era5_season", "2010-01-10T18:00:00+00:00", location_qc=("location_3.0km_from_site_median",),
             source_file="profiles/2009-2010/Goat's Eye/100110.jpg"),
        _pit("tak", "2022-02-03T18:00:00+00:00", site="tak_falls",
             flags=("printed_date_2022-02-03_differs_from_filename_2022-02-02",),
             source_file="profiles/2021-2022/Tak Falls/2022-02-02 Tak.pdf"),
    ]
    jsonl = tmp_path / "observed_profiles.jsonl"
    jsonl.write_text("".join(json.dumps(o) + "\n" for o in obs))
    site = tmp_path / "web_data"
    (site / "goats_eye").mkdir(parents=True)
    (site / "goats_eye" / "2021-2022.json").write_text(json.dumps({"steer": {"updates": [
        {"pit": "clean_a", "factor": 1.0, "method": "layers"},
        {"pit": "gps", "factor": 0.9, "method": "layers"},
        {"pit": "date", "factor": 1.0, "note": "model too shallow; no update"}]}}))
    (site / "goats_eye" / "2021-2022_forecasts.json").write_text("{}")
    return jsonl, site


def test_flagged_pits_cli_writes_the_review_list(tmp_path, monkeypatch):
    import csv

    from typer.testing import CliRunner

    from snowagent.cli import app
    from snowagent.engine.snowpack import REPO_ROOT
    from snowagent.obs.pit_review import COLUMNS

    monkeypatch.chdir(REPO_ROOT)  # config/plot_forcing.yaml (seasons) as the site build reads it
    jsonl, site = _review_fixture(tmp_path)
    out = tmp_path / "review"
    r = CliRunner().invoke(app, ["obs", "flagged-pits", "--observed", str(jsonl), "--out", str(out),
                                 "--site-data", str(site)])
    assert r.exit_code == 0, r.output
    res = json.loads(r.stdout)
    rows = {x["profile_id"]: x for x in csv.DictReader((out / "flagged_pits.csv").open())}
    # study-plot pits with a review reason only (not clean pits, not the test-profile copy), duplicates included
    assert set(rows) == {"gps", "date", "place", "dup", "era5_season", "tak"}
    assert next(csv.reader((out / "flagged_pits.csv").open())) == COLUMNS
    assert (rows["date"]["printed_date"], rows["date"]["filename_date"]) == ("2022-01-05", "2021-01-05")
    assert rows["place"]["printed_site_name"] == "Brewster Rock, Alberta" and rows["place"]["flag_types"] == "site"
    assert rows["place"]["other_copies"] == "place_copy"
    assert rows["gps"]["location_qc"] == "location_17.6km_from_site_median" and rows["gps"]["review_flags"] == ""
    # steering per learn/steer.py: measured-weather seasons of the site plots, unique usable pits
    steers = {k: v["steers_site_run"] == "True" for k, v in rows.items()}
    assert steers == {"gps": True, "date": True, "place": True, "dup": False, "era5_season": False, "tak": False}
    assert rows["gps"]["steer_run"] == "goats_eye/2021-2022"
    assert rows["gps"]["steer_update_utc"] == "2021-12-11T00:00:00+00:00"
    assert rows["gps"]["built_run_update"] == "updated goats_eye/2021-2022 (layers, factor 0.9)"
    assert rows["date"]["built_run_update"].startswith("no update in goats_eye/2021-2022: model too shallow")
    assert rows["place"]["built_run_update"] == "not in the built season files"
    assert rows["tak"]["confidence"] == "medium" and rows["tak"]["source_file"].endswith("2022-02-02 Tak.pdf")
    assert res["flagged_study_plot_pits"] == 6 and res["not_duplicates"] == 5 and res["steering_site_runs"] == 3
    assert res["by_flag_type"] == {"date": {"pits": 2, "steering_site_runs": 1},
                                   "site": {"pits": 1, "steering_site_runs": 1},
                                   "location": {"pits": 3, "steering_site_runs": 1}}
    assert res["exclude_flagged_pits_from_steering_and_scoring"] is False
    md = (out / "flagged_pits.md").read_text()
    assert "6 flagged pits (5 not duplicates); 3 steer a site run." in md
    assert "| place | goats_eye | site | 2022-01-20 | 2022-01-20 | Brewster Rock, Alberta |" in md
    assert "not an avalanche forecast" in md


def test_flagged_pits_do_not_steer_when_the_switch_is_on(tmp_path, monkeypatch):
    from snowagent.engine.snowpack import REPO_ROOT
    from snowagent.obs.pit_review import write_review

    monkeypatch.chdir(REPO_ROOT)
    jsonl, _ = _review_fixture(tmp_path)
    res = write_review(jsonl, tmp_path / "review", None, config=_config(tmp_path, "true"))
    assert res["flagged_study_plot_pits"] == 6 and res["steering_site_runs"] == 0
    assert "Built season files read for the actual updates: none" in (tmp_path / "review" / "flagged_pits.md").read_text()
