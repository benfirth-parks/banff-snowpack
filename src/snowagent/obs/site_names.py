"""Does a pit's printed site/location name name the study plot whose folder holds it? (ADR-049)

The printed name ("Bow Summit Study Plot", "Bow Pass, Alberta", "Wawa Test Profile") is compared with the names
of the folder's study plot: its key, its configured name, ``site_aliases`` and ``printed_site_names`` in
``config/observations.yaml``. The check is conservative. A name is flagged only when no part of it is a name of the
folder's plot AND it names a place, i.e. it has a word that is not generic (study, plot, profile, a province, an
elevation band, a month, digits of a date). A name that is a name of another study plot says which. Spelling slips
("Takakkaww", "Bow CSSummit", "Tack Falls") are accepted by a close match. Nothing is reassigned, moved or excluded
here: the flag only puts the pit on the owner's review list.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

# words that do not name a place on their own
GENERIC = frozenset({
    "study", "stidy", "studyplot", "plot", "plo", "ploy", "sp", "profile", "profiles", "full", "snow", "wx", "weather",
    "site", "station", "pit", "the", "at", "of", "and", "near", "lower", "upper", "main", "area", "alberta", "british",
    "columbia", "ab", "bc", "canada", "tl", "btl", "alp", "treeline", "jan", "feb", "mar", "apr", "may", "jun", "jul",
    "aug", "sep", "sept", "oct", "nov", "dec",
})
FUZZY_MIN_CHARS = 5  # names shorter than this ("ge", "ssv", "tak") must match exactly
FUZZY_RATIO = 0.85


def words(text: str) -> list[str]:
    """Lower-case words; apostrophes dropped ("Goat's" -> "goats"), digits and punctuation are separators."""
    s = text.lower().replace("’", "").replace("'", "")
    return re.sub(r"[^a-z]+", " ", s).split()


def plot_names(cfg: dict) -> dict[str, list[tuple[str, ...]]]:
    """Each study plot's names as word tuples (key, configured name, site_aliases, printed_site_names)."""
    aliases = cfg.get("site_aliases") or {}
    extra = cfg.get("printed_site_names") or {}
    out: dict[str, list[tuple[str, ...]]] = {}
    for key, p in (cfg.get("study_plots") or {}).items():
        names = [key.replace("_", " "), (p or {}).get("name") or "", *aliases.get(key, []), *(extra.get(key) or [])]
        out[key] = sorted({tuple(words(n)) for n in names if words(n)})
    return out


def names_plot(ws: list[str], names: list[tuple[str, ...]]) -> bool:
    """True if a run of words (up to one word longer than a name, spaces ignored) equals or closely matches a name."""
    for name in names:
        target = "".join(name)
        for i in range(len(ws)):
            for j in range(i + 1, min(len(ws), i + len(name) + 1) + 1):
                cand = "".join(ws[i:j])
                if cand == target or (len(target) >= FUZZY_MIN_CHARS
                                      and SequenceMatcher(None, cand, target).ratio() >= FUZZY_RATIO):
                    return True
    return False


def printed_site_flag(name: str | None, site_key: str | None, names: dict[str, list[tuple[str, ...]]]
                      ) -> str | None:
    """QC flag when a printed site/location name clearly names a place other than the folder's study plot.

    ``printed_site_name_is_other_plot:<folder plot>-><other plot>:<name as printed>`` when it is a name of another
    study plot only; ``printed_site_name_not_folder_plot:<folder plot>:<name as printed>`` when it names a place that
    is not a study plot; None when it names the folder's plot, names no place, or there is no plot or name."""
    if not name or not name.strip() or site_key not in names:
        return None
    ws = words(name)
    if names_plot(ws, names[site_key]):
        return None
    printed = " ".join(name.split())
    others = [k for k, v in names.items() if k != site_key and names_plot(ws, v)]
    if others:
        return f"printed_site_name_is_other_plot:{site_key}->{','.join(others)}:{printed}"
    if any(w not in GENERIC for w in ws):
        return f"printed_site_name_not_folder_plot:{site_key}:{printed}"
    return None
