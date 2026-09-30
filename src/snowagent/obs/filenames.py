"""Observation dates from profile file names (secondary to header text when present).

Unambiguous formats are parsed; a missing year is inferred from the season folder
(Aug-Dec -> first year, Jan-Jul -> second) and flagged; ambiguous or impossible dates
return None with a flag. Nothing is guessed silently.
"""

from __future__ import annotations

import re
from datetime import date

MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}


def _valid(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _season_year(month: int, season: str | None) -> int | None:
    if not season:
        return None
    y0, y1 = (int(x) for x in season.split("-"))
    return y0 if month >= 8 else y1


def parse_filename_date(name: str, season: str | None) -> tuple[str | None, list[str]]:
    n = name.lower()
    flags: list[str] = []
    if m := re.match(r"(\d{4})-(\d{2})-(\d{2})", n):  # YYYY-MM-DD at start
        d = _valid(int(m[1]), int(m[2]), int(m[3]))
        return (d.isoformat(), flags) if d else (None, [f"filename_date_invalid:{m[0]}"])
    if m := re.search(r"(?<!\d)(20\d{2})(\d{2})(\d{2})(?!\d)", n):  # YYYYMMDD
        if d := _valid(int(m[1]), int(m[2]), int(m[3])):
            return d.isoformat(), flags
    if m := re.search(r"(?<!\d)(\d{2})-(\d{2})-(20\d{2})(?!\d)", n):  # MM-DD-YYYY (DD > 12 disambiguates)
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        cands = {x for x in (_valid(y, a, b), _valid(y, b, a)) if x}
        if len(cands) == 1:
            return cands.pop().isoformat(), flags
        return None, [f"filename_date_ambiguous:{m[0]}"]
    if m := re.search(r"(?<!\d)(\d{2})(\d{2})(20\d{2})(?!\d)", n):  # DDMMYYYY / MMDDYYYY
        a, b, y = int(m[1]), int(m[2]), int(m[3])
        cands = {x for x in (_valid(y, a, b), _valid(y, b, a)) if x}
        if len(cands) == 1:
            return cands.pop().isoformat(), flags
        return None, [f"filename_date_ambiguous:{m[0]}"]
    if m := re.match(r"(\d{2})(\d{2})(\d{2})(?!\d)", n):  # YYMMDD at start
        if d := _valid(2000 + int(m[1]), int(m[2]), int(m[3])):
            return d.isoformat(), ["filename_date_yymmdd"]
    if m := re.search(r"(?<!\d)(\d{1,2})\s*-?\s*(" + "|".join(MONTHS) + r")[a-z]*\s*-?\s*(\d{2,4})?(?![a-z])", n):
        day, mon = int(m[1]), MONTHS[m[2]]
        if m[3]:
            y = int(m[3]) + (2000 if len(m[3]) == 2 else 0)
        else:
            y = _season_year(mon, season)
            flags.append("filename_year_inferred_from_season")
        if y and (d := _valid(y, mon, day)):
            return d.isoformat(), flags
    return None, ["no_date_in_filename"]
