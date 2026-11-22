"""Named-layer check (ADR-138; owner 2026-11-22, "Build a layer-timing check"): observers often name a layer by the
day it formed ("Nov 5 crust complex", "April 10th suncrust", "Jan 7 interface"). Does an agent's forecast hold a
layer of that kind where the named layer sits in the withheld pit?

It reads a finished competition run: its stored predictions and, on the evaluator side only, the withheld pits'
layers with their observer tags (``SnowLayer.date_tag``). No agent sees a tag: tags are forbidden in every visible
file (``benchmark.package.FORBIDDEN_FIELDS``) and this check only reads.

The rules, fixed before any agent was scored with them:

- A tag is a *named layer* when a month can be read from it. The formation date is the month and, when written,
  the day; the year is the pit's winter. The kind comes from the tag's words (sun crust, rain crust, crust, surface
  hoar, facets, depth hoar), otherwise from the layer's recorded grain form.
- *Formed since the last pit the agent saw*: the formation date is after the newest pit visible in the case. Those
  layers the agent had to build from the weather; the others it could carry forward from an earlier pit. A
  month-only date in the same month as that pit is ``unclear``.
- A *hit*: a forecast layer present with probability at least ``PRESENT_P`` (the score's own threshold), of the
  same kind (crust: melt-freeze or ice forms; facets and depth hoar count for each other; surface hoar; any other
  grain form by its class), overlapping the named layer widened by ``TOL_FRAC`` of the snow depth. Depths are
  compared as fractions of each column's own snow depth, as the layering score does, so a forecast that is too
  shallow overall is not punished twice.
- *Chance*: how often a layer of the named layer's thickness put at a random depth would be a hit against the same
  forecast. A hit rate is worth something only above chance.
"""

from __future__ import annotations

import calendar
import json
import re
from dataclasses import dataclass
from datetime import UTC, date, datetime

import numpy as np
import pandas as pd

from snowagent.lab import LAB_DISCLAIMER
from snowagent.lab.storage.paths import LabPaths

PRESENT_P = 0.5
TOL_FRAC = 0.10
CHECK_VERSION = "lab-named-layers-1"
LOCKED_SEASONS = 3

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10,
          "nov": 11, "dec": 12}
_MONTH_RE = re.compile(r"\b(jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?(?:\s*(\d{1,2})"
                       r"(?:st|nd|rd|th)?\b)?", re.I)
# kinds and the forecast grain forms that match them
KINDS = ("sun_crust", "rain_crust", "crust", "surface_hoar", "facets", "other")
CRUST_FORMS = ("MF", "IF")
FACET_FORMS = ("FC", "DH")
KIND_LABEL = {"sun_crust": "sun crusts", "rain_crust": "rain crusts", "crust": "other crusts",
              "surface_hoar": "surface hoar", "facets": "facets and depth hoar", "other": "other named layers"}
SEASON_PART = {10: "early winter (Oct-Nov)", 11: "early winter (Oct-Nov)", 12: "midwinter (Dec-Feb)",
               1: "midwinter (Dec-Feb)", 2: "midwinter (Dec-Feb)", 3: "spring (Mar-Jun)", 4: "spring (Mar-Jun)",
               5: "spring (Mar-Jun)", 6: "spring (Mar-Jun)"}


@dataclass(frozen=True)
class NamedTag:
    month: int
    day: int | None
    kind: str


def tag_kind(tag: str, grain: str | None) -> str:
    t = tag.lower()
    if re.search(r"\bsun|suncr", t):
        return "sun_crust"
    if re.search(r"\brain|\brc\b", t):
        return "rain_crust"
    if re.search(r"crust|\bcr\b|\bcr\?|\bmfcr\b|\bice\b", t):
        return "crust"
    if re.search(r"surface hoar|\bsh\b", t):
        return "surface_hoar"
    if re.search(r"facet|\bfc|depth hoar|\bdh\b|drought", t):
        return "facets"
    g = (grain or "")[:2].upper()
    if g in CRUST_FORMS:
        return "crust"
    if g == "SH":
        return "surface_hoar"
    if g in FACET_FORMS:
        return "facets"
    return "other"


def parse_tag(tag: str | None, grain: str | None = None) -> NamedTag | None:
    """The formation month (and day) and kind of an observer's layer name; None when no month can be read."""
    if not tag or not str(tag).strip():
        return None
    s = str(tag)
    m = _MONTH_RE.search(s)
    if m:
        month = MONTHS[m.group(1).lower()[:3]]
        day = int(m.group(2)) if m.group(2) else None
    elif re.search(r"\bx-?mas\b|christmas", s, re.I):
        month, day = 12, 25
    else:  # numbers: YYMMDD ("121106 RC"), MMDD ("1017"), MM/DD ("11/05")
        n = re.search(r"\b\d{2}(\d{2})(\d{2})\b", s) or re.search(r"\b(\d{2})(\d{2})\b", s) \
            or re.search(r"\b(\d{1,2})/(\d{1,2})\b", s)
        if not n:
            return None
        month, day = int(n.group(1)), int(n.group(2))
        if month not in SEASON_PART:
            return None
    if day is not None and not 1 <= day <= 31:
        day = None
    return NamedTag(month, day, tag_kind(str(tag), grain))


def formation_date(t: NamedTag, pit_day: date) -> tuple[date, bool]:
    """The tag's date in the pit's winter (never after the pit) and whether the day is known (else the 1st)."""
    year = pit_day.year if t.month <= pit_day.month else pit_day.year - 1
    day = min(t.day or 1, calendar.monthrange(year, t.month)[1])  # "Feb 30": the month's last day
    formed = date(year, t.month, day)
    if formed > pit_day:  # a day later in the pit's own month: the year before
        formed = date(year - 1, t.month, min(day, calendar.monthrange(year - 1, t.month)[1]))
    return formed, t.day is not None


def since_last_pit(formed: date, day_known: bool, last_pit: date | None) -> str:
    """``new`` (formed after the newest visible pit), ``carried`` (before it) or ``unclear``."""
    if last_pit is None:
        return "new"
    if day_known:
        return "new" if formed > last_pit else "carried"
    if (formed.year, formed.month) == (last_pit.year, last_pit.month):
        return "unclear"
    return "new" if formed > last_pit else "carried"


# ------------------------------------------------------------------------------------------------- matching


def matches(kind: str, obs_grain: str | None, forecast_forms: list[str]) -> bool:
    """Whether a forecast layer's grain forms make it the named layer's kind, or the grain class the pit recorded for
    it."""
    f = (forecast_forms[0] if forecast_forms else "")[:2].upper()
    if not f:
        return False
    if kind in ("sun_crust", "rain_crust", "crust") and f in CRUST_FORMS:
        return True
    if kind == "surface_hoar" and f == "SH":
        return True
    if kind == "facets" and f in FACET_FORMS:
        return True
    # or the layer as the pit found it ("Nov 6 crust faceted out", recorded FC: facets there are right too)
    g = (obs_grain or "")[:2].upper()
    if not g or g == "UN":
        return False
    return f == g or (g in FACET_FORMS and f in FACET_FORMS) or (g in CRUST_FORMS and f in CRUST_FORMS)


def forecast_spans(pred: dict, kind: str, obs_grain: str | None) -> list[tuple[float, float]]:
    """The matching forecast layers as (top, bottom) fractions of the forecast's snow depth."""
    hs = float(((pred.get("bulk_state") or {}).get("snow_depth_m") or {}).get("p50") or 0.0)
    if hs <= 0:
        return []
    out = []
    for ly in pred.get("layers") or []:
        if float(ly.get("probability_present") or 0.0) < PRESENT_P:
            continue
        if not matches(kind, obs_grain, list(ly.get("grain_form") or [])):
            continue
        top, bot = float(ly["top_depth_m"]["p50"]) / hs, float(ly["bottom_depth_m"]["p50"]) / hs
        out.append((max(0.0, top), min(1.0, bot)))
    return out


def hit_and_chance(spans: list[tuple[float, float]], top: float, bottom: float,
                   tol: float = TOL_FRAC) -> tuple[bool, float]:
    """Whether a span overlaps [top - tol, bottom + tol] (fractions of depth), and the share of random placements of a
    layer that thick in [0, 1] that would."""
    th = max(0.0, min(1.0, bottom - top))
    hit = any(b > top - tol and t < bottom + tol for t, b in spans)
    if not spans:
        return hit, 0.0
    starts = np.linspace(0.0, max(0.0, 1.0 - th), 201)
    ok = np.zeros(len(starts), dtype=bool)
    for t, b in spans:
        ok |= (starts + th > t - tol) & (starts < b + tol)
    return hit, float(ok.mean())


# ------------------------------------------------------------------------------------------------- the pits


def named_layers_by_profile(paths: LabPaths) -> dict[str, list[dict]]:
    """The named layers of every pit (evaluator side), by profile id: depths as fractions of the pit's snow depth."""
    from snowagent.lab.storage.tables import read_table

    prof = read_table(paths.profiles, columns=["profile_id", "observed_at", "snow_depth_m"])
    when = {r.profile_id: pd.Timestamp(r.observed_at) for r in prof.itertuples()}
    hs = {r.profile_id: r.snow_depth_m for r in prof.itertuples()}
    lay = read_table(paths.layers)
    if "date_tag" not in lay.columns or not len(lay):
        return {}
    if "comment" not in lay.columns:
        lay = lay.assign(comment=None)
    out: dict[str, list[dict]] = {}
    for r in lay.to_dict("records"):
        tag = r["date_tag"] if isinstance(r["date_tag"], str) and r["date_tag"].strip() else None
        if tag is None:  # structured records keep the observer's name in the layer comment ("Nov 6 rc")
            c = r.get("comment")
            tag = c if isinstance(c, str) and _MONTH_RE.search(c) else None
        if tag is None:
            continue
        pid = r["profile_id"]
        h, t = hs.get(pid), when.get(pid)
        if t is None or h is None or not h == h or h <= 0:
            continue
        nt = parse_tag(tag, r.get("grain_primary"))
        if nt is None:
            continue
        formed, known = formation_date(nt, t.date())
        out.setdefault(pid, []).append({
            "tag": str(tag).strip()[:120], "kind": nt.kind, "formed": formed.isoformat(), "day_known": known,
            "grain": r.get("grain_primary"), "hardness": r.get("hardness"),
            "top_m": float(r["top_depth_m"]), "bottom_m": float(r["bottom_depth_m"]), "pit_hs_m": float(h),
            "top": float(r["top_depth_m"]) / h, "bottom": float(r["bottom_depth_m"]) / h})
    return out


def named_layer_table(paths: LabPaths) -> pd.DataFrame:
    """Every named layer with its pit, for studies (crust formation and decay): plot, pit time, winter, depth."""
    from snowagent.lab.storage.tables import read_table

    prof = read_table(paths.profiles, columns=["profile_id", "observed_at", "site_code", "plot_id"])
    meta = {r.profile_id: r for r in prof.itertuples()}
    rows = []
    for pid, items in named_layers_by_profile(paths).items():
        p = meta.get(pid)
        for x in items:
            t = pd.Timestamp(p.observed_at)
            season = f"{t.year - 1}-{t.year}" if t.month < 8 else f"{t.year}-{t.year + 1}"
            rows.append({"profile_id": pid, "plot_id": str(p.plot_id), "pit_time_utc": t.isoformat(), "season": season}
                        | {k: v for k, v in x.items() if k not in ("top", "bottom")})
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------------- the check


def score_case(pred: dict, named: list[dict], last_pit: date | None) -> list[dict]:
    rows = []
    for x in named:
        spans = forecast_spans(pred, x["kind"], x["grain"])
        hit, chance = hit_and_chance(spans, x["top"], x["bottom"])
        formed = date.fromisoformat(x["formed"])
        rows.append({"tag": x["tag"], "kind": x["kind"], "formed": x["formed"], "month": formed.month,
                     "since_last_pit": since_last_pit(formed, x["day_known"], last_pit), "hit": hit,
                     "chance": chance})
    return rows


def check_rows(paths: LabPaths, run_id: str) -> tuple[pd.DataFrame, dict]:
    """One row per (case, named layer, agent) of a finished competition run."""
    from snowagent.lab.storage.tables import read_table

    run_dir = paths.outputs / "competitions" / run_id
    if not (run_dir / "cases").is_dir():
        raise FileNotFoundError(f"no competition run {run_id!r} with stored cases")
    named = named_layers_by_profile(paths)
    prof = read_table(paths.profiles, columns=["profile_id", "observed_at"])
    when = {r.profile_id: pd.Timestamp(r.observed_at) for r in prof.itertuples()}
    labels: dict[str, str] = {}
    rows = []
    for f in sorted((run_dir / "cases").glob("*.json")):
        c = json.loads(f.read_text())
        pid = c.get("target_profile_id")
        if not c.get("truth_used") or pid not in named:
            continue
        seen = [when[v].date() for v in c.get("visible_profile_ids") or [] if v in when]
        last = max(seen) if seen else None
        site = next((r.get("site_code") for r in c.get("rows") or []), None)
        for r in c.get("rows") or []:
            labels.setdefault(r["agent_id"], r.get("label") or r["agent_id"])
            pred = (c.get("predictions") or {}).get(r["agent_id"])
            if r.get("status") != "ok" or pred is None:
                continue
            for x in score_case(pred, named[pid], last):
                rows.append({"case_id": c["case_id"], "profile_id": pid, "season": c["season"], "site": site,
                             "agent_id": r["agent_id"]} | x)
    return pd.DataFrame(rows), labels


def _rate(d: pd.DataFrame) -> dict | None:
    if not len(d):
        return None
    return {"n": int(len(d)), "hit": float(d["hit"].mean()), "chance": float(d["chance"].mean())}


def _winter_range(d: pd.DataFrame, seed: int = 0, n: int = 2000) -> tuple[float, float] | None:
    """90% range of (hit - chance) when whole winters are drawn again (pits of a winter are not independent)."""
    seasons = sorted(d["season"].unique())
    if len(seasons) < 3:
        return None
    g = {s: (float(x["hit"].sum() - x["chance"].sum()), len(x)) for s, x in d.groupby("season")}
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(n):
        pick = rng.choice(seasons, len(seasons))
        num = sum(g[s][0] for s in pick)
        den = sum(g[s][1] for s in pick)
        vals.append(num / den)
    return float(np.quantile(vals, 0.05)), float(np.quantile(vals, 0.95))


def summarise(df: pd.DataFrame, labels: dict[str, str], locked: list[str]) -> dict:
    out = {}
    for aid, d in df.groupby("agent_id"):
        parts = {"all": d, "training winters": d[~d["season"].isin(locked)],
                 "locked winters": d[d["season"].isin(locked)]}
        a = {"label": labels.get(aid, aid), "subsets": {k: _rate(v) for k, v in parts.items()}}
        a["range90"] = _winter_range(d)
        a["by_kind"] = {k: _rate(d[d["kind"] == k]) for k in KINDS}
        a["by_since"] = {k: _rate(d[d["since_last_pit"] == k]) for k in ("new", "carried", "unclear")}
        a["by_part"] = {p: _rate(d[d["month"].map(SEASON_PART) == p]) for p in dict.fromkeys(SEASON_PART.values())}
        out[aid] = a
    return out


def run_named_layer_check(paths: LabPaths, run_id: str) -> dict:
    """Score a competition run's agents on the named layers; writes ``named_layer_checks/<run>/`` and returns the
    result."""
    from snowagent.lab.benchmark.loader import case_dirs, read_manifest

    df, labels = check_rows(paths, run_id)
    seasons = sorted({read_manifest(d).season for d in case_dirs(paths, "all")})
    locked = seasons[-LOCKED_SEASONS:] if len(seasons) >= LOCKED_SEASONS + 2 else []
    res = {"check_version": CHECK_VERSION, "competition_run": run_id, "created_utc": datetime.now(UTC).isoformat(),
           "present_p": PRESENT_P, "tolerance_frac": TOL_FRAC, "locked_seasons": locked,
           "named_layers": int(df[["profile_id", "tag", "formed"]].drop_duplicates().shape[0]) if len(df) else 0,
           "pits": int(df["profile_id"].nunique()) if len(df) else 0,
           "cases": int(df["case_id"].nunique()) if len(df) else 0,
           "agents": summarise(df, labels, locked) if len(df) else {}, "label": LAB_DISCLAIMER}
    out = paths.outputs / "named_layer_checks" / run_id
    out.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out / "rows.parquet", index=False)
    (out / "result.json").write_text(json.dumps(res, indent=1))
    (out / "report.md").write_text("\n".join(report_lines(res)) + "\n")
    return res


def _pct(x: float | None) -> str:
    return "-" if x is None else f"{100 * x:.0f}%"


def report_lines(res: dict) -> list[str]:
    lines = [f"# Named-layer check of competition {res['competition_run']}", "", f"_{res['label']}_", "",
             f"Named layers: {res['named_layers']} in {res['pits']} withheld pits whose observer wrote the day a "
             "layer formed beside it (\"Nov 5 crust\", \"April 10th suncrust\"), forecast in "
             f"{res.get('cases', res['pits'])} cases (a pit is the target of a 3-day and a next-pit case). The counts "
             "n below are per case.", "",
             "**How to read it.** A hit: the forecast has a layer of the same kind (crusts with crusts, facets "
             "with facets or depth hoar, surface hoar with surface hoar) where the named layer sits, within a tenth "
             "of the snow depth. \"Chance\" is how often a layer of that thickness put at a random depth would hit "
             "the same forecast. \"Formed since the last pit\" are the layers the agent had to build from the "
             "weather alone; the others it could carry forward from an earlier pit.", ""]
    if not res["agents"]:
        return lines + ["No named layer in this run's scored pits."]
    agents = list(res["agents"].values())
    head = "| | " + " | ".join(a["label"] for a in agents) + " |"
    sep = "| --- |" + " --- |" * len(agents)

    def row(name: str, get) -> str:
        cells = []
        for a in agents:
            r = get(a)
            cells.append("-" if not r else f"{_pct(r['hit'])} (chance {_pct(r['chance'])}, n {r['n']})")
        return f"| {name} | " + " | ".join(cells) + " |"

    lines += [head, sep]
    for k in ("all", "training winters", "locked winters"):
        lines.append(row(k.capitalize(), lambda a, k=k: a["subsets"].get(k)))
    lines.append(row("Formed since the last pit", lambda a: a["by_since"].get("new")))
    lines.append(row("Carried from an earlier pit", lambda a: a["by_since"].get("carried")))
    for k in KINDS:
        lines.append(row(KIND_LABEL[k].capitalize(), lambda a, k=k: a["by_kind"].get(k)))
    for p in dict.fromkeys(SEASON_PART.values()):
        lines.append(row(f"Formed in {p}", lambda a, p=p: a["by_part"].get(p)))
    lines.append("")
    for a in agents:
        r90 = a.get("range90")
        if r90:
            lines.append(f"- {a['label']}: hit rate above chance, 90% range by winter "
                         f"{100 * r90[0]:+.0f} to {100 * r90[1]:+.0f} points.")
    return lines


__all__ = ["NamedTag", "check_rows", "formation_date", "hit_and_chance", "matches", "named_layer_table",
           "named_layers_by_profile", "parse_tag", "report_lines", "run_named_layer_check", "score_case",
           "since_last_pit", "tag_kind"]
