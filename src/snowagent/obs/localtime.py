"""Local time of field observations.

Profile exports carry no zone designator. ``config/observations.yaml`` names the zone they were written in
(``time_zone``, fixed MST, ``Etc/GMT+7``, user-confirmed 2026-09-30). Alberta moved to Alberta Time, UTC-6 all
year, on 2026-11-01, and the exports follow local time (user-confirmed 2026-11-15). So a local date on or after
2026-11-01 written in MST is read as UTC-6; earlier observations keep the configured zone and are unchanged.

The change is a fixed date rather than ``America/Edmonton`` so results do not depend on how new the machine's
time-zone data is, and earlier observations keep exactly the offset they were processed with.
"""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

import pandas as pd

ALBERTA_TIME_SINCE = date(2026, 11, 1)
# configured zone -> (first local date of the new zone, new zone)
ZONE_CHANGES: dict[str, tuple[date, str]] = {"Etc/GMT+7": (ALBERTA_TIME_SINCE, "Etc/GMT+6")}


def zone_on(tz: str, local_day: date) -> str:
    """The zone an observation written in ``tz`` on ``local_day`` (its own local date) was really in."""
    change = ZONE_CHANGES.get(tz)
    if change and local_day >= change[0]:
        return change[1]
    return tz


def localize(local: pd.Timestamp | str, tz: str) -> pd.Timestamp:
    """A local wall-clock time (naive) written in ``tz`` -> an aware timestamp in the zone it was really in."""
    ts = pd.Timestamp(local)
    return ts.tz_localize(ZoneInfo(zone_on(tz, ts.date())))


def to_utc(local: pd.Timestamp | str, tz: str) -> pd.Timestamp:
    return localize(local, tz).tz_convert("UTC")


def to_local(utc: pd.Timestamp | str, tz: str) -> pd.Timestamp:
    """An instant -> the observer's local wall-clock time (aware), as it would have been written in an export."""
    ts = pd.Timestamp(utc)
    if ts.tzinfo is None:
        ts = ts.tz_localize("UTC")
    new = ZONE_CHANGES.get(tz)
    if new:
        later = ts.tz_convert(ZoneInfo(new[1]))
        if later.date() >= new[0]:
            return later
    return ts.tz_convert(ZoneInfo(tz))
