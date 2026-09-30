# Sample outputs (real SNOWPACK engine, SYNTHETIC inputs)

Copied from `snowagent demo` run `fc_synthetic_demo_20260115T0000Z_iss20260115T0430Z_8b7e9ae9`.
Inputs (terrain, weather, forecast) are invented; these files demonstrate software and process behaviour
only and say nothing about Banff accuracy.

- `manifest.json` provenance, capability flags (transport unresolved), perturbations, budget residual
- `summary.csv`, `lead_024h.geojson` map-ready per-unit ensemble summaries
- `u003_001_member0.jsonl` full layered profiles (control member, all leads) for one unit
- `profile_query_u003_001_lead24h.json` output of `snowagent profile`
- `hs_map.png`, `profiles_contrast_lead072h.png`, `profile_u003_001_lead24h.png` inspection plots
- `mass_budget.csv`, `forcing.csv` per unit x member diagnostics
