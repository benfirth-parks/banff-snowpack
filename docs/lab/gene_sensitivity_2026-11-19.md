# Gene sensitivity screen, 19 Nov 2026 (ADR-123)

Research and decision support only, not an avalanche forecast.

Which settings of each genome structure move the score at all. Each setting was moved on its own, halfway to its
lower bound and halfway to its upper bound, with every other setting at its default, on a stratified sample of
150 cases from the training winters (1997-1998 to 2022-2023). The locked test winters
(2023-2024, 2024-2025, 2025-2026) were never loaded, and Parker Ridge is not in this lab's cases.
"Moves the score" is the largest change of the mean case score among a setting's probes, in points of 100. It
says the setting matters, not that changing it helps. A trimmed run evolves the settings marked **keep**; the
others stay fixed at their starting values.

Reproduce with `snowagent lab sensitivity <family> --workers 4` (150 cases, seed 0).

| Structure | Settings | Kept in a trimmed run | Default score on the sample |
|---|---|---|---|
| Column model (`column`) | 42 | 12 | 62.0 |
| Process switches (`process`) | 45 | 12 | 61.5 |
| Weather recipes (`recipe`) | 39 | 12 | 58.7 |
| Layer life stories (`story`) | 31 | 12 | 59.0 |
| Column + site traits (`scorecard`) | 39 | 12 | 62.0 |

## Column model (`column`)

| Rank | Setting | What it is | Moves the score | At each probe | Trimmed run |
|---|---|---|---|---|---|
| 1 | `col_sh_growth_m_h` | surface hoar growth per hour with the surface below the air's dew point | 16.37 | 0.000125: +0.10; 0.0011: -16.37 | **keep** |
| 2 | `depth_spread_frac` | p10/p90 snow depth = p50 x (1 -/+ this) -/+ floor | 1.97 | 0.07: +0.43; 0.31: -1.97 | **keep** |
| 3 | `col_new_weak_factor` | multiplies the presence probability of facet, depth hoar and surface hoar layers in snow that fell since the last pit | 1.78 | 0.6: +1.78 | **keep** |
| 4 | `col_facet_hours` | hours at twice the faceting gradient near 0 degC that turn snow to facets | 1.75 | 42: -0.01; 276: +1.75 | **keep** |
| 5 | `col_temp_coeff` | grain changes slow by exp(coeff x T) below 0 degC | 1.13 | 0.025: -0.23; 0.1: +1.13 | **keep** |
| 6 | `col_precip_mult_goat` | Goat's Eye: multiplies the plot's adopted precipitation factor (gauge 0.9, ERA5 1.3) on measured hours | 0.97 | 0.8: -0.97; 1.3: +0.49 | **keep** |
| 7 | `col_facet_gradient_k_m` | temperature gradient above which dry snow facets | 0.93 | 7.5: +0.49; 20: +0.93 | **keep** |
| 8 | `new_snow_density_temp_coeff` | density increase per K above -10 degC | 0.76 | 2: +0.76; 7: +0.41 | **keep** |
| 9 | `col_lw_offset_wm2` | added to incoming longwave from the sky (measured or estimated) | 0.73 | -20: +0.41; 20: -0.73 | **keep** |
| 10 | `hardness_merge_tol` | engine elements of one grain class within this hardness are one layer | 0.62 | 0.375: -0.04; 1: -0.62 | **keep** |
| 11 | `col_destructive_mult` | multiplies the early settling of new snow as its crystals break down | 0.60 | 0.65: +0.60; 2: -0.32 | **keep** |
| 12 | `col_albedo_fresh` | albedo of fresh snow | 0.54 | 0.815: -0.54; 0.915: +0.20 | **keep** |
| 13 | `col_hard_facet_offset` | added to the density-based hardness of model-made facets and depth hoar | 0.53 | -1: -0.17; 0.25: -0.53 | fixed |
| 14 | `col_crust_water_frac` | refrozen water, as a share of the element's ice mass, that makes a melt-freeze crust | 0.52 | 0.06: +0.52; 0.25: +0.18 | fixed |
| 15 | `col_sensor_offset_weight` | share of the sensor's offset from the start pit's depth removed (0 = the sensor's own depth) | 0.48 | 0.5: +0.48 | fixed |
| 16 | `col_turb_wind` | turbulent heat exchange added per m/s of wind | 0.45 | 1.5: +0.45; 4.25: +0.10 | fixed |
| 17 | `new_snow_density_wind_coeff` | density increase per m/s of wind | 0.44 | 2: +0.13; 12: +0.44 | fixed |
| 18 | `col_albedo_old` | albedo of old snow | 0.43 | 0.55: +0.02; 0.725: +0.43 | fixed |
| 19 | `col_ground_temp_c` | temperature of the ground under the snow | 0.43 | -1.5: +0.30; 0.25: -0.43 | fixed |
| 20 | `col_water_hold_frac` | liquid water an element holds before passing it down, as a share of its ice mass | 0.43 | 0.05: +0.43; 0.14: +0.38 | fixed |
| 21 | `depth_spread_floor_m` | minimum half-width of the depth interval | 0.42 | 0.025: -0.00; 0.175: -0.42 | fixed |
| 22 | `col_albedo_decay_days` | e-folding time of the albedo from fresh to old after snowfall | 0.38 | 3: -0.13; 12.5: +0.38 | fixed |
| 23 | `col_conductivity_mult` | multiplies the snow's thermal conductivity (Sturm et al. 1997) | 0.34 | 0.75: -0.18; 1.5: +0.34 | fixed |
| 24 | `col_turb_base` | turbulent heat exchange between air and snow surface in calm air | 0.34 | 1: +0.32; 5: +0.34 | fixed |
| 25 | `new_snow_density_kg_m3` | new-snow density at -10 degC and calm | 0.34 | 60: +0.30; 140: -0.34 | fixed |
| 26 | `col_viscosity_mult` | multiplies snow viscosity (Anderson 1976): higher settles slower under load | 0.33 | 0.65: -0.33; 2: +0.26 | fixed |
| 27 | `col_precip_mult_bow` | Bow Summit: multiplies the plot's adopted precipitation factor (gauge 1.15, ERA5 1.15) on measured hours | 0.32 | 0.8: +0.32; 1.3: -0.12 | fixed |
| 28 | `col_dh_hours` | hours above the depth hoar gradient that turn facets to depth hoar | 0.23 | 224: -0.23; 950: +0.16 | fixed |
| 29 | `col_pp_hours` | hours for new snow (PP) to decompose to DF near 0 degC | 0.22 | 15: +0.19; 72: -0.22 | fixed |
| 30 | `col_rain_snow_width_k` | width of the linear rain-snow ramp | 0.22 | 1.25: +0.22; 3: +0.13 | fixed |
| 31 | `col_sensor_add_m` | new snow is added when the snow depth sensor reads this much above the model | 0.21 | 0.0125: +0.13; 0.05: -0.21 | fixed |
| 32 | `col_rain_snow_mid_c` | air temperature of a 50/50 rain-snow mix | 0.21 | 0.35: -0.21; 2.1: +0.17 | fixed |
| 33 | `col_precip_mult_simp` | Simpson: multiplies the plot's adopted precipitation factor (gauge 1.15, ERA5 1.15) on measured hours | 0.19 | 0.8: +0.01; 1.3: +0.19 | fixed |
| 34 | `presence_confidence` | probability a predicted layer is present (scaled per agent) | 0.17 | 0.5: +0.15; 0.85: -0.17 | fixed |
| 35 | `col_round_hours` | hours under a weak gradient that round facets back to rounded grains (facets round slowly, weeks to months) | 0.15 | 420: -0.12; 1560: +0.15 | fixed |
| 36 | `storm_gap_h` | a dry spell longer than this starts a new layer | 0.12 | 7.5: -0.06; 42: +0.12 | fixed |
| 37 | `col_dh_gradient_k_m` | gradient that grows facets into depth hoar | 0.11 | 15: -0.03; 40: +0.11 | fixed |
| 38 | `col_df_hours` | hours under a weak gradient for DF to become rounded grains | 0.04 | 72: +0.02; 300: -0.04 | fixed |
| 39 | `col_sh_max_wind_ms` | surface hoar grows only at this wind or less | 0.00 | 1.75: +0.00; 4.5: +0.00 | fixed |
| 40 | `boundary_spread_m` | half-width of layer boundary intervals | 0.00 | 0.025: +0.00; 0.175: +0.00 | fixed |
| 41 | `col_max_density_kg_m3` | density at which settlement stops | 0.00 | 475: +0.00; 600: -0.00 | fixed |
| 42 | `col_sh_destroy_wind_ms` | wind above this removes surface hoar on the surface | 0.00 | 4.5: +0.00; 10.5: +0.00 | fixed |

## Process switches (`process`)

| Rank | Setting | What it is | Moves the score | At each probe | Trimmed run |
|---|---|---|---|---|---|
| 1 | `col_sh_growth_m_h` | surface hoar growth per hour with the surface below the air's dew point | 6.42 | 0.000125: +0.09; 0.0011: -6.42 | **keep** |
| 2 | `col_new_weak_factor` | multiplies the presence probability of facet, depth hoar and surface hoar layers in snow that fell since the last pit | 2.28 | 0.6: +2.28 | **keep** |
| 3 | `depth_spread_frac` | p10/p90 snow depth = p50 x (1 -/+ this) -/+ floor | 1.96 | 0.07: +0.43; 0.31: -1.96 | **keep** |
| 4 | `proc_sh_vapour_ref_hpa` | hoar: vapour pressure excess of the air over the surface at which growth is the column model's rate (set on training winters so the default makes about as many hoar layers as the column model) | 1.08 | 1.15: -1.08; 2.5: +0.00 | **keep** |
| 5 | `snow_mult_goat` | Goat's Eye: multiplies the plot's adopted precipitation factor on measured hours | 1.01 | 0.8: -1.01; 1.3: +0.15 | **keep** |
| 6 | `col_hard_facet_offset` | added to the density-based hardness of model-made facets and depth hoar | 0.86 | -1: -0.14; 0.25: -0.86 | **keep** |
| 7 | `proc_nsf_gradient_k_m` | surface: hourly gradient above which near-surface snow facets | 0.82 | 65: -0.82; 200: +0.57 | **keep** |
| 8 | `proc_sw_surface` | near-surface faceting in clear cold spells (Birkeland 1998) | 0.66 | off: +0.66 | **keep** |
| 9 | `hardness_merge_tol` | engine elements of one grain class within this hardness are one layer | 0.50 | 0.375: -0.16; 1: -0.50 | **keep** |
| 10 | `col_pp_hours` | hours for new snow (PP) to decompose to DF near 0 degC | 0.47 | 15: +0.07; 72: -0.47 | **keep** |
| 11 | `depth_spread_floor_m` | minimum half-width of the depth interval | 0.43 | 0.025: -0.00; 0.175: -0.43 | **keep** |
| 12 | `proc_facet_density_max` | kinetics: no faceting or depth hoar growth at this density or more (Marbouty 1980) | 0.37 | 300: -0.37; 400: +0.27 | **keep** |
| 13 | `proc_nsf_hours` | surface: hours at that gradient that make near-surface facets | 0.36 | 24: -0.31; 78: +0.36 | fixed |
| 14 | `col_facet_hours` | hours at twice the faceting gradient near 0 degC that turn snow to facets | 0.33 | 42: -0.05; 276: -0.33 | fixed |
| 15 | `proc_sw_wind` | drifting wind packs loose surface snow (Sommer et al. 2018) | 0.33 | off: +0.33 | fixed |
| 16 | `col_facet_gradient_k_m` | temperature gradient above which dry snow facets | 0.30 | 7.5: -0.07; 20: -0.30 | fixed |
| 17 | `proc_sw_crust` | faster faceting next to a crust (Hammonds et al. 2015) | 0.28 | off: -0.28 | fixed |
| 18 | `proc_drift_wind_ms` | wind: speed above which dry surface snow drifts and packs (Li and Pomeroy 1997) | 0.28 | 5.85: -0.16; 9.85: +0.28 | fixed |
| 19 | `col_temp_coeff` | grain changes slow by exp(coeff x T) below 0 degC | 0.27 | 0.025: +0.07; 0.1: -0.27 | fixed |
| 20 | `proc_facet_peak_width_k` | kinetics: faceting is half as fast this far from the peak temperature | 0.26 | 5.5: -0.26; 11.5: -0.14 | fixed |
| 21 | `proc_sw_kinetics` | faceting depends on snow temperature and density (Marbouty 1980) | 0.26 | off: -0.26 | fixed |
| 22 | `col_round_hours` | hours under a weak gradient that round facets back to rounded grains (facets round slowly, weeks to months) | 0.26 | 420: -0.26; 1560: -0.09 | fixed |
| 23 | `proc_ground_cool_frac` | ground: share of the ten-day air temperature (below 0 degC) the ground takes under no snow, fading with depth | 0.23 | 0.1: +0.03; 0.4: +0.23 | fixed |
| 24 | `presence_confidence` | probability a predicted layer is present (scaled per agent) | 0.16 | 0.5: +0.12; 0.85: -0.16 | fixed |
| 25 | `snow_mult_bow` | Bow Summit: multiplies the plot's adopted precipitation factor on measured hours | 0.15 | 0.8: -0.14; 1.3: -0.15 | fixed |
| 26 | `proc_sw_wet` | dry snow on wet snow facets within hours (Jamieson and Fierz 2004) | 0.15 | off: +0.15 | fixed |
| 27 | `col_dh_hours` | hours above the depth hoar gradient that turn facets to depth hoar | 0.15 | 224: -0.15; 950: +0.00 | fixed |
| 28 | `proc_slab_density_kg_m3` | wind: density wind-packed snow approaches | 0.14 | 240: +0.14; 340: -0.11 | fixed |
| 29 | `col_dh_gradient_k_m` | gradient that grows facets into depth hoar | 0.14 | 15: -0.14; 40: -0.03 | fixed |
| 30 | `snow_mult_simp` | Simpson: multiplies the plot's adopted precipitation factor on measured hours | 0.11 | 0.8: -0.07; 1.3: +0.11 | fixed |
| 31 | `proc_sw_round` | rounding is faster the weaker the gradient | 0.11 | off: -0.11 | fixed |
| 32 | `proc_facet_peak_c` | kinetics: snow temperature of the fastest faceting | 0.10 | -7.5: +0.01; -3.5: -0.10 | fixed |
| 33 | `proc_sw_ground` | the ground under thin snow is colder than under deep snow | 0.06 | off: +0.06 | fixed |
| 34 | `proc_nsf_max_density` | surface: only snow lighter than this facets near the surface | 0.06 | 150: -0.06; 250: +0.02 | fixed |
| 35 | `proc_sh_best_wind_ms` | hoar: wind of the fastest surface hoar growth | 0.04 | 1: +0.04; 2.25: -0.04 | fixed |
| 36 | `proc_sh_calm_frac` | hoar: growth in calm air as a share of the fastest | 0.04 | 0.25: -0.00; 0.75: +0.04 | fixed |
| 37 | `col_df_hours` | hours under a weak gradient for DF to become rounded grains | 0.04 | 72: -0.03; 300: -0.04 | fixed |
| 38 | `col_sh_max_wind_ms` | surface hoar grows only at this wind or less | 0.03 | 1.75: +0.00; 4.5: -0.03 | fixed |
| 39 | `proc_crust_boost_below` | crust: multiplies the faceting gradient in the snow under a crust | 0.02 | 1.75: -0.02; 4.25: +0.00 | fixed |
| 40 | `proc_wet_facet_mult` | wet: multiplies faceting of dry snow lying on wet or refreezing snow | 0.02 | 0.625: +0.02; 2.5: -0.01 | fixed |
| 41 | `proc_crust_boost_above` | crust: multiplies the faceting gradient in the snow on a crust | 0.02 | 1.25: +0.01; 3.75: -0.02 | fixed |
| 42 | `proc_sw_hoar` | surface hoar growth depends on a light breeze and the vapour excess (Stoessel et al. 2010) | 0.01 | off: -0.01 | fixed |
| 43 | `proc_nsf_depth_m` | surface: near-surface faceting reaches this deep | 0.00 | 0.1: +0.00; 0.2: -0.00 | fixed |
| 44 | `col_sh_destroy_wind_ms` | wind above this removes surface hoar on the surface | 0.00 | 4.5: -0.00; 10.5: +0.00 | fixed |
| 45 | `boundary_spread_m` | half-width of layer boundary intervals | 0.00 | 0.025: +0.00; 0.175: +0.00 | fixed |

## Weather recipes (`recipe`)

| Rank | Setting | What it is | Moves the score | At each probe | Trimmed run |
|---|---|---|---|---|---|
| 1 | `col_new_weak_factor` | multiplies the presence probability of facet, depth hoar and surface hoar layers in snow that fell since the last pit | 3.23 | 0.6: +3.23 | **keep** |
| 2 | `depth_spread_frac` | p10/p90 snow depth = p50 x (1 -/+ this) -/+ floor | 1.92 | 0.07: +0.33; 0.31: -1.92 | **keep** |
| 3 | `rcp_facet_gradient_k_m` | pack: ten-day mean air temperature over snow depth above which the lower pack facets | 1.13 | 7.5: +0.14; 20: -1.13 | **keep** |
| 4 | `rcp_sh_hours` | surface hoar: clear, dark, humid, light-wind hours since snow last fell that leave a hoar layer when buried (set on training winters: about three clear nights) | 0.93 | 19: -0.80; 68: +0.93 | **keep** |
| 5 | `rcp_sh_max_wind_ms` | surface hoar: grows at this wind or less | 0.92 | 1.75: +0.92; 4.5: -0.09 | **keep** |
| 6 | `hardness_merge_tol` | engine elements of one grain class within this hardness are one layer | 0.80 | 0.375: +0.19; 1: -0.80 | **keep** |
| 7 | `col_viscosity_mult` | multiplies snow viscosity (Anderson 1976): higher settles slower under load | 0.79 | 0.65: -0.79; 2: +0.29 | **keep** |
| 8 | `rcp_sh_min_rh` | surface hoar: grows at this relative humidity or more (Stoessel et al. 2010) | 0.74 | 0.45: -0.10; 0.775: +0.74 | **keep** |
| 9 | `rcp_dh_hours` | pack: further hours that make depth hoar | 0.72 | 286: -0.72; 1000: +0.09 | **keep** |
| 10 | `snow_mult_goat` | Goat's Eye: multiplies the plot's adopted precipitation factor on measured hours | 0.66 | 0.8: -0.45; 1.3: +0.66 | **keep** |
| 11 | `rcp_facet_height_m` | pack: height above the ground up to which it facets | 0.65 | 0.25: -0.65; 0.7: +0.17 | **keep** |
| 12 | `rcp_nsf_hours` | near-surface facets: clear cold hours since snow last fell (Birkeland 1998) | 0.55 | 24: -0.55; 138: +0.19 | **keep** |
| 13 | `rcp_facet_hours` | pack: hours above that gradient that make facets | 0.52 | 96: -0.16; 444: -0.52 | fixed |
| 14 | `depth_spread_floor_m` | minimum half-width of the depth interval | 0.45 | 0.025: -0.00; 0.175: -0.45 | fixed |
| 15 | `rcp_pp_days` | days new snow stays PP | 0.42 | 0.875: -0.11; 3.25: -0.42 | fixed |
| 16 | `col_destructive_mult` | multiplies the early settling of new snow as its crystals break down | 0.40 | 0.65: -0.05; 2: -0.40 | fixed |
| 17 | `rcp_crust_thaw_h` | crust: thaw hours before a freeze | 0.39 | 2: -0.22; 13.5: +0.39 | fixed |
| 18 | `snow_mult_bow` | Bow Summit: multiplies the plot's adopted precipitation factor on measured hours | 0.35 | 0.8: -0.35; 1.3: -0.33 | fixed |
| 19 | `presence_confidence` | probability a predicted layer is present (scaled per agent) | 0.34 | 0.5: +0.32; 0.85: -0.34 | fixed |
| 20 | `rcp_melt_mm_per_k_day` | melt per degree of air temperature above 0 degC per day | 0.33 | 1.75: +0.33; 5.5: +0.07 | fixed |
| 21 | `rcp_nsf_depth_m` | near-surface facets: depth of snow turned to facets | 0.29 | 0.065: -0.10; 0.175: +0.29 | fixed |
| 22 | `rcp_cf_temp_c` | facets on a crust: air at least this cold | 0.29 | -14: +0.29; -5: -0.04 | fixed |
| 23 | `rcp_nsf_max_density` | near-surface facets: only snow lighter than this | 0.28 | 150: -0.28; 250: -0.01 | fixed |
| 24 | `rcp_cf_hours` | facets on a crust: cold hours after new snow buries a fresh crust (Jamieson and Fierz 2004) | 0.27 | 13.5: -0.05; 72: +0.27 | fixed |
| 25 | `rcp_nsf_temp_c` | near-surface facets: air at least this cold | 0.24 | -17.5: +0.23; -6.5: +0.24 | fixed |
| 26 | `rcp_destroy_wind_ms` | surface hoar: an hour of wind above this wipes the hoar hours | 0.22 | 4.5: +0.08; 10.5: -0.22 | fixed |
| 27 | `rcp_df_days` | further days it stays DF before it is rounded | 0.19 | 3.5: -0.19; 13: +0.12 | fixed |
| 28 | `col_hard_facet_offset` | added to the density-based hardness of model-made facets and depth hoar | 0.18 | -1: -0.12; 0.25: -0.18 | fixed |
| 29 | `rcp_crust_thick_m` | crust: thickness | 0.14 | 0.0125: +0.01; 0.04: +0.14 | fixed |
| 30 | `rcp_round_hours` | pack: hours under half that gradient that round facets | 0.12 | 420: -0.12; 1560: +0.00 | fixed |
| 31 | `snow_mult_simp` | Simpson: multiplies the plot's adopted precipitation factor on measured hours | 0.11 | 0.8: +0.01; 1.3: +0.11 | fixed |
| 32 | `rcp_wind_hours` | wind slab: hours of that wind on new snow | 0.10 | 3.5: -0.04; 27: +0.10 | fixed |
| 33 | `rcp_cf_max_burial_m` | facets on a crust: only under this much new snow | 0.09 | 0.175: +0.09; 0.45: +0.00 | fixed |
| 34 | `rcp_wind_ms` | wind slab: wind that drifts dry new snow (Li and Pomeroy 1997) | 0.07 | 5.85: -0.01; 10.85: +0.07 | fixed |
| 35 | `rcp_slab_density_kg_m3` | wind slab: density the top 20 cm of new snow is packed to | 0.07 | 230: +0.02; 340: -0.07 | fixed |
| 36 | `rcp_cf_thick_m` | facets on a crust: thickness of the facet layer | 0.01 | 0.0125: +0.01; 0.035: +0.00 | fixed |
| 37 | `boundary_spread_m` | half-width of layer boundary intervals | 0.00 | 0.025: +0.00; 0.175: +0.00 | fixed |
| 38 | `col_max_density_kg_m3` | density at which settlement stops | 0.00 | 475: +0.00; 600: -0.00 | fixed |
| 39 | `rcp_crust_rain_mm` | crust: rain on snow before a freeze | 0.00 | 1.25: +0.00; 6: +0.00 | fixed |

## Layer life stories (`story`)

| Rank | Setting | What it is | Moves the score | At each probe | Trimmed run |
|---|---|---|---|---|---|
| 1 | `col_new_weak_factor` | multiplies the presence probability of facet, depth hoar and surface hoar layers in snow that fell since the last pit | 3.88 | 0.6: +3.88 | **keep** |
| 2 | `depth_spread_frac` | p10/p90 snow depth = p50 x (1 -/+ this) -/+ floor | 2.05 | 0.07: +0.36; 0.31: -2.05 | **keep** |
| 3 | `sty_w_big` | depth hoar: weight of hours under a gradient of 20 K/m or more (400 h) | 1.65 | 0.5: +0.15; 2: -1.65 | **keep** |
| 4 | `sty_sh_hours` | surface hoar: hours of hoar weather standing on the surface that leave a layer when buried (set on training winters; the column model's own rule finds fewer real hoar layers with fewer false ones) | 1.46 | 31: -1.46; 80: +0.44 | **keep** |
| 5 | `snow_mult_goat` | Goat's Eye: multiplies the plot's adopted precipitation factor on measured hours | 0.94 | 0.8: -0.94; 1.3: -0.49 | **keep** |
| 6 | `sty_sh_max_wind_ms` | surface hoar: hoar weather needs this wind or less | 0.92 | 1.75: +0.92; 4.5: -0.16 | **keep** |
| 7 | `snow_mult_bow` | Bow Summit: multiplies the plot's adopted precipitation factor on measured hours | 0.74 | 0.8: +0.27; 1.3: -0.74 | **keep** |
| 8 | `sty_sh_min_rh` | surface hoar: hoar weather needs at least this relative humidity (Stoessel et al. 2010) | 0.72 | 0.45: -0.06; 0.775: +0.72 | **keep** |
| 9 | `sty_w_dh_thin` | depth hoar: weight of hours with under 60 cm of snow on the ground (720 h) | 0.50 | 1.5: -0.50 | **keep** |
| 10 | `depth_spread_floor_m` | minimum half-width of the depth interval | 0.48 | 0.025: +0.00; 0.175: -0.48 | **keep** |
| 11 | `sty_w_grad` | facets: weight of the temperature gradient above 10 K/m (72 h at twice that make facets at weight 1) | 0.41 | 0.5: +0.31; 2: -0.41 | **keep** |
| 12 | `sty_pp_hours` | hours for new snow (PP) to decompose to DF near 0 degC | 0.40 | 15: +0.04; 72: -0.40 | **keep** |
| 13 | `presence_confidence` | probability a predicted layer is present (scaled per agent) | 0.37 | 0.5: +0.36; 0.85: -0.37 | fixed |
| 14 | `hardness_merge_tol` | engine elements of one grain class within this hardness are one layer | 0.33 | 0.375: +0.00; 1: -0.33 | fixed |
| 15 | `sty_w_near` | facets: weight of hours near the surface under a gradient over 100 K/m (36 h, Birkeland 1998) | 0.15 | 0.5: -0.05; 2: -0.15 | fixed |
| 16 | `sty_memory_days` | a layer's history fades with this e-folding time | 0.14 | 17.5: +0.07; 60: -0.14 | fixed |
| 17 | `col_hard_facet_offset` | added to the density-based hardness of model-made facets and depth hoar | 0.12 | -1: +0.12; 0.25: -0.12 | fixed |
| 18 | `sty_sh_wind_loss_h` | surface hoar: hours of hoar weather a windy hour (over 6 m/s) removes | 0.10 | 5: -0.03; 35: +0.10 | fixed |
| 19 | `snow_mult_simp` | Simpson: multiplies the plot's adopted precipitation factor on measured hours | 0.09 | 0.8: -0.09; 1.3: -0.02 | fixed |
| 20 | `sty_w_wet` | facets: weight of hours lying dry on wet or refreezing snow (24 h) | 0.06 | 0.25: +0.06; 1.75: -0.03 | fixed |
| 21 | `sty_hold` | facets round again once their score falls this far below zero | 0.06 | 0.25: -0.06; 1.25: +0.00 | fixed |
| 22 | `sty_w_thin` | facets: weight of hours with under 60 cm of snow on the ground (720 h) | 0.06 | 1.5: -0.06 | fixed |
| 23 | `sty_df_hours` | further hours for DF to become rounded grains | 0.05 | 72: +0.05; 300: +0.04 | fixed |
| 24 | `sty_w_prior` | facets: weight of the start pit having reported facets (fades with the memory) | 0.04 | 1.5: -0.04; 4.5: +0.00 | fixed |
| 25 | `sty_w_crust` | facets: weight of hours next to a crust (240 h) | 0.03 | 0.25: +0.01; 1.75: -0.03 | fixed |
| 26 | `sty_w_dense` | facets: weight, against, of hours denser than 350 kg/m3 (720 h, Marbouty 1980) | 0.03 | 0.25: +0.00; 1.75: -0.03 | fixed |
| 27 | `sty_w_weak` | facets: weight, against, of hours under a gradient below 5 K/m (720 h) | 0.02 | 0.25: -0.00; 1.75: +0.02 | fixed |
| 28 | `sty_w_warm` | facets: weight, against, of hours warmer than -2 degC (720 h) | 0.01 | 1.5: +0.01 | fixed |
| 29 | `boundary_spread_m` | half-width of layer boundary intervals | 0.00 | 0.025: +0.00; 0.175: +0.00 | fixed |
| 30 | `sty_sh_melt_loss_h` | surface hoar: hours of hoar weather a melting or rainy hour removes | 0.00 | 15: +0.00; 45: +0.00 | fixed |
| 31 | `sty_w_dh_dense` | depth hoar: weight, against, of hours denser than 350 kg/m3 (720 h) | 0.00 | 0.25: +0.00; 1.75: +0.00 | fixed |

## Column + site traits (`scorecard`)

| Rank | Setting | What it is | Moves the score | At each probe | Trimmed run |
|---|---|---|---|---|---|
| 1 | `col_sh_growth_m_h` | surface hoar growth per hour with the surface below the air's dew point | 16.37 | 0.000125: +0.10; 0.0011: -16.37 | **keep** |
| 2 | `sc_hoar_ground` | weight of the ground index (damp ground, deadfall, cold air pooling) on surface hoar growth | 9.39 | -0.35: -0.35; 0.35: -9.39 | **keep** |
| 3 | `sc_hoar_exposure` | weight of the exposure index (wind exposure and standing above the surroundings) on surface hoar growth | 9.10 | -0.35: -9.10; 0.35: -0.45 | **keep** |
| 4 | `sc_hoar_canopy` | weight of the canopy index (trees closing in) on surface hoar growth | 5.53 | -0.35: -3.94; 0.35: -5.53 | fixed |
| 5 | `depth_spread_frac` | p10/p90 snow depth = p50 x (1 -/+ this) -/+ floor | 1.97 | 0.07: +0.43; 0.31: -1.97 | **keep** |
| 6 | `col_new_weak_factor` | multiplies the presence probability of facet, depth hoar and surface hoar layers in snow that fell since the last pit | 1.78 | 0.6: +1.78 | **keep** |
| 7 | `col_facet_hours` | hours at twice the faceting gradient near 0 degC that turn snow to facets | 1.75 | 42: -0.01; 276: +1.75 | **keep** |
| 8 | `sc_snow_ground` | weight of the ground index (damp ground, deadfall, cold air pooling) on precipitation | 1.28 | -0.35: -1.28; 0.35: -0.37 | fixed |
| 9 | `col_temp_coeff` | grain changes slow by exp(coeff x T) below 0 degC | 1.13 | 0.025: -0.23; 0.1: +1.13 | **keep** |
| 10 | `col_facet_gradient_k_m` | temperature gradient above which dry snow facets | 0.93 | 7.5: +0.49; 20: +0.93 | **keep** |
| 11 | `sc_snow_canopy` | weight of the canopy index (trees closing in) on precipitation | 0.87 | -0.35: +0.87; 0.35: -0.36 | fixed |
| 12 | `sc_ground_ground` | weight of the ground index (damp ground, deadfall, cold air pooling) on ground temperature (2 K per unit) | 0.69 | -0.35: +0.20; 0.35: -0.69 | fixed |
| 13 | `hardness_merge_tol` | engine elements of one grain class within this hardness are one layer | 0.62 | 0.375: -0.04; 1: -0.62 | **keep** |
| 14 | `sc_facet_ground` | weight of the ground index (damp ground, deadfall, cold air pooling) on faceting and depth hoar rates | 0.59 | -0.35: +0.59; 0.35: +0.20 | fixed |
| 15 | `col_hard_facet_offset` | added to the density-based hardness of model-made facets and depth hoar | 0.53 | -1: -0.17; 0.25: -0.53 | **keep** |
| 16 | `sc_facet_canopy` | weight of the canopy index (trees closing in) on faceting and depth hoar rates | 0.49 | -0.35: +0.49; 0.35: +0.20 | fixed |
| 17 | `sc_crust_ground` | weight of the ground index (damp ground, deadfall, cold air pooling) on ease of crust formation | 0.42 | -0.35: +0.16; 0.35: +0.42 | fixed |
| 18 | `depth_spread_floor_m` | minimum half-width of the depth interval | 0.42 | 0.025: -0.00; 0.175: -0.42 | **keep** |
| 19 | `sc_wind_ground` | weight of the ground index (damp ground, deadfall, cold air pooling) on wind speed at the plot | 0.28 | -0.35: +0.26; 0.35: +0.28 | fixed |
| 20 | `sc_lee_snow` | extra snowfall per 10 deg of shelter from the wind's direction at 8 m/s or more (lee loading; negative: scouring) | 0.24 | -0.25: +0.24; 0.25: +0.08 | fixed |
| 21 | `col_dh_hours` | hours above the depth hoar gradient that turn facets to depth hoar | 0.23 | 224: -0.23; 950: +0.16 | **keep** |
| 22 | `col_pp_hours` | hours for new snow (PP) to decompose to DF near 0 degC | 0.22 | 15: +0.19; 72: -0.22 | fixed |
| 23 | `sc_snow_exposure` | weight of the exposure index (wind exposure and standing above the surroundings) on precipitation | 0.19 | -0.35: +0.19; 0.35: -0.07 | fixed |
| 24 | `sc_ground_canopy` | weight of the canopy index (trees closing in) on ground temperature (2 K per unit) | 0.18 | -0.35: -0.18; 0.35: +0.17 | fixed |
| 25 | `presence_confidence` | probability a predicted layer is present (scaled per agent) | 0.17 | 0.5: +0.15; 0.85: -0.17 | fixed |
| 26 | `sc_facet_exposure` | weight of the exposure index (wind exposure and standing above the surroundings) on faceting and depth hoar rates | 0.17 | -0.35: +0.17; 0.35: +0.09 | fixed |
| 27 | `col_round_hours` | hours under a weak gradient that round facets back to rounded grains (facets round slowly, weeks to months) | 0.15 | 420: -0.12; 1560: +0.15 | fixed |
| 28 | `sc_crust_canopy` | weight of the canopy index (trees closing in) on ease of crust formation | 0.12 | -0.35: +0.05; 0.35: +0.12 | fixed |
| 29 | `sc_wind_exposure` | weight of the exposure index (wind exposure and standing above the surroundings) on wind speed at the plot | 0.12 | -0.35: +0.12; 0.35: -0.00 | fixed |
| 30 | `col_dh_gradient_k_m` | gradient that grows facets into depth hoar | 0.11 | 15: -0.03; 40: +0.11 | fixed |
| 31 | `sc_shelter_wind` | wind slows by exp(-this x shelter / 10 deg) from the direction it blows from (map shelter, Winstral Sx) | 0.11 | 0.5: +0.11 | fixed |
| 32 | `sc_wind_canopy` | weight of the canopy index (trees closing in) on wind speed at the plot | 0.10 | -0.35: +0.10; 0.35: +0.07 | fixed |
| 33 | `sc_crust_exposure` | weight of the exposure index (wind exposure and standing above the surroundings) on ease of crust formation | 0.09 | -0.35: +0.02; 0.35: +0.09 | fixed |
| 34 | `col_df_hours` | hours under a weak gradient for DF to become rounded grains | 0.04 | 72: +0.02; 300: -0.04 | fixed |
| 35 | `sc_ground_exposure` | weight of the exposure index (wind exposure and standing above the surroundings) on ground temperature (2 K per unit) | 0.03 | -0.35: +0.02; 0.35: -0.03 | fixed |
| 36 | `col_sh_max_wind_ms` | surface hoar grows only at this wind or less | 0.00 | 1.75: +0.00; 4.5: +0.00 | fixed |
| 37 | `boundary_spread_m` | half-width of layer boundary intervals | 0.00 | 0.025: +0.00; 0.175: +0.00 | fixed |
| 38 | `col_sh_destroy_wind_ms` | wind above this removes surface hoar on the surface | 0.00 | 4.5: +0.00; 10.5: +0.00 | fixed |
| 39 | `sc_shelter_scale` | blend of the shelter within 300 m (0) and within 2 km (1) | 0.00 | 0.25: +0.00; 0.75: +0.00 | fixed |
