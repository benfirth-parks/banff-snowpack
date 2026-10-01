# Phase 2 acceptance: Simpson 6 km domain, archived GFS run 2026-03-08 00 UTC

README §9 Phase 2: "A domain and archived forecast produce distinct future profiles at unobserved units without a
new pit; no future-weather leakage." Result: **met for this case** (all checks below pass). Second plot after
Goat's Eye (phase2_goats_eye_2026032300.md); same code and settings. One case shows the chain works on this
terrain; it does not establish forecast skill. EXPERIMENTAL structure prediction, not avalanche guidance.

Run `fc_simpson_6km_20260308T0000Z_iss20260308T0500Z_7810fffd` (5 members, leads 0/12/24/48/70/72 h, status ok).
Full numbers: `phase2_simpson_2026030800.json` (from `snowagent phase2-report`). Figure:
`figures/phase2_simpson_2026030800.png`.

## Reproduce
```
W=artifacts/phase2/simpson_2026030800
snowagent prepare-domain --domain-id simpson_6km --center-lat 50.98516 --center-lon -115.98430 \
    --out $W --size-m 6000 --unit-size-m 600 --buffer-m 12000 --sites simpson
snowagent case-inputs --plot simpson --gfs-run 2026-03-08T00:00:00Z --out $W/weather
snowagent init --domain $W/domain --history $W/weather/history.csv --until 2026-03-03T00:00:00Z \
    --initial-condition snow_free
snowagent predict --domain $W/domain --forecast $W/weather/forecast_20260308T00Z.csv \
    --actuals $W/weather/recent.csv --lead-hours 0,12,24,48,70,72
snowagent phase2-report --workspace $W --pit-id 2026-03-10_simpson_c1e4b1
```
Horizon buffer 12 km instead of 15 km: the study DEM ends 12.5 km south of the domain (horizon rays stop at the
DEM edge either way). Wall time on 4 cores, shared with another job: replay 2 min, forecast 1 min, report 1 min.

## Domain (ADR-032)
| units | supported | forest (unsupported, no canopy scheme) | rock | open | elevation (supported) | slope | aspects (supported) |
|---|---|---|---|---|---|---|---|
| 100 blocks + 1 site | 25 + site | 75 | 11 | 14 | 1253-2489 m | 4-40 deg | N 3, E 11, S 1, W 10 |

Three quarters of the blocks are forest-majority, so only 25 blocks are simulated: rock and open alpine units along
the west edge (1980-2490 m), open slopes along the north edge (1420-1840 m) and open, nearly flat valley floor in
the north-east corner (1250-1320 m). The plot is represented by the site unit `site_simpson` (flat, open, 2115 m;
ADR-026; the DEM gives 2105 m at the plot coordinates).

## Inputs and availability (ADR-033)
| series | content | declared latency | usable at issue up to |
|---|---|---|---|
| history | TA and RH Simpson Lower 100%; PSUM Sunshine gauge 95% (no gauge at Simpson), ERA5 5%; wind and radiation ERA5 | 5 days (ERA5T) | 2026-03-03 00 UTC |
| recent | TA and RH Simpson Lower 100%; PSUM Sunshine gauge 98%, GFS day-1 2%; wind and radiation GFS day-1 | 5 h | 2026-03-08 00 UTC |
| forecast | GFS 0.25 deg run 2026-03-08 00 UTC at the Simpson Lower point (surface 1882 m), raw | available 05 UTC | issue 2026-03-08 05 UTC |

Precipitation is the uncorrected Sunshine gauge (the x1.15 transfer factor of ADR-024 is not part of the Phase 2
chain). Both actuals series run to 2026-03-12, four days past the issue time, so the availability filter is
exercised rather than assumed.

## 1. Distinct future profiles at unobserved units
| lead (h) | 0 | 12 | 24 | 48 | 70 | 72 |
|---|---|---|---|---|---|---|
| local time (MST) | 7 Mar 17:00 | 8 Mar 05:00 | 8 Mar 17:00 | 9 Mar 17:00 | 10 Mar 15:00 | 10 Mar 17:00 |
| unique profiles / units (25 distinct terrains) | 25/25 | 25/25 | 25/25 | 25/25 | 25/25 | 25/25 |
| HS range (m) | 1.12-2.24 | 1.10-2.33 | 1.09-2.38 | 1.14-2.35 | 1.13-2.33 | 1.13-2.34 |
| units wet in the top 10 cm (> 0.5% by volume) | 18 | 13 | 14 | 8 | 11 | 11 |
| units with a melt-freeze crust in the top 0.25 m | 16 | 9 | 8 | 11 | 11 | 10 |
| top 10 cm density range (kg m-3) | 88-284 | 83-295 | 87-278 | 66-116 | 68-120 | 65-117 |
| new snow since the forecast start (cm) | 0 | 0-11 | 1-19 | 8-22 | 8-22 | 8-22 |

Pairwise DTW similarity between the 25 unit profiles at 72 h: mean 0.46, range 0.11-1.00 (300 pairs), no identical
pair.

Terrain response (forcing is identical at the source; differences come from the unit's terrain):
- Temperature follows elevation (rank correlation -1.00; unit means -13.3 to -5.2 C over 1253-2489 m).
- New snow follows elevation (Spearman 0.85-0.98): 8-9 cm on the valley-floor units, 18-22 cm above 1700 m.
  Precipitation is applied uniformly (18-23 mm per unit); the gradient is the phase: below 1330 m about 75% of it
  falls as rain (phase from the hourly unit air temperature, moved from the GFS surface at 1882 m by the lapse
  rate), 24-55% at 1420-1590 m, none above 1980 m.
- The valley-floor units carry the densest surface before the storm (260-295 kg m-3 in the top 10 cm). The rank
  correlation between slope shortwave and near-surface density grows through the forecast (0.06 at 12 h, 0.43 at
  48 h, 0.58 at 70 h).
- Crusts in the top 0.25 m at 72 h sit on the higher units (10 units, median 1996 m); the 15 without are lower
  (median 1584 m).

## 2. Without a new pit
No observation file is an input to `init` or `predict`; both checkpoints record `observations_used: []`. The
withheld pit is read only by `phase2-report`, after the run.

## 3. No future-weather leakage (10 audit checks + 2 refusal probes, all pass)
| check | result |
|---|---|
| forecast run available by issue time | available 05:00 UTC = issue 05:00 UTC |
| analysis checkpoint st_20260303T0000Z_v1_3198d70b: cutoff <= issue; forcing ends at its analysis time | cutoff 03-08 00 UTC; last record 03-03 00 UTC |
| advanced checkpoint st_20260308T0000Z_v1_ff9b268e: cutoff <= issue; forcing ends at its analysis time | cutoff 03-08 05 UTC; last record 03-08 00 UTC |
| forecast starts from a state at its initial time | 03-08 00 UTC |
| history / recent series extend past issue, usable part ends at the declared latency | to 03-12; usable to 03-03 05 / 03-08 00 UTC |
| probe: issue 1 h before the GFS run was available | refused (DataLeakage) |
| probe: rerun of the issued forecast | refused (ImmutableRecord) |

Forecast integrity: the 26 unit states of both checkpoints match their recorded sha256 after the forecast
(branches copy the state). Column mass budgets close to 0.10 kg m-2.

## 4. Withheld evaluation (observation after issue; not used by the run)
Pit 2026-03-10 21:57 UTC (lead 70 h, site unit): pit HS 220 cm, forecast 212 cm (members 212-214); grain-class
agreement 0.36, hand-hardness MAE 0.91 steps (forecast softer by 0.67), boundary F1 0.21, DTW similarity 0.46.

Stations during the forecast window (Simpson Lower snow depth at the plot; Sunshine gauge, ~15 km, which supplied
the history precipitation):

| lead (h) | 0 | 12 | 24 | 48 | 70 | 72 |
|---|---|---|---|---|---|---|
| site HS forecast, control (m) | 2.03 | 2.11 | 2.15 | 2.14 | 2.12 | 2.12 |
| Simpson Lower HS (m) | 1.95 | 2.05 | 2.22 | 2.18 | 2.17 | 2.16 |
| site SWE forecast, median (kg m-2) | 671 | 681 | 691 | 695 | 695 | 695 |
| Sunshine gauge since init (mm) | 0 | 17.3 | 26.9 | 28.0 | 31.4 | 31.4 |

- Initial state 8 cm deeper than the station sensor (2.03 vs 1.95 m).
- GFS gave 22.8 mm for the 72 h against 31.4 mm at the Sunshine gauge (-27%; Simpson's own precipitation is not
  measured). The forecast gained 9 cm of HS, the station 21 cm; the 5-member spread (2 cm) did not cover it.
- The pit (220 cm) is within 3 cm of the station sensor at the same time and 8 cm deeper than the forecast; unlike
  the Goat's Eye case (pit 37 cm below its station sensor), plot and station agree here.

## Limits of this result
- One archived case per plot (three cases in all). Distinctness and leakage are acceptance properties; skill needs
  many cases (hindcast across all pits with GFS runs 1-3 days before, held-out seasons and locations; Phase 4/6).
- GFS raw (no bias correction); precipitation uniform over the domain (no calibrated elevation gradient).
- 75 of 100 blocks are forest and not simulated (no canopy scheme); transport unresolved (no wind
  redistribution); wind not downscaled. No precipitation gauge at Simpson: history precipitation is transferred
  from Sunshine.
- Unit profiles represent 600 m unit-mean terrain; the display grid is not evidence of skill at finer scales.
