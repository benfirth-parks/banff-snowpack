# Stability check stability-train-facet-hours-r10-p10-2026-11-25-b

_Research and decision-support product only. Outputs are never a substitute for field assessment, professional avalanche forecasting or local observations, and are not an avalanche forecast or danger rating._

Pits: 590 forecasts of pits with a stability test that broke at a known depth (1076 broken layers, 350 of them easy and sudden), winters 1999-2000 to 2022-2023.
The locked test winters (2023-2024, 2024-2025, 2025-2026) were left out, so they remain a clean final test.

**How to read it.** For each pit we look at the layer each number calls weakest. If it is within 10 cm of a layer that broke in the pit's test, that is a hit. "Chance" is how often a pick at a random depth would hit, because a pit with many broken layers is easy to hit by luck. "Found" is the share of broken layers the number flags as weak by its published threshold, and "also flags" is the share of the rest of the column it flags too (lower is better).

## Like for like: the four-sign count

Every model can count four signs of a weak layer in its own layers: persistent grains (facets, depth hoar, surface hoar), soft snow (fist hard or softer), a big jump in hardness to the layer above or below, and a top between 18 and 94 cm deep. The layer with the most signs is the model's pick. This is the one number that compares models with and without SNOWPACK directly.

| Model | Pits | Weakest spot hits | Chance | Verdict | Found | Also flags | Easy and sudden: hits (chance) |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Lester Soprano | 590 | 38% | 33% | better than chance | 21% | 7% | 28% (25%) |
| Carver Daniels | 590 | 39% | 33% | better than chance | 23% | 8% | 29% (25%) |

## Like for like: SK38, and what depth alone gives

SK38 is SNOWPACK's skier stability index: the strength of the snow at each depth over the stress the snow above and a skier put on it. For models without SNOWPACK it is computed from the model's own layers by SNOWPACK's published formula. "Depth alone" is the same formula on a column with no layers at all (one uniform snow, the model's depth): pit tests mostly break 20 to 40 cm down, where a skier's stress peaks, so any column scores well. A model's layering skill is how far it beats depth alone, not chance.

| Model | Pits | SK38 hits | Depth alone | Chance | Layering skill | Easy and sudden: SK38 (depth alone) |
| --- | --- | --- | --- | --- | --- | --- |
| Lester Soprano | 590 | 56% | 53% | 33% | +2.8 pts | 52% (49%) |
| Carver Daniels | 590 | 56% | 53% | 33% | +3.0 pts | 51% (49%) |

## Lester Soprano

This model does not run SNOWPACK. It is checked on the layers of its own forecast (the layers the site shows) with the four-sign count and, if it is a column model, on its own model layers with SK38 computed by SNOWPACK's published formula (ADR-147).

**Every layer that broke** (590 pits, 1076 layers)

| Number | Weakest spot hits | Chance | Verdict | Found | Also flags |
| --- | --- | --- | --- | --- | --- |
| SK38 (skier stability index) | 56% | 33% | better than chance | 65% | 15% |
| four-sign count (lemons without grain size) | 38% | 33% | better than chance | 21% | 7% |
| SK38 of a column without layers (depth alone) | 53% | 33% | better than chance | 52% | 8% |

**Layers that broke easily and suddenly** (257 pits, 350 layers)

| Number | Weakest spot hits | Chance | Verdict | Found | Also flags |
| --- | --- | --- | --- | --- | --- |
| SK38 (skier stability index) | 52% | 25% | better than chance | 69% | 14% |
| four-sign count (lemons without grain size) | 28% | 25% | no better than chance | 20% | 6% |
| SK38 of a column without layers (depth alone) | 49% | 25% | better than chance | 58% | 7% |

**Weakest spot hits by plot** (every layer that broke)

| Plot | Pits | SK38 (skier stability index) | four-sign count (lemons without grain size) | SK38 of a column without layers (depth alone) | Chance |
| --- | --- | --- | --- | --- | --- |
| BOW | 278 | 49% | 45% | 48% | 35% |
| GOAT | 257 | 62% | 34% | 60% | 33% |
| SIMP | 55 | 60% | 24% | 47% | 29% |

## Carver Daniels

This model does not run SNOWPACK. It is checked on the layers of its own forecast (the layers the site shows) with the four-sign count and, if it is a column model, on its own model layers with SK38 computed by SNOWPACK's published formula (ADR-147).

**Every layer that broke** (590 pits, 1076 layers)

| Number | Weakest spot hits | Chance | Verdict | Found | Also flags |
| --- | --- | --- | --- | --- | --- |
| SK38 (skier stability index) | 56% | 33% | better than chance | 67% | 16% |
| four-sign count (lemons without grain size) | 39% | 33% | better than chance | 23% | 8% |
| SK38 of a column without layers (depth alone) | 53% | 33% | better than chance | 52% | 8% |

**Layers that broke easily and suddenly** (257 pits, 350 layers)

| Number | Weakest spot hits | Chance | Verdict | Found | Also flags |
| --- | --- | --- | --- | --- | --- |
| SK38 (skier stability index) | 51% | 25% | better than chance | 71% | 15% |
| four-sign count (lemons without grain size) | 29% | 25% | better than chance | 21% | 6% |
| SK38 of a column without layers (depth alone) | 49% | 25% | better than chance | 58% | 7% |

**Weakest spot hits by plot** (every layer that broke)

| Plot | Pits | SK38 (skier stability index) | four-sign count (lemons without grain size) | SK38 of a column without layers (depth alone) | Chance |
| --- | --- | --- | --- | --- | --- |
| BOW | 278 | 49% | 45% | 48% | 35% |
| GOAT | 257 | 62% | 36% | 60% | 33% |
| SIMP | 55 | 59% | 28% | 44% | 29% |

Computing time: 2 h 19 min.
