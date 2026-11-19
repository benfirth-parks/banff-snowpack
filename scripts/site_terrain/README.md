# Map-derived terrain details of the study plots (ADR-122)

One-off scripts that produced `scripts/site_terrain/site_terrain_2026-11-18.json`, the source of the `maps` values in
`config/site_traits.yaml`. Run 2026-11-19 about 03:15 UTC, before any scorecard agent was trained. They read small
windows of public rasters over HTTPS (no account needed) and write raw windows and a manifest under
`data/raw/site_terrain/` (git-ignored). Run from the repository root, in this order:

    python scripts/site_terrain/site_terrain.py   # NRCan MRDEM-30: slope, aspect, horizon, sun hours, shelter, position
    curl -o data/raw/site_terrain/meta_chm_tiles.geojson \
      https://dataforgood-fb-data.s3.amazonaws.com/forests/v1/alsgedi_global_v6_float/tiles.geojson
    python scripts/site_terrain/site_cover.py     # ESA WorldCover 10 m, Meta/WRI canopy height 1 m
    python scripts/site_terrain/clearing.py       # openings around each marker, and the picture
    python scripts/site_terrain/s2_melt.py        # Sentinel-2 scene classification: spring melt-out brackets

Only the MRDEM-30 numbers (height against the ground within 500 m, aspect, shelter by wind direction) feed a model.
The tree height map is a cross-check of the site sheet and is unreliable at Goat's Eye and Parker Ridge; the
melt-out brackets are a check and are never trained on. Canada's public lidar (HRDEM) has no coverage at or within
10 km of any of the four plots. Rerunning does not change `config/site_traits.yaml`: that file is dated and fixed.
