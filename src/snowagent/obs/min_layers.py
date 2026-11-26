"""What a MIN report says about the snowpack, read into a few checkable statements, and whether the site's main
simulated snowpack agrees with each one (ADR-164; owner 2026-11-26: "let's try it out").

MIN reports near a plot often name a crust, facets, surface hoar, a stability-test result with its depth, or the
total snow depth. Most of that is free text (the 2026-27 trip report has no structured snowpack part), so this
module reads it with fixed word rules, the same way every time, and keeps the words each statement came from. It
reads only what the report says; it never adds a layer the report does not name. Reports are at their own slope,
elevation and aspect, so a statement is compared with the plot's simulated profile only when the report is within
``ELEV_TOL_M`` of the plot (when it gives an elevation) and the profile is within ``PROFILE_MAX_H`` of the report.

Kinds read (``ReportStatement.kind``):
- ``hs``: total snow depth ("HS 35 cm", "HS 50-60cm", "snow depth 40 cm"), a range when given as one.
- ``surface_hoar``: at the surface, buried, or unknown.
- ``crust``: melt-freeze/sun/rain/temperature crusts (``wind`` crusts are read but not compared: the column model
  has no wind crust).
- ``facets`` and ``depth_hoar``; ``facets`` "under/below a crust" is checked as facets beneath a crust.
- ``test``: a column, extended column or propagation saw test result ("CTM13 30cm down", "ECTP12 down 40").
Each is ``present`` true, or false when negated in its sentence ("no distinct crust", "no surface hoar").

Old-format reports add their structured fields: the snow depth, the test (failure depth and crystal type) and the
surface conditions (surface hoar, crust). Verdicts: ``agrees``, ``differs`` or ``not_checked`` (with the reason).
This is a light check for the morning meeting, not a score: nothing here feeds the engine, the lab or training.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

READER_VERSION = "min-read-1"
ELEV_TOL_M = 300.0  # a report this far above or below the plot is listed, not compared
PROFILE_MAX_H = 12.0  # the simulated profile used is the nearest one within this many hours of the report
HS_TOL_CM, HS_TOL_FRAC = 15.0, 0.35  # snow depth agrees within the larger of these (slopes vary a lot)
DEPTH_TOL_CM, DEPTH_TOL_FRAC = 10.0, 0.25  # a layer's depth agrees within the larger of these
CRUST_GRAINS = {"MFcr", "IF"}
FACET_GRAINS = {"FC", "FCxr", "FCso", "FCsf", "DH", "DHcp", "DHpr", "DHch", "DHla", "DHxr"}
PERSISTENT_GRAINS = FACET_GRAINS | {"SH", "SHsu", "SHcv", "SHxr"}

Kind = Literal["hs", "surface_hoar", "crust", "facets", "depth_hoar", "test"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReportStatement(_Strict):
    kind: Kind
    present: bool = True
    where: Literal["surface", "buried", "under_crust", "unknown"] = "unknown"
    subtype: str | None = None  # crust: melt_freeze | sun | rain | temperature | wind; test: e.g. "CTM13"
    depth_cm: float | None = None  # below the surface ("down"), when the report says
    value_cm: tuple[float, float] | None = None  # hs: (low, high)
    quote: str  # the report's words (or the structured field) the statement came from


class ReportReading(_Strict):
    reader: str = READER_VERSION
    elevation_m: float | None = None
    statements: list[ReportStatement] = Field(default_factory=list)


class StatementCheck(_Strict):
    kind: Kind
    verdict: Literal["agrees", "differs", "not_checked"]
    said: str  # plain words: what the report says
    model: str  # plain words: what the model shows (or why it was not checked)


# ------------------------------------------------------------------------------------------------ reading
_NUM = r"(\d{1,3}(?:\.\d)?)"
_RANGE = rf"{_NUM}(?:\s*(?:-|–|to)\s*{_NUM})?"
_NEG = re.compile(r"\b(?:no|not|without|none|nor|lack of|absent)\b(?:\W+\w+){0,4}?\W+$", re.I)
_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")
_HS = [re.compile(rf"\bHS\s*(?:of|was|is|=|:)?\s*(?:about|around|approx\.?|~|≈)?\s*{_RANGE}\s*(?:cm)?", re.I),
       re.compile(rf"\b(?:total\s+)?(?:snow\s*pack|snow)\s+depth\s*(?:of|was|is|:)?\s*(?:about|around|~)?\s*{_RANGE}\s*cm",
                  re.I),
       re.compile(rf"\b{_RANGE}\s*cm\s+(?:total\s+)?(?:snow\s*pack|snow\s+depth|HS)\b", re.I)]
_ELEV = re.compile(r"\b([12]\d{3}|3[0-4]\d{2})\s?m\b(?!\w)")
_SH = re.compile(r"\b(?:surface\s*hoar|hoar\s*frost|(?<!depth )hoar|SH)\b", re.I)
_SH_CODE = re.compile(r"\bSH\b")  # the code only in capitals ("sh" is a word)
_CRUST = re.compile(r"\b(?:(melt[\s-]*freeze|sun|solar|rain|temperature|temp|wind|thermal|MF)\s+)?crusts?\b|\bMFcr\b",
                    re.I)
_FACET = re.compile(r"\b(?:facet(?:s|ed|ing)?|sugar(?:y)?|FCs?|FCxr)\b")
_FACET_I = re.compile(r"\b(?:facet(?:s|ed|ing)?|sugar(?:y)?)\b", re.I)
_DH = re.compile(r"\b(?:depth\s*hoar)\b", re.I)
_DH_CODE = re.compile(r"\bDH\b")
_TEST = re.compile(r"\b(ECT|CT|PST|DT|RB)\s*([VEMHNPX])?\s*(\d{1,2}(?:/\d{2,3})?)?\s*(?:\(?[A-Z]{2,3}\)?\s*)?"
                   r"(?:(?:@|at|on)\s*)?(?:[A-Za-z,/()-]+\s+){0,6}?(?:(\d{1,3})\s*cm\s*(?:down|dn|below)|(?:down|dn)\s*(\d{1,3})(?!\d)\s*(?:cm)?)",
                   re.I)
_DOWN = re.compile(r"(?:(\d{1,3})\s*cm\s*(?:down|dn|below\s+(?:the\s+)?surface)|(?:down|dn|buried)\s*(\d{1,3})\s*(?:cm\b|\b(?!\s*(?:m\b|mm|%|°))))",
                   re.I)
_DATED = re.compile(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s*\d{1,2}(?:st|nd|rd|th)?\s+"
                    r"(?:\w+\s+){0,1}?(?:surface\s*hoar|SH|crust|facet\w*|layer|interface)", re.I)
_SURFACE_WORDS = re.compile(r"\b(?:on\s+(?:the\s+)?(?:top|surface)|surface\s+hoar\s+(?:is\s+|was\s+)?(?:forming|growing)|"
                            r"forming|growing|on\s+top|at\s+the\s+surface|on\s+the\s+snow\s+surface)\b", re.I)
_BURIED_WORDS = re.compile(r"\b(?:buried|under(?:neath)?|below|beneath|down)\b", re.I)
_UNDER_CRUST = re.compile(r"\b(?:crust\w*\s+(?:\w+\s+){0,6}?(?:over|above|on(?:\s+top\s+of)?)\s+(?:\w+\s+){0,3}?"
                          r"(?:facet\w*|sugar\w*)|(?:facet\w*|sugar\w*)\s+(?:\w+\s+){0,3}?(?:under|below|beneath)\s+"
                          r"(?:\w+\s+){0,4}?crust|crust\w*\s+(?:\w+\s+){0,4}?with\s+(?:\w+\s+){0,2}?(?:facet\w*|sugar\w*)"
                          r"\s+(?:under|below|beneath))", re.I)


def _sentences(text: str) -> list[str]:
    return [s.strip() for s in _SPLIT.split(text or "") if s and s.strip()]


def _negated(sentence: str, start: int) -> bool:
    return bool(_NEG.search(sentence[:start]))


def _quote(sentence: str, limit: int = 160) -> str:
    return sentence if len(sentence) <= limit else sentence[: limit - 1] + "…"


def _depth(sentence: str) -> float | None:
    m = _DOWN.search(sentence)
    return float(m.group(1) or m.group(2)) if m else None


def _range(m: re.Match) -> tuple[float, float]:
    lo = float(m.group(1))
    hi = float(m.group(2)) if m.group(2) else lo
    return (min(lo, hi), max(lo, hi))


def read_text(text: str) -> tuple[list[ReportStatement], float | None]:
    """Statements and the first plausible elevation (metres) a free-text snowpack note gives."""
    out: list[ReportStatement] = []
    elev = None
    seen: set[tuple] = set()

    def add(st: ReportStatement) -> None:
        key = (st.kind, st.present, st.where) + ((st.subtype, st.depth_cm) if st.kind in ("crust", "test") else ())
        if key not in seen:
            seen.add(key)
            out.append(st)

    for s in _sentences(text):
        if elev is None and (m := _ELEV.search(s)):
            elev = float(m.group(1))
        for rx in _HS:
            if any(x.kind == "hs" for x in out):
                break  # the first snow depth only (later ones are often other slopes)
            if m := rx.search(s):
                lo, hi = _range(m)
                if 0 < hi <= 600:
                    add(ReportStatement(kind="hs", value_cm=(lo, hi), quote=_quote(s)))
                break
        for m in _TEST.finditer(s):
            d = float(m.group(4) or m.group(5))
            code = (m.group(1).upper() + (m.group(2) or "").upper() + (m.group(3) or "")).strip()
            add(ReportStatement(kind="test", subtype=code, depth_cm=d, where="buried", quote=_quote(s)))
        under_crust = bool(_UNDER_CRUST.search(s))
        for m in list(_SH.finditer(s)):
            if m.group(0) == "SH" or m.group(0).lower() == "sh":
                if not _SH_CODE.fullmatch(m.group(0)):
                    continue
            neg = _negated(s, m.start())
            where = "buried" if re.search(r"\bburied\b", s, re.I) or _DATED.search(s) else (
                "surface" if _SURFACE_WORDS.search(s) else ("buried" if _BURIED_WORDS.search(s[m.end():m.end() + 30])
                                                            or _depth(s) else "unknown"))
            add(ReportStatement(kind="surface_hoar", present=not neg, where=where,
                                depth_cm=_depth(s) if where == "buried" else None, quote=_quote(s)))
            break
        for m in _CRUST.finditer(s):
            if not _negated(s, m.start()) and not m.group(1) and any(x.kind == "crust" and x.present for x in out):
                continue  # "the crust was supportive": the crust already read
            word = (m.group(1) or "").lower()
            sub = {"melt freeze": "melt_freeze", "melt-freeze": "melt_freeze", "meltfreeze": "melt_freeze",
                   "mf": "melt_freeze", "sun": "sun", "solar": "sun", "rain": "rain", "temperature": "temperature",
                   "temp": "temperature", "thermal": "temperature", "wind": "wind"}.get(re.sub(r"\s+", " ", word))
            if m.group(0) == "MFcr":
                sub = "melt_freeze"
            if word.startswith("melt"):
                sub = "melt_freeze"
            add(ReportStatement(kind="crust", present=not _negated(s, m.start()), subtype=sub or "unknown",
                                depth_cm=_depth(s), quote=_quote(s)))
        fm = _FACET_I.search(s) or _FACET.search(s)
        if fm:
            neg = _negated(s, fm.start())
            add(ReportStatement(kind="facets", present=not neg, where="under_crust" if under_crust and not neg
                                else "unknown", depth_cm=None if neg else _depth(s), quote=_quote(s)))
        dm = _DH.search(s) or _DH_CODE.search(s)
        if dm:
            add(ReportStatement(kind="depth_hoar", present=not _negated(s, dm.start()), quote=_quote(s)))
    return out, elev


_STRUCT_GRAIN = {"facets": "facets", "depthHoar": "depth_hoar", "surfaceHoar": "surface_hoar"}


def read_report(r) -> ReportReading:
    """A parsed report (``obs.public.PublicObservation``): its structured fields, then each comment's text."""
    st: list[ReportStatement] = []
    if r.hs_cm:  # 0 is the old form's blank
        st.append(ReportStatement(kind="hs", value_cm=(r.hs_cm, r.hs_cm), quote=f"snow depth field: {r.hs_cm:g} cm"))
    if r.test and r.test.depth_cm:
        q = " ".join(x for x in (r.test.initiation, r.test.fracture) if x)
        st.append(ReportStatement(kind="test", subtype=q or None, depth_cm=r.test.depth_cm, where="buried",
                                  quote=f"test field: {q or 'result'} at {r.test.depth_cm:g} cm"
                                        + (f" on {', '.join(r.test.crystal_types)}" if r.test.crystal_types else "")))
        for c in r.test.crystal_types:
            if c in _STRUCT_GRAIN:
                st.append(ReportStatement(kind=_STRUCT_GRAIN[c], where="buried", depth_cm=r.test.depth_cm,
                                          quote=f"test failure layer field: {c}"))
    for s in r.surface:
        if s == "surfaceHoar":
            st.append(ReportStatement(kind="surface_hoar", where="surface", quote="surface field: surface hoar"))
        elif s == "crust":
            st.append(ReportStatement(kind="crust", subtype="unknown", where="surface", quote="surface field: crust"))
    elev = r.elevation_m
    for text in r.comments.values():
        more, e = read_text(text)
        elev = elev if elev is not None else e
        tests = {x.depth_cm for x in st if x.kind == "test"}
        st += [x for x in more if not (x.kind == "hs" and r.hs_cm) and not (x.kind == "test" and x.depth_cm in tests)]
    seen: set[tuple] = set()
    st = [x for x in st if not ((k := (x.kind, x.present, x.where, x.subtype, x.depth_cm)) in seen or seen.add(k))]
    placed = {x.kind for x in st if x.present and x.where != "unknown"}  # "unknown" adds nothing to a placed one
    st = [x for x in st if not (x.present and x.where == "unknown" and x.kind in placed - {"crust"})]
    return ReportReading(elevation_m=elev, statements=st)


# ------------------------------------------------------------------------------------------------ checking
_NAMES = {"FC": "facets", "FCxr": "rounding facets", "DH": "depth hoar", "SH": "surface hoar", "MFcr": "melt-freeze crust",
          "IF": "ice layer", "PP": "new snow", "DF": "settling new snow", "RG": "rounded grains", "MF": "wet grains"}


def _rows(L: list[list]) -> list[dict]:
    """Display layer rows -> dicts with depth below the surface (cm)."""
    hs = L[0][0] if L else 0.0
    return [{"top": r[0], "bot": r[1], "g": r[2], "f": r[7] or 0, "d_top": hs - r[0], "d_bot": hs - r[1]} for r in L]


def _crust(r: dict) -> bool:
    return r["g"] in CRUST_GRAINS or bool(r["f"] & 1)


def _near(r: dict, depth: float) -> bool:
    tol = max(DEPTH_TOL_CM, DEPTH_TOL_FRAC * depth)
    return r["d_top"] - tol <= depth <= r["d_bot"] + tol


def _layers_text(rows: list[dict], pick) -> str:
    """The picked layers in plain words, touching layers of the same name joined ("facets 12-30 cm down")."""
    spans: list[list] = []
    for r in rows:
        if not pick(r):
            continue
        name = _NAMES.get(r["g"], r["g"])
        if spans and spans[-1][0] == name and r["d_top"] - spans[-1][2] <= 0.5:
            spans[-1][2] = r["d_bot"]
        else:
            spans.append([name, r["d_top"], r["d_bot"]])
    if not spans:
        return "none"
    words = [f"{n} {a:.0f} cm down" if round(b) - round(a) < 2 else f"{n} {a:.0f}-{b:.0f} cm down" for n, a, b in spans]
    return "; ".join(words[:3]) + (f" (+{len(words) - 3} more)" if len(words) > 3 else "")


def check_statement(s: ReportStatement, hs: float | None, L: list[list]) -> StatementCheck:
    rows = _rows(L)
    if s.kind == "hs":
        lo, hi = s.value_cm or (0, 0)
        said = f"snow depth {lo:g} cm" if lo == hi else f"snow depth {lo:g}-{hi:g} cm"
        if hs is None:
            return StatementCheck(kind="hs", verdict="not_checked", said=said, model="no simulated depth")
        mid = (lo + hi) / 2
        tol = max(HS_TOL_CM, HS_TOL_FRAC * mid)
        ok = lo - tol <= hs <= hi + tol
        return StatementCheck(kind="hs", verdict="agrees" if ok else "differs", said=said, model=f"{hs:.0f} cm")
    if s.kind == "crust" and s.subtype == "wind":
        return StatementCheck(kind="crust", verdict="not_checked", said=("a" if s.present else "no") + " wind crust",
                              model="the model has no wind crusts")
    if s.kind == "test":
        d = s.depth_cm or 0.0
        said = f"test {s.subtype or 'result'} {d:g} cm down"
        hit = [r for r in rows if (r["g"] in PERSISTENT_GRAINS or _crust(r) or r["f"] & 2) and _near(r, d)]
        return StatementCheck(kind="test", verdict="agrees" if hit else "differs", said=said,
                              model=("weak layer or crust near that depth: " + _layers_text(hit, lambda r: True))
                              if hit else f"no weak layer or crust within {max(DEPTH_TOL_CM, DEPTH_TOL_FRAC * d):.0f} cm "
                                          "of that depth")
    pick, what = {
        "crust": (_crust, "crust"),
        "facets": (lambda r: r["g"] in FACET_GRAINS, "facets"),
        "depth_hoar": (lambda r: r["g"].startswith("DH"), "depth hoar"),
        "surface_hoar": (lambda r: r["g"].startswith("SH"), "surface hoar"),
    }[s.kind]
    base = pick
    where = s.where
    if s.kind == "surface_hoar" and where == "surface":
        pick = lambda r: base(r) and r["d_top"] <= 1.0  # noqa: E731
    elif s.kind == "surface_hoar" and where == "buried":
        pick = lambda r: base(r) and r["d_top"] > 1.0  # noqa: E731
    elif s.kind == "facets" and where == "under_crust":
        tops = [r["d_bot"] for r in rows if _crust(r)]
        pick = lambda r: base(r) and any(r["d_top"] >= t - 0.5 for t in tops)  # noqa: E731
    if s.depth_cm is not None and s.kind != "surface_hoar":
        inner = pick
        pick = lambda r: inner(r) and _near(r, s.depth_cm)  # noqa: E731
    found = [r for r in rows if pick(r)]
    place = {"surface": " on the surface", "buried": " buried", "under_crust": " under a crust"}.get(where, "")
    said = (f"{what}{place}" if s.present else f"no {what}") + (f" {s.depth_cm:g} cm down" if s.depth_cm else "")
    agrees = bool(found) == s.present
    return StatementCheck(kind=s.kind, verdict="agrees" if agrees else "differs", said=said,
                          model=_layers_text(rows, pick) if found else f"no {what}{place}")


def check_report(reading: ReportReading, plot_elev_m: float | None, profile: dict | None,
                 hours_off: float | None) -> dict:
    """Every statement checked against one simulated profile (``{"t", "hs", "L"}``), or the reason none is."""
    reason = None
    if not reading.statements:
        reason = "the report names no snowpack layer or depth"
    elif profile is None or hours_off is None or abs(hours_off) > PROFILE_MAX_H:
        reason = "no simulated profile within 12 hours of the report"
    elif reading.elevation_m is not None and plot_elev_m is not None and \
            abs(reading.elevation_m - plot_elev_m) > ELEV_TOL_M:
        reason = f"the report is at {reading.elevation_m:.0f} m and the plot at {plot_elev_m:.0f} m"
    checks = [] if reason else [check_statement(s, profile.get("hs"), profile.get("L") or [])
                                for s in reading.statements]
    n = {v: sum(c.verdict == v for c in checks) for v in ("agrees", "differs", "not_checked")}
    return {"reader": reading.reader, "profile_t": None if reason or profile is None else profile.get("t"),
            "not_compared": reason, "checks": [c.model_dump() for c in checks], **n}
