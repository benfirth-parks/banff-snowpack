"""Plotly figures of the lab UI: the vertical snow profile and the weather small multiples. SI in, display units out
(cm, deg C, m/s, mm), local time on axes."""

from __future__ import annotations

import pandas as pd

from snowagent.forecast.plots import GRAIN_COLOURS  # the project's IACS-like grain colours (one owner)

HARDNESS_TICKS = {1: "F", 2: "4F", 3: "1F", 4: "P", 5: "K", 6: "I"}
UNKNOWN_HARDNESS_WIDTH = 0.5  # drawn narrow and labelled "unknown" in the hover
SERIES = "#2a78d6"
NEUTRAL = "#9a9890"
CONCERN = "#c2185b"


def grain_colour(code: str | None) -> str:
    if not code or code == "UNKNOWN":
        return NEUTRAL
    return GRAIN_COLOURS.get(code) or GRAIN_COLOURS.get(code[:2], NEUTRAL)


def profile_figure(layers: pd.DataFrame, snow_depth_m: float | None = None, title: str = ""):
    """Depth from the surface downward (0 at the top), one block per layer coloured by primary grain form, width =
    hand hardness (F..I). Layers of concern get a marker at the right edge. ``layers``: the lab's layer table rows."""
    import plotly.graph_objects as go

    fig = go.Figure()
    if layers is None or layers.empty:
        fig.add_annotation(text="no layers", showarrow=False, x=0.5, y=0.5, xref="paper", yref="paper")
    else:
        d = layers.sort_values("top_depth_m").copy()
        d["thick_cm"] = (d["bottom_depth_m"] - d["top_depth_m"]) * 100
        d["mid_cm"] = (d["top_depth_m"] + d["bottom_depth_m"]) * 50
        d["width"] = pd.to_numeric(d["hardness_index"], errors="coerce").fillna(UNKNOWN_HARDNESS_WIDTH)
        for grain, g in d.groupby("grain_primary", sort=False):
            hover = [
                f"<b>{r.grain_primary}</b>{' / ' + r.grain_secondary if isinstance(r.grain_secondary, str) else ''}"
                f"<br>{r.top_depth_m * 100:.0f}-{r.bottom_depth_m * 100:.0f} cm below surface"
                f"<br>hardness {r.hardness if isinstance(r.hardness, str) else 'unknown'}"
                f"<br>size {_size(r)} · wetness {r.wetness if isinstance(r.wetness, str) else '-'}"
                f"<br>class {r.critical_class}{' · layer of concern' if r.is_layer_of_concern else ''}"
                for r in g.itertuples()]
            fig.add_trace(go.Bar(
                y=g["mid_cm"], x=g["width"], width=g["thick_cm"], base=0, orientation="h", name=str(grain),
                marker={"color": grain_colour(str(grain)), "line": {"width": 1, "color": "rgba(255,255,255,0.9)"}},
                hovertext=hover, hoverinfo="text"))
        c = d[d["is_layer_of_concern"]]
        if len(c):
            fig.add_trace(go.Scatter(
                x=[6.4] * len(c), y=c["mid_cm"], mode="markers", name="layer of concern",
                marker={"symbol": "triangle-left", "size": 10, "color": CONCERN},
                hovertext=[f"layer of concern: {', '.join(_basis(b))}" for b in c["concern_basis_json"]],
                hoverinfo="text"))
    bottom = max([snow_depth_m * 100 if snow_depth_m else 0.0]
                 + ([float(layers["bottom_depth_m"].max()) * 100] if layers is not None and len(layers) else [10.0]))
    if snow_depth_m:
        fig.add_hline(y=snow_depth_m * 100, line={"color": NEUTRAL, "dash": "dot", "width": 1},
                      annotation_text=f"HS {snow_depth_m * 100:.0f} cm (ground)", annotation_position="bottom left")
    fig.update_layout(title=title, barmode="overlay", bargap=0, height=560, legend_title_text="grain form",
                      margin={"l": 60, "r": 20, "t": 50 if title else 20, "b": 50})
    fig.update_xaxes(range=[0, 6.7], tickvals=list(HARDNESS_TICKS), ticktext=list(HARDNESS_TICKS.values()),
                     title="hand hardness", showgrid=False)
    fig.update_yaxes(range=[bottom * 1.03 + 1, -2], title="depth from surface (cm)", zeroline=False)
    return fig


def _size(r) -> str:
    if pd.isna(r.grain_size_mm):
        return "-"
    if not pd.isna(r.grain_size_max_mm):
        return f"{r.grain_size_mm:g} mm (to {r.grain_size_max_mm:g})"
    return f"{r.grain_size_mm:g} mm"


def _basis(text: str) -> list[str]:
    import json

    return json.loads(text) if isinstance(text, str) else []


def weather_figure(w: pd.DataFrame, tz: str):
    """Small multiples, one axis each (never two scales on one axis): air temperature, precipitation (daily sums when
    the range is longer than 30 days), snow depth, wind speed. Only QC ok/suspect values are present in the table."""
    import plotly.graph_objects as go
    from plotly.subplots import make_subplots

    rows = [("air_temperature_k", "air temperature (°C)", lambda s: s - 273.15),
            ("precipitation_mm", "precipitation (mm)", lambda s: s),
            ("snow_depth_m", "snow depth (cm)", lambda s: s * 100),
            ("wind_speed_ms", "wind speed (m/s)", lambda s: s)]
    fig = make_subplots(rows=len(rows), cols=1, shared_xaxes=True, vertical_spacing=0.04,
                        subplot_titles=[r[1] for r in rows])
    t = pd.to_datetime(w["observed_at"], utc=True).dt.tz_convert(tz)
    daily = len(w) and (t.max() - t.min()) > pd.Timedelta(days=30)
    for i, (col, label, conv) in enumerate(rows, start=1):
        s = pd.Series(conv(w[col].astype(float)).to_numpy(), index=t)
        src = w.get(f"{col}_source")
        if s.isna().all():
            fig.add_annotation(text="no values in this range", showarrow=False, xref=f"x{i if i > 1 else ''} domain",
                               yref=f"y{i if i > 1 else ''} domain", x=0.5, y=0.5, font={"color": NEUTRAL})
        if col == "precipitation_mm":
            if daily:
                s = s.resample("D").sum(min_count=1)
            fig.add_trace(go.Bar(x=s.index, y=s.to_numpy(), name=label, marker_color=SERIES,
                                 hovertemplate="%{x}<br>%{y:.1f} mm<extra></extra>"), row=i, col=1)
        else:
            custom = src.to_numpy() if src is not None else None
            fig.add_trace(go.Scatter(x=s.index, y=s.to_numpy(), name=label, mode="lines",
                                     line={"color": SERIES, "width": 2}, customdata=custom, connectgaps=False,
                                     hovertemplate="%{x}<br>%{y:.1f}" + (" · %{customdata}" if custom is not None
                                                                          else "") + "<extra></extra>"),
                          row=i, col=1)
    fig.update_layout(height=820, showlegend=False, hovermode="x unified", margin={"l": 60, "r": 20, "t": 40, "b": 40})
    fig.update_xaxes(showgrid=False)
    return fig


def prediction_frame(pred: dict) -> pd.DataFrame:
    """An agent's predicted layers (median depths) as the layer-table rows ``profile_figure`` draws; the presence
    probability is appended to the grain's hover through ``grain_secondary``."""
    from snowagent.lab.agents.common import HARD_CODES  # one owner of the code -> index table

    index = {v: float(k) for k, v in HARD_CODES.items()}
    rows = []
    for ly in pred.get("layers", []):
        h = ly.get("hardness")
        hi = None
        if h:
            hi = index.get(h.rstrip("+-"))
            if hi is not None:
                hi += 1 / 3 if h.endswith("+") else -1 / 3 if h.endswith("-") else 0.0
        rows.append({"top_depth_m": ly["top_depth_m"]["p50"], "bottom_depth_m": ly["bottom_depth_m"]["p50"],
                     "grain_primary": (ly.get("grain_form") or ["UNKNOWN"])[0],
                     "grain_secondary": f"p={ly['probability_present']:.2f}", "hardness": h, "hardness_index": hi,
                     "wetness": ly.get("wetness"), "grain_size_mm": None, "grain_size_max_mm": None,
                     "critical_class": ly.get("critical_class", "unknown"),
                     "is_layer_of_concern": bool(ly.get("is_layer_of_concern")),
                     "concern_basis_json": '["predicted"]'})
    cols = ["top_depth_m", "bottom_depth_m", "grain_primary", "grain_secondary", "hardness", "hardness_index",
            "wetness", "grain_size_mm", "grain_size_max_mm", "critical_class", "is_layer_of_concern",
            "concern_basis_json"]
    return pd.DataFrame(rows, columns=cols).astype({"hardness_index": float, "grain_size_mm": float,
                                                    "grain_size_max_mm": float})
