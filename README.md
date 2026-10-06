# ERA5 box months for the Snowpack Agent Lab (ADR-079)

Derived data, not code. `snowagent lab prepare` copies the files in `era5/` into `data/interim/era5/` of a clone
when they are missing there, so a fresh clone does not have to read every month from the remote mirror (about 5
minutes per month on a fast connection; on a home connection the reads often time out).

- 240 months, 199709 to 202605 (September to June of the lab's seasons; the older seasons only up to the month of
  their last pit), plus `era5_box_z.npz` (surface geopotential of the grid cells).
- Each `era5_box_YYYYMM.npz` holds hourly 2 m temperature and dewpoint, 10 m wind, surface pressure, total cloud
  cover, precipitation rate and downward shortwave and longwave radiation for the 0.25-degree cells of the box
  50.5-52.0 N, 117.0-115.25 W, as written by `snowagent.ingest.era5.extract_month`; its `.json` file records the
  source and retrieval time. Units SI, times UTC.
- Source: ERA5 hourly reanalysis (Hersbach et al., 2020) from the NSF NCAR mirror on AWS Open Data
  (s3://nsf-ncar-era5). Contains modified Copernicus Climate Change Service information 2026; neither the European
  Commission nor ECMWF is responsible for any use of it. Licence: CC-BY 4.0.

Research and decision support only, not an avalanche forecast. Fetch only this branch with
`git fetch origin claude/lab-era5-box`; `git clone --single-branch` of another branch leaves it out.
