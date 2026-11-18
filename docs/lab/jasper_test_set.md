# The Jasper test set (ADR-115)

Decision support and research only, not an avalanche forecast.

The lab's agents train on the three Banff plots. The Jasper test set scores them on pits from a plot outside Banff,
the Parker Ridge study plot in Jasper National Park, which they never train on. It answers one question: does a change
that helps in Banff carry over to a different place, or does it only fit Banff? The owner's plan (2026-11-18): Banff
stays the training set, Jasper is a held-out second scoring set, and only agents that win in both go forward.

## What is in it

| | Parker Ridge study plot |
|---|---|
| Plot | 52.19587 N, 117.10408 W, 2010 m (owner, 2026-11-02) |
| Lab | `config/lab_jasper.yaml` (extends `config/lab.yaml`: same genome allow-list, scoring and case rules), site code PARK |
| Data root | `data/lab_jasper` (its own: its cases never enter the Banff case set) |
| Pits now | 93 exact SnowPro files, 2001-02 to 2013-14 (90 after duplicates), in `profiles/<season>/Parker Ridge/` |
| Pits to come | about 38 PDF charts and photos of 2019-20 to 2024-25 and about 12 photos of 2015-18, to be transcribed |
| Left out | 40 SnowPro pits of 1994-97, which say 1849 m, about 160 m below the plot |

Weather:

- **2018-19 on (station seasons):** Big Bend (FTS360 "BIG BEND 2125m", 2.9 km south-east, 115 m higher), with
  Tangle Ridge (3009 m) when Big Bend has no temperature. Temperature and humidity are moved to the plot's height
  (lapse rate 0.0065 K/m, dew point kept), labelled `big_bend_moved_from_2125m`. Wind, precipitation (Big Bend's
  gauge, no catch correction) and snow depth stay as measured. The north-west ERA5 box fills the gaps, including the
  months whose minus signs the Jasper feed lost (2021-11-21 to 2023-06-12, ADR-108).
- **2001-02 to 2013-14 (reanalysis seasons):** ERA5 alone, from September to the month of each season's last pit,
  as Banff's older seasons (ADR-076). Scores of these cases mix ERA5's errors with the model's;
  `lab compete --weather-source` and the Leaderboard split them.
- No archived GFS reaches the Jasper plot, so its 72-hour cases use measured weather as a stand-in
  (`measured_standin`), as Banff cases without an archived run do.

Nothing is tuned to Jasper: the SNOWPACK agent's plot factors come from `config/plot_forcing.yaml`, which has no
Jasper plot (so 1.0), and per-plot genes fall back to their defaults at PARK.

## In the lab app (Mac)

1. Update the Mac as after any merge (stop the app, `git pull`, start the app).
2. **Training** page, box "Start a new training run": press **Set up Parker Ridge check**. It copies the Jasper weather
   from the bundle branch (one download of about 0.15 GB, once), loads the Parker Ridge pits and builds their cases:
   about 5 to 10 minutes. Its progress shows on the page. Press it again after new Jasper pits arrive.
3. Tick **Also score each round on the Parker Ridge pits (Jasper), never training on them** (ticked by default once
   the check is set up) and start the run as usual.
4. While it runs, the section **Parker Ridge (Jasper) check** shows the round's best agent at Parker Ridge against
   standard SNOWPACK. **Results › Training reports** adds a section "Somewhere it has never been".

## From a terminal

```bash
# inputs: the bundle branch, then the Jasper lab's own pit file (data/lab_jasper/observed_profiles.jsonl)
snowagent lab prepare-area jasper
snowagent lab init        --config config/lab_jasper.yaml --data-root data/lab_jasper
snowagent lab import      --config config/lab_jasper.yaml --data-root data/lab_jasper \
                          --profiles-file data/lab_jasper/observed_profiles.jsonl
snowagent lab build-cases --config config/lab_jasper.yaml --data-root data/lab_jasper

# training on Banff with the Parker Ridge check each round
snowagent lab train --rounds 10 --population 10 --screen-cases 30 --workers 8 --area-check jasper

# a competition at Parker Ridge (standard models, or a run's agents as <run>/<round>/<rank>)
snowagent lab compete --config config/lab_jasper.yaml --data-root data/lab_jasper \
  --agents snowpack-default --agents snowpack-era5-fix
```

A training run started with `--area-check` stores the Parker Ridge case ids in its plan: a resume scores the same
cases, and cases built after the run started are left out.

## The bundle branch

`claude/lab-jasper-data` carries what a machine without an FTS360 key or a fast connection cannot fetch:
`era5_north_west/` (the ERA5 box months the Jasper cases read, `era5_box_YYYYMM.npz|json` and `era5_box_z.npz`) and
`stations/big_bend/`, `stations/tangle_ridge/` (the converted station tables). It is refreshed from the project's
machines:

```bash
snowagent ingest fts360 --section fts360_new_sites          # Big Bend and Tangle Ridge (FTS360 key needed)
snowagent ingest era5 --region north_west --start 2018-09 --end 2026-06   # and the reanalysis months
```

then the two folders are copied to the branch (`data/interim/era5_north_west` and `data/interim/new_sites/stations`,
Fidelity and Round Hill left out) and pushed. `lab prepare-area` never overwrites an ERA5 month and replaces a station
table only when the bundle's copy differs.

## Still to do

- Transcribe the 2015-2025 Parker Ridge charts and photos (Big Bend's weather covers 2018-19 on).
- Bald Hills (site code BALD): the plot's location and height, the Maligne station (the only one nearby, hourly from
  early 2020) and a wider ERA5 box (Bald Hills is north of the north-west box's 52.5 N).
- Archived GFS at the Jasper plot, for true 72-hour forecast cases.
