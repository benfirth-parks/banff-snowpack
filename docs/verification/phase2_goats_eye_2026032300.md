# Phase 2 acceptance: Goat's Eye 6 km domain, archived GFS run 2026-03-23 00 UTC

README §9 Phase 2: "A domain and archived forecast produce distinct future profiles at unobserved units without a
new pit; no future-weather leakage." Result: **met for this case** (all checks below pass). One case shows the
product works end to end on real terrain and a real archived forecast; it does not establish forecast skill.

Run `fc_goats_eye_6km_20260323T0000Z_iss20260323T0500Z_a8296c2c` (5 members, leads 0/12/24/48/66/72 h, status ok).
Full numbers: `phase2_goats_eye_2026032300.json` (from `snowagent phase2-report`). Figure:
`figures/phase2_goats_eye_2026032300.png`. EXPERIMENTAL structure prediction, not avalanche guidance.

## Reproduce
```
W=artifacts/phase2/goats_eye_2026032300
snowagent prepare-domain --domain-id goats_eye_6km --center-lat 51.089530 --center-lon -115.754620 \
    --out $W --size-m 6000 --unit-size-m 600 --sites goats_eye
snowagent case-inputs --plot goats_eye --gfs-run 2026-03-23T00:00:00Z --out $W/weather
snowagent init --domain $W/domain --history $W/weather/history.csv --until 2026-03-18T00:00:00Z \
    --initial-condition snow_free
snowagent predict --domain $W/domain --forecast $W/weather/forecast_20260323T00Z.csv \
    --actuals $W/weather/recent.csv --lead-hours 0,12,24,48,66,72
snowagent phase2-report --workspace $W --pit-id 2026-03-25_goats_eye_3ae757
```
Wall time on 4 cores: season replay 5 min (52 columns, 2025-09-15 -> 2026-03-18), forecast 3 min (advance to
init + 52 units x 5 members), report 4 min (incl. 1275 pairwise DTW alignments).

## Domain (ADR-032)
6 x 6 km square centred on the Goat's Eye plot; Copernicus GLO-30 DSM (30 m, UTM 11N) with a 15 km horizon buffer;
ESA WorldCover 2021 land cover (mode-resampled to 30 m). 600 m units (the smallest round multiple of the 30 m
cell giving units large enough to carry a mean orientation; 1200 m left 12 supported units).

| units | supported | forest (unsupported, no canopy scheme) | rock | open | elevation (supported) | slope | aspects (supported) |
|---|---|---|---|---|---|---|---|
| 100 blocks + 1 site | 51 + site | 49 | 33 | 18 | 2123-2635 m | 2-39 deg | N 9, E 15, S 11, W 16 |

The plot lies in a forest-majority block (u030_030), so it is represented by a site unit `site_goats_eye`: the
flat, open, unshaded 30 m column used for all plot baselines (ADR-026). The site is the observed location; the
51 blocks are the unobserved units.

## Inputs and availability (ADR-033)
| series | content | declared latency | usable at issue up to |
|---|---|---|---|
| history | station-first plot forcing, ERA5 fill: TA Sunshine 94% / Lookout 6%, RH Lookout 97%, PSUM Sunshine gauge 92%; wind and radiation ERA5 | 5 days (ERA5T) | 2026-03-18 00 UTC |
| recent | same stations, fill from each day's 00 UTC GFS leads 1-24 h. Sunshine station outage 03-19 02 UTC to 03-22: 79 of 127 h precipitation from GFS, TA from Lookout | 5 h | 2026-03-23 00 UTC |
| forecast | GFS 0.25 deg run 2026-03-23 00 UTC at the plot point (surface 2067 m), raw | available 05 UTC | issue 2026-03-23 05 UTC |

Both actuals series were written to 2026-03-27, four days past the issue time, so the availability filter is
exercised rather than assumed. Station values are moved to the plot elevation (2280 m) and then, like the GFS
values, to each unit by the forcing builder (lapse rate, dewpoint conservation, phase, slope/horizon shortwave).

## 1. Distinct future profiles at unobserved units
| lead (h) | 0 | 12 | 24 | 48 | 66 | 72 |
|---|---|---|---|---|---|---|
| local time (MST) | 22 Mar 17:00 | 23 Mar 05:00 | 23 Mar 17:00 | 24 Mar 17:00 | 25 Mar 11:00 | 25 Mar 17:00 |
| unique profiles / units (51 distinct terrains) | 51/51 | 51/51 | 51/51 | 51/51 | 51/51 | 51/51 |
| HS range (m) | 1.89-2.40 | 1.90-2.41 | 1.89-2.40 | 2.00-2.46 | 2.00-2.45 | 1.96-2.44 |
| units wet in the top 10 cm (> 0.5% by volume) | 46 | 0 | 42 | 0 | 0 | 2 |
| units with a melt-freeze crust in the top 0.25 m | 23 | 37 | 33 | 37 | 36 | 36 |
| top 10 cm density range (kg m-3) | 57-165 | 61-171 | 63-177 | 77-84 | 92-114 | 102-139 |

Pairwise DTW similarity between the 51 unit profiles at 72 h: mean 0.62, range 0.31-0.98, no identical pair.
Profiles differ exactly where terrain differs: the synthetic test domain, whose ridge repeats along x, gives
identical profiles for identical units and distinct ones otherwise (integration test).

Terrain response (forcing is identical at the source; all differences come from the unit's terrain):
- Daily melt-refreeze on the sunlit units: late in the clear afternoons (leads 0 and 24) 46 and 42 units hold
  liquid water in the top 10 cm, and by 05:00 local every unit has refrozen, leaving near-surface crusts (37 units
  at 12 h). How much melts follows slope shortwave: Spearman 0.93 (lead 0) and 0.88 (lead 24) between mean slope
  shortwave and top 10 cm liquid water; steep N-facing units stay dry (0 of 5), steep S-facing units are wettest
  (3.7-6.5% by volume). Near-surface density follows the same ranking (Spearman 0.79 at 24 h; steep S-facing
  126-177 vs steep N-facing 63-123 kg m-3).
- Temperature follows elevation (rank correlation -1.00; unit means -6.9 to -10.2 C).
- Crusts follow elevation as well as sun: the 14 units that never carry a crust in the top 0.25 m during the
  forecast are all at or above 2417 m (median 2492 m, incl. both steep north faces); the 37 that do are lower
  (median 2331 m). The pre-forecast state differs too: HS 1.89-2.40 m from the season's elevation/aspect history,
  and high shaded units carry crusts only at depth (early season).
- The storm (24-26 March) adds 11-15 cm of new snow almost uniformly: forecast precipitation is applied evenly
  (no elevation gradient configured) and is solid everywhere; the small elevation signal in new-snow depth comes
  from new-snow density (80 vs 88 kg m-3), not from precipitation.

## 2. Without a new pit
No observation file is an input to `init` or `predict`; both checkpoints record `observations_used: []`. The
withheld pit is read only by `phase2-report`, after the run.

## 3. No future-weather leakage (10 audit checks + 2 refusal probes, all pass)
| check | result |
|---|---|
| forecast run available by issue time | available 05:00 UTC = issue 05:00 UTC |
| analysis checkpoint st_20260318T0000Z_v1_e2ab437e: cutoff <= issue; forcing ends at its analysis time | cutoff 03-23 00 UTC; last record 03-18 00 UTC |
| advanced checkpoint st_20260323T0000Z_v1_e05b1efd: cutoff <= issue; forcing ends at its analysis time | cutoff 03-23 05 UTC; last record 03-23 00 UTC |
| forecast starts from a state at its initial time | 03-23 00 UTC |
| history / recent series extend past issue, usable part ends at the declared latency | to 03-27; usable to 03-18 / 03-23 00 UTC |
| probe: issue 1 h before the GFS run was available | refused (DataLeakage) |
| probe: rerun of the issued forecast | refused (ImmutableRecord) |

Forecast integrity: the 158 files of the analysis checkpoint are hash-identical before and after the forecast
(branches copy the state; the advance created a new checkpoint). Column mass budgets close to 0.043 kg m-2.

## 4. Withheld evaluation (observations after issue; not used by the run)
Pit 2026-03-25 17:31 UTC (lead 66 h, site unit): pit HS 175 cm, forecast 208 cm (members 208-209), hand-hardness
MAE 0.98 steps. Grain agreement and DTW are not computable: this pit's transcription has no grain forms.

Sunshine AB station during the forecast window (the station that supplied the history precipitation):

| lead (h) | 0 | 24 | 48 | 66 | 72 |
|---|---|---|---|---|---|
| site HS forecast, control (m) | 2.03 | 2.00 | 2.06 | 2.08 | 2.05 |
| station HS (m) | 1.87 | 1.84 | 1.95 | 2.12 | 2.10 |
| site SWE forecast (kg m-2) | 703 | 703 | 711 | 719 | 719 |
| pillow SWE (kg m-2) | 728 | 727 | 735 | 752 | 753 |
| gauge precipitation since init (mm) | 0 | 0 | 14.9 | 31.5 | 31.6 |

- Initial state: mass within 3% of the pillow (703 vs 728 kg m-2), 16 cm deeper than the station sensor (model snow
  lighter, as in the 2025-26 pillow comparison).
- The GFS forecast 16.6 mm for the 72 h against 31.6 mm measured (-47%), so the forecast gained 2 cm of HS (13 cm
  new snow minus settlement) where the station gained 23 cm. The 5-member spread (about 1 cm) did not cover it:
  the ensemble perturbs weather by the configured amounts only and is not calibrated (scenario spread).
- The pit is 33 cm shallower than the forecast and 37 cm shallower than the station sensor at the same time: the
  station-site vs plot difference recorded earlier (+14 to +20 cm in two periods) recurs here.

## Limits of this result
- One archived case. Distinctness and leakage are acceptance properties; skill needs many cases (hindcast across
  all pits with GFS runs 1-3 days before, held-out seasons and locations; Phase 4/6).
- GFS is used raw. Over the 40 days before the case the station was 3.4 K warmer than the GFS day-1 temperature at
  plot elevation, so forecast unit temperatures are probably several K too cold; precipitation was under-forecast by
  half in this storm. A lead-dependent GFS correction is a learned component and needs a LOSO test first.
- Forest units (49 of 100) are not simulated (no canopy scheme). Transport unresolved: no wind redistribution, no
  lee loading; wind not downscaled. Precipitation uniform over the domain (no calibrated elevation gradient).
- Unit profiles represent 600 m unit-mean terrain; the display grid is not evidence of skill at finer scales.
