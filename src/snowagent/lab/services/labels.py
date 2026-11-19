"""The lab's names for its evolutions and its competitions (owner, 2026-11-18; ADR-119), kept in one place so every
page, list and report uses the same ones. Evolutions, the lines of models that training evolves, have plain
descriptive names here; competitions, the tests the models meet, carry field units' names. They are display labels
only: no run, case, score or file name depends on them.

A person can give any of them a name of their own on their own computer (the Overview page's "The names used in this
lab"). Those are kept in ``names.json`` in the lab's data folder, which git ignores, so they never reach the
repository: a name that should stay private goes there and not in this file."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from snowagent.lab.storage.paths import LabPaths

# key: (name, what it is)
EVOLUTIONS: dict[str, tuple[str, str]] = {
    "tuned": ("Tuned SNOWPACK", "SNOWPACK with evolved settings"),
    "column": ("Column model", "the lab's own model, with no SNOWPACK"),
    # ADR-120: three more genome structures on the column model, each an agent family of the same name
    "process": ("Process switches", "the column model with whole processes switched on or off"),
    "story": ("Layer life stories", "each layer's form decided from its whole history"),
    "recipe": ("Weather recipes", "layers made from weather patterns, with no heat physics"),
    "column_site": ("Column + site traits", "the lab's own model plus the site traits"),
}
COMPETITIONS: dict[str, tuple[str, str]] = {
    "banff": ("Banff", "the model-change trials"),
    "jasper": ("Jasper", "the Parker Ridge check"),
    "llyk": ("Lake Louise, Yoho and Kootenay", "the live blind test"),
    "mrg": ("Mount Revelstoke and Glacier", "the forecaster rules trial"),
}
COLUMN = "column"  # the agent family of the lab's own model (ADR-118)
OWN_FAMILIES = (COLUMN, "process", "story", "recipe")  # opt-in families that are an evolution of their own
LOCAL_FILE = "names.json"  # in the lab's data folder (git ignores it): this computer's own names
MAX_NAME = 40


def _known(key: str) -> tuple[str, str]:
    return EVOLUTIONS.get(key) or COMPETITIONS[key]


def local_names(paths: LabPaths | None) -> dict[str, str]:
    """This computer's own names, ``{key: name}``, from the lab data folder's ``names.json``. Empty when there is
    no such file or it cannot be read; unknown keys and empty names are left out."""
    if paths is None:
        return {}
    try:
        stored = json.loads((paths.root / LOCAL_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(stored, dict):
        return {}
    known = EVOLUTIONS.keys() | COMPETITIONS.keys()
    return {k: v.strip()[:MAX_NAME] for k, v in stored.items() if k in known and isinstance(v, str) and v.strip()}


def save_local_names(paths: LabPaths, names: dict[str, str]) -> dict[str, str]:
    """Keep this computer's own names (an empty name goes back to the plain one). Written only to the lab's data
    folder, never to a file that git follows. Returns what was kept."""
    known = EVOLUTIONS.keys() | COMPETITIONS.keys()
    kept = {k: v.strip()[:MAX_NAME] for k, v in names.items() if k in known and isinstance(v, str) and v.strip()}
    paths.root.mkdir(parents=True, exist_ok=True)
    (paths.root / LOCAL_FILE).write_text(json.dumps(kept, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return kept


def name(key: str, paths: LabPaths | None = None) -> str:
    """'Tuned SNOWPACK', 'Banff'; this computer's own name for it when ``paths`` is given and one is kept."""
    return local_names(paths).get(key) or _known(key)[0]


def titled(key: str, paths: LabPaths | None = None) -> str:
    """'Jasper: the Parker Ridge check'."""
    return f"{name(key, paths)}: {_known(key)[1]}"


def prefixed(key: str | None, text: str, paths: LabPaths | None = None) -> str:
    """``text`` with its evolution's name in front ('Tuned SNOWPACK: Quick check'), unless it has none or already
    starts with that name ('Column model, no SNOWPACK')."""
    if not key:
        return text
    label = name(key, paths)
    return text if text.lower().startswith(label.lower()) else f"{label}: {text}"


def evolution_of(families: list[str] | None) -> str | None:
    """The evolution a training run or preset belongs to, from the agent families it starts with: the lab's own
    model alone is ``column``, and each other structure built on it (ADR-120) alone is the evolution of its own
    name; anything with the standard families is ``tuned``. None when nothing is known."""
    if not families:
        return None
    kinds = set(families)
    if len(kinds) == 1 and (only := next(iter(kinds))) in OWN_FAMILIES:
        return only
    return "tuned"


def run_evolution(plan: dict | None) -> str | None:
    """``evolution_of`` for a training run's stored plan (its ``initial`` genomes)."""
    initial = (plan or {}).get("initial") or []
    return evolution_of([g.get("family") for g in initial if isinstance(g, dict) and g.get("family")])


def preset_evolution(preset: dict | None) -> str | None:
    """``evolution_of`` for one of the Start a run page's presets (no ``initial``: the standard families)."""
    if not preset:
        return None  # Custom: whatever is chosen under Advanced
    return evolution_of(preset.get("initial") or ["snowpack"])
