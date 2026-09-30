# Terrain-to-Snowpack Forecast Product

Revision 2 | 30 September 2026 | Authoritative product and implementation correction

The goal is to train and deploy a system that takes weather forecasts and terrain and produces a realistic snowpack model across that terrain. Historical weather and field observations are the means of building and improving that predictive system, not its primary output. This specification overrides conflicting station-only scope, fixed virtual-slope production geometry, wind-transport exclusions and phase ordering in the original guide.

## Prediction contract

The user selects a point or geographic domain and a forecast issue time. The system returns future layered snowpack profiles throughout the domain, with spatial differences and uncertainty, without requiring a pit at each point or each forecast cycle.

The proposed transition is:

\[
p(S_{t+1:t+H}\mid S_t,F_{t:t+H},T,\theta)
\]

Here \(S_t\) is the current spatial snowpack-state ensemble; \(F\) is forecast meteorology available at the issue time; \(T\) is terrain and land cover; and \(\theta\) contains versioned physics configuration and learned parameters. The transition, not the explanatory LLM, generates every predicted layer.

The application should normally retrieve \(S_t\) automatically from its history, so the user-facing request can genuinely be “forecast weather + terrain → snowpack.” For a new domain, initialize from a replay of historical weather, an appropriate observed profile plus explicit transfer uncertainty, or a qualified ensemble of initial states. Never silently initialize a midwinter domain as bare ground, and never use a pit received after forecast issue time in an archived forecast.

SNOWPACK explicitly evolves snow layers and microstructure from forcing, so it supplies a defensible first transition engine; its one-dimensional formulation alone omits lateral transfers ([SNOWPACK concepts](https://snowpack.slf.ch/doc-release/html/general.html)). Maintaining a state ensemble and coupling terrain processes is the proposed engineering response, not a claim of already-established local skill.

## Product outputs

- **Queryable profile:** At a requested location and lead time, layer boundaries, grain-form distribution, density, hardness estimate, temperature, liquid water, deposition/burial timing, and provenance.
- **Spatial snowpack field:** Map-ready total depth, new snow, crust presence/depth, candidate weak-layer presence/depth and layer continuity hypotheses. Do not equate a simulated weak layer with instability or danger.
- **Time evolution:** Profiles and layer tracking at successive forecast steps; events such as new burial, warming, wetting, refreezing or erosion explicitly recorded.
- **Uncertainty:** Separate initial-state, weather, terrain-representation and model uncertainties. Ensemble fractions are scenario support until checked for probability calibration.
- **Limits:** Show effective model resolution, observation support, data freshness and unsupported processes at the queried unit. Never present a fine display raster as proof of fine-scale predictive skill.

A representative profile should come from an actual member or a documented alignment-based summary. Do not average unrelated layer indices, because member 12's third layer may not correspond to member 3's third layer.

## Terrain is an input to the physics

Generate terrain units from a DEM and land cover. Weather stations are observations feeding these units; they are not the prediction domain. A pilot can use terrain facets or clustered response units before a denser grid, with final resolution selected after domain and data inspection.

| Terrain attribute | Required role | Initial treatment and limitation |
|---|---|---|
| Elevation and source-grid elevation | Temperature, humidity and precipitation-phase adjustment | Record both elevations; avoid repeating elevation corrections already applied by a provider |
| Slope and aspect | Incident solar energy and snow-column geometry | Use actual terrain orientation, not four generic 38° slopes |
| Horizon and sky-view factor | Terrain shading and radiation exchange | Separate direct and diffuse shortwave; flag simplified longwave treatment |
| Canopy/land cover | Interception, shelter and radiation | Implement a supported canopy scheme or mark forest units unsupported |
| Ridge/lee position and directional exposure | Terrain-conditioned wind and transport | Exposure alone is a proxy, not a quantitative snow-deposition model |
| Neighbour connectivity and unit area | Conservative lateral mass transfer | Required before claiming spatial wind loading or erosion |
| Ground/substrate conditions | Lower boundary and initialization | Version assumptions and test shallow-snow sensitivity |

In Kananaskis, terrain-aware downscaling and wind/gravitational redistribution were important to reproducing snow-depth variability, but the model's two-layer snow scheme did not establish detailed weak-layer skill ([Vionnet et al., 2021](https://tc.copernicus.org/articles/15/743/2021/)). Accordingly, retain a detailed vertical stratigraphy engine while evaluating transport and snow-depth skill separately from internal-layer skill.

Each radiative or geometric correction must have exactly one owner: either the terrain forcing adapter or the snow engine. In particular, do not project shortwave onto a slope twice, and explicitly distinguish vertical depth from slope-normal thickness in storage, assimilation and display.

## Runtime pipeline

1. Validate the requested domain, terrain dataset, forecast issue time and available forcing horizon.
2. Load the most recent terrain-state checkpoint; advance it to the issue time with available actuals or labelled historical forcing.
3. Read only forecasts issued and available by that time. Preserve issue time, valid time, accumulation interval, source grid, model version and any provider downscaling.
4. Build terrain-unit forcing, including elevation adjustments, shading and local radiation geometry.
5. Branch the checkpoint into forecast ensemble members, with coherent weather and initial-state perturbations.
6. Advance each unit's layered state. If the validated transport module is active, exchange mass consistently between units and account for sublimation and boundary export.
7. Save states, diagnostics, member profiles, layer identities and map-ready outputs at each requested lead.
8. Expose structured point/profile queries and optional visualization. Text explanation is secondary.

No observation-update or model-retraining step is mandatory within this loop. New observations can create a new analysis version, but must never rewrite an already-issued forecast record.

## Transport: staged, but part of the target

The initial runnable slice is terrain-conditioned independent columns. It must already differentiate sunny versus shaded and high versus low terrain, but it must say `transport_status: unresolved` rather than imply realistic lee-slope accumulation.

The complete target adds a validated spatial transport adapter, selected after evaluating Alpine3D or another compatible solver. The cited Alpine3D adaptation couples wind downscaling with SNOWPACK erosion and horizontal redistribution, but its Antarctic accumulation evaluation is not a direct validation for steep Rockies terrain ([Keenan et al., 2023](https://gmd.copernicus.org/articles/16/3203/2023/)).

The coupling contract must specify:

- **Available material:** Erode only available eligible surface snow; reject negative layer mass.
- **Conservation:** Domain change equals snowfall/rain input minus meltwater export, sublimation and boundary fluxes, with transport only redistributing internal mass.
- **Deposition properties:** Specify transported-snow density and microstructure rather than treating drifted snow as unchanged fresh precipitation.
- **Layer history:** Preserve source lineage, expose/erode layers consistently and create deposition events; do not reuse depth as layer identity.
- **No double counting:** Disable competing virtual-slope redistribution when spatial transport is active. Snow moved between units is not additional atmospheric snowfall.
- **Excluded processes:** State whether gravitational redistribution, canopy and subgrid wind processes are enabled; omit unsupported claims.

## What is trained

Training should improve the transition from weather and terrain to the next snowpack state. Field observations are sparse supervision and independent checks, not prerequisites for runtime prediction.

| Stage | Learned target | Evidence of success |
|---|---|---|
| Terrain/weather correction | Errors in local precipitation, radiation, wind and phase, conditioned on terrain | Better weather and snow outcomes at withheld locations, not just station fit |
| State correction | Differences between predicted and observed stratigraphy | Better subsequent independent profiles, not fit to the assimilated pit |
| Transition residual | Systematic errors in layer growth, settlement, crust and facet evolution | Forecast improvements across held-out seasons and terrain classes |
| Optional emulator | Fast next-state prediction from current state, weather sequence and terrain | Reproduces the teacher across diverse scenarios and improves or preserves performance on withheld real observations |

For an emulator, synthetic training examples can be generated by replaying weather across terrain configurations with a verified physical simulator. Label these as synthetic; they carry the teacher's errors and cannot validate real-world accuracy. Include current layer states, relevant history, surface energy forcing and terrain, rather than fitting a memoryless forecast-to-profile mapping.

Learned components must preserve physical bounds, valid layer ordering and explicit mass/energy accounting, and must report out-of-distribution inputs. Start with the physical transition and add learning only where it demonstrates improvement; do not delay the first terrain forecast product until a neural network is trained.

## New software contracts

Add `terrain/`, `spatial_forcing/`, `state/`, `transport/` and `forecast/` modules to the proposed package. Keep `learn/` and `agent/` separate from deterministic forecast execution.

Required records:

- **TerrainUnit:** unit_id, polygon/centroid, CRS, area, elevation, slope, aspect, horizon bins, sky view, canopy, directional exposure, neighbour IDs and terrain_version.
- **StateCheckpoint:** state_id, analysis_time, terrain_version, engine_version, parameter_version, forcing lineage, assimilation cutoff, member weights and per-unit layers.
- **ForecastRequest:** domain_id, issue_time, forecast_source/run, horizon, output steps, initialization policy and model version.
- **ForecastResult:** run_id, unit_id, valid_time, member_id, profile layers, mass/energy diagnostics, capability flags and input provenance.

API acceptance example:

```text
predict(domain, forecast_run, initial_state="latest_valid")
  -> snowpack_field + profile_query_index + uncertainty + provenance
```

This is a target interface, not an already implemented function. Return `initialization_required` when no defensible starting state or history exists; missing pits alone must not trigger this failure.

## Validation and definition of done

The following are proposed acceptance tests, not numerical accuracy guarantees.

- **No-pit runtime:** Run a held-out period without new pits and still produce profiles from forecast weather and terrain. Evaluate against observations withheld until after issuance.
- **Terrain response:** Controlled solar-forcing cases produce appropriate differences between illuminated and shadowed slopes; elevation/phase tests respond near the rain–snow transition. These are process tests, not claims every south slope always differs in a fixed way.
- **Spatial generalization:** Hold out entire locations/terrain groups as well as seasons. Prevent nearby-pit and same-storm leakage.
- **Initialization transparency:** Different plausible initial weak-layer states remain represented when observations cannot distinguish them.
- **Forecast integrity:** Evaluate issued forecasts, not future actual weather or retrospectively corrected analyses.
- **Structural fidelity:** Measure native-depth layer errors, presence/absence and crust/weak-layer timing as well as DTW alignment scores. Height-rescaled agreement must not hide snow-depth error.
- **Transport fidelity:** Verify mass budgets and independent windward/lee snow-depth evidence before publishing resolved transport. Total-depth agreement alone does not establish realistic stratigraphy.
- **Uncertainty and limits:** Check coverage and calibration, report small sample sizes, and flag unsupported terrain rather than extrapolating silently.

Completion means a user can choose terrain, supply or select forecast weather, and retrieve plausible, uncertainty-qualified future snowpack profiles at unobserved points. Successful station calibration, an informative briefing or a visually convincing map alone does not satisfy the goal.
