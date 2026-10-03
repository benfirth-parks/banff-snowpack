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
