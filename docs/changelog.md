# Changelog

## 0.1.0 — 2026-09-30
- Pinned native SNOWPACK build (b324cbd) + upstream MST96 example check.
- Typed contracts (terrain unit, weather meta, checkpoint, layer/profile, forecast request/result).
- DEM/land-cover/boundary ingest with CRS/units/no-data validation; slope, aspect, horizons, sky view,
  exposure proxy; configurable terrain units.
- Weather adapter with explicit units/UTC, availability times, gap QC and leakage guards.
- Terrain-conditioned forcing with single-owner corrections (engine `PERP_TO_SLOPE=TRUE`).
- Write-once, hash-verified checkpoints; history replay from explicit snow-free start; advance with actuals.
- Forecast branching, seeded scenario ensemble, what-if branches, per-unit/member/lead profiles,
  map-ready GeoJSON/CSV, mass budget, point/profile query, plots.
- Synthetic demo fixture; 67 tests (unit + real-engine integration).
- Verification numbers: none against real data yet (synthetic only).
