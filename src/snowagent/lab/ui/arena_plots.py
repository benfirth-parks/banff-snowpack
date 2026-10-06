"""Plotly figures of the Arena page (ADR-078): the race, the heat strip and the evolution tree. Families keep one
colour and one marker everywhere (fixed order, validated for colour-vision deficiency in light and dark); the
labels carry identity too, so colour is never the only cue."""

from __future__ import annotations

import math

import pandas as pd

FAMILY_ORDER = ("snowpack", "hybrid", "analogue", "weather_rule", "persistence")
PALETTE = {"light": ("#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"),
           "dark": ("#3987e5", "#d95926", "#199e70", "#c98500", "#d55181")}
SYMBOLS = ("circle", "diamond", "square", "triangle-up", "hexagon")
SEQ = ("#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b")  # blue, light -> dark
NEUTRAL = "#9a9890"
MARGIN = {"l": 10, "r": 10, "t": 40, "b": 10}


def family_colour(family: str, mode: str = "light") -> str:
    pal = PALETTE.get(mode, PALETTE["light"])
    return pal[FAMILY_ORDER.index(family)] if family in FAMILY_ORDER else NEUTRAL


def family_symbol(family: str) -> str:
    return SYMBOLS[FAMILY_ORDER.index(family)] if family in FAMILY_ORDER else "circle"


def ink(mode: str) -> tuple[str, str]:
    """Primary and secondary text colours for the mode."""
    return ("#ffffff", "#c3c2b7") if mode == "dark" else ("#0b0b0b", "#52514e")


def race_figure(board: pd.DataFrame, incumbent: dict | None, mode: str = "light", title: str = ""):
    """Horizontal bars of the running mean case composite, best on top; the incumbent's value is the dashed line
    to beat. ``incumbent``: {"value", "label", "note"} or None."""
    import plotly.graph_objects as go

    text, _sec = ink(mode)
    b = board.dropna(subset=["mean"])
    fig = go.Figure()
    if len(b):
        b = b.iloc[::-1]  # plotly draws the first category at the bottom
        ys = [f"{lab}" for lab in b["label"]]
        fig.add_trace(go.Bar(
            x=b["mean"], y=ys, orientation="h", marker={"color": [family_colour(f, mode) for f in b["family"]],
                                                        "line": {"width": 0}, "cornerradius": 4},
            text=[f"{m:.3f}  ({n} cases)" for m, n in zip(b["mean"], b["cases"], strict=True)],
            textposition="outside", textfont={"color": text}, cliponaxis=False,
            customdata=b[["family", "cases"]].to_numpy(),
            hovertemplate="%{y}<br>%{customdata[0]}<br>mean case composite %{x:.4f}<br>%{customdata[1]} cases"
                          "<extra></extra>"))
    if incumbent and incumbent.get("value") is not None:
        fig.add_vline(x=incumbent["value"], line={"color": text, "width": 2, "dash": "dash"},
                      annotation={"text": f"bar to beat: {incumbent['label']} {incumbent['value']:.3f}"
                                  + (f" ({incumbent['note']})" if incumbent.get("note") else ""),
                                  "font": {"color": text, "size": 12}},
                      annotation_position="top")
    hi = float(b["mean"].max()) if len(b) else 1.0
    fig.update_layout(title=title, height=90 + 34 * max(1, len(b)), margin=MARGIN | {"r": 120, "t": 60},
                      xaxis={"title": "mean case composite so far (0-1)", "range": [0, min(1.0, hi * 1.25 + 0.02)]},
                      yaxis={"automargin": True}, showlegend=False, bargap=0.25,
                      transition={"duration": 300, "easing": "cubic-in-out"})
    return fig


def heat_figure(upto: pd.DataFrame, order: list[str], mode: str = "light"):
    """Agents (rows, race order) x cases (columns, in the order they were scored), coloured by case composite."""
    import plotly.graph_objects as go

    cases = list(dict.fromkeys(upto["case_id"]))
    labels = {a: lab for a, lab in zip(upto["agent_id"], upto["label"], strict=True)}
    z, hover = [], []
    idx = upto.set_index(["agent_id", "case_id"])
    idx = idx[~idx.index.duplicated(keep="last")]
    for a in order:
        zr, hr = [], []
        for c in cases:
            if (a, c) in idx.index:
                r = idx.loc[(a, c)]
                v = r.get("composite")
                zr.append(None if v is None or (isinstance(v, float) and math.isnan(v)) else float(v))
                parts = [f"<b>{labels.get(a, a)}</b>", c, f"{r.get('site_code')} · {r.get('case_type')} · "
                         f"season {r.get('season')}", f"pit {str(r.get('pit_time') or '?')[:16]}"]
                if zr[-1] is None:
                    parts.append(f"not scored ({r.get('status')})")
                else:
                    parts.append(f"composite {zr[-1]:.3f}")
                    parts += [f"{k.replace('_', ' ')} {float(r[k]):.3f}" for k in
                              ("snow_depth", "layer_structure", "critical_layers", "uncertainty")
                              if r.get(k) is not None and not (isinstance(r.get(k), float) and math.isnan(r[k]))]
                hr.append("<br>".join(parts))
            else:
                zr.append(None)
                hr.append("")
        z.append(zr)
        hover.append(hr)
    seq = SEQ if mode != "dark" else SEQ[::-1]
    scale = [[i / (len(seq) - 1), c] for i, c in enumerate(seq)]
    fig = go.Figure(go.Heatmap(z=z, x=list(range(1, len(cases) + 1)), y=[labels.get(a, a) for a in order],
                               text=hover, hoverinfo="text", hovertemplate="%{text}<extra></extra>",
                               colorscale=scale, zmin=0, zmax=1, xgap=1, ygap=2,
                               colorbar={"title": {"text": "case<br>composite"}, "thickness": 12}))
    fig.update_layout(height=80 + 30 * max(1, len(order)), margin=MARGIN | {"t": 20},
                      xaxis={"title": "cases, in the order they were scored", "showgrid": False},
                      yaxis={"autorange": "reversed", "automargin": True, "showgrid": False})
    return fig


def evolution_figure(nodes: pd.DataFrame, edges: list[dict], mode: str = "light"):
    """Family tree: x = round, y = rank in the round (1 on top); node size = composite; edges from parents
    (mutation solid, crossover dashed, survivor carried dotted); survivors ringed, screened-out children faded."""
    import plotly.graph_objects as go

    text, sec = ink(mode)
    fig = go.Figure()
    pos = {(r["round"], r["genome_hash"]): (r["round"], r["y"]) for r in nodes.to_dict("records")}
    styles = {"mutation": ("solid", 1.6), "crossover": ("dash", 1.6), "crossover+mutation": ("dash", 1.6),
              "survivor": ("dot", 1.2)}
    for op, (dash, width) in styles.items():
        xs, ys = [], []
        for e in edges:
            if e["kind"] != op:
                continue
            a, b = pos.get(e["from"]), pos.get(e["to"])
            if a and b:
                xs += [a[0], b[0], None]
                ys += [a[1], b[1], None]
        if xs:
            fig.add_trace(go.Scatter(x=xs, y=ys, mode="lines", line={"color": sec if op != "survivor" else NEUTRAL,
                                                                     "dash": dash, "width": width},
                                     name={"survivor": "kept (survivor)"}.get(op, op), hoverinfo="skip",
                                     opacity=0.7))
    c = pd.to_numeric(nodes["composite"], errors="coerce")
    lo, hi = (float(c.min()), float(c.max())) if c.notna().any() else (0.0, 1.0)
    for fam in [f for f in FAMILY_ORDER if f in set(nodes["family"])] + \
            sorted(set(nodes["family"]) - set(FAMILY_ORDER)):
        d = nodes[nodes["family"] == fam]
        cc = pd.to_numeric(d["composite"], errors="coerce")
        size = 10 + 26 * ((cc - lo) / (hi - lo) if hi > lo else 0.5)
        fig.add_trace(go.Scatter(
            x=d["round"], y=d["y"], mode="markers", name=fam,
            marker={"color": family_colour(fam, mode), "symbol": family_symbol(fam), "size": size.fillna(8),
                    "opacity": [0.3 if f else 1.0 for f in d["faded"]],
                    "line": {"color": [text if s else "rgba(0,0,0,0)" for s in d["survives"]],
                             "width": [3 if s else 0 for s in d["survives"]]}},
            text=d["hover"], hovertemplate="%{text}<extra></extra>"))
    fig.update_layout(height=max(360, 110 + 48 * int(nodes["y"].max() if len(nodes) else 4)),
                      margin=MARGIN | {"b": 80}, xaxis={"title": "round", "dtick": 1, "showgrid": False},
                      yaxis={"title": "rank in round", "autorange": "reversed", "dtick": 1, "showgrid": False},
                      legend={"orientation": "h", "y": -0.32, "yanchor": "top"}, title="Family tree (size = composite; ringed = "
                      "survives into the next round; faded = screened out)")
    return fig


def trend_figure(x: list, y: list, title: str, ytitle: str, mode: str = "light", zero: bool = False,
                 flags: list | None = None):
    import plotly.graph_objects as go

    fig = go.Figure()
    if zero:
        fig.add_hline(y=0, line={"color": NEUTRAL, "width": 1})
    fig.add_trace(go.Scatter(x=x, y=y, mode="lines+markers", line={"color": PALETTE.get(mode, PALETTE["light"])[0],
                                                                   "width": 2}, marker={"size": 8},
                             hovertemplate="round %{x}<br>%{y:.4f}<extra></extra>"))
    if flags:
        fx = [a for a, f in zip(x, flags, strict=True) if f]
        fy = [b for b, f in zip(y, flags, strict=True) if f]
        if fx:
            fig.add_trace(go.Scatter(x=fx, y=fy, mode="markers+text", text=["flag"] * len(fx),
                                     textposition="top center", marker={"size": 12, "symbol": "triangle-up",
                                                                        "color": "#c2185b"},
                                     hovertemplate="round %{x}: gap widened K rounds in a row<extra></extra>"))
    fig.update_layout(title=title, height=230, margin=MARGIN, xaxis={"title": "round", "dtick": 1},
                      yaxis={"title": ytitle}, showlegend=False)
    return fig
