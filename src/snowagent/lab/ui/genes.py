"""An agent's genes in plain words for the app's agent card: a readable name, the default, this agent's value, the
change (×1.12 for a multiplier, +0.40 °C for an offset, OLD → NEW for a choice) and what the gene does."""

from __future__ import annotations

from snowagent.lab.schemas.genome import GenomeSpec

# The SNOWPACK physics genes (ADR-070), named as a forecaster would; the other genes read well from their own names.
PLAIN = {
    "sp_precip_mult_bow": "Bow Summit precipitation",
    "sp_precip_mult_goat": "Goat's Eye precipitation",
    "sp_precip_mult_simp": "Simpson precipitation",
    "sp_rain_snow_mid_c": "rain/snow temperature",
    "sp_rain_snow_width_k": "rain/snow transition width",
    "sp_wind_mult": "wind speed",
    "sp_hn_density": "new-snow density method",
    "sp_hn_density_parameterization": "new-snow density formula",
    "sp_hn_density_fixed_kg_m3": "new-snow density (fixed)",
    "sp_viscosity_model": "settlement model",
    "sp_roughness_length_m": "surface roughness",
    "sp_hoar_thresh_ta_c": "surface hoar: warmest air",
    "sp_hoar_thresh_rh": "surface hoar: most humid air",
    "sp_hoar_thresh_vw_ms": "surface hoar: strongest wind",
    "sp_hoar_density_buried_kg_m3": "buried surface hoar density",
    "sp_hoar_min_size_buried_mm": "smallest buried surface hoar kept",
    "hardness_merge_tol": "layer merging (hardness)",
    # weak-layer genes (ADR-092)
    "sp_ta_offset_bow_k": "Bow Summit air temperature",
    "sp_ta_offset_goat_k": "Goat's Eye air temperature",
    "sp_ta_offset_simp_k": "Simpson air temperature",
    "sp_ilwr_offset_wm2": "heat from the night sky",
    "sp_ground_temp_c": "ground temperature",
    "sp_atmospheric_stability": "calm cold air at the surface",
    "sp_vapour_transport": "vapour moving between layers",
    "sp_hoar_density_surf_kg_m3": "surface hoar density (on the surface)",
    "sp_hoar_min_size_surf_mm": "smallest surface hoar recorded",
    "sp_facet_dpdz_hpa_m": "faceting: gradient for full speed",
    "sp_facet_rate": "faceting speed",
    "sp_crust_facet": "faceting next to crusts",
}
UNITS = {"degC": "°C", "K": "°C", "1": "", "-": ""}


def plain_name(gene: str) -> str:
    if gene in PLAIN:
        return PLAIN[gene]
    words = [w for w in gene.split("_") if w not in ("c", "k", "m", "ms", "h", "mm", "kg", "m3", "per", "day", "d")]
    return " ".join(words).replace("sh ", "surface hoar ") or gene


def _is_multiplier(gene: str, doc: str) -> bool:
    return "mult" in gene or gene.endswith("_factor") or doc.lower().startswith("multiplies")


def _fmt(v, unit: str) -> str:
    if isinstance(v, bool) or not isinstance(v, int | float):
        return str(v)
    return f"{v:.3g}{UNITS.get(unit, f' {unit}')}"


def change_text(gene: str, default, value, unit: str, doc: str) -> str:
    if isinstance(default, bool) or not isinstance(default, int | float) or not isinstance(value, int | float):
        return f"{default} → {value}"
    if _is_multiplier(gene, doc) and default:
        return f"×{value / default:.2f}"
    u = UNITS.get(unit, f" {unit}")
    return f"{value - default:+.3g}{u}"


def gene_rows(changed: dict[str, list], spec: GenomeSpec) -> list[dict]:
    """One row per changed gene (``changed``: gene -> [default, this agent], as lineage records store it)."""
    docs = {name: g for block in spec.blocks.values() for name, g in block.items()}
    rows = []
    for gene, (default, value) in changed.items():
        g = docs.get(gene)
        unit, doc = (g.unit, g.doc) if g else ("", "")
        rows.append({"setting": plain_name(gene), "default": _fmt(default, unit), "this agent": _fmt(value, unit),
                     "change": change_text(gene, default, value, unit, doc), "what it does": doc, "gene": gene})
    return rows
