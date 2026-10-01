# Phase 2 acceptance: Bow Summit 6 km domain, archived GFS run 2026-03-02 00 UTC

README §9 Phase 2: "A domain and archived forecast produce distinct future profiles at unobserved units without a
new pit; no future-weather leakage." Result: **met for this case** (all checks below pass). Third plot after
Goat's Eye and Simpson; same code and settings. A quiet case (3 mm forecast precipitation in 72 h): the terrain
signal comes from sun and elevation, not a storm. It does not establish forecast skill. EXPERIMENTAL structure
prediction, not avalanche guidance.

Run `fc_bow_summit_6km_20260302T0000Z_iss20260302T0500Z_3c9e0244` (5 members, leads 0/12/24/48/68/72 h, status ok).
Full numbers: `phase2_bow_summit_2026030200.json` (from `snowagent phase2-report`). Figure:
`figures/phase2_bow_summit_2026030200.png`.

## Reproduce
```
W=artifacts/phase2/bow_summit_2026030200
snowagent prepare-domain --domain-id bow_summit_6km --center-lat 51.70946 --center-lon -116.47950 \
    --out $W --size-m 6000 --unit-size-m 600 --buffer-m 12000 --sites bow_summit
snowagent case-inputs --plot bow_summit --gfs-run 2026-03-02T00:00:00Z --out $W/weather
snowagent init --domain $W/domain --history $W/weather/history.csv --until 2026-02-25T00:00:00Z \
    --initial-condition snow_free
snowagent predict --domain $W/domain --forecast $W/weather/forecast_20260302T00Z.csv \
    --actuals $W/weather/recent.csv --lead-hours 0,12,24,48,68,72
snowagent phase2-report --workspace $W --pit-id 2026-03-04_bow_summit_07eea7
```
Horizon buffer 12 km instead of 15 km: the study DEM ends 13.8 km north of the domain. Wall time on 4 cores:
replay 4.5 min, forecast 2 min, report 2.5 min.

## Domain (ADR-032)
| units | supported | forest | rock | open | water, glacier | elevation (supported) | slope | aspects (supported) |
|---|---|---|---|---|---|---|---|---|
| 100 blocks + 1 site | 43 + site | 51 | 36 | 7 | 3, 3 | 1996-2862 m | 2-32 deg | N 8, E 6, S 8, W 21 |

Forest, water and glacier blocks are not simulated (no canopy, lake or glacier-ice scheme). The supported units are
alpine rock and open terrain, mostly 2400-2860 m (32 of 43). The plot is represented by the site unit
`site_bow_summit` (flat, open, 2040 m; ADR-026; the DEM gives 2037 m at the plot coordinates).

## Inputs and availability (ADR-033)
| series | content | declared latency | usable at issue up to |
|---|---|---|---|
| history | TA and RH Bow Summit 100%; PSUM Bow Summit gauge 99.5%, ERA5 0.5%; wind and radiation ERA5 | 5 days (ERA5T) | 2026-02-25 00 UTC |
| recent | TA and RH Bow Summit 100%; PSUM gauge 99%, GFS day-1 1%; wind and radiation GFS day-1 | 5 h | 2026-03-02 00 UTC |
| forecast | GFS 0.25 deg run 2026-03-02 00 UTC at the plot point (surface 2295 m), raw | available 05 UTC | issue 2026-03-02 05 UTC |

Precipitation is the uncorrected gauge (the x1.15 factor of ADR-024 is not part of the Phase 2 chain). Both
actuals series run to 2026-03-06, four days past the issue time, so the availability filter is exercised.

## 1. Distinct future profiles at unobserved units
| lead (h) | 0 | 12 | 24 | 48 | 68 | 72 |
|---|---|---|---|---|---|---|
| local time (MST) | 1 Mar 17:00 | 2 Mar 05:00 | 2 Mar 17:00 | 3 Mar 17:00 | 4 Mar 13:00 | 4 Mar 17:00 |
| unique profiles / units (43 distinct terrains) | 43/43 | 43/43 | 43/43 | 43/43 | 43/43 | 43/43 |
| HS range (m) | 1.49-1.73 | 1.47-1.71 | 1.46-1.71 | 1.43-1.69 | 1.46-1.70 | 1.46-1.71 |
| units wet in the top 10 cm (> 0.5% by volume) | 14 | 0 | 20 | 12 | 0 | 0 |
| units with a melt-freeze crust in the top 0.25 m | 0 | 0 | 0 | 0 | 0 | 0 |
| top 10 cm density range (kg m-3) | 88-116 | 101-122 | 105-125 | 110-143 | 95-121 | 93-112 |
| new snow since the forecast start (cm) | 0 | 0 | 0 | 0-1 | 3-4 | 3-5 |

Pairwise DTW similarity between the 43 unit profiles at 72 h: mean 0.67, range 0.44-0.98 (903 pairs), no identical
pair.

Terrain response (forcing is identical at the source; differences come from the unit's terrain):
- Afternoon surface wetting follows the sun: the 20 units wet in the top 10 cm at 17:00 MST on 2 March all face
  south to west (aspects 159-287 deg; mean slope shortwave 138 vs 92 W m-2 for the dry units). By 05:00 every unit
  is dry again, and no unit forms a melt-freeze crust in the top 0.25 m during the forecast.
- Near-surface density follows slope shortwave (Spearman 0.42-0.48 at every lead).
- Temperature follows elevation (rank correlation -1.00; unit means -11.1 to -5.5 C).
- All precipitation is solid (3 mm forecast); new snow 3-5 cm by 72 h on every unit.

## 2. Without a new pit
No observation file is an input to `init` or `predict`; both checkpoints record `observations_used: []`. The
withheld pit is read only by `phase2-report`, after the run.

## 3. No future-weather leakage (10 audit checks + 2 refusal probes, all pass)
| check | result |
|---|---|
| forecast run available by issue time | available 05:00 UTC = issue 05:00 UTC |
| analysis checkpoint st_20260225T0000Z_v1_868871d8: cutoff <= issue; forcing ends at its analysis time | cutoff 03-02 00 UTC; last record 02-25 00 UTC |
| advanced checkpoint st_20260302T0000Z_v1_f9e046f9: cutoff <= issue; forcing ends at its analysis time | cutoff 03-02 05 UTC; last record 03-02 00 UTC |
| forecast starts from a state at its initial time | 03-02 00 UTC |
| history / recent series extend past issue, usable part ends at the declared latency | to 03-06; usable to 02-25 05 / 03-02 00 UTC |
| probe: issue 1 h before the GFS run was available | refused (DataLeakage) |
| probe: rerun of the issued forecast | refused (ImmutableRecord) |

Forecast integrity: the 44 unit states of both checkpoints match their recorded sha256 after the forecast.
Column mass budgets close to 0.02 kg m-2.

## 4. Withheld evaluation (observation after issue; not used by the run)
Pit 2026-03-04 19:57 UTC (lead 68 h, site unit): pit HS 150 cm, forecast 149 cm (members 149-150); grain-class
agreement 0.32, hand-hardness MAE 1.08 steps (forecast harder by 0.36), boundary F1 0.29, DTW similarity 0.49.

| lead (h) | 0 | 12 | 24 | 48 | 68 | 72 |
|---|---|---|---|---|---|---|
| site HS forecast, control (m) | 1.53 | 1.51 | 1.50 | 1.47 | 1.49 | 1.49 |
| Bow Summit station HS (m) | 1.56 | 1.54 | 1.52 | 1.52 | 1.53 | 1.53 |
| site SWE forecast, median (kg m-2) | 475 | 474 | 474 | 473 | 475 | 476 |
| Bow Summit gauge since init (mm) | 0 | 0 | 0 | 1.3 | 5.0 | 5.0 |

- Initial state 3 cm below the station sensor; GFS gave 3.3 mm against 5.0 mm measured. Depth is within 1-5 cm
  of the station throughout. The site tool's nowcast for the same pit (measured weather up to the pit, gauge x1.15)
  scores 0.33 / 1.05 / 0.36 (grain class, hardness MAE, boundary F1), so in this quiet period the forecast step
  adds little error; the structure error is the model's, not the forecast's.

## Limits of this result
- One archived case per plot (three cases in all), and this one is quiet. Distinctness and leakage are acceptance
  properties; skill needs many cases (hindcast across all pits with GFS runs 1-3 days before; Phase 4/6).
- GFS raw (no bias correction); precipitation uniform over the domain.
- 57 of 100 blocks not simulated (forest, water, glacier); transport unresolved (no wind redistribution, no lee
  loading, which matters on the alpine units that dominate this domain); wind not downscaled.
- Unit profiles represent 600 m unit-mean terrain; the display grid is not evidence of skill at finer scales.
