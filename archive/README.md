# archive/

Small derived extracts kept in git so they survive the ephemeral compute environment (user decision
2026-09-30). Raw sources are public and immutable; see `docs/decisions.md` ADR-019.

- `forecasts/gfs/gfs_<YYYYMMDDHH>.csv`: archived GFS 0.25 deg forecasts at the stations and study plots,
  bilinear point values with the model surface height (`model_elev_m`), leads 0-72 h every 3 h, units as
  in the column suffixes (K, %, m s-1, kg m-2, W m-2, Pa). Accumulations/averages as GFS reports them
  (`*_desc` column gives the window). Not downscaled, not bias-corrected.
- `forecasts/gfs/*.provenance.json.gz`: per GRIB message the source URL, byte range and sha256.
- `ops/runs.jsonl`: one line per `snowagent update fetch` / `update build` / `update restore-web` run (start time UTC, command, ok, exit
  code, duration, failed steps, key counts, warnings per level); appended by the CLI, never rewritten (ADR-044).
- `snowpackbc/<season>/<run>/<location>/<YYYYMMDDTHHMMZ>.json.gz`: SnowpackBC's flat-field profiles (last 3 days and
  its forecast) as its data service returned them, one snapshot a day; `index.jsonl` gives each file's URL, retrieval
  time, sha256 and time span (ADR-112). Blind-test comparison only.
