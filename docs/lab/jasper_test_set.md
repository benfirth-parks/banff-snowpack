# The Jasper test set (ADR-117)

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
| Pits now | 93 exact SnowPro files, 2001-02 to 2013-14 (90 after duplicates), and 37 transcribed charts of 2019-20 to 2024-25 (Propagation Labs, SnowPilot, niViz), in `profiles/<season>/Parker Ridge/` |
| Pits to come | about 12 photos of 2015-18, to be transcribed |
| Left out | 40 SnowPro pits of 1994-97, which say 1849 m, about 160 m below the plot; ridge, nose, treeline and Parker 6 pits; the Big Bend weather station's pits |

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

## First scores (2026-11-18)

Standard models on all 165 Parker Ridge cases: 89 three-day forecasts and 76 next-pit cases from the winters 2001-02
to 2013-14, all on ERA5 weather, nothing tuned to Jasper. Scores run from 0 to 1, higher is better (scoring v5, as in
Banff).

| Model | Overall | Snow depth | Layering | Weak layers | Depth error |
|---|---|---|---|---|---|
| Standard SNOWPACK | 0.539 | 0.468 | 0.531 | 0.546 | 16.1 cm |
| With the new-snow check (ADR-103) | 0.550 | 0.468 | 0.531 | 0.586 | 16.1 cm |

- The new-snow check helps at Parker Ridge too, for the same reason as in Banff: fewer weak layers forecast where the
  pit had none (false facet layers 231 to 164, depth hoar 43 to 27, surface hoar 31 to 21). It was better on 42 cases,
  worse on 25 and the same on 98.
- The snow-depth model (ADR-102) and the snowfall fix (ADR-106) score the same as their parents here: these winters
  have no snow depth sensor and Parker Ridge has no snowfall factor.
- SNOWPACK's snow is too shallow on 72% of the pits, by 12 cm on average (the pits average 127 cm).
- Crusts are the weakest part, as in Banff: 57 of 133 pit crusts found.
- For comparison, Banff's 945 pits (October 2026): standard 0.586, new-snow check 0.609, snowfall fix 0.619. Parker
  Ridge scores lower, as expected for reanalysis weather at a plot nothing was tuned to.
- Cost: the 165 cases with 4 standard models took 4 minutes on 3 workers in the cloud. In training the check scores
  every starting agent in round 1 and the round's top agents after that, so it adds minutes to a round.

## With the transcribed charts (2026-11-18)

The 37 study plot charts of 2019-20 to 2024-25 add 62 cases (34 three-day forecasts, 28 next-pit), the first at
Parker Ridge on the plot's own station weather: 227 cases in all (123 three-day forecasts, 104 next-pit). Three new
pits wait on the owner's review list and are left out, as every flagged pit is (two print "Big Bend" as the place,
one has a GPS fix 1.4 km from the plot). The 165 ERA5-winter cases score exactly as above.

| Model | All 227 cases | 62 station-winter cases | Depth error (station winters) |
|---|---|---|---|
| Standard SNOWPACK | 0.561 | 0.632 | 10.3 cm |
| With the new-snow check (standard model, snowfall from the depth sensor) | 0.573 | 0.629 | 13.4 cm |
| Snow-depth model (ADR-102) | 0.557 | 0.610 | 13.4 cm |
| New-snow check on precipitation (test genome, not a standard model) | 0.575 | 0.647 | 10.3 cm |

- The station winters score higher than the ERA5 winters for every model (standard 0.632 against 0.539). Station
  weather instead of reanalysis is the likely main reason, but the winters, observers and chart formats differ too, so
  the two groups are not a clean comparison.
- The snow depth sensor does not help here. Banff's sensors stand at the plots; Parker Ridge's only sensor is Big
  Bend's, 2.9 km away and 115 m higher. Driving snowfall from it leaves the snow 4.8 cm too shallow on average
  (standard: 0.8 cm) and the depth error grows from 10.3 to 13.4 cm.
- The new-snow check still carries over: weak layers 0.618 to 0.712 on the sensor base, 0.618 to 0.679 on the
  precipitation base, with false facet layers 60 to 38 (sensor base) or 50 (precipitation base) and false depth hoar
  20 to 15 or 7. On the precipitation base it was better than standard SNOWPACK on 15 of the 62 cases, worse on 5
  and the same on 42.
- Seasons: 2019-20 5 cases, 2020-21 15, 2021-22 5, 2022-23 13, 2023-24 19, 2024-25 5. The first pit of each
  season has no next-pit case (no earlier pit to start from).
- Cost: all 227 cases with 4 standard models took 7 minutes on 3 cloud workers.

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

- Transcribe the 12 or so Parker Ridge photos of 2015-18 (before Big Bend's record, so ERA5 winters).
- Decide whether the Jasper lab should read Big Bend's snow depth as the plot's: the station is not at the plot,
  and the sensor models lose depth accuracy there.
- Bald Hills (site code BALD): the plot's location and height, the Maligne station (the only one nearby, hourly from
  early 2020) and a wider ERA5 box (Bald Hills is north of the north-west box's 52.5 N).
- Archived GFS at the Jasper plot, for true 72-hour forecast cases.
