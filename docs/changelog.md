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

## 0.3.0 — in progress
- Observed-profile builder (`observed_profiles.jsonl`): height above ground, hardness index, pit-bottom trim,
  de-duplication (content hash, identical layers, same-pit heuristic), location QC against site medians,
  printed-UTM conversion.
- Exact structured parsers: SnowPro 2.1 / 3.x / Plus XML (1997-2014) and CAAML v5 (niViz, 2018-19).
- Image transcriptions (`observations/transcriptions/`, schema `transcription-1`, IACS 2009 / OGRS symbols,
  unreviewed) with validator and guide.
- Wave 2 complete: 480 transcriptions (466 profiles, 14 non-profiles), all valid; blind re-read QA (36 pairs)
  and image-vs-exact QA (25 pairs) in ADR-017. Observed set: 1,059 unique observations, 732 usable at the five
  study plots (Bow Summit 257, Goat's Eye 218, Tak Falls 128, Vermilion 89, Simpson 40), 1996-97 to 2025-26.
- Fixes: transparent PNGs rendered on white; one vertical conversion for layers/temperatures/tests (ADR-018);
  locale-dependent SnowPro dates; agreement metric now covers depth-only charts.
- No model behaviour change; no verification numbers (no real weather forcing yet).
- FTS360 ingest: `requests` declared as a dependency; request windows made half-open (the API's endDate is
  inclusive, so the boundary hour was stored in two monthly files); raw files written atomically.
- FTS360 ingest: request times sent as whole seconds (fractional seconds made the current-month request fail).
- FTS360 ingest: dropped connections are retried with back-off instead of aborting the run.
