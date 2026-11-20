"""Keep observer names and e-mail addresses out of the repository's text (ADR-121).

The repository is public. Pit charts print the observer's name (often an e-mail address); transcriptions,
inbox receipts and inventories must not copy it. ``strip_observer_text`` removes, from free text:

- e-mail addresses (whole or partly printed, e.g. ``.name@pc.gc.ca``);
- the value of an observer label (``Observer: ...``, ``Observers: ...``, ``Obs: ...``, ``observer '...'``,
  ``Name printed in the org position: ...``) up to
  the end of its sentence or quote, unless the value is blank (``--``) or names a unit such as
  ``Banff Visitor Safety`` or ``Snow Safety`` (an organisation, not a person).

Names written elsewhere in free text (a comment box saying who dug the pit) cannot be recognised by rule; the
transcription guide tells readers not to copy them. Nothing else in a note changes, and no measured field is touched,
so the observed set and scores do not change.
"""

from __future__ import annotations

import re
from typing import Any

REMOVED = "[name removed]"

EMAIL = re.compile(r"[\w.+-]*@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b")
# "Observer: x." / "Observers: x;" / "Obs: x" (value runs to the sentence end: ". ", ";", "|" or end of text)
LABEL = re.compile(r"(?i)\b(observers?|obs|name printed in the org position)(\s*[:=]\s*)(?!\[name removed\])('[^']*'|\"[^\"]*\"|(?:[^.;|\n]|\.(?!\s|$))*)")
# "observer 'Stephen X'" / 'observer "x"'
QUOTED = re.compile(r"(?i)\b(observers?)(\s+)('[^']*'|\"[^\"]*\")")
KEEP = re.compile(r"(?i)^\W*(--|-|blank|none|n/?a|)\W*$|safety|parks|\bnational\b|visitor|\[name removed\]")


def _value(label: str, sep: str, value: str) -> str:
    inner = value.strip()
    if KEEP.search(inner.strip("'\"")):
        return label + sep + value
    q = inner[:1] if inner[:1] in "'\"" else ""
    trail = value[len(value.rstrip()):]
    return f"{label}{sep}{q}{REMOVED}{q}{trail}"


def strip_observer_text(text: str | None) -> str | None:
    """``text`` with e-mail addresses and observer-label values replaced by ``[name removed]``."""
    if not text:
        return text
    out = EMAIL.sub(REMOVED, text)
    out = LABEL.sub(lambda m: _value(m.group(1), m.group(2), m.group(3)), out)
    out = QUOTED.sub(lambda m: _value(m.group(1), m.group(2), m.group(3)), out)
    return out


# free-text fields of a transcription record (obs.transcription); measured fields are never touched
_FREE_TEXT = {"notes", "transcriber_notes", "unreadable_reason", "comment", "raw", "reviewer"}


def scrub_record(data: Any) -> tuple[Any, int]:
    """Copy of a transcription/receipt dict with its free-text fields stripped, and how many fields changed."""
    changed = 0

    def walk(o: Any, key: str | None = None) -> Any:
        nonlocal changed
        if isinstance(o, dict):
            return {k: walk(v, k) for k, v in o.items()}
        if isinstance(o, list):
            return [walk(x, key) for x in o]
        if isinstance(o, str) and key in _FREE_TEXT:
            new = strip_observer_text(o)
            changed += new != o
            return new
        return o

    return walk(data), changed


def observer_text_found(data: Any) -> list[str]:
    """The free-text values of a record that ``scrub_record`` would change (empty when clean)."""
    found: list[str] = []

    def walk(o: Any, key: str | None = None) -> None:
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, k)
        elif isinstance(o, list):
            for x in o:
                walk(x, key)
        elif isinstance(o, str) and key in _FREE_TEXT and strip_observer_text(o) != o:
            found.append(key)

    walk(data)
    return found
