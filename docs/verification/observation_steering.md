# Observation steering: how much should a pit move the simulation? (ADR-038)

Decision support only, not an avalanche forecast. Reproduce with `snowagent.learn.steer.run_experiment`,
`run_experiment2` and `snowagent.learn.steer_report` (raw rows: `artifacts/steer/exp1_depth_nudge.csv`,
`exp2_reinit.csv`, regenerable).

## Set-up
- Pit pairs: consecutive study-plot pits at the same plot 3-45 days apart, both with snow depth, model >= 20 cm
  at the first pit. 156 pairs, 2015-16 .. 2025-26, Bow Summit, Simpson, Sunshine Goat's Eye; median gap 15.9 days.
- Each pair: start from the free (measured-weather) run's restart state at the first 00 UTC after pit A, apply
  the update, run SNOWPACK on measured weather to pit B, score against pit B (which the update never saw).
- Weight chosen leave-one-season-out: for each held-out season the weight with the lowest mean next-pit depth
  error on the other seasons, scored on the held-out season. Never a random split within a season.

## Experiment 1: depth update, weight w
Every layer thickness (and mass) scaled by f = 1 + w (HS_pit / HS_model - 1).

| w | next-pit abs. depth error (cm) | bias (cm) | grain agreement | hardness MAE (index) | boundary F1 |
|---|---|---|---|---|---|
| 0 (free run) | 15.3 | +5.6 | 0.474 | 0.945 | 0.209 |
| 0.25 | 12.7 | +4.5 | 0.479 | 0.931 | 0.213 |
| 0.5 | 10.3 | +3.3 | 0.485 | 0.918 | 0.220 |
| 0.75 | 8.4 | +2.1 | 0.487 | 0.911 | 0.220 |
| 1 | 7.4 | +1.0 | 0.494 | 0.906 | 0.216 |

Leave-one-season-out, held-out seasons:

| scope | seasons | w chosen | free (cm) | updated (cm) | seasons better / worse | grain free -> updated |
|---|---|---|---|---|---|---|
| all plots | 11 | 1 in 11 | 15.3 | 7.4 | 11 / 0 | 0.473 -> 0.494 |
| Sunshine Goat's Eye | 11 | 1 in 11 | 19.7 | 7.5 | 11 / 0 | 0.507 -> 0.538 |
| Bow Summit | 11 | 1 in 11 | 13.0 | 7.8 | 10 / 1 | 0.427 -> 0.443 |
| Simpson | 9 | 1 in 8, 0.75 in 1 | 12.0 | 6.7 | 9 / 0 | 0.523 -> 0.531 |

Optimal-interpolation check (w = s_m^2 / (s_m^2 + s_o^2)): pit-to-pit depth noise from the 6 same-plot pit pairs
within 3 days is s_o = 7.2 cm; the free run's error at pits is 16.4 cm SD, so s_m = 14.8 cm and w_OI = 0.81. With
a pessimistic s_o = 15 cm (pit placed off the sensor spot) w_OI = 0.17. The tested optimum (1) sits above w_OI
because the free run's depth error persists for weeks (it is mostly a precipitation bias, not noise).

## Experiment 2: structure from the pit
Re-initialisation: the layering replaced by the pit's (grain class -> SNOWPACK microstructure by class medians,
hand hardness -> density, temperatures from the model). Same 156 pairs.

| method | next-pit abs. depth (cm) | grain agreement | hardness MAE | boundary F1 |
|---|---|---|---|---|
| free run | 15.3 | 0.474 | 0.945 | 0.209 |
| depth update (w = 1, adopted) | 7.4 | 0.494 | 0.906 | 0.216 |
| re-initialised from the pit | 12.8 | 0.564 | 0.893 | 0.260 |
| previous pit carried forward | 17.0 | 0.547 | 0.768 | 0.348 |

Re-initialised grain agreement by plot: Goat's Eye 0.550, Simpson 0.554, Bow Summit 0.580.

## Reading
- Pits fix depth well: the update halves the next-pit depth error, in every season, at every plot.
- Pits carry structural information the model loses: re-initialising raises grain agreement more than any weight
  of the depth update, but its depth drifts (hardness -> density is coarse) so it is not adopted yet. The
  persistence row is the bar a structural update has to clear for hardness and layer boundaries; no model method
  clears it yet, so the site keeps showing the observed pit beside the simulation.
- Next to test: re-initialise structure and match depth together; a pit weight that decays with days since the pit.

## General learning: pits in the precipitation-factor choice
`calibrate.loso(pit_weight=w)`: the factor per plot chosen on the other seasons by (1 - w) x sensor depth MAE +
w x pit depth error, 11 seasons 2015-16 .. 2025-26 on measured weather (raw: precip_loso_11seasons.json).

| plot | pit weight | factor chosen | held-out sensor MAE (cm) | held-out pit depth error (cm) | adopted |
|---|---|---|---|---|---|
| Goat's Eye | 0, 0.5 | 0.9 (11/11) | 16.3 -> 13.0 (8/11 better) | 18.0 -> 10.1 (11/11 better) | yes, 0.9 |
| Goat's Eye | 1 | 0.8 (11/11) | 16.3 -> 17.3 | 18.0 -> 8.8 (8/11) | no: worse on the sensor |
| Simpson | 0, 0.5, 1 | 1.15 (11/11) | 19.8 -> 16.8 (7/11) | 19.3 -> 9.8 (7/10) | yes, 1.15 (unchanged) |
| Bow Summit | 0, 0.5, 1 | 1.15 (10/10) | 15.3 -> 11.1 (9/10) | 13.0 -> 10.9 (6/10) | yes, 1.15 (unchanged) |

Pits and the sensor agree on the factor except at Goat's Eye, where pits alone push lower (0.8) at the cost of
the continuous sensor record; a pit weight of 0.5 keeps the sensor's choice, so 0.5 is the recorded default. The
earlier 5-season test (ADR-024) chose 1.0 at Goat's Eye but did not test factors below 1.
