"""Inspection plots (not a UI): layered profile charts and a unit map per lead."""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from matplotlib.patches import Polygon as MplPolygon  # noqa: E402

from snowagent.contracts import ProfileRecord, TerrainDomain  # noqa: E402

# IACS-like colours per primary grain form
GRAIN_COLOURS = {
    "PP": "#00ff00", "PPgp": "#808080", "DF": "#228b22", "RG": "#ffb6c1", "FC": "#add8e6", "FCxr": "#6495ed",
    "DH": "#0000ff", "SH": "#ff00ff", "MF": "#ff0000", "MFcr": "#8b0000", "IF": "#00ffff",
}


def plot_profile(ax_h, ax_t, rec: ProfileRecord, title: str) -> None:
    for ly in rec.layers:
        hard = ly.hand_hardness_index if ly.hand_hardness_index is not None else 0.5
        col = GRAIN_COLOURS.get(ly.grain_form_primary or "", "#cccccc")
        ax_h.barh(ly.bottom_vertical_m, hard, height=ly.thickness_vertical_m, align="edge", color=col,
                  edgecolor="none")
        mid = 0.5 * (ly.top_vertical_m + ly.bottom_vertical_m)
        if ly.is_crust:
            ax_h.plot([6.05], [mid], marker="s", color="#8b0000", ms=3)
        if ly.is_candidate_weak_layer:
            ax_h.plot([5.8], [mid], marker="<", color="#ff00ff", ms=3)
    hs = rec.diagnostics.hs_vertical_m
    ax_h.set_xlim(0, 6.2)
    ax_h.set_xticks([1, 2, 3, 4, 5, 6], ["F", "4F", "1F", "P", "K", "I"])
    ax_h.set_ylim(0, max(hs * 1.08, 0.1))
    ax_h.set_xlabel("hand hardness (engine index)")
    ax_h.set_ylabel("height above ground, vertical (m)")
    ax_h.set_title(title, fontsize=8)
    if rec.layers:
        z = [0.5 * (ly.top_vertical_m + ly.bottom_vertical_m) for ly in rec.layers]
        ax_t.plot([ly.temperature_c for ly in rec.layers], z, color="k", lw=1)
    ax_t.set_xlabel("temperature (C), black line")
    ax_t.set_xlim(-30, 1)


def write_profile_figure(path: Path, recs: list[ProfileRecord], titles: list[str], suptitle: str) -> None:
    n = len(recs)
    fig, axes = plt.subplots(1, n, figsize=(max(4.2 * n, 6.0), 5.8), squeeze=False)
    for i, (rec, t) in enumerate(zip(recs, titles, strict=True)):
        ax = axes[0, i]
        plot_profile(ax, ax.twiny(), rec, t)
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in GRAIN_COLOURS.values()]
    handles += [plt.Line2D([], [], marker="s", color="#8b0000", ls=""),
                plt.Line2D([], [], marker="<", color="#ff00ff", ls="")]
    fig.legend(handles, list(GRAIN_COLOURS) + ["crust", "candidate WL"], loc="lower center", ncol=7, fontsize=7,
               frameon=False)
    fig.suptitle(suptitle + "\nEXPERIMENTAL structure prediction - not avalanche guidance", fontsize=8)
    fig.tight_layout(rect=(0, 0.08, 1, 0.9))
    fig.savefig(path, dpi=110)
    plt.close(fig)


def write_run_plots(run_dir: Path, meta, domain: TerrainDomain, by_unit: dict[str, list[ProfileRecord]],
                    summary: pd.DataFrame) -> None:
    synth = " [SYNTHETIC INPUTS]" if meta.synthetic_inputs else ""
    leads = meta.request.output_lead_hours
    # maps of HS (control) per lead
    x_ref = min(x for x, _ in domain.boundary_xy)
    y_ref = min(y for _, y in domain.boundary_xy)

    def km(ring):
        return [((x - x_ref) / 1000.0, (y - y_ref) / 1000.0) for x, y in ring]

    fig, axes = plt.subplots(1, len(leads), figsize=(3.6 * len(leads), 4.2), squeeze=False)
    vmax = float(np.nanmax(summary.get("hs_vertical_m_control", pd.Series([1.0])).to_numpy(dtype=float)))
    for ax, h in zip(axes[0], leads, strict=True):
        sub = summary[summary.lead_hours == h].set_index("unit_id")
        for u in domain.units:
            val = sub.loc[u.unit_id].get("hs_vertical_m_control") if u.unit_id in sub.index else np.nan
            face = "#dddddd" if not u.supported or val is None or pd.isna(val) else plt.cm.Blues(float(val) / (vmax or 1))
            ax.add_patch(MplPolygon(km(u.polygon_xy), closed=True, facecolor=face, edgecolor="grey", lw=0.3,
                                    hatch=None if u.supported else "//"))
        bx, by = zip(*km(domain.boundary_xy), strict=True)
        ax.plot(bx, by, "k-", lw=0.8)
        ax.set_aspect("equal")
        ax.set_title(f"HS control, lead {h} h", fontsize=8)
        ax.set_xlabel(f"km east of {x_ref:.0f} ({domain.crs})", fontsize=6)
        ax.autoscale_view()
        ax.tick_params(labelsize=6)
    sm = plt.cm.ScalarMappable(cmap="Blues", norm=plt.Normalize(0, vmax))
    fig.colorbar(sm, ax=axes[0].tolist(), shrink=0.7, label="HS vertical (m); hatched = unsupported")
    fig.suptitle(f"{meta.run_id}{synth} transport: {meta.capability.transport_status}", fontsize=7)
    fig.savefig(run_dir / "plots" / "hs_map.png", dpi=110)
    plt.close(fig)

    # profile pairs: most sunlit vs most shaded supported unit at similar elevation
    units = {u.unit_id: u for u in domain.units if u.unit_id in by_unit}
    if not units:
        return
    lead = leads[-1]
    pairs = contrast_pair(list(units.values()))
    if pairs:
        recs = [next(r for r in by_unit[u.unit_id] if r.member_id == 0 and r.lead_hours == lead) for u in pairs]
        titles = [f"{u.unit_id} z={u.elevation_m:.0f} m slope={u.slope_deg:.0f} asp={u.aspect_deg or 0:.0f} "
                  f"svf={u.sky_view_factor:.2f}" for u in pairs]
        write_profile_figure(run_dir / "plots" / f"profiles_contrast_lead{lead:03d}h.png", recs, titles,
                             f"{meta.run_id}{synth} member 0, lead {lead} h")


def contrast_pair(units: list) -> list:
    """Pick a south-facing and a north-facing unit with the closest elevations."""
    south = [u for u in units if u.aspect_deg is not None and 135 <= u.aspect_deg <= 225 and u.slope_deg >= 15]
    north = [u for u in units if u.aspect_deg is not None and (u.aspect_deg >= 315 or u.aspect_deg <= 45)
             and u.slope_deg >= 15]
    if not south or not north:
        return []
    best = min(((abs(s.elevation_m - n.elevation_m), s, n) for s in south for n in north), key=lambda t: t[0])
    return [best[1], best[2]]
