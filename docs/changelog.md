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

## 0.2.0 — 2026-09-30
- `snowagent obs inventory`: Propagation Labs header parser (icon glyphs, unit mix, missing spaces),
  per-profile QC flags, duplicate and device-GPS detection, study-plot consensus locations.
- Uploads 2023-24, 2024-25, 2025-26: 136 profile files (64 study-plot, 64 test, 8 unclassified);
  38 with parseable header text, 34 with HS, 0 with machine-readable layers.
- Filename-date parser for all naming conventions seen (ambiguous/invalid dates flagged, never guessed);
  folder-layout classifier for the differing season structures; whitespace-tolerant header labels.
- Derived Goat's Eye and Bow Summit study-plot locations recorded (flagged as derived).
- No model behaviour change; no verification numbers (no real weather forcing yet).

## 0.3.0 — in progress
- Observed-profile builder (`observed_profiles.jsonl`): height above ground, hardness index, pit-bottom trim,
  de-duplication (content hash, identical layers, same-pit heuristic), location QC against site medians,
  printed-UTM conversion.
- Exact structured parsers: SnowPro 2.1 / 3.x / Plus XML (1997-2014) and CAAML v5 (niViz, 2018-19).
- Image transcriptions (`observations/transcriptions/`, schema `transcription-1`, IACS 2009 / OGRS symbols,
  unreviewed) with validator and guide; independent re-read QA still to do.
- No model behaviour change; no verification numbers (no real weather forcing yet).
