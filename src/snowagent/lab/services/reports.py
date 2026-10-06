"""Analysis reports the app builds and offers as a download (ADR-082): a training run's report compares the chosen
agent of a round with its family's default agent on the same cases, round by round, by score component, plot, case
type and season, and on the monitor season; lists the settings evolution changed; and ends with next steps chosen by
fixed rules. Every number comes from the run's committed files; nothing is invented or predicted here.

A report is a list of blocks rendered to Markdown or to one self-contained HTML file (inline style and chart, no
external files) that opens in any browser, prints to PDF and opens in Word."""

from __future__ import annotations

import html
import json
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

from snowagent.lab import LAB_DISCLAIMER
from snowagent.lab.schemas.genome import GenomeSpec
from snowagent.lab.services.names import nickname
from snowagent.lab.storage.paths import LabPaths
from snowagent.lab.training.lineage import lineage_for
from snowagent.lab.training.loop import committed_rounds, load_round, round_dir, training_root
from snowagent.lab.training.loso import list_checks, load_check
from snowagent.lab.ui.genes import gene_rows, plain_name

__all__ = ["Block", "Report", "plain_change", "plain_changes", "report_filename", "save_report", "training_report"]

TIE = 0.005  # a per-case composite change smaller than this counts as a tie
EDGE = 0.05  # a gene within this share of its range from a limit is "near its limit"
COMPONENTS = [("composite", "Composite (leaderboard)"), ("snow_depth", "Snow depth (weight 0.20)"),
              ("layer_structure", "Layer structure (0.30)"), ("critical_layers", "Critical layers (0.25)"),
              ("uncertainty", "Uncertainty (0.15)"), ("robustness", "Robustness (0.10)")]
DIAGNOSTICS = [("depth_mae_m", "Mean depth error (m, lower is better)", "lb"),
               ("depth_bias_m", "Mean depth bias (m, + is too deep)", "lb"),
               ("depth_coverage", "Observed depth inside the predicted range", "lb"),
               ("match_f1", "Layer match F1", "case"), ("grain_agreement", "Grain type agreement", "case"),
               ("hardness_agreement", "Hardness agreement", "case"),
               ("brier", "Layer probability Brier score (lower is better)", "case"),
               ("predicted_layers", "Layers predicted per case", "case"),
               ("observed_layers", "Layers observed per case", "case")]


# --------------------------------------------------------------------------------------------- the document


@dataclass
class Block:
    kind: str  # "h2", "p", "bullets", "table", "chart"
    data: object


@dataclass
class Report:
    title: str
    meta: list[tuple[str, str]]
    blocks: list[Block] = field(default_factory=list)

    def h2(self, text: str) -> None:
        self.blocks.append(Block("h2", text))

    def p(self, text: str) -> None:
        self.blocks.append(Block("p", text))

    def bullets(self, items: list[str]) -> None:
        if items:
            self.blocks.append(Block("bullets", items))

    def table(self, df: pd.DataFrame, note: str | None = None) -> None:
        self.blocks.append(Block("table", (df, note)))

    def chart(self, title: str, x: list, series: dict[str, list]) -> None:
        self.blocks.append(Block("chart", (title, x, series)))

    # ---- Markdown
    def to_markdown(self) -> str:
        out = [f"# {self.title}", "", f"> {LAB_DISCLAIMER}", ""]
        out += [f"- **{k}:** {v}" for k, v in self.meta] + [""]
        for b in self.blocks:
            if b.kind == "h2":
                out += [f"## {b.data}", ""]
            elif b.kind == "p":
                out += [str(b.data), ""]
            elif b.kind == "bullets":
                out += [f"- {x}" for x in b.data] + [""]
            elif b.kind == "table":
                df, note = b.data
                out += [_md_table(df), ""] + ([f"_{note}_", ""] if note else [])
            elif b.kind == "chart":
                title, x, series = b.data
                out += [f"_{title}: chart in the HTML version; the numbers are in the table below._", ""]
        return "\n".join(out).rstrip() + "\n"

    # ---- HTML
    def to_html(self) -> str:
        body = [f"<h1>{_inline(self.title)}</h1>", f'<p class="disclaimer">{html.escape(LAB_DISCLAIMER)}</p>',
                "<dl>" + "".join(f"<dt>{html.escape(k)}</dt><dd>{_inline(v)}</dd>" for k, v in self.meta) + "</dl>"]
        for b in self.blocks:
            if b.kind == "h2":
                body.append(f"<h2>{_inline(b.data)}</h2>")
            elif b.kind == "p":
                body.append(f"<p>{_inline(b.data)}</p>")
            elif b.kind == "bullets":
                body.append("<ul>" + "".join(f"<li>{_inline(x)}</li>" for x in b.data) + "</ul>")
            elif b.kind == "table":
                df, note = b.data
                body.append(_html_table(df) + (f'<p class="note">{_inline(note)}</p>' if note else ""))
            elif b.kind == "chart":
                body.append(_svg_chart(*b.data))
        return (f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" "
                f"content=\"width=device-width, initial-scale=1\"><title>{html.escape(self.title)}</title>"
                f"<style>{CSS}</style></head><body><main>{''.join(body)}</main></body></html>\n")


CSS = """
body{background:#fff;color:#1d2433;font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;margin:0}
main{max-width:920px;margin:0 auto;padding:24px 16px 48px}
h1{font-size:1.6em;margin:0 0 8px}h2{font-size:1.2em;margin:28px 0 8px;border-bottom:1px solid #d5dbe5;padding-bottom:4px}
.disclaimer{background:#fff6e0;border:1px solid #f0d58a;padding:8px 12px;border-radius:6px;font-size:.9em}
dl{display:grid;grid-template-columns:max-content 1fr;gap:2px 12px;font-size:.9em}dt{font-weight:600}dd{margin:0}
table{border-collapse:collapse;margin:8px 0;font-size:.88em;width:100%}
th,td{border-bottom:1px solid #e3e7ee;padding:4px 8px;text-align:left;vertical-align:top}
th{background:#f3f5f9}td.num{text-align:right;font-variant-numeric:tabular-nums}
.note{font-size:.85em;color:#5a6475}.chart{max-width:100%;height:auto}code{background:#f3f5f9;padding:0 3px}
@media print{main{max-width:none;padding:0}h2{break-after:avoid}table{break-inside:auto}tr{break-inside:avoid}}
"""


def _inline(text: str) -> str:
    t = html.escape(str(text))
    t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
    return re.sub(r"`(.+?)`", r"<code>\1</code>", t)


def _cell(v, signed: bool = False) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return ""
    if isinstance(v, float):
        if signed:
            return f"{v:+.4f}"
        return f"{v:.4g}" if abs(v) >= 1 else f"{v:.4f}"
    return str(v)


def _md_table(df: pd.DataFrame) -> str:
    cols = [str(c) for c in df.columns]
    rows = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    signed = [c == "change" for c in cols]
    rows += ["| " + " | ".join(_cell(v, sg).replace("|", "/") for v, sg in zip(r, signed, strict=True)) + " |"
             for r in df.itertuples(index=False)]
    return "\n".join(rows)


def _html_table(df: pd.DataFrame) -> str:
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in df.columns)
    signed = [str(c) == "change" for c in df.columns]
    rows = []
    for r in df.itertuples(index=False):
        rows.append("<tr>" + "".join(
            f"<td class=\"num\">{_cell(v, sg)}</td>" if isinstance(v, int | float) and not isinstance(v, bool)
            else f"<td>{_inline(_cell(v))}</td>" for v, sg in zip(r, signed, strict=True)) + "</tr>")
    return f"<table><thead><tr>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table>"


def _scale(v: float, lo: float, hi: float, a: float, b: float) -> float:
    return a + (v - lo) / (hi - lo) * (b - a)


def _svg_chart(title: str, x: list, series: dict[str, list]) -> str:
    """A small line chart (one panel per series, so different scales stay readable)."""
    colours = ["#2f6db5", "#c4572e", "#3b8f5a", "#7a4fb0"]
    w, h, pad = 860, 150, 44
    panels = []
    x0 = min(x) if x else 0
    x1 = max(x) if x and max(x) > x0 else x0 + 1
    for k, (name, ys) in enumerate(series.items()):
        pts = [(xi, yi) for xi, yi in zip(x, ys, strict=False) if yi is not None and not math.isnan(yi)]
        if not pts:
            continue
        lo = min(p[1] for p in pts)
        hi = max(max(p[1] for p in pts), lo + 1e-6)
        xy = [(_scale(a, x0, x1, pad, w - pad), _scale(b, lo, hi, h - 24, 24)) for a, b in pts]
        colour = colours[k % len(colours)]
        line = " ".join(f"{a:.1f},{b:.1f}" for a, b in xy)
        dots = "".join(f'<circle cx="{a:.1f}" cy="{b:.1f}" r="2.5" fill="{colour}"/>' for a, b in xy)
        panels.append(
            f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="{html.escape(name)}">'
            f'<text x="{pad}" y="14" font-size="12" fill="#1d2433">{html.escape(name)}</text>'
            f'<text x="4" y="28" font-size="10" fill="#5a6475">{hi:.3g}</text>'
            f'<text x="4" y="{h - 20}" font-size="10" fill="#5a6475">{lo:.3g}</text>'
            f'<line x1="{pad}" y1="{h - 24}" x2="{w - pad}" y2="{h - 24}" stroke="#d5dbe5"/>'
            f'<text x="{pad}" y="{h - 8}" font-size="10" fill="#5a6475">round {x0}</text>'
            f'<text x="{w - pad}" y="{h - 8}" font-size="10" fill="#5a6475" text-anchor="end">round {x1}</text>'
            f'<polyline points="{line}" fill="none" stroke="{colour}" stroke-width="2"/>{dots}</svg>')
    return f"<figure><figcaption class=\"note\">{html.escape(title)}</figcaption>{''.join(panels)}</figure>"


# --------------------------------------------------------------------------------------------- training run report


def _json(f: Path) -> dict | None:
    try:
        return json.loads(f.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def _scores(run_dir: Path, r: int, agent_id: str) -> pd.DataFrame:
    df = pd.read_parquet(round_dir(run_dir, r) / "scores.parquet")
    return df[df["agent_id"] == agent_id].set_index("case_id")


def _fmt_s(seconds: float | None) -> str:
    if not seconds:
        return ""
    m = round(seconds / 60)
    return f"{m // 60} h {m % 60:02d} min" if m >= 60 else f"{m} min"


def _r(v, n: int = 4):
    return None if v is None or (isinstance(v, float) and math.isnan(v)) else round(float(v), n)


def _pts(v: float) -> str:
    """A 0-1 score as points out of 100, the way the plain report states every score."""
    return f"{100 * v:.1f}"


def _dpts(v: float) -> str:
    return f"{100 * v:+.1f} points"


def _change_table(a: pd.DataFrame, b: pd.DataFrame, by: pd.Series, name: str) -> pd.DataFrame:
    d = pd.DataFrame({name: by, "base": a["composite"].astype(float), "best": b["composite"].astype(float)})
    g = d.groupby(name).agg(cases=("base", "size"), base=("base", "mean"), best=("best", "mean")).reset_index()
    g["change"] = g["best"] - g["base"]
    return g.round(4)


def _gene_spec(gene: str, spec: GenomeSpec):
    return next((blk[gene] for blk in spec.blocks.values() if gene in blk), None)


def _near_limit(gene: str, value, spec: GenomeSpec) -> str | None:
    g = _gene_spec(gene, spec)
    if g is None or g.kind == "choice" or not isinstance(value, int | float):
        return None
    span = g.max - g.min
    if value >= g.max - EDGE * span:
        return "upper"
    if value <= g.min + EDGE * span:
        return "lower"
    return None


def _size(gene: str, default, value, spec: GenomeSpec) -> float:
    """How far a gene moved, as a share of its allowed range (a changed choice counts as 1)."""
    g = _gene_spec(gene, spec)
    if g is None or g.kind == "choice" or not isinstance(value, int | float) or not isinstance(default, int | float):
        return 1.0
    return abs(value - default) / (g.max - g.min)


PLOT_NAMES = {"BOW": "Bow Summit", "GOAT": "Goat's Eye", "SIMP": "Simpson"}
CASE_TYPES = {"forecast_h72": "3-day forecasts", "next_pit": "predicting the next pit"}
PLAIN_COMPONENT = {"snow_depth": "getting total snow depth right", "layer_structure": "getting the layers right",
                   "critical_layers": "finding the weak layers", "uncertainty": "being honest about its uncertainty"}


def _span(seasons: list[str]) -> str:
    """'2023-2024', '2024-2025', '2025-2026' -> 'winters 2023-24 to 2025-26'."""
    s = sorted(seasons)
    return f"winter {_winter(s[0])}" if len(s) == 1 else f"winters {_winter(s[0])} to {_winter(s[-1])}"


def _winter(season: str) -> str:
    """'2025-2026' -> '2025-26'."""
    a, _, b = str(season).partition("-")
    return f"{a}-{b[2:]}" if len(a) == 4 and len(b) == 4 else str(season)


def _more_less(ratio: float, more: str, less: str) -> str:
    return f"{abs(ratio - 1) * 100:.0f}% {more if ratio > 1 else less}"


def plain_change(gene: str, default, value) -> str:
    """One sentence a non-specialist can read for one changed setting."""
    up = isinstance(value, int | float) and isinstance(default, int | float) and value > default
    if gene.startswith("sp_precip_mult_"):
        plot = PLOT_NAMES.get(gene.rsplit("_", 1)[1].upper(), gene)
        return f"Assumes {_more_less(value / default, 'more', 'less')} snowfall at {plot} than the gauge measured."
    if gene == "sp_wind_mult":
        return f"Treats the wind as {_more_less(value / default, 'stronger', 'weaker')} than measured."
    if gene == "sp_rain_snow_mid_c":
        return (f"Lets precipitation fall as snow up to a {'warmer' if up else 'colder'} temperature "
                f"({default:.1f} → {value:.1f} °C).")
    if gene == "sp_rain_snow_width_k":
        return f"Makes the switch from snow to rain {'more gradual' if up else 'sharper'}."
    if gene == "presence_confidence":
        return (f"Is {'more' if up else 'less'} sure that the layers it predicts are really there "
                f"({default:.0%} → {value:.0%} sure).")
    if gene == "hardness_merge_tol":
        return f"Splits the snowpack into {'coarser' if up else 'finer'} layers."
    if gene in ("depth_spread_frac", "depth_spread_floor_m"):
        return f"Gives a {'wider' if up else 'narrower'} range for total snow depth."
    if gene == "boundary_spread_m":
        return f"Gives a {'wider' if up else 'narrower'} range for where each layer starts and ends."
    if gene == "sp_hn_density":
        return f"Uses a {'fixed' if value == 'FIXED' else 'calculated'} density for new snow."
    if gene == "sp_hn_density_parameterization":
        return f"Switches the new-snow density formula ({default} → {value})."
    if gene == "sp_hn_density_fixed_kg_m3":
        return f"Sets fixed new-snow density to {value:.0f} kg/m³ (was {default:.0f})."
    if gene == "sp_hoar_min_size_buried_mm":
        return (f"Keeps {'only larger' if up else 'smaller'} crystals of buried surface hoar, a common weak layer, "
                f"as a layer of their own ({default:g} → {value:.1f} mm).")
    if gene == "sp_hoar_density_buried_kg_m3":
        return f"Makes buried surface hoar {'denser' if up else 'lighter'} ({default:.0f} → {value:.0f} kg/m³)."
    if gene.startswith("sp_hoar_thresh"):
        return "Adjusts the weather in which surface hoar can form."
    if gene == "sp_roughness_length_m":
        return f"Makes the snow surface slightly {'rougher' if up else 'smoother'}."
    if isinstance(default, int | float) and isinstance(value, int | float):
        return f"Changes {plain_name(gene)} ({default:g} → {value:.3g})."
    return f"Changes {plain_name(gene)} ({default} → {value})."


EDGE_NAMES = {"hardness_merge_tol": "how finely it splits layers", "presence_confidence": "how sure it is of its layers"}


def plain_changes(changed: dict[str, list], spec: GenomeSpec, min_size: float = 0.05) -> list[str]:
    """Plain sentences for the changed settings, biggest change first; related settings share one sentence and
    changes under ``min_size`` of their range are counted, not listed."""
    ch = dict(changed)
    out: list[tuple[float, str]] = []
    if ch.get("sp_hn_density", [None, None])[1] == "FIXED":
        size = _size("sp_hn_density", *ch["sp_hn_density"], spec)
        kg = ch.get("sp_hn_density_fixed_kg_m3", [None, None])[1]
        kg = kg if kg is not None else _gene_spec("sp_hn_density_fixed_kg_m3", spec).default
        out.append((size, f"Uses a fixed density for new snow ({kg:.0f} kg/m³) instead of calculating it from "
                          "the weather."))
        for g in ("sp_hn_density", "sp_hn_density_parameterization", "sp_hn_density_fixed_kg_m3"):
            ch.pop(g, None)
    spread = [g for g in ("depth_spread_frac", "depth_spread_floor_m") if g in ch]
    if len(spread) == 2:
        frac_up = ch["depth_spread_frac"][1] > ch["depth_spread_frac"][0]
        floor_up = ch["depth_spread_floor_m"][1] > ch["depth_spread_floor_m"][0]
        size = max(_size(g, *ch[g], spec) for g in spread)
        if frac_up == floor_up:
            text = f"Gives a {'wider' if frac_up else 'narrower'} range for total snow depth."
        else:
            text = (f"Gives a {'wider' if frac_up else 'narrower'} range for total snow depth when the snow is deep, "
                    f"and a {'wider' if floor_up else 'narrower'} one when it is shallow.")
        out.append((size, text))
        for g in spread:
            ch.pop(g)
    hoar = [g for g in ch if g.startswith("sp_hoar_thresh")]
    if hoar:
        size = max(_size(g, *ch[g], spec) for g in hoar)
        out.append((size, plain_change(hoar[0], *ch[hoar[0]])))
        for g in hoar:
            ch.pop(g)
    out += [(_size(g, *v, spec), plain_change(g, *v)) for g, v in ch.items()]
    out.sort(key=lambda t: -t[0])
    big = [t for s_, t in out if s_ >= min_size]
    small = len(out) - len(big)
    return big + ([f"Makes {small} other small adjustment{'s' if small != 1 else ''}."] if small else [])


def _gather(paths: LabPaths, run_id: str, round_no: int | None, rank: int) -> dict:
    """Everything the report states, read from the run's committed files."""
    run_dir = training_root(paths) / run_id
    rounds = committed_rounds(run_dir)
    if not rounds:
        raise ValueError(f"training run {run_id} has no committed round yet")
    r = int(round_no or rounds[-1])
    if r not in rounds:
        raise ValueError(f"round {r} of {run_id} is not committed (rounds: {rounds[0]}-{rounds[-1]})")
    meta = _json(run_dir / "run.json") or {}
    rd, r1 = load_round(run_dir, r), load_round(run_dir, rounds[0])
    ranked = rd["leaderboard"]["ranked"]
    if not 1 <= rank <= len(ranked):
        raise ValueError(f"rank {rank} is outside round {r}'s leaderboard (1-{len(ranked)})")
    best = ranked[rank - 1]
    lb_r = {x["agent_id"]: x for x in rd["leaderboard"]["overall"]}
    base = next((x for x in r1["leaderboard"]["overall"] if x["label"] == f"{best['family']}-default"), None)
    base_is_default = base is not None
    if base is None:
        base = {x["agent_id"]: x for x in r1["leaderboard"]["overall"]}[r1["leaderboard"]["ranked"][0]["agent_id"]]
    sb, sa = _scores(run_dir, r, best["agent_id"]), _scores(run_dir, rounds[0], base["agent_id"])
    common = sa.index.intersection(sb.index)
    sa, sb = sa.loc[common], sb.loc[common]
    trace = []
    for k in rounds:
        info = load_round(run_dir, k)["round"]
        g = info.get("gap") or {}
        ev = info.get("eval") or {}
        trace.append({"round": k, "best agent": info["best"]["label"], "best composite": info["best"]["composite"],
                      "gap": g.get("gap"), "gap flag": "FLAG" if g.get("flag") else "",
                      "wall time": _fmt_s(info.get("wall_s")), "wall_s": info.get("wall_s"),
                      "screen_s": ((info.get("screen") or {}).get("eval") or {}).get("wall_s"),
                      "engine_runs": ev.get("engine_runs"), "engine_s": ev.get("engine_s"),
                      "committed_at": info.get("committed_at")})
    gap_r = rd["round"].get("gap") or {}
    monitor = gap_r.get("monitor_season") or (r1["round"].get("gap") or {}).get("monitor_season")
    try:
        changed = lineage_for(paths, best["genome_hash"], run_id)[0].get("changed_vs_default") or {}
    except KeyError:
        changed = {}
    checks = []
    for cid in list_checks(paths):
        try:
            chk = load_check(paths, cid)
        except (FileNotFoundError, json.JSONDecodeError):
            continue
        ref = str(chk["check"].get("plan", {}).get("genome_ref", ""))
        if ref.startswith(run_id) or best["genome_hash"] in ref:
            checks.append((cid, chk))
    return {"run_id": run_id, "run_dir": run_dir, "rounds": rounds, "r": r, "rank": rank, "meta": meta,
            "plan": meta.get("plan") or {}, "ranked": ranked, "best": best, "B": lb_r[best["agent_id"]], "A": base,
            "base_is_default": base_is_default, "sa": sa, "sb": sb,
            "diff": sb["composite"].astype(float) - sa["composite"].astype(float), "trace": pd.DataFrame(trace),
            "gap_r": gap_r, "monitor": monitor, "changed": changed, "checks": checks,
            "status": _json(run_dir / "status.json") or {}, "locked": _locked(run_dir, rounds, r, best, base)}


def _locked(run_dir: Path, rounds: list[int], r: int, best: dict, base: dict) -> dict | None:
    """ADR-083: the chosen agent and the comparison agent on the run's locked test winters, case by case (the
    agent from round r's locked scores, the comparison agent from round 1's, where every initial agent was tested)."""
    info = load_round(run_dir, r)["round"].get("locked_test")
    if not info:
        return None
    out = {"seasons": info["seasons"], "cases": info["cases"], "tested": False}
    f_r, f_1 = round_dir(run_dir, r) / "locked_scores.parquet", round_dir(run_dir, rounds[0]) / "locked_scores.parquet"
    if not (f_r.is_file() and f_1.is_file()):
        return out
    lb, la = pd.read_parquet(f_r), pd.read_parquet(f_1)
    lb, la = lb[lb["agent_id"] == best["agent_id"]].set_index("case_id"), la[la["agent_id"] == base["agent_id"]].set_index(
        "case_id")
    common = la.index.intersection(lb.index)
    if not len(common):
        return out  # this agent was not among the round's leaders, who alone are tested
    d = lb.loc[common, "composite"].astype(float) - la.loc[common, "composite"].astype(float)
    se = float(d.std(ddof=1) / math.sqrt(len(d))) if len(d) > 1 else float("nan")
    by = {a["agent_id"]: a for a in info["agents"]}
    r1 = {a["agent_id"]: a for a in load_round(run_dir, rounds[0])["round"]["locked_test"]["agents"]}
    return out | {"tested": True, "n": len(d), "mean": float(d.mean()), "se": se,
                  "verdict": "none" if math.isnan(se) or abs(d.mean()) < 2 * se else ("better" if d.mean() > 0
                                                                                      else "worse"),
                  "best": by.get(best["agent_id"], {}), "base": r1.get(base["agent_id"], {}),
                  "wins": int((d > TIE).sum()), "losses": int((d < -TIE).sum())}


def _monitor_stats(c: dict) -> dict | None:
    m = c["sb"]["season"] == c["monitor"] if c["monitor"] else None
    if m is None or not m.any():
        return None
    dm = c["diff"][m]
    se = float(dm.std(ddof=1) / math.sqrt(len(dm))) if len(dm) > 1 else float("nan")
    verdict = "none" if math.isnan(se) or abs(dm.mean()) < 2 * se else ("better" if dm.mean() > 0 else "worse")
    sa, sb = c["sa"], c["sb"]
    comps = [k for k in PLAIN_COMPONENT if k in sa.columns]
    worst = min(comps, key=lambda k: float((sb.loc[m, k] - sa.loc[m, k]).mean())) if comps else None
    err = "depth_error_m" in sb.columns
    bias = pd.to_numeric(sb.loc[m, "depth_error_m"], errors="coerce").mean() if err else math.nan
    mae_a = pd.to_numeric(sa.loc[m, "depth_error_m"], errors="coerce").abs().mean() if err else math.nan
    mae_b = pd.to_numeric(sb.loc[m, "depth_error_m"], errors="coerce").abs().mean() if err else math.nan
    return {"mask": m, "n": len(dm), "mean": float(dm.mean()), "se": se, "verdict": verdict, "worst": worst,
            "bias_m": float(bias), "mae_a": float(mae_a), "mae_b": float(mae_b),
            "other_a": sa.loc[~m, "composite"].astype(float).mean(), "other_b": sb.loc[~m, "composite"].astype(float).mean(),
            "mon_a": sa.loc[m, "composite"].astype(float).mean(), "mon_b": sb.loc[m, "composite"].astype(float).mean()}


def training_report(paths: LabPaths, run_id: str, spec: GenomeSpec, round_no: int | None = None, rank: int = 1,
                    now: datetime | None = None, technical: bool = False) -> Report:
    """The analysis of one training run in plain language: agent ``rank`` of round ``round_no`` (default: the last
    committed round) against its family's default agent of round 1 (or round 1's best when the run started without
    that default). ``technical`` adds an appendix with the full tables."""
    c = _gather(paths, run_id, round_no, rank)
    A, B, best, sa, sb, diff = c["A"], c["B"], c["best"], c["sa"], c["sb"], c["diff"]
    now = now or datetime.now(UTC)
    base_name = "standard SNOWPACK" if c["base_is_default"] and best["family"] == "snowpack" else A["label"]
    seasons = _change_table(sa, sb, sb["season"], "season")
    better_seasons = int((seasons["change"] > 0).sum())
    mon = _monitor_stats(c)
    done_checks = [x for x in c["checks"] if x[1]["result"] is not None]
    passed = any(x[1]["result"]["passed"] for x in done_checks)

    name = nickname(best["genome_hash"], best["label"])
    rep = Report(title=f"Training report: how good is {name}?",
                 meta=[("Agent", f"{name}, the lab's {best['label']} (number {c['rank']} in round {c['r']})"),
                       ("Compared with", base_name),
                       ("Training run", f"{c['run_id']}, {len(c['rounds'])} rounds"),
                       ("Tests", f"{len(sb)} forecasts checked against real snow pits at "
                                 f"{_plural(len(sb['site_code'].unique()), 'plot')}, winters {_winter(sb['season'].min())} to "
                                 f"{_winter(sb['season'].max())}"),
                       ("Written", now.strftime("%Y-%m-%d %H:%M UTC"))])

    # ---- in short
    gain = B["composite"] - A["composite"]
    rep.h2("In short")
    lk = c["locked"]
    lead = []
    if lk and lk["tested"] and lk["best"].get("composite") is not None and lk["base"].get("composite") is not None:
        word = {"none": "no clear difference", "better": "yes", "worse": "no, it did worse"}[lk["verdict"]]
        lead.append(f"**Better on winters it never trained on ({_span(lk['seasons'])}): {word}.** There it scored "
                    f"{_pts(lk['best']['composite'])} against {_pts(lk['base']['composite'])} for {base_name}. These "
                    "winters were locked away from training, so this is the fairest test in this report.")
    elif lk and not lk["tested"]:
        lead.append(f"**Winters it never trained on ({_span(lk['seasons'])}): not tested for this agent.** Only each "
                    "round's top agents are tested on them; pick rank 1 or 2.")
    shorts = lead + [f"**Better on the winters it learned from: {'yes' if gain > 0 else 'no'}.** Its score went from "
              f"{_pts(A['composite'])} to {_pts(B['composite'])} out of 100 ({_dpts(gain)}), and it did better in "
              f"{better_seasons} of {len(seasons)} winters."]
    if mon and not (lk and lk["tested"]):
        word = {"none": "no clear difference", "better": "yes", "worse": "no, it did worse"}[mon["verdict"]]
        shorts.append(f"**Better on the most recent winter ({_winter(c['monitor'])}): {word}.** That winter is the best hint "
                      "of how it would do on a winter it has never seen.")
    if lk and not done_checks:
        shorts.append("**Promotion check: not run yet.** It is the final word before the agent could be used; until "
                      "then standard SNOWPACK stays the model behind the site.")
    elif not done_checks:
        shorts.append("**Proven on unseen winters: not yet.** It must pass the promotion check first. Until then, "
                      "standard SNOWPACK stays the model behind the site.")
    else:
        shorts.append("**Proven on unseen winters: " + ("yes, it passed the promotion check.** Using it on the site "
                      "is now the owner's call." if passed else "no, it failed the promotion check.** Standard "
                      "SNOWPACK stays the model behind the site."))
    rep.bullets(shorts)

    # ---- what the score means
    rep.h2("What the score means")
    rep.p("Each test uses a snow pit someone actually dug. The agent forecasts the snowpack without seeing the pit, "
          "then we score how close it came, from 0 to 100. Most pits give two tests: a 3-day forecast, and a "
          "prediction of the next pit. The score combines four things: total snow depth, the "
          "layers, the weak layers that matter for avalanches, and whether it is honest about how sure it is. "
          "100 would be a perfect match; nobody gets close to that.")

    # ---- what got better
    rep.h2("What got better")
    lb_mean = lambda df, k: float(pd.to_numeric(df[k], errors="coerce").mean())  # noqa: E731
    rows = [{"what we measure": "Overall score (out of 100)", base_name: _pts(A["composite"]),
             "evolved agent": _pts(B["composite"])}]
    if A.get("depth_mae_m") is not None and B.get("depth_mae_m") is not None:
        rows.append({"what we measure": "Snow depth error, on average", base_name: f"{100 * A['depth_mae_m']:.0f} cm",
                     "evolved agent": f"{100 * B['depth_mae_m']:.0f} cm"})
    for k, label in (("layer_structure", "Layers right (out of 100)"),
                     ("critical_layers", "Weak layers found (out of 100)"),
                     ("uncertainty", "Honest about uncertainty (out of 100)")):
        if A.get(k) is not None and B.get(k) is not None:
            rows.append({"what we measure": label, base_name: _pts(A[k]), "evolved agent": _pts(B[k])})
    rep.table(pd.DataFrame(rows))
    weights = {"snow_depth": 0.20, "layer_structure": 0.30, "critical_layers": 0.25, "uncertainty": 0.15}
    contrib = {k: weights[k] * ((B.get(k) or 0) - (A.get(k) or 0)) for k in weights}
    top = max(contrib, key=lambda k: abs(contrib[k]))
    if contrib[top] > 0:
        rep.p(f"The biggest improvement is in **{PLAIN_COMPONENT[top]}**.")
    wins, losses = int((diff > TIE).sum()), int((diff < -TIE).sum())
    ties = len(diff) - wins - losses
    rep.p(f"Test by test, it beat {base_name} {_plural(wins, 'time')}, lost {_plural(losses, 'time')} and tied "
          f"{_plural(ties, 'time')}.")
    plots = _change_table(sa, sb, sb["site_code"], "plot")
    types = _change_table(sa, sb, sb["case_type"], "type")
    rep.bullets([
        "By plot: " + ", ".join(f"{PLOT_NAMES.get(p, p)} {_dpts(v)}" for p, v in zip(plots["plot"], plots["change"],
                                                                                  strict=True)) + ".",
        "By task: " + ", ".join(f"{CASE_TYPES.get(t, t)} {_dpts(v)}" for t, v in zip(types["type"], types["change"],
                                                                                 strict=True)) + ".",
        ("Winters where it did worse: " + ", ".join(_winter(x) for x in seasons.loc[seasons["change"] < 0, "season"])
         + "."
         if (seasons["change"] < 0).any() else "It did better in every winter.")])

    # ---- locked winters (ADR-083)
    if lk and lk["tested"]:
        rep.h2("The fair test: winters it never trained on")
        rep.p(f"This run kept {_span(lk['seasons'])} locked away: no agent was trained or chosen on them. After each "
              "round, the leaders were tested on them. That is how the agent would do on a new winter.")
        rows = [{"what we measure": "Overall score (out of 100)", base_name: _pts(lk["base"]["composite"]),
                 "evolved agent": _pts(lk["best"]["composite"])}]
        if lk["base"].get("depth_mae_m") is not None and lk["best"].get("depth_mae_m") is not None:
            rows.append({"what we measure": "Snow depth error, on average", base_name:
                         f"{100 * lk['base']['depth_mae_m']:.0f} cm", "evolved agent":
                         f"{100 * lk['best']['depth_mae_m']:.0f} cm"})
        for k, label in (("layer_structure", "Layers right (out of 100)"),
                         ("critical_layers", "Weak layers found (out of 100)"),
                         ("uncertainty", "Honest about uncertainty (out of 100)")):
            if lk["base"].get(k) is not None and lk["best"].get(k) is not None:
                rows.append({"what we measure": label, base_name: _pts(lk["base"][k]),
                             "evolved agent": _pts(lk["best"][k])})
        rep.table(pd.DataFrame(rows))
        what = {"none": "too close to call", "better": "a real improvement", "worse": "a real step back"}[lk["verdict"]]
        rep.p(f"Test by test on these {lk['n']} tests it won {lk['wins']} and lost {lk['losses']}; the average change "
              f"is {_dpts(lk['mean'])}, where anything within about {100 * 2 * lk['se']:.1f} points is too close to "
              f"call. Verdict: {what}.")

    # ---- learning or memorising
    if mon:
        rep.h2("Is it learning, or just memorising?")
        rep.p("An agent can score well on past winters by fitting their quirks, like a student who memorises last "
              "year's exam. " + ("Besides the locked winters above, we watch the latest winter it trained on: a "
                                 "growing gap there is an early warning." if lk else
                                 "To catch that, we watch the most recent winter separately."))
        what = {"none": "no better and no worse than", "better": "better than", "worse": "worse than"}[mon["verdict"]]
        rep.p(f"On {_winter(c['monitor'])} ({mon['n']} tests) it was {what} {base_name}: {_dpts(mon['mean'])}, where anything "
              f"within about {100 * 2 * mon['se']:.1f} points is too close to call with this few tests. On all the "
              f"other winters it gained {_dpts(mon['other_b'] - mon['other_a'])}.")
        if mon["mean"] < 0 and mon["worst"]:
            extra = ""
            if mon["worst"] == "snow_depth" and not math.isnan(mon["mae_b"]):
                extra = (f" Its average snow depth error went from {100 * mon['mae_a']:.1f} to "
                         f"{100 * mon['mae_b']:.1f} cm")
                extra += (f", mostly by predicting too {'much' if mon['bias_m'] > 0 else 'little'} snow."
                          if abs(mon["bias_m"]) >= 0.05 else ".")
            rep.p(f"Where it slipped on that winter: {PLAIN_COMPONENT[mon['worst']]}.{extra}")
        if c["gap_r"].get("flag"):
            rep.p("This is why the Training page shows a warning flag. It does not prove the agent is memorising, "
                  "but its gains are not trustworthy until the promotion check is done.")

    # ---- progress
    tr = c["trace"]
    rep.h2("Progress over the run")
    rep.chart("Best score each round", tr["round"].tolist(), {"Best score (out of 100)": [100 * float(v) for v in tr["best composite"]]})
    msg = (f"The best score rose from {_pts(tr['best composite'].iloc[0])} in round {tr['round'].iloc[0]} to "
           f"{_pts(tr['best composite'].iloc[-1])} in round {tr['round'].iloc[-1]}.")
    if len(tr) >= 6:
        late = tr["best composite"].iloc[-1] - tr["best composite"].iloc[-6]
        early = (tr["best composite"].iloc[-6] - tr["best composite"].iloc[0]) / (len(tr) - 6)
        msg += (f" The last 5 rounds added {_dpts(late)}. "
                + ("Progress is slowing down, so more rounds of this run would add little."
                   if late / 5 < 0.5 * max(early, 1e-9) else "It was still improving, so more rounds could help."))
    rep.p(msg)
    _timing(rep, c)

    # ---- what it changed
    rep.h2("What the agent changed")
    changed = c["changed"]
    if not changed:
        rep.p("Nothing: it uses the standard settings.")
    else:
        rep.p("Compared with the standard settings, it:")
        rep.bullets(plain_changes(changed, spec))

    # ---- cautions
    cautions = []
    edge = [EDGE_NAMES.get(g, plain_name(g)) for g in changed if _near_limit(g, changed[g][1], spec)]
    if edge:
        cautions.append(f"Some settings are pushed to the limit of what is allowed ({', '.join(edge)}). That can "
                        "mean the score rewards an extreme value rather than better physics.")
    old_scoring = c["plan"].get("scoring_version") in (None, "lab-scoring-2")  # ADR-088 closed this in version 3
    if old_scoring and "brier" in sa.columns and B.get("critical_layers") is not None and \
            A.get("critical_layers") is not None:
        ba, bb = lb_mean(sa, "brier"), lb_mean(sb, "brier")
        if bb > ba + 0.01 and B["critical_layers"] > A["critical_layers"]:
            cautions.append("It became more confident about its layers without becoming more accurate about which "
                            "ones are really there. Part of its weak-layer gain may come from the way the score "
                            "rewarded confidence in this run's scoring version (fixed for runs started later).")
    fams = sorted({x["family"] for x in c["ranked"]})
    if len(fams) == 1 and not c["plan"].get("family_slots"):
        cautions.append(f"Every agent left in round {c['r']} comes from the same family, so the run explored only "
                        "one kind of agent.")
    if cautions:
        rep.h2("Things to keep an eye on")
        rep.bullets(cautions)

    # ---- next steps
    rep.h2("What to do next")
    steps = []
    if lk and lk["tested"] and lk["verdict"] == "worse" and not done_checks:
        steps.append("It did worse on the locked winters, so a promotion check would most likely fail. Start a new "
                     "run instead (see below) and compare its locked-winter score.")
    elif not done_checks:
        recent = sorted(sb["season"].unique())[-3:]
        steps.append(f"Run a promotion check. On the Training page, open Promotion check, choose Round {c['r']} and "
                     f"Rank {c['rank']}, hold out only {', '.join(_winter(x) for x in recent)}, and set Rounds per fold to 5 and "
                     "Population to 6. Press Estimate, then Start. It re-trains without those winters and tests "
                     "the result on them.")
    elif passed:
        steps.append("It passed: decide whether to use it on the site (the owner's decision).")
    else:
        steps.append("It failed: keep standard SNOWPACK, and treat this agent as a research result.")
    if len(fams) == 1 and not c["plan"].get("family_slots"):
        steps.append("For the next training run, turn on Family slots and pick a new seed, so different kinds of "
                     "agent stay in the running.")
    rep.bullets(steps)

    # ---- words
    rep.h2("Words used here")
    rep.bullets(["**Agent:** one set of forecasting settings. Training breeds new agents from the best ones.",
                 "**Round:** every agent takes every test once; the best two survive into the next round.",
                 "**Standard SNOWPACK:** the model the site uses today, with its usual settings.",
                 *([f"**{base_name}:** the agent this one is compared with, its family's usual settings."]
                   if base_name != "standard SNOWPACK" else []),
                 "**Promotion check:** re-training with some winters hidden, then testing on those winters. It is the "
                 "only fair test of a winter the agent has never seen."])

    # ---- reference (always: traceability)
    plan = c["plan"]
    versions = sorted({str(v) for v in sb.get("snowpack_version", pd.Series(dtype=str)).dropna().unique()})
    rep.h2("Reference")
    rep.table(pd.DataFrame([
        {"item": "Run", "value": f"`{c['run_id']}`"},
        {"item": "Agent", "value": f"{best['label']} `{best['agent_id']}`"},
        {"item": "Compared with", "value": f"{A['label']} `{A['agent_id']}`"},
        {"item": "Settings", "value": f"{plan.get('rounds')} rounds planned, population {plan.get('population')}, "
                                      f"survivors {plan.get('survivors')}, screen cases {plan.get('screen_cases') or 'off'}, "
                                      f"seed {plan.get('seed')}, family slots {'on' if plan.get('family_slots') else 'off'}"},
        {"item": "Scoring version", "value": str(plan.get("scoring_version", ""))},
        {"item": "Run plan hash", "value": f"`{c['meta'].get('plan_hash', '')}`"},
        {"item": "Genome hash", "value": f"`{best['genome_hash']}`"},
        {"item": "SNOWPACK version", "value": ", ".join(versions) or "not used"}]))

    if technical:
        _technical(rep, c, spec, seasons, mon)
    return rep


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _hours(seconds: float) -> str:
    """Plain duration: 'under 1 min', '45 min', '2 h 10 min', '8 h'."""
    if seconds < 60:
        return "under 1 min"
    m = int(round(seconds / 60))
    if m < 60:
        return f"{m} min"
    return f"{m // 60} h" + (f" {m % 60} min" if m % 60 else "")


def _timing(rep: Report, c: dict) -> None:
    """How long the run took, round by round, and what that means for planning the next one. Times are wall-clock
    times on the computer that ran it; another computer, more workers or more tests change them."""
    tr, status = c["trace"], c["status"]
    walls = [float(v) for v in tr["wall_s"] if v]
    if not walls:
        return
    rep.h2("How long it took")
    later = [float(v) for k, v in zip(tr["round"], tr["wall_s"], strict=True) if k >= 2 and v] or walls
    typical, last = sorted(later)[len(later) // 2], tr.iloc[-1]
    workers = ((c["meta"].get("estimate") or {}).get("workers"))
    total = None
    if status.get("started_at") and last.get("committed_at"):
        total = (datetime.fromisoformat(last["committed_at"]) - datetime.fromisoformat(status["started_at"]))
        total = total.total_seconds()
    lines = []
    if total:
        lines.append(f"**Whole run:** {_hours(total)} for {len(tr)} rounds"
                     + (f", using {_plural(int(workers), 'worker')} (processor cores)." if workers else "."))
    screen = f", including a {_hours(last['screen_s'])} quick screening of new agents" if last.get("screen_s") else ""
    lines.append(f"**Last round (round {last['round']}):** {_hours(last['wall_s'])}{screen}.")
    lines.append(f"**A typical round:** {'about ' if typical >= 60 else ''}{_hours(typical)} (shortest {_hours(min(later))}, longest "
                 f"{_hours(max(later))})."
                 + (" Round 1 was quick because earlier runs had already scored the standard agents."
                    if len(walls) > 1 and walls[0] < 0.5 * typical else ""))
    if last.get("engine_runs") and last.get("engine_s"):
        per = float(last["engine_s"]) / float(last["engine_runs"])
        lines.append(f"**SNOWPACK runs:** {int(last['engine_runs']):,} in the last round, about {per:.1f} s each. "
                     "Rounds where many new agents change SNOWPACK's physics take longest, because those agents "
                     "need fresh SNOWPACK runs.")
    rep.bullets(lines)
    night = 8 * 3600
    per_night = max(int(night // max(typical, 1.0)), 1)
    rep.p(f"**For planning:** at {'about ' if typical >= 60 else ''}{_hours(typical)} a round, an 8-hour night "
          "covers about "
          f"{per_night if per_night <= 200 else 'more than 200'} rounds, and a 20-round run takes about "
          f"{_hours(20 * typical)}. Round time "
          "grows roughly in step with Population and the number of tests, and shrinks with more workers. These "
          "times are for the computer that ran this training; for a promotion check, the Estimate button uses "
          "the same kind of measurement.")
    rep.chart("Time per round (minutes)", tr["round"].tolist(), {"Minutes": [None if not v else float(v) / 60
                                                                           for v in tr["wall_s"]]})
    rep.table(pd.DataFrame({"round": tr["round"], "time": [_hours(float(v)) if v else "" for v in tr["wall_s"]],
                            "best score (out of 100)": [_pts(v) for v in tr["best composite"]]}))


def _technical(rep: Report, c: dict, spec: GenomeSpec, seasons: pd.DataFrame, mon: dict | None) -> None:
    """The appendix for specialists: every score component and diagnostic, the round trace, the per-plot,
    per-case-type and per-season tables, and every changed gene with its range note."""
    A, B, best, sa, sb = c["A"], c["B"], c["best"], c["sa"], c["sb"]
    rename = {"base": A["label"], "best": best["label"]}
    rep.h2("Appendix: details for specialists")
    rep.p("Scores here are on the lab's 0-1 scale. All numbers are in-sample (split mode `all`).")
    rows = []
    for k, label in COMPONENTS:
        va, vb = A.get(k), B.get(k)
        rows.append({"measure": label, A["label"]: _r(va), best["label"]: _r(vb),
                     "change": _r(vb - va) if va is not None and vb is not None else None})
    for k, label, src in DIAGNOSTICS:
        if src == "lb":
            va, vb = A.get(k), B.get(k)
        elif k in sa.columns:
            va, vb = pd.to_numeric(sa[k], errors="coerce").mean(), pd.to_numeric(sb[k], errors="coerce").mean()
        else:
            continue
        rows.append({"measure": label, A["label"]: _r(va), best["label"]: _r(vb),
                     "change": _r(vb - va) if va is not None and vb is not None else None})
    rep.table(pd.DataFrame(rows), "Composite and robustness are leaderboard values; the rest are per-case means.")
    rep.chart("Train-vs-monitor gap per round (warning signal only)", c["trace"]["round"].tolist(),
              {"Gap": [None if v is None else float(v) for v in c["trace"]["gap"]]})
    rep.table(c["trace"].drop(columns=["wall_s", "screen_s", "engine_runs", "engine_s", "committed_at"]))
    if mon:
        rep.table(pd.DataFrame([
            {"agent": A["label"], "other seasons": _r(mon["other_a"]), c["monitor"]: _r(mon["mon_a"]),
             "gap": _r(mon["other_a"] - mon["mon_a"])},
            {"agent": best["label"], "other seasons": _r(mon["other_b"]), c["monitor"]: _r(mon["mon_b"]),
             "gap": _r(mon["other_b"] - mon["mon_b"])}]),
            f"Monitor season: paired change {mon['mean']:+.4f} ± {mon['se']:.4f} (one standard error) on "
            f"{mon['n']} cases.")
    rep.table(_change_table(sa, sb, sb["site_code"], "plot").rename(columns=rename))
    rep.table(_change_table(sa, sb, sb["case_type"], "case type").rename(columns=rename))
    if "depth_error_m" in sa.columns:
        bias = pd.DataFrame({"plot": sb["site_code"], "base": pd.to_numeric(sa["depth_error_m"], errors="coerce"),
                             "best": pd.to_numeric(sb["depth_error_m"], errors="coerce")}).groupby("plot").mean()
        rep.table(bias.reset_index().round(3).rename(columns=rename),
                  "Mean depth bias per plot in metres (predicted minus observed; + is too deep).")
    rep.table(seasons.rename(columns=rename))
    if c["changed"]:
        rows = gene_rows(c["changed"], spec)
        for row in rows:
            side = _near_limit(row["gene"], c["changed"][row["gene"]][1], spec)
            row["note"] = f"near its {side} limit" if side else ""
        rep.table(pd.DataFrame(rows)[["gene", "default", "this agent", "change", "note", "what it does"]])
    for cid, chk in c["checks"]:
        res = chk["result"]
        rep.p(f"Promotion check `{cid}`: " + ("unfinished." if res is None else
              f"{'PASS' if res['passed'] else 'FAIL'}, pooled held-out composite {res['pooled_evolved_composite']} "
              f"vs {res['pooled_incumbent_composite']} on {res['pooled_cases']} cases."))


def report_filename(run_id: str, round_no: int, rank: int, ext: str) -> str:
    return f"report-{run_id}-r{round_no:02d}-k{rank}.{ext}"


def save_report(paths: LabPaths, rep: Report, run_id: str, round_no: int, rank: int) -> list[Path]:
    """Keep a copy of both formats under ``outputs/reports`` (regenerated reports replace earlier copies)."""
    out = paths.outputs / "reports"
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for ext, text in (("html", rep.to_html()), ("md", rep.to_markdown())):
        f = out / report_filename(run_id, round_no, rank, ext)
        f.write_text(text, encoding="utf-8")
        files.append(f)
    return files
