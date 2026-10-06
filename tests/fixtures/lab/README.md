# Lab test fixtures (SYNTHETIC)

Synthetic stand-ins for the lab's inputs; no real observation or station data.

- `observed_profiles.jsonl`: records in the layout of `data/interim/obs/observed_profiles.jsonl` (`snowagent obs
  profiles`): a height-above-ground pit with HS (Bow Summit), its duplicate, a depth chart without HS (Goat's Eye),
  a pit without HS and with a zero-thickness and a missing-boundary layer (Simpson), a pit at another plot, an
  unparsed file and a pit without a date.
- `fts360/<station>/<station>_2024-01.csv`: two days of hourly FTS360-style records for the two Bow Summit stations
  (an out-of-range temperature, a snow-depth spike, a two-hour gap, a gauge reset).

Tests copy them into a temporary checkout layout (`data/raw/fts360`, `data/interim/obs`).
