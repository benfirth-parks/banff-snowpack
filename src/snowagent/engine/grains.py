"""Mapping of SNOWPACK Swiss grain codes (F1F2F3) to IACS/OGRS classes.

Source verified against the installed engine: ``ElementData::snowType`` in
``snowpack/DataClasses.cc`` returns ``a*100 + b*10 + c`` where ``a``/``b`` are the
primary/secondary classes below and ``c == 2`` marks a melt-freeze crust.
"""

from __future__ import annotations

SWISS_DIGIT_TO_IACS = {
    0: "PPgp",  # graupel (also used by engine for water/technical-snow markers)
    1: "PP",
    2: "DF",
    3: "RG",
    4: "FC",
    5: "DH",
    6: "SH",
    7: "MF",
    8: "IF",
    9: "FCxr",
}

# Persistent grain forms used for *candidate* weak-layer flags (structure only).
PERSISTENT_PRIMARY = {4: "FC", 5: "DH", 6: "SH", 9: "FCxr"}


def decode(code: int | None) -> tuple[str | None, str | None, bool]:
    """Return (primary, secondary, is_melt_freeze_crust) for an engine code."""
    if code is None or code < 0:
        return None, None, False
    a, rem = divmod(int(code), 100)
    b, c = divmod(rem, 10)
    primary = SWISS_DIGIT_TO_IACS.get(a)
    secondary = SWISS_DIGIT_TO_IACS.get(b)
    crust = c == 2
    if crust and primary == "MF":
        primary = "MFcr"
    return primary, secondary, crust


def is_crust(code: int | None) -> bool:
    """Crust = melt-freeze crust (MFcr) or ice formation (IF) as primary form.

    Layers with other primary forms that carry the engine's melt-freeze marker
    (F3 == 2, e.g. faceting former crusts) are not called crusts; see
    ``melt_freeze_marker``.
    """
    primary, _, _ = decode(code)
    return primary in ("MFcr", "IF")


def melt_freeze_marker(code: int | None) -> bool:
    return decode(code)[2]


def candidate_weak_layer_basis(code: int | None) -> str | None:
    if code is None:
        return None
    a = int(code) // 100
    if a in PERSISTENT_PRIMARY:
        return f"engine primary grain form {PERSISTENT_PRIMARY[a]} (persistent class); structure flag only"
    return None
