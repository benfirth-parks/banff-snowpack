# Jasper test set inputs (ADR-115)

Data branch, not code: derived weather inputs of the lab's Jasper test set (`config/lab_jasper.yaml`), for machines
that cannot fetch them (no FTS360 key; ERA5 downloads that time out on a home connection). The owner agreed to this
branch on 2026-11-18. Nothing here is merged into `main`.

- `era5_north_west/`: ERA5 hourly box months over Jasper's Icefields to Rogers Pass (50.9-52.5 N, 118.1-116.6 W,
  ADR-108), `era5_box_YYYYMM.npz` with a `.json` provenance file each, and the cell heights `era5_box_z.npz`. Source:
  the NSF NCAR ERA5 mirror on AWS Open Data, extracted by `snowagent ingest era5 --region north_west`. The months the
  Parker Ridge cases read: September 2018 to June 2026, and for 2001-02 to 2013-14 September to each season's last pit.
- `stations/big_bend/`, `stations/tangle_ridge/`: the FTS360 Big Bend (2125 m) and Tangle Ridge (3009 m) records
  converted to the lab's station tables by `snowagent ingest fts360 --section fts360_new_sites` (the temperatures that
  lost their minus sign, 2021-11-21 to 2023-06-12, are left out there, ADR-108).

`snowagent lab prepare-area jasper` (or the lab app's Training page, "Set up Parker Ridge check") copies them into
`data/interim/era5_north_west` and `data/interim/new_sites/stations`. See `docs/lab/jasper_test_set.md` on `main`.

Decision support and research only, not an avalanche forecast.
