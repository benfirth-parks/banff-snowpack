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
    # depth hoar genes (ADR-096)
    "sp_dh_growth": "depth hoar: grain growth",
    "sp_dh_size_mm": "depth hoar: grain size it starts at",
    # snowfall source (ADR-100)
    "sp_snowfall_source": "snowfall from",
    # weak layers in new snow (ADR-103)
    "sp_new_weak_factor": "trust in weak layers in new snow",
    # reanalysis precipitation (ADR-106)
    "sp_era5_precip": "reanalysis snowfall correction",
    "sp_dh_base": "depth hoar only at the base", "sp_dh_base_m": "depth hoar base: height above ground",
    "sp_dh_base_frac": "depth hoar base: share of the snow depth",
    # the lab's own models (ADR-118, ADR-120, ADR-122): the settings every family shares
    "snow_mult_bow": "Bow Summit snowfall", "snow_mult_goat": "Goat's Eye snowfall", "snow_mult_simp": "Simpson snowfall",
    "col_precip_mult_bow": "Bow Summit snowfall", "col_precip_mult_goat": "Goat's Eye snowfall",
    "col_precip_mult_simp": "Simpson snowfall", "col_rain_snow_mid_c": "rain/snow temperature",
    "col_rain_snow_width_k": "rain/snow transition width", "col_sensor_add_m": "snow depth sensor: when to add snow",
    "col_sensor_offset_weight": "snow depth sensor: offset removed",
    "new_snow_density_kg_m3": "new-snow density", "new_snow_density_temp_coeff": "new snow heavier when warm",
    "new_snow_density_wind_coeff": "new snow heavier when windy", "storm_gap_h": "dry spell that starts a new layer",
    "col_albedo_fresh": "how much sun fresh snow reflects", "col_albedo_old": "how much sun old snow reflects",
    "col_albedo_decay_days": "how fast snow stops reflecting sun", "col_turb_base": "heat from the air (calm)",
    "col_turb_wind": "heat from the air (per bit of wind)", "col_lw_offset_wm2": "heat from the night sky",
    "col_ground_temp_c": "ground temperature", "col_conductivity_mult": "how well snow passes heat",
    "col_viscosity_mult": "how stiff snow is (settling)", "col_destructive_mult": "early settling of new snow",
    "col_max_density_kg_m3": "densest snow gets", "col_water_hold_frac": "melt water a layer holds",
    "col_crust_water_frac": "refrozen water that makes a crust",
    "col_facet_gradient_k_m": "faceting: gradient it needs", "col_facet_hours": "faceting: hours it takes",
    "col_dh_gradient_k_m": "depth hoar: gradient it needs", "col_dh_hours": "depth hoar: hours it takes",
    "col_round_hours": "facets rounding back: hours", "col_pp_hours": "new snow breaking down: hours",
    "col_df_hours": "broken-down snow rounding: hours", "col_temp_coeff": "grain changes slow in the cold",
    "col_sh_max_wind_ms": "surface hoar: strongest wind it grows in",
    "col_sh_destroy_wind_ms": "surface hoar: wind that blows it away", "col_sh_growth_m_h": "surface hoar: growth speed",
    "col_old_facets": "old facets from the pit kept soft",
    "col_dh_base": "depth hoar only at the base", "col_dh_base_m": "depth hoar base: height above ground",
    "col_dh_base_frac": "depth hoar base: share of the snow depth", "col_hard_facet_offset": "hardness of facets it makes",
    "col_new_weak_factor": "trust in weak layers in new snow",
    "depth_spread_frac": "snow depth range (share)", "depth_spread_floor_m": "snow depth range (smallest)",
    "boundary_spread_m": "layer boundary range", "presence_confidence": "how sure a layer is there",
    # process switches (ADR-120)
    "proc_sw_kinetics": "switch: faceting depends on temperature and density",
    "proc_sw_crust": "switch: faster faceting next to crusts", "proc_sw_wet": "switch: facets on wet snow",
    "proc_sw_surface": "switch: near-surface facets in clear cold spells",
    "proc_sw_hoar": "switch: surface hoar likes a light breeze", "proc_sw_wind": "switch: wind packs surface snow",
    "proc_sw_ground": "switch: colder ground under thin snow", "proc_sw_round": "switch: faster rounding when calm",
    "proc_facet_density_max": "no faceting in snow denser than", "proc_facet_peak_c": "temperature of fastest faceting",
    "proc_facet_peak_width_k": "faceting temperature window", "proc_crust_boost_below": "faceting boost under a crust",
    "proc_crust_boost_above": "faceting boost on a crust", "proc_wet_facet_mult": "faceting boost on wet snow",
    "proc_nsf_depth_m": "near-surface facets: depth", "proc_nsf_gradient_k_m": "near-surface facets: gradient it needs",
    "proc_nsf_hours": "near-surface facets: hours it takes",
    "proc_nsf_max_density": "near-surface facets: densest snow it works on",
    "proc_sh_best_wind_ms": "surface hoar: best wind", "proc_sh_calm_frac": "surface hoar: growth in dead calm",
    "proc_sh_vapour_ref_hpa": "surface hoar: moisture it needs", "proc_drift_wind_ms": "wind that drifts snow",
    "proc_slab_density_kg_m3": "wind slab density", "proc_ground_cool_frac": "ground cooling under thin snow",
    # layer life stories (ADR-120)
    "sty_memory_days": "how long a layer remembers", "sty_w_grad": "facets: weight of a strong gradient",
    "sty_w_near": "facets: weight of near-surface cold", "sty_w_crust": "facets: weight of a nearby crust",
    "sty_w_wet": "facets: weight of wet snow below", "sty_w_thin": "facets: weight of a thin snowpack",
    "sty_w_weak": "facets: weight against, weak gradient", "sty_w_warm": "facets: weight against, warm snow",
    "sty_w_dense": "facets: weight against, dense snow", "sty_w_prior": "facets: weight of the last pit's facets",
    "sty_hold": "when facets round again", "sty_w_big": "depth hoar: weight of a very strong gradient",
    "sty_w_dh_thin": "depth hoar: weight of a thin snowpack", "sty_w_dh_dense": "depth hoar: weight against, dense snow",
    "sty_sh_hours": "surface hoar: hours of hoar weather", "sty_sh_max_wind_ms": "surface hoar: strongest wind",
    "sty_sh_min_rh": "surface hoar: least humidity", "sty_sh_wind_loss_h": "surface hoar: lost to a windy hour",
    "sty_sh_melt_loss_h": "surface hoar: lost to a melt hour", "sty_pp_hours": "new snow breaking down: hours",
    "sty_df_hours": "broken-down snow rounding: hours",
    # weather recipes (ADR-120)
    "rcp_sh_hours": "surface hoar: hours of hoar weather", "rcp_sh_max_wind_ms": "surface hoar: strongest wind",
    "rcp_sh_min_rh": "surface hoar: least humidity", "rcp_destroy_wind_ms": "surface hoar: wind that wipes it",
    "rcp_nsf_hours": "near-surface facets: clear cold hours", "rcp_nsf_temp_c": "near-surface facets: air this cold",
    "rcp_nsf_depth_m": "near-surface facets: depth", "rcp_nsf_max_density": "near-surface facets: densest snow",
    "rcp_wind_ms": "wind slab: wind it needs", "rcp_wind_hours": "wind slab: hours of wind",
    "rcp_slab_density_kg_m3": "wind slab: density", "rcp_melt_mm_per_k_day": "melt per warm degree-day",
    "rcp_crust_thaw_h": "crust: thaw hours", "rcp_crust_rain_mm": "crust: rain it needs",
    "rcp_crust_thick_m": "crust: thickness", "rcp_cf_hours": "facets on a crust: cold hours",
    "rcp_cf_temp_c": "facets on a crust: air this cold", "rcp_cf_max_burial_m": "facets on a crust: most new snow",
    "rcp_cf_thick_m": "facets on a crust: thickness", "rcp_pp_days": "new snow stays new: days",
    "rcp_df_days": "broken-down snow: days", "rcp_facet_gradient_k_m": "old snow faceting: gradient it needs",
    "rcp_facet_height_m": "old snow faceting: height above ground", "rcp_facet_hours": "old snow faceting: hours",
    "rcp_dh_hours": "depth hoar: further hours", "rcp_round_hours": "facets rounding back: hours",
    # site traits scorecard (ADR-122)
    "sc_shelter_wind": "wind slowed by shelter", "sc_lee_snow": "extra snow in the lee",
    "sc_shelter_scale": "shelter: near (0) or far (1)",
}
_DIAL_ON = {"snow": "snowfall", "wind": "wind", "facet": "faceting", "hoar": "surface hoar", "crust": "crusts",
            "ground": "ground temperature"}
_DIAL_BY = {"ground": "damp ground and cold air", "exposure": "wind exposure", "canopy": "trees"}
PLAIN |= {f"sc_{on}_{by}": f"{_DIAL_BY[by]} → {what}" for on, what in _DIAL_ON.items() for by in _DIAL_BY}
UNITS = {"degC": "°C", "K": "°C", "1": "", "-": "", "K m-1": " °C per m", "m s-1": " m/s", "m h-1": " m per hour",
         "kg m-3": " kg/m³", "W m-2": " W/m²", "hand-hardness index": " hardness steps", "d": " days",
         "hPa m-1": " hPa per m", "d-1": " per day"}


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
