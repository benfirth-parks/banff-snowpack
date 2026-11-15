"""Profile times: fixed MST before 2026-11-01, Alberta Time (UTC-6) from then on (ADR-111)."""

from datetime import date

import pandas as pd

from snowagent.obs.localtime import ALBERTA_TIME_SINCE, to_local, to_utc, zone_on

MST = "Etc/GMT+7"


def test_before_alberta_time_unchanged():
    assert to_utc("2026-02-10T09:00", MST) == pd.Timestamp("2026-02-10T16:00Z")
    assert to_utc("2026-10-31T23:30", MST) == pd.Timestamp("2026-11-01T06:30Z")


def test_from_alberta_time_utc_minus_6():
    assert ALBERTA_TIME_SINCE == date(2026, 11, 1)
    assert to_utc("2026-12-10T09:00", MST) == pd.Timestamp("2026-12-10T15:00Z")
    assert to_utc("2026-11-01", MST) == pd.Timestamp("2026-11-01T06:00Z")  # date-only: local midnight
    assert zone_on(MST, date(2026, 11, 1)) == "Etc/GMT+6"
    assert zone_on(MST, date(2026, 10, 31)) == MST


def test_other_zones_untouched():
    assert zone_on("UTC", date(2027, 1, 1)) == "UTC"
    assert to_utc("2027-01-01T12:00", "UTC") == pd.Timestamp("2027-01-01T12:00Z")


def test_local_date_round_trips():
    for local in ["2026-03-01T08:15", "2026-10-31T22:00", "2026-11-01T00:30", "2027-01-15T07:00"]:
        back = to_local(to_utc(local, MST), MST)
        assert back.tz_localize(None) == pd.Timestamp(local)
    assert to_local("2026-12-10T15:00Z", MST).hour == 9
    assert to_local("2026-02-10T16:00Z", MST).hour == 9
