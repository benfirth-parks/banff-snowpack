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
    """Date written in a file name (never guessed; ambiguous/invalid forms are flagged)."""
    date, flags = _parse_filename_date(name, season)
    if date and season and re.fullmatch(r"\d{4}-\d{4}", season):
        start = int(season[:4])
        if not (start, 8) <= (int(date[:4]), int(date[5:7])) <= (start + 1, 7):
            flags = flags + [f"filename_date_outside_season_folder:{season}"]
    return date, flags


def _parse_filename_date(name: str, season: str | None) -> tuple[str | None, list[str]]:
    n = name.lower()
    flags: list[str] = []
    if m := re.match(r"(\d{4})-(\d{2})-(\d{2})", n):  # YYYY-MM-DD at start
        d = _valid(int(m[1]), int(m[2]), int(m[3]))
        return (d.isoformat(), flags) if d else (None, [f"filename_date_invalid:{m[0]}"])
    if m := re.search(r"(?<!\d)((?:19|20)\d{2})-(\d{2})-(\d{2})(?!\d)", n):  # YYYY-MM-DD anywhere
        if d := _valid(int(m[1]), int(m[2]), int(m[3])):
            return d.isoformat(), flags
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
    if m := re.match(r"(?:[a-z]+\s*)?(\d{2})[ _.-](\d{2})[ _.-](\d{2})(?!\d)", n):  # "BS 05 12 27" YY MM DD
        y = int(m[1])
        if d := _valid(y + (1900 if y > 50 else 2000), int(m[2]), int(m[3])):
            return d.isoformat(), ["filename_date_yy_mm_dd"]
    if m := re.match(r"[a-z]{1,4}\s?(\d{2})(\d{2})(\d{2})(?!\d)", n):  # "TT980331", "Bs000211", "BS 011122"
        y = int(m[1])
        if d := _valid(y + (1900 if y > 50 else 2000), int(m[2]), int(m[3])):
            return d.isoformat(), ["filename_date_prefix_yymmdd"]
    if m := re.match(r"(\d{2})(\d{2})(\d{2})(?!\d)", n):  # YYMMDD at start
        y = int(m[1])
        if d := _valid(y + (1900 if y > 50 else 2000), int(m[2]), int(m[3])):
            return d.isoformat(), ["filename_date_yymmdd"]
    if season and (m := re.search(r"(?<![\d.])(\d{2})(\d{2})(\d{2})(?!\d)(?!\.\d)", n)):  # "purple bowl 091205"
        y = int(m[1]) + 2000
        d = _valid(y, int(m[2]), int(m[3]))
        start = int(season[:4])
        if d and (d.year, d.month) >= (start, 8) and (d.year, d.month) <= (start + 1, 7):
            return d.isoformat(), ["filename_date_yymmdd_in_name_matches_season"]
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
