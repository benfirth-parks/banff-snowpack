"""Grain form -> broad critical class, and which layers count as layers of concern (ADR-057).

ONE reviewable table. The pits carry no explicit "layer of concern" mark, so a layer is of concern when its grain
class is one the build guide names for critical-layer scoring (surface hoar, facets, depth hoar, melt-freeze or rain
crusts and ice layers), or when the observer gave it a name or date tag ("Nov crust", "Jan 24"): observers tag the
layers they track. The owner can change the table; the critical class and the basis are stored with every layer.
"""

from __future__ import annotations

from snowagent.lab.schemas.profile import UNKNOWN_GRAIN, CriticalClass

# IACS 2009 code -> class. Codes not listed are "other". Review here; nothing else decides it.
CRITICAL_CLASS_BY_GRAIN: dict[str, CriticalClass] = {
    **dict.fromkeys(("SH", "SHsu", "SHcv", "SHxr"), CriticalClass.surface_hoar),
    **dict.fromkeys(("FC", "FCso", "FCsf", "FCxr"), CriticalClass.facets),
    **dict.fromkeys(("DH", "DHcp", "DHpr", "DHch", "DHla", "DHxr"), CriticalClass.depth_hoar),
    # melt-freeze crust, rain crust, sun crust, ice formation / ice layer. Not ice columns (IFic) or basal ice (IFbi)
    **dict.fromkeys(("MFcr", "IFrc", "IFsc", "IF", "IFil"), CriticalClass.crust),
}
CONCERN_CLASSES = frozenset({CriticalClass.surface_hoar, CriticalClass.facets, CriticalClass.depth_hoar,
                             CriticalClass.crust})


def critical_class(primary: str | None, secondary: str | None = None) -> tuple[CriticalClass, str | None]:
    """Class of a layer from its primary grain form; the secondary form decides only when no primary form was
    recorded. Returns (class, the form that decided it)."""
    for form in (primary, secondary):
        if form and form != UNKNOWN_GRAIN:
            return CRITICAL_CLASS_BY_GRAIN.get(form, CriticalClass.other), form
    return CriticalClass.unknown, None


def layer_of_concern(primary: str | None, secondary: str | None = None, date_tag: str | None = None
                     ) -> tuple[CriticalClass, bool, list[str]]:
    """(critical class, is layer of concern, basis). Basis entries: ``grain_class:<class>`` (from <form>; with
    ``:secondary`` when the secondary form decided) and ``observer_tag:<tag>``."""
    cls, form = critical_class(primary, secondary)
    basis = []
    if cls in CONCERN_CLASSES:
        basis.append(f"grain_class:{cls.value}:{form}" + (":secondary" if form != primary else ""))
    if date_tag and date_tag.strip():
        basis.append(f"observer_tag:{date_tag.strip()}")
    return cls, bool(basis), basis
