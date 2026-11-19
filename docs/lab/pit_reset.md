# The pit reset: what it is worth, and tests of a smarter one (ADR-124)

Decision support and research only, not an avalanche forecast.

The owner asked (2026-11-19) to try correcting the model with each new pit: reset the modelled snowpack to the pit and
run forward. That correction is already part of every SNOWPACK run of the lab and the site (ADR-038, ADR-039, October
2026), the column model starts from the latest pit by design (ADR-118), and a case never shows the pit it is scored
against. So this page records what the reset is worth, how long a pit keeps helping, and what came of the attempts to
use a pit better. Nothing was adopted: every default, score and stored result of the adopted run is as it was.

## The reset as adopted

At the first 00 UTC after each pit of the season that a case may see, the SNOWPACK run restarts from a state built
from the pit (`learn.steer.pit_to_sno`):

- the pit's layers as 2 cm elements, each with the microstructure of its grain class and a density from the layer's
  measured density or, without one, from its hand hardness;
- the densities scaled so that the pack holds the model's water equivalent times pit depth over model depth, which is
  the model's mean density at the pit's depth;
- the model's temperatures and deposition dates at the same relative height.

A pit without usable layers corrects the depth only (ADR-038). Not used: the pit's temperature readings (tested
below, no clear gain), grain sizes, wetness and stability tests. Where the snow depth sensor drives the snowfall
(ADR-100), it goes on driving it after the reset, from its own reading.

## Test switches

`lab compete` can run the SNOWPACK agents with another reset. The defaults are the adopted run.

| Option | What it does |
|---|---|
| `--pit-reset layers` | the adopted reset (ADR-039) |
| `--pit-reset depth` | the pit's depth only: the model's own layers scaled to it (ADR-038) |
| `--pit-reset layers_model_mass` | the pit's layers holding the model's own water equivalent, so the model's amount of snow is kept and only its layering is replaced |
| `--pit-reset off` | pits are never used |
| `--sensor-trust X` | only where the sensor drives the snowfall: the reset goes to a depth between the sensor's (0) and the pit's (1), and the sensor then drives changes from that depth. At 1 the sensor's offset from the pit is removed at every pit |
| `--learn-precip-cm C` | the winter's snowfall error is learned from its pits. After each pit of a stretch that ran on precipitation, the precipitation that follows is multiplied by (snow that should have fallen since the last reset + C) / (snow the unscaled precipitation gave + C), kept within 0.5 to 2.0. C cm of snow at no error is the starting belief, so one pit does not decide; a stretch with less than 5 cm of new snow teaches nothing |

A run with a test switch names it in its plan (so it cannot be resumed with another reset), never reuses a site run,
and has restart segments and cached engine profiles of its own. Training (`lab train`) has no such switch.

```bash
snowagent lab compete --agents snowpack-era5-fix --pit-reset off --workers 4
snowagent lab compete --agents snowpack-era5-fix --sensor-trust 1.0 --learn-precip-cm 30 \
    --config config/lab_jasper.yaml --data-root data/lab_jasper
```

## How it was measured

Cloud runs of 2026-11-19. The standard model with every adopted change (`snowpack-era5-fix`: snow depth sensors,
new-snow check, snowfall fix) ran on all 945 Banff cases and all 227 Parker Ridge cases (ADR-117), once per reset.
The column model (ADR-118) ran at default settings, as it is and with its starting pit hidden. "Training" is the 877
Banff cases of the winters to 2022-23, "locked" the 68 cases of 2023-24 to 2025-26; nothing is ever trained on Parker
Ridge.

Scores are out of 100 here (the lab's 0 to 1 score times 100, scoring v5). A difference is paired on the same cases
(mean of the case scores), with a 90% interval from resampling whole winters. Differences under about half a point are
mostly within that interval.

The rule was fixed before the runs: a change is adopted only if it helps on the training winters, and the locked
winters and Parker Ridge then confirm it. They never choose.

## What the reset is worth

Leaderboard score, with the mean snow depth error in brackets.

| Run | Banff training (877) | Banff locked (68) | Parker Ridge (227) |
|---|---|---|---|
| SNOWPACK, pits never used (`off`) | 54.2 (12.0 cm) | 52.0 (12.6 cm) | 41.8 (40.0 cm) |
| SNOWPACK, the pit's depth only (`depth`) | 57.2 (8.8 cm) | 53.0 (11.4 cm) | 53.1 (14.1 cm) |
| SNOWPACK, the pit's layers, the model's own mass (`layers_model_mass`) | 61.4 (9.3 cm) | 57.7 (11.2 cm) | 46.5 (33.5 cm) |
| SNOWPACK, adopted (`layers`) | 62.1 (8.7 cm) | 58.8 (10.8 cm) | 57.2 (15.4 cm) |
| Column model, starting pit hidden | 56.5 (11.7 cm) | 55.2 (12.2 cm) | 42.9 (41.5 cm) |
| Column model as it is | 60.4 (9.3 cm) | 58.5 (11.8 cm) | 55.7 (15.7 cm) |
| The last pit carried forward unchanged | 47.5 (14.9 cm) | 37.8 (25.2 cm) | 43.2 (20.2 cm) |

- The adopted reset is worth +7.9 to SNOWPACK on the training winters (6.7 to 9.1; better in all 25 winters), +7.9 on
  the locked winters (7.0 to 8.8; 3 of 3) and +15.7 at Parker Ridge (13.3 to 18.0; all 18 winters that had an earlier
  pit to use).
- The pit's depth alone gives +3.0 of that on the training winters, +1.2 on the locked winters and +11.4 at Parker
  Ridge, where the run without pits is 36 cm too shallow on average: nothing calibrates the snowfall there. The pit's
  layers give the rest (+4.9, +6.7, +4.3).
- Both halves are needed. The pit's layers on the model's own mass lose 0.8 on the training winters and 11.8 at Parker
  Ridge against the adopted reset.
- The column model gains +4.1 on the training winters (2.7 to 5.6; 18 of 25 winters), +3.0 on the locked winters and
  +13.2 at Parker Ridge. Without any pit of the winter it is the better model in Banff (55.7 against SNOWPACK's 48.8
  on the 60 training cases before a winter's first pit) and not at Parker Ridge (43.6 against 46.0, 19 cases).
- A model run between pits is worth far more than the pit alone: carrying the last pit forward scores 14.6 below the
  adopted run on the training winters and 20.9 below on the locked winters.

### How long a pit keeps helping

Mean case score without pits and with the adopted reset, by the days from the winter's last visible pit to the scored
pit.

| Days since the last pit | Banff training: cases | without | with | Parker Ridge: cases | without | with |
|---|---|---|---|---|---|---|
| no pit of the winter yet | 60 | 48.8 | 48.8 | 19 | 46.0 | 46.0 |
| 1 to 7 | 29 | 58.8 | 66.9 | 16 | 41.3 | 54.8 |
| 8 to 14 | 314 | 53.8 | 62.5 | 66 | 40.3 | 60.5 |
| 15 to 21 | 361 | 53.9 | 62.6 | 62 | 40.1 | 58.2 |
| 22 to 35 | 89 | 55.7 | 64.1 | 38 | 40.7 | 57.2 |
| 36 or more | 24 | 54.3 | 56.5 | 26 | 42.8 | 52.8 |

In Banff the gain holds at 8 to 9 points for five weeks and then drops to 2. At Parker Ridge it shrinks slowly, from 20
points in the second week to 10 after five weeks. The reset also matters more as a winter goes on: with one pit seen a
Banff training case scores 53.7 without and 64.0 with, with eight or more 44.6 and 57.7. A run left alone drifts.

## Tests of a smarter reset

Each row is the test minus the adopted reset on the same cases (90% interval in brackets).

| Test | Banff training | Banff locked | Parker Ridge | Outcome |
|---|---|---|---|---|
| `--sensor-trust 1.0`: the sensor drives changes from the pit's depth | +0.11 (-0.05 to +0.29) | +0.01 (-0.64 to +0.60) | +0.41 (-0.01 to +0.92) | no effect in Banff; kept as a switch |
| `--sensor-trust 0.5`: reset half-way between sensor and pit | +0.06 (-0.06 to +0.18) | +0.14 (-0.42 to +0.73) | -1.27 (-2.49 to -0.24) | dropped |
| `--learn-precip-cm 30`: the winter's snowfall error learned from its pits | -0.26 (-0.46 to -0.07) | +0.08 (+0.02 to +0.15) | +1.05 (-0.15 to +2.33) | slightly worse in Banff; kept as a switch |
| Old snow keeps the hardness of the pit layer it came from | +0.22 (+0.13 to +0.32) | -0.07 (-0.18 to +0.05) | +0.14 (-0.11 to +0.38) | dropped (see the older winters) |
| The same, and old snow is layered along the pit's layer boundaries | +0.61 (+0.17 to +1.03) | -0.15 (-0.38 to +0.09) | +1.39 (+0.30 to +2.70) | dropped (see the older winters) |
| Every forecast layer 0.4 hand-hardness steps harder | +0.26 (+0.15 to +0.36) | -0.05 (-0.20 to +0.11) | +0.23 (+0.08 to +0.36) | dropped (see the older winters) |
| Old snow keeps the pit's grain types | -0.88 (-1.33 to -0.41) | -1.35 (-2.13 to -0.52) | not run | worse: the model's ageing of old snow helps |
| The model's snow temperatures corrected to the pit's readings | +0.11 (+0.02 to +0.21) | +0.18 (-0.07 to +0.45) | -0.02 (-0.31 to +0.24) | too small to adopt; not built |

The first three are engine runs with the switches above, and the last an engine run with a scratch engine that was
not built into the lab. The others were rescored from the stored adopted runs: every element that was in the pack at
the last reset was tied to the pit layer it was made from, and the forecast layers were rebuilt with that memory. They
need no engine change and were not built into the lab either.

**The sensor.** In Banff each plot's sensor reads within 4 to 6 cm of the pits (Bow Summit +1.1 cm, Goat's Eye +3.2 cm,
Simpson -5.3 cm on average), so there is little to remove. Parker Ridge's sensor is Big Bend, 2.9 km away, and reads
24.8 cm shallower than the Parker Ridge pits on average. After a reset to the pit's depth the run saw no new snow
until the sensor had climbed past that depth, so it missed the first snowfall after every pit. With `--sensor-trust 1.0`
the depth error on the 62 Parker Ridge cases of winters with a sensor falls from 13.4 to 10.7 cm and their score rises
by 1.5 (0.1 to 3.5). On the 272 Banff training cases with a sensor the change is +0.35 (-0.19 to +0.88).

**The snowfall.** Banff's snowfall factors were calibrated on these winters, so one pit's depth error is mostly chance
and learning from it adds noise (-0.37 on the reanalysis winters, -0.04 on the station winters). Parker Ridge has no
factor and runs short of snow all winter, so its pits carry a signal: the depth error falls from 15.4 to 14.3 cm and
the bias from -9.9 to -7.2 cm. The learning acts only in stretches that run on precipitation: at Parker Ridge the
reanalysis winters (+1.36, -0.24 to +2.95, on 165 cases), not the station winters, where the sensor drives the
snowfall (3 of 62 cases change). With a starting belief of 15 cm instead of 30 the Parker Ridge gain is +1.26 (+0.22 to
+2.40) and with 60 cm +0.63 (-0.28 to +1.54). These are a check of how much the choice matters, not a choice: 30 cm
was set before the runs.

To check that the learning does what it should where the snowfall really is wrong, Banff's snowfall was made wrong
on purpose: the three plots' snowfall factors (genes `sp_precip_mult_*`) times 0.7 or 1.4, on the 597 training cases
of the reanalysis winters, where no sensor drives the snowfall. The learning columns are the paired difference to the
same input without learning.

| Snowfall input | Score without learning | Depth error | Learning, 30 cm | Learning, 15 cm |
|---|---|---|---|---|
| as calibrated (the adopted run) | 62.9 | 9.0 cm | -0.37 (-0.66 to -0.07) | -0.59 (-0.98 to -0.20) |
| 30% too low | 60.9 | 10.3 cm | +0.57 (+0.12 to +1.07) | +0.69 (+0.23 to +1.18) |
| 40% too high | 61.2 | 10.7 cm | +1.26 (+0.63 to +1.91) | +1.04 (+0.32 to +1.77) |

Either error costs about 2 points (-1.79 and -1.94 against the calibrated input). Learning wins back a third of it when
the snowfall is too low and two thirds when it is too high. The learned multiplier at the last reset settles at a
median of 1.23 (30 cm) and 1.27 (15 cm) where 1.43 would undo the error, and at 0.75 and 0.72 where 0.71 would. A
weaker starting belief learns a little faster and costs more where the input is right, so 30 cm stays. The switch is
for plots whose snowfall input is likely to be well off.

**The older winters mislead.** The three hardness tests gain on the training winters and nothing on the locked
winters. Split by weather source, the gain of the fifth row is +0.82 (+0.36 to +1.33) on the 597 Banff training cases
of the reanalysis winters (to 2013-14) and +0.14 (-0.66 to +0.87) on the 280 of the station winters; at Parker Ridge
+2.21 (+0.82 to +3.74) on the 165 reanalysis-winter cases and -0.78 (-1.23 to -0.09) on the 62 station-winter cases.
The model's hand hardness is 0.39 steps softer than the pits in the training winters and 0.14 in the locked winters,
and a pit's hardness carried to the next pit is off by 0.72 steps in the training winters and 0.84 in the locked ones.
The older pits repeat their hardness from pit to pit more than today's do. Two thirds of the training cases (68%) are
from those winters, so a change can gain in training and do nothing for the pits being dug now. The locked winters
caught it; the rule held.

**The pit's temperatures.** The adopted reset keeps the model's snow temperatures, although 9 of 10 pits have at
least three temperature readings (809 of the 892 pits the Banff runs reset to, 201 of 217 at Parker Ridge; fewer in the
winters since 2022-23, 57% and 64%). A scratch engine used them: the difference pit minus model at each reading,
interpolated over the pack and added to the model's temperatures, fading to nothing over the top 30 cm, because the
pit was dug hours before the reset at 00 UTC. Below those 30 cm the model was already within about 1 K of the pit
(mean absolute difference 1.1 K at both; on average the model is 0.1 K warmer than the pits in Banff and 0.5 K colder
at Parker Ridge; 13% and 12% of pits differ by more than 2 K). So there is little to correct, and the scores
say the same: +0.11 on the training winters (better in 14 of 25 winters), +0.18 on the locked winters (2 of 3) and
-0.02 at Parker Ridge (9 of 19). On the training winters the gain is larger where the pit was further from the model
(-0.14 on the 164 cases whose last reset differed by under 0.5 K, +0.36, -0.02 to +0.80, on the 100 at 2 K or more),
as a real effect would be. On the locked winters it is not: nothing on the 38 cases whose last reset had pit
temperatures, and Parker Ridge shows no clear pattern. With this many ideas tested, a tenth of a point just clear of its
interval can come from chance alone. It was not adopted and no switch was built; the scratch engine is kept with the
study's scripts in the project's shared files.

## Where the remaining error sits

SNOWPACK with the adopted reset, Banff training winters.

- Snow that was in the pack at the last pit is 78% of a forecast profile. Its grain type is right 66% of the time,
  against 58% if the pit's grain types were carried forward unchanged.
- Snow that fell since the last pit is the other 22%, and its grain type is right 40% of the time. Calling all of it
  decomposing fragments would be right 44% of the time. The forecast has fresh snow in 27% of it where the pits have
  14%, decomposing fragments in 29% against 44%, facets in 31% against 24% and rounds in 7% against 16%.
- Surface hoar: 50 of the 125 layers the pits show are found with the reset and 2 without (the column model: 54 and
  0). Nearly every surface hoar layer either model finds was recorded by an earlier pit. Neither forms one of its own
  that a later pit confirms.
- Facets and depth hoar are found in more than 8 of 10 cases with or without pits. The reset removes false ones:
  facet layers no pit shows fall from 1,711 to 639, depth hoar from 352 to 91, crusts from 1,357 to 453.

So the reset is doing its part, and what is left is in the snow after the last pit: the grain types of new snow and
surface hoar that forms between pits. Those are questions for the model and its training, not for the reset.

## Not done

- No default changed. Whether Parker Ridge should run with `--sensor-trust 1.0` or `--learn-precip-cm` is the owner's
  call: both were found to help by looking at Parker Ridge scores, so adopting them on that evidence would spend part
  of that set's independence. The first acts in today's winters there, the second only in winters without a sensor.
  Bald Hills, when its pits arrive, can test them cleanly.
- The pit's grain sizes are not used and were not tested: the score does not judge grain size, and SNOWPACK types a
  layer partly from its size, so a pit's sizes would first have to be made to agree with its grain types.
- The pit's temperature readings have no switch. If a later plot's model runs several degrees off its pits, the
  scratch engine is the place to start.
- The site's daily runs use the same reset code and were not rerun.
