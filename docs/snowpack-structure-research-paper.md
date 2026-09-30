# Predicting Snowpack Structure from Weather: A Research Synthesis and Design Basis for a Self-Improving Snowpack Agent

Scope: Canadian Rockies (Banff, Yoho, Kootenay and adjacent ranges), for avalanche forecasting support
Prepared: 30 September 2026
Companion document: `README.md` (build guide for Claude Code)

## Revision 2: prediction is the product

The required product is a terrain-resolved prediction of future snowpack structure from weather forecasts, not merely calibration of station simulations. Observations and historical weather support initialization and learning; the deployed system must predict at terrain units without a fresh snowpit. The authoritative engineering specification is `terrain-forecast-product-spec.md`, which supersedes the original station-only scope and deferral of terrain transport as a product requirement.

Terrain must enter the evolving snow state, not just the final visualization. A Canadian Rockies study in Kananaskis downscaled HRDPS to a variable-resolution terrain mesh, including slope irradiance, shadowing, wind and gravitational redistribution; however, that study used a two-layer Snobal model and validated snow depth/persistence, not avalanche-relevant detailed stratigraphy ([Vionnet et al., 2021](https://tc.copernicus.org/articles/15/743/2021/)). Alpine3D demonstrates coupling a detailed SNOWPACK column with horizontal transport, but the cited 2023 implementation was evaluated on Antarctic accumulation rather than Rockies avalanche-layer structure ([Keenan et al., 2023](https://gmd.copernicus.org/articles/16/3203/2023/)). These are architectural precedents, not proof that the proposed terrain-to-profile product is already validated.

The revised research recommendation is therefore a terrain-conditioned state-transition system: maintain an ensemble of current layered states, downscale forecast weather to terrain units, evolve layers with a physical core, couple conservative transport where supported, and learn corrections from independent field observations. A trained emulator may later accelerate that transition; physics-generated training profiles must remain labelled synthetic, and only withheld field observations can demonstrate real-world structural skill. The numeric defaults, thresholds and performance figures later in this paper are study-specific examples, not guaranteed Banff performance or automatic deployment gates.

---

## Abstract

Predicting snowpack structure — the sequence of layers, their grain forms, hardness, density, temperature, moisture, and the weak layers and crusts that control avalanche hazard — is a solved problem in principle and an unsolved problem in practice. Physics-based, one-dimensional snow cover models such as SNOWPACK and Crocus simulate layer-by-layer evolution from meteorological forcing and are already embedded in operational chains in Canada, Switzerland, France and Austria. Large-scale validation in western Canada shows that these chains detect roughly 75% of the critical layers forecasters track, but with only about 40% precision, and that errors are dominated by precipitation inputs rather than snow physics ([Herla et al., 2024](https://nhess.copernicus.org/articles/24/2727/2024/); [Avalanche Journal](https://avalanchejournal.ca/inside-the-snowpack-model-dashboard/)). Field observations can materially improve simulations when they are used to re-initialize the model, correct precipitation, or weight ensemble members ([Binder et al., 2024](https://arc.lib.montana.edu/snow-science/objects/ISSW2024_O3.10.pdf); [Cluzet et al., 2021](https://gmd.copernicus.org/articles/14/1595/2021/)).

This synthesis concludes that the right architecture for a "self-evolving" snowpack agent is not a pure machine-learning model trained to map weather to profiles. The observational record is too small, too sparse and too heterogeneous for that. The defensible design is a hybrid: a physics core (SNOWPACK) driven by bias-corrected weather, an observation-assimilation loop that uses field profiles to re-initialize and correct the model, a thin learned layer that corrects systematic errors and calibrates stability outputs against local observations, and an LLM orchestration layer that runs the pipeline, explains results and flags uncertainty — but never invents layers. The companion README specifies how to build it.

---

## 1. What "snowpack structure" means for forecasting

For avalanche forecasting, structure is not a generic snow depth or snow water equivalent problem. What matters is the vertical stratigraphy and how it changes:

- **Layer properties**: thickness, grain form and size, hand hardness, density, temperature, liquid water content. The Canadian standard for recording them is the Canadian Avalanche Association's OGRS, which fixes the hardness scale (F, 4F, 1F, P, K, I), grain size to the nearest 0.5 mm, and liquid water classes from dry (0%) through slush (>15%) ([CAA OGRS 2024](https://cdn.ymaws.com/www.avalancheassociation.ca/resource/resmgr/standards_docs/ogrs2024web.pdf)). Grain forms follow the international classification ([Fierz et al., 2009](https://www.geobotany.org/library/pubs/FierzeC2009_snow_classif_UNESCO.pdf)).
- **Critical layers**: persistent weak layers (faceted crystals FC, depth hoar DH, surface hoar SH) and crusts (melt-freeze MFcr, ice IF) that forecasters name with date tags and track for weeks. Canadian forecasters conventionally tag crusts by deposition date and other weak layers by burial date ([sarp.snowprofile documentation](https://cran.r-project.org/web/packages/sarp.snowprofile/sarp.snowprofile.pdf); [Herla et al., 2025](https://nhess.copernicus.org/articles/25/625/2025/)).
- **Slab properties** above those layers: thickness, density, cohesion, and how the load changes with new snow.
- **Stability**: field tests (CT, ECT, PST, RB) with fracture character codes (SP, SC, PC, RP, BRK) recorded under OGRS ([CAA OGRS 2024](https://cdn.ymaws.com/www.avalancheassociation.ca/resource/resmgr/standards_docs/ogrs2024web.pdf)).

An agent that "predicts snowpack structure" therefore has to produce layered profiles with these attributes, identify and track critical layers by date tag, and attach stability information — not just totals.

### 1.1 Why the Canadian Rockies are a hard case

The Rockies have a continental snow climate: colder temperatures, more clear skies, less snowfall and a thin snowpack that favours depth hoar and persistent weak layers ([Krawetz, 2024](https://summit.sfu.ca/_flysystem/fedora/2025-02/etd23586.pdf); [Shandro, SFU thesis](https://summit.sfu.ca/_flysystem/fedora/sfu_migrate/17682/etd10393_BShandro.pdf)). Field work across 16 faceted layers found typical mid-winter treeline depths of 1–1.5 m in the Rockies versus 2–4 m in the Columbias, a faceting threshold of about 10 °C/m, and much slower strength gain in the continental snowpack: maximum strength after about 90 days under average loads of 1.75 kPa, versus about 60 days and 5.25 kPa in the intermountain climate ([Johnson & Jamieson, 2000](https://arc.lib.montana.edu/snow-science/objects/issw-2000-086-093.pdf)). Analyses of eight seasons of western Canadian bulletins classify the Rockies as continental and show persistent and deep persistent slab situations as a large share of hazard ([Shandro & Haegeli, 2018](https://nhess.copernicus.org/articles/18/1141/2018/nhess-18-1141-2018.pdf)).

Two consequences follow for model design. First, basal and mid-pack facets and depth hoar dominate the structure, and they are the layer types models detect with high recall but low precision ([Herla et al., 2024](https://nhess.copernicus.org/articles/24/2727/2024/)). Second, the Rockies are where weather-model precipitation tends to be under-predicted: HRDPS-driven simulations were systematically too shallow in many Rockies areas, and Kananaskis had the lowest profile similarity (0.50) of the 21 regions tested ([Horton & Haegeli, 2022](https://tc.copernicus.org/articles/16/3393/2022/tc-16-3393-2022.pdf)).

---

## 2. How weather creates structure: processes the agent must represent

The table below links each structural outcome to its weather drivers and to what the literature says about modelling it. It is the conceptual core the agent's feature engineering and explanations should follow.

| Structural outcome | Weather drivers | Evidence and modelling notes |
|---|---|---|
| New snow layers and density | Precipitation amount, phase, air temperature, wind, humidity | SNOWPACK estimates new-snow density with empirical parameterizations of TA, TSS, RH, wind and HH ([SNOWPACK SnLaws](https://snowpack.slf.ch/doc-dev/html/classSnLaws.html)); typical new-snow density is 30–150 kg/m³ ([Keenan et al., 2021](https://tc.copernicus.org/articles/15/1065/2021/)). |
| Settlement and slab stiffening | Load from new snow, temperature | Load, slab thickness and slab density were the strongest correlates of facet-layer strength gain (R ≈ 0.68–0.71) ([Johnson & Jamieson, 2000](https://arc.lib.montana.edu/snow-science/objects/issw-2000-086-093.pdf)). |
| Faceting and depth hoar (TG metamorphism) | Cold air, clear skies (longwave loss), shallow snowpack | Faceting threshold of about 10 °C/m; thin continental snowpacks favour it ([Johnson & Jamieson, 2000](https://arc.lib.montana.edu/snow-science/objects/issw-2000-086-093.pdf)). SNOWPACK tends to overestimate temperature gradients and kinetic metamorphism, and classifies facets larger than 1.5 mm as depth hoar ([Ehrnsperger et al., 2024](https://arc.lib.montana.edu/snow-science/objects/ISSW2024_P3.14.pdf)). |
| Surface hoar | Clear, calm, humid nights; strong radiative cooling | Modelled growth peaked at TA −10 to 0 °C, RH 70–90%, TSS −20 to −10 °C, ILWR 175–200 W/m², wind <1.5 m/s; never above 3.5 m/s ([Horton et al., 2015](https://tc.copernicus.org/articles/9/1523/2015/tc-9-1523-2015.pdf)). Accumulated latent heat flux correlated with crystal size at r = 0.77 using station data ([Horton et al., 2013](https://arc.lib.montana.edu/snow-science/objects/ISSW13_paper_O4-01.pdf)). |
| Sun crusts | Incoming shortwave on steep solar aspects | Accumulated net shortwave on 30° south slopes correlated with crust thickness (r = 0.63 station, 0.86 with 2.5 km GEM) ([Horton et al., 2013](https://arc.lib.montana.edu/snow-science/objects/ISSW13_paper_O4-01.pdf)). |
| Rain and melt-freeze crusts | Rain-on-snow, warm spells, freezing level | Phase errors propagate directly: a single event treated as rain instead of snow produced a spurious thick basal crust ([The Cryosphere, 2011](https://tc.copernicus.org/articles/5/1115/2011/tc-5-1115-2011.pdf)). Models tend to convert crusts to melt forms earlier than observed ([Mitterer et al., 2011](https://www.slf.ch/fileadmin/user_upload/WSL/Mitarbeitende/schweizj/Mitterer_etal__wet_snow_58A191_2011.pdf)). |
| Wind slabs | Wind speed/direction with available snow | Not represented in flat-field 1D runs; Avalanche Canada's chain does not model stripping or deposition ([Avalanche Journal](https://avalanchejournal.ca/inside-the-snowpack-model-dashboard/)). SNOWPACK's virtual-slope redistribution erodes the windward slope and deposits on the lee ([SNOWPACK SnowDrift](https://snowpack.slf.ch/doc-release/html/classSnowDrift.html)); distributed models (Alpine3D, SnowTran-3D, CHM) handle it spatially ([Alpine3D, 2023](https://gmd.copernicus.org/articles/16/3203/2023/); [CHM](https://github.com/Chrismarsh/CHM)). |
| Wet snow and loss of strength | Warming, solar input, rain | An LWC index (layer-weighted mean LWC divided by 3%) ≥ 1 indicates strength loss; a random forest built on it reached F1 ≈ 0.8 in Switzerland ([Journal of Glaciology](https://www.cambridge.org/core/journals/journal-of-glaciology/article/automated-prediction-of-wetsnow-avalanche-activity-in-the-swiss-alps/166D6321D57FFAC04DC7CEE773C0A45A)). |

Precipitation phase deserves special attention in the Rockies because rain crusts and early-season basal structure are highly sensitive to it. Humidity-aware partitioning based on wet-bulb temperature reduced western US snow-depth bias from −23% to −12.5% compared with an air-temperature scheme ([Wang et al., 2019](https://repository.library.noaa.gov/view/noaa/54993/noaa_54993_DS1.pdf)). A SNOWPACK reconstruction at Ram Mountain, Alberta, found the best fit with a 2.6 °C phase-change temperature, precipitation increased by 55% and radiation reduced by 30% relative to NARR reanalysis — a reminder that local calibration can be large ([Crémel et al., 2026](https://www.tandfonline.com/doi/full/10.1080/15230430.2026.2627695)).

---

## 3. Physics-based snow cover models

### 3.1 SNOWPACK

SNOWPACK is a one-dimensional, Lagrangian finite-element model developed to support Swiss avalanche warning. It solves heat transfer and settlement with phase change, vapour and liquid water transport, and metamorphism, and carries microstructure state variables (dendricity, sphericity, grain and bond size) from which grain type, hardness and stability are derived. Its Lagrangian grid can represent very thin layers such as ice crusts and surface hoar ([SNOWPACK general concepts](https://snowpack.slf.ch/doc-release/html/general.html); [Schweizer et al., 2006](https://www.slf.ch/fileadmin/user_upload/WSL/Mitarbeitende/schweizj/Schweizer_etal_SNOWPACK_stability_CRST_2006.pdf)). It neglects lateral transfer ([SNOWPACK general concepts](https://snowpack.slf.ch/doc-release/html/general.html)).

Forcing requirements are modest and match what station networks and NWP provide: air temperature, relative humidity, wind speed, incoming and/or reflected shortwave, incoming longwave and/or surface temperature, precipitation and/or snow height, and ground temperature or geothermal flux, preferably hourly. Missing longwave can be parameterized from shortwave clearness, and precipitation phase can be supplied directly or split at temperature thresholds ([SNOWPACK data requirements](https://snowpack.slf.ch/doc-release/html/requirements.html)). Pre-processing (filtering, resampling, gap filling) is handled by the MeteoIO library ([SNOWPACK data requirements](https://snowpack.slf.ch/doc-release/html/requirements.html); [MeteoIO SNIO](https://meteoio.slf.ch/doc-dev/html/snowpack.html)).

The source is open (LGPL-3.0) on [GitHub](https://github.com/snowpack-model/snowpack) and the [WSL GitLab](https://gitlabext.wsl.ch/snow-models/snowpack), and it can be containerized from the published Debian package; a sample season runs in about 10 seconds ([UiT Research Software](https://research-software.uit.no/blog/2023-building-snowpack/)). Python I/O for SMET, PRO and iCSV files is available through `snowpat` ([PyPI](https://pypi.org/project/snowpat/)).

### 3.2 Crocus

Crocus (SURFEX platform, Météo-France) is the main alternative: multilayer Lagrangian discretization with up to 50 layers, prognostic density, enthalpy, optical diameter, sphericity and a history tracker, plus the ESCROC multiphysics ensemble and particle-filter assimilation. Its 2026 reference paper reports errors comparable to state-of-the-art models across ESM-SnowMIP sites and notes that errors under regional forcing are dominated by meteorology ([Lafaysse et al., 2026](https://gmd.copernicus.org/articles/19/6273/2026/)).

### 3.3 Distributed and wind-aware models

For spatial redistribution of snow by wind, which 1D models cannot capture, the options are Alpine3D's drift module ([Alpine3D snowdrift](https://alpine3d.slf.ch/doc-dev/html/snowdrift.html)), SnowModel/SnowTran-3D ([Liston et al.](https://journals.ametsoc.org/view/journals/hydr/7/6/jhm548_1.xml)), and the Canadian Hydrological Model, which includes blowing snow and can embed a SNOWPACK-type multilayer scheme ([CHM](https://github.com/Chrismarsh/CHM); [Marsh, CHM status](https://gwf.usask.ca/impc/documents/presentations/2nd-agm/day1/8_Marsh_CHM.pdf)). These are much heavier to operate and should be deferred.

### 3.4 Recommendation

| Criterion | SNOWPACK | Crocus |
|---|---|---|
| Canadian operational precedent | Yes — Avalanche Canada chain, SFU research | Limited |
| Weak-layer and stability outputs | SK38, RTA, critical crack length, p_unstable ecosystem | MEPRA, stability indices |
| Station-driven point runs | Native (SMET) | Via SURFEX forcing |
| Tooling for profile comparison | sarp.snowprofile, snowpat, AWSOME, niViz | snowtools |
| Built-in ensemble / assimilation | No (must be built) | ESCROC, CrocO |

SNOWPACK is the better core for this project because the Canadian validation literature, the SFU/Avalanche Canada tooling and the stability post-processing ecosystem are all built on it. Crocus's ensemble and particle-filter ideas should be borrowed conceptually.

---

## 4. Operational model chains in practice

| Chain | Forcing | Configuration | Outputs | Source |
|---|---|---|---|---|
| Avalanche Canada (national) | HRDPS 2.5 km for 48 h, RDPS to 84 h; elevation-adjusted | SNOWPACK on 2.5 km grid; flat + 38° N/E/S/W | New snow, wind slab, PWL, wet snow metrics (0–1); clustering | [Avalanche Journal](https://avalanchejournal.ca/inside-the-snowpack-model-dashboard/); [snowpack.avalanche.ca](https://snowpack.avalanche.ca/) |
| SFU research chain (BYK, GNP, S2S) | HRDPS | SNOWPACK v3.4, flat field, no wind transport, no layer aggregation | Date-tagged critical layers, p_unstable | [Herla et al., 2024](https://nhess.copernicus.org/articles/24/2727/2024/) |
| Switzerland (SLF) | IMIS AWS for nowcast; 1 km NWP up to 27 h | 147 stations; flat + four 38° slopes | Danger-level RF, instability RF, natural-avalanche model | [Techel et al., 2025](https://nhess.copernicus.org/articles/25/3333/2025/nhess-25-3333-2025.pdf) |
| France | SAFRAN reanalysis/forecast | Crocus by massif, elevation, aspect | MEPRA, ML avalanche activity | [Viallon-Galinier et al., 2023](https://tc.copernicus.org/articles/17/2245/2023/tc-17-2245-2023.pdf) |
| Austria/Norway (AWSOME) | AWS + AROME/IFS | SNOWPACK; profile-initialized runs | avapro, aggregatepro, qmah (GeoJSON) | [Herla et al., 2024 ISSW](https://arc.lib.montana.edu/snow-science/objects/ISSW2024_P1.33.pdf) |
| ÖBB railways (Austria) | AWS + NWP ensemble | SNOWPACK per avalanche path | 10/50/90th percentile stability, fracture depth | [Hatvan et al., 2024](https://arc.lib.montana.edu/snow-science/objects/ISSW2024_P7.11.pdf) |

Common patterns worth copying: separate nowcast (observations) and forecast (NWP) chains; flat plus four virtual 38° slopes; post-processing into avalanche-problem metrics; and representing spatial uncertainty through distributions rather than single profiles. The Avalanche Canada dashboard reports that its hazard metric generally tracked and often led public treeline danger changes in 2024–25, but missed a storm where NWP under-called 60 cm of snowfall — the typical failure mode ([Avalanche Journal](https://avalanchejournal.ca/inside-the-snowpack-model-dashboard/)).

---

## 5. How good are simulated profiles?

### 5.1 Evidence

| Study | Setting | Key result |
|---|---|---|
| [Herla et al., 2024](https://nhess.copernicus.org/articles/24/2727/2024/) | 10 seasons, BYK/GNP/S2S, HRDPS + SNOWPACK | Critical layers: POD ≈ 75%, precision ≈ 40%, false-alarm ≈ 30%; SH precision 80–100%; facets POD up to 90% but precision 20–40% |
| [Herla et al., 2025](https://nhess.copernicus.org/articles/25/625/2025/) | 10 seasons, GNP treeline | Best agreement on storm-problem presence and persistent-problem likelihood/size; Kendall's τ 0.16–0.32 |
| [Horton & Haegeli, 2022](https://tc.copernicus.org/articles/16/3393/2022/tc-16-3393-2022.pdf) | 21 regions, 2020–21 | Daily HS-change correlation median 0.54; Rockies under-predicted; precipitation scaling factors 0.72–1.91 |
| [Bellaire & Jamieson, 2012](https://arc.lib.montana.edu/snow-science/objects/issw-2012-172-178.pdf) | Mt. Fidelity, GEM15 | Critical layer (SH, MFcr) presence/absence hit rate 81%; temperature bias −1.7 K in January |
| [Binder et al., 2024](https://arc.lib.montana.edu/snow-science/objects/ISSW2024_O3.10.pdf) | Weissfluhjoch, 47 profiles | DTW similarity Φ ≈ 0.53 (AWS) / 0.50 (NWP); profile-initialized NWP runs beat season-start AWS runs for up to ~6 weeks |
| [Palomaki & Miller, 2023](https://arc.lib.montana.edu/snow-science/objects/ISSW2023_P1.26.pdf) | Bridger Range, MT | Similarity 0.21–0.62; AWS forcing beat HRRR; HRRR shortwave 35–56% high |
| [Schweizer et al., 2006](https://www.slf.ch/fileadmin/user_upload/WSL/Mitarbeitende/schweizj/Schweizer_etal_SNOWPACK_stability_CRST_2006.pdf); [Monti et al., 2014](https://www.slf.ch/fileadmin/user_upload/WSL/Mitarbeitende/schweizj/Monti_etal_Hardness_estimation_WL_detection_2014.pdf) | Swiss verification | Density biased by grain type; grain size differs significantly; SH and recent-snow weak layers under-represented |
| [Gluckman & Clark, 2026](https://arc.lib.montana.edu/snow-science/objects/ISSW2026_P2.10.pdf) | Little Cottonwood, UT | Mean DTW similarity of 34 modelled vs manual pits: 0.59 |

### 5.2 Interpretation

Three conclusions shape the agent's expectations and its verification targets:

1. **Models over-produce weak layers.** High recall, low precision is consistent across studies. The agent should treat model weak layers as candidates to be confirmed, and calibration against local stability tests is where learning adds most value.
2. **Layer timing is more reliable than layer properties.** Date-tagged events (a surface hoar burial, a rain crust) are captured more robustly than grain size, density or hardness. Verification should reward correct timing and layer type first.
3. **A realistic similarity ceiling is about 0.6–0.8.** Even model-versus-model similarity under different forcing sits around 0.6–0.8 ([Binder et al., 2024](https://arc.lib.montana.edu/snow-science/objects/ISSW2024_O3.10.pdf)), and field profiles carry their own spatial variability. Success should be defined as improvement relative to an uncorrected SNOWPACK baseline, not as agreement with a single pit.

---

## 6. Error sources, ranked

1. **Precipitation (amount, timing, phase).** Named as the largest error source by Avalanche Canada and by multiple validations ([Avalanche Journal](https://avalanchejournal.ca/inside-the-snowpack-model-dashboard/); [Herla et al., 2024](https://nhess.copernicus.org/articles/24/2727/2024/)). Across 28 western Canadian stations, all precipitation products overestimated HS by 11% overall (HRDPS +17%, CaPA-Exp +14%) ([Krawetz, 2024](https://summit.sfu.ca/_flysystem/fedora/2025-02/etd23586.pdf)). In the Sobol' analysis at Weissfluhjoch, precipitation changed slab load by a factor of six and swung SK38 from 0.32 to 3.05 across the plausible input range ([Richter et al., 2020](https://nhess.copernicus.org/preprints/nhess-2019-433/nhess-2019-433.pdf)).
2. **Radiation and cloud.** NWP shortwave biases of 35–56% have been documented ([Palomaki & Miller, 2023](https://arc.lib.montana.edu/snow-science/objects/ISSW2023_P1.26.pdf)); longwave errors of about 28–33 W/m² were reported for GEM ([Horton et al., 2013](https://arc.lib.montana.edu/snow-science/objects/ISSW13_paper_O4-01.pdf)). These govern surface hoar, near-surface facets and sun crusts.
3. **Humidity and wind at sub-grid scale.** HRDPS did not resolve valley cloud, gap winds or local wind effects; 40 m winds represented sheltered 10 m sites better than 10 m winds ([Horton et al., 2015](https://tc.copernicus.org/articles/9/1523/2015/tc-9-1523-2015.pdf)).
4. **Temperature and elevation downscaling.** GEM temperature standard errors of about 2.7–2.8 °C and cold biases of 0.8–1.7 K have been reported ([Horton et al., 2013](https://arc.lib.montana.edu/snow-science/objects/ISSW13_paper_O4-01.pdf); [Bellaire & Jamieson, 2012](https://arc.lib.montana.edu/snow-science/objects/issw-2012-172-178.pdf)).
5. **Model physics.** Grain-size and density parameterizations, crust decay, and overestimated kinetic growth ([Schweizer et al., 2006](https://www.slf.ch/fileadmin/user_upload/WSL/Mitarbeitende/schweizj/Schweizer_etal_SNOWPACK_stability_CRST_2006.pdf); [Ehrnsperger et al., 2024](https://arc.lib.montana.edu/snow-science/objects/ISSW2024_P3.14.pdf)).
6. **Lateral processes** (wind transport, sloughing, avalanching) — absent in 1D.

Design implication: most of the achievable improvement lies upstream of the snow model, in forcing correction. Station actuals are the most valuable data the user can supply.

---

## 7. Using field observations to correct and evolve the model

Four families of methods exist, ordered from simplest to most complex.

### 7.1 Snow-height constraint and precipitation scaling

SNOWPACK can be forced to follow measured snow height (`ENFORCE_MEASURED_SNOW_HEIGHTS`), which Krawetz used as the reference simulation ([Krawetz, 2024](https://summit.sfu.ca/_flysystem/fedora/2025-02/etd23586.pdf)). Where HS is observed but the goal is forecasting, a precipitation adjustment factor \(k = HS_{obs}/HS_{mod}\) can be estimated and applied before re-running, as tested regionally by [Horton & Haegeli (2022)](https://tc.copernicus.org/articles/16/3393/2022/tc-16-3393-2022.pdf). Avalanche Canada's production chain adjusts HRDPS weekly when modelled and observed HS changes differ by more than 10% ([Krawetz, 2024](https://summit.sfu.ca/_flysystem/fedora/2025-02/etd23586.pdf)).

### 7.2 Re-initialization from observed profiles

SNOWPACK can start from an observed profile (CAAML). Density is estimated from grain type and hardness following Monti et al. (2014) — for example, 520 kg/m³ for melt-freeze crusts and 910 kg/m³ for ice lenses — and microstructure parameters are derived from grain type and size ([Binder & Mitterer, 2023](https://arc.lib.montana.edu/snow-science/objects/ISSW2023_O4.02.pdf)). Profile-initialized runs improved similarity at two Austrian sites (0.72 vs 0.62; 0.76 vs 0.46) ([Binder & Mitterer, 2023](https://arc.lib.montana.edu/snow-science/objects/ISSW2023_O4.02.pdf)), and at Weissfluhjoch outperformed season-start runs for up to about six weeks, with NWP-driven runs degrading about twice as fast as AWS-driven runs ([Binder et al., 2024](https://arc.lib.montana.edu/snow-science/objects/ISSW2024_O3.10.pdf)). A Bavarian study recommends re-integrating high-quality profiles about every two weeks, with plausibility checks, because a bad pit can corrupt the run; it also found SNOWPACK discards secondary grain type and observed hardness on import, and that a 0.5 mm new-snow grain size behaved better than 5 mm ([Ehrnsperger et al., 2024](https://arc.lib.montana.edu/snow-science/objects/ISSW2024_P3.14.pdf)).

### 7.3 Ensemble weighting (particle filter)

CrocO runs an ensemble of perturbed forcings and model physics and reweights or resamples members by their fit to observations. Assimilating snow height with 40 members improved CRPS by about 60% on average ([Cluzet et al., 2021](https://gmd.copernicus.org/articles/14/1595/2021/)). The same logic can be applied to profiles by using DTW similarity as the likelihood — a natural way to use sparse pit data without forcing the model to replicate a single, spatially noisy observation.

### 7.4 Learned correction

Hybrid physics–ML work on snow water equivalent shows that ML post-processing of physical-model output is best for known stations in new years, whereas augmenting training with physical simulations transfers better to new locations ([Pomarol Moya et al., 2025](https://egusphere.copernicus.org/preprints/2025/egusphere-2025-1845/)). For a small network of Rockies sites, this argues for keeping the physics model as the spatial backbone and learning site-specific corrections.

### 7.5 Comparing profiles: the enabling tool

All of the above require a quantitative way to compare a simulated and an observed profile. Dynamic time warping (DTW) aligns layers between profiles by grain type, hardness and optionally date, producing a similarity score from 0 to 1 that up-weights weak layers and crusts ([Herla et al., 2021](https://gmd.copernicus.org/preprints/gmd-2020-171/gmd-2020-171.pdf); [SFU ARP](https://sfuarp.ca/publications/2020_herlaothers_profilealignment/)). Recommended settings include 0.5 cm resampling, a Sakoe–Chiba window of about 0.3, a symmetric P = 1 slope constraint, open-end alignment and a hardness-to-grain weight ratio of 1:4 ([Herla, 2023 thesis](https://summit.sfu.ca/_flysystem/fedora/2024-01/etd22760.pdf)); for model–observation comparison, Binder used an "rta WLdetection" configuration with a 0.6 window and height rescaling ([Binder et al., 2024](https://arc.lib.montana.edu/snow-science/objects/ISSW2024_O3.10.pdf)). DTW barycenter averaging (DBA) produces representative profiles from many simulations ([Herla et al., 2022](https://tc.copernicus.org/articles/16/3149/2022/)). Reference implementations are the R packages `sarp.snowprofile` and `sarp.snowprofile.alignment` ([CRAN vignette](https://cran.r-project.org/web/packages/sarp.snowprofile.alignment/vignettes/workflow.html)).

---

## 8. From structure to stability

| Indicator | Meaning | Typical threshold | Source |
|---|---|---|---|
| SK38 | Skier stability index on 38° slope | ≤ 1 unstable | [Herla et al., 2024](https://nhess.copernicus.org/articles/24/2727/2024/) |
| RTA (relative threshold sum) | Structural weakness from grain, hardness, size contrasts | ≥ 0.8 potential weak layer | [Hatvan et al., 2024](https://arc.lib.montana.edu/snow-science/objects/ISSW2024_P7.11.pdf) |
| Critical crack length \(r_c\) | Crack propagation propensity | ≤ 0.3–0.4 m | [Herla et al., 2024](https://nhess.copernicus.org/articles/24/2727/2024/); [Richter et al., 2019](https://tc.copernicus.org/articles/13/3353/2019/) |
| p_unstable (random forest) | Probability layer is unstable | ≥ 0.77 | [Mayer et al., 2022](https://www.dora.lib4ri.ch/wsl/dload/wsl:32201/PDF/Mayer-2022-A_random_forest_model_to-(published_version).pdf) |
| TSA (lemons) | Field-style structural flags | ≥ 5 poor | [Herla et al., 2022](https://tc.copernicus.org/articles/16/3149/2022/) |
| LWC index | Wet-snow strength loss | ≥ 1 | [Journal of Glaciology](https://www.cambridge.org/core/journals/journal-of-glaciology/article/automated-prediction-of-wetsnow-avalanche-activity-in-the-swiss-alps/166D6321D57FFAC04DC7CEE773C0A45A) |

The Mayer random forest is the most important single tool. Trained on only 146 balanced layers matched between Swiss observed and simulated profiles, it uses six features — viscous deformation rate, critical cut length, skier penetration depth, weak-layer sphericity, slab density/grain-size ratio and weak-layer grain size — and reached 88% cross-validated accuracy ([Mayer et al., 2022](https://www.dora.lib4ri.ch/wsl/dload/wsl:32201/PDF/Mayer-2022-A_random_forest_model_to-(published_version).pdf)). It was applied in Canada without retraining ([Herla et al., 2024](https://nhess.copernicus.org/articles/24/2727/2024/)). This small training set is encouraging: a Rockies recalibration from a few seasons of local stability tests is feasible.

The improved critical crack length parameterization raised weak-layer detection from 0.18 to 0.89 at Swiss sites ([Richter et al., 2019](https://tc.copernicus.org/articles/13/3353/2019/)). Avalanche problem types (new snow, wind slab, persistent, wet) can be derived algorithmically from simulated profiles using thresholds such as slab thickness > 0.18 m, 0.05 m new snow in 24 h, and 1% volumetric LWC ([Reuter et al., 2022](https://www.dora.lib4ri.ch/wsl/islandora/object/wsl:29301/datastream/PDF2/Reuter-2022-Characterizing_snow_instability_with_avalanche-(accepted_version).pdf)); this logic is packaged as `avapro` in AWSOME ([Herla et al., 2024 ISSW](https://arc.lib.montana.edu/snow-science/objects/ISSW2024_P1.33.pdf)).

---

## 9. Machine learning and hybrid approaches

| Approach | Result | Source |
|---|---|---|
| RF danger level from SNOWPACK + weather (Switzerland) | ~70% accuracy; pipeline to regional grids | [Maissen et al., 2024](https://gmd.copernicus.org/articles/17/7569/2024/) |
| Model vs human discriminatory skill | Model-based forecasts broadly comparable to human forecasts | [Techel et al., 2025](https://nhess.copernicus.org/articles/25/3333/2025/nhess-25-3333-2025.pdf) |
| Natural dry avalanche days from p_unstable + new snow | F1 up to 0.87 | [Mayer et al., 2023](https://nhess.copernicus.org/articles/23/3445/2023/) |
| RF on Crocus stability indices and derivatives (France) | Recall ~75%; mechanical indices add ~11 points of detection | [Viallon-Galinier et al., 2023](https://tc.copernicus.org/articles/17/2245/2023/tc-17-2245-2023.pdf) |
| Transformer on 5 days of SNOWPACK output → SAR avalanche activity (Norway) | Regional r = 0.80; cell r = 0.55; not yet operational | [Grah et al., 2026](https://arxiv.org/abs/2609.15485) |
| XGBoost on observed + modelled factors (UDOT) | Balanced accuracy ~86% at 24 h | [Gluckman & Clark, 2026](https://arc.lib.montana.edu/snow-science/objects/ISSW2026_P2.10.pdf) |
| Weather-only sequence models (Colorado) | Macro-F1 ~0.51–0.54 for danger | [Schwartzreich & Rodriguez, 2026](https://www.frontiersin.org/journals/earth-science/articles/10.3389/feart.2026.1764442/full) |

Lessons for this project:

- **Physics features consistently help.** Adding snowpack-derived stability features outperforms meteorology alone in France, Switzerland and Tibet ([Viallon-Galinier et al., 2023](https://tc.copernicus.org/articles/17/2245/2023/tc-17-2245-2023.pdf); [Fu et al., 2026](https://egusphere.copernicus.org/preprints/2026/egusphere-2026-4540/egusphere-2026-4540.pdf)).
- **Evidence reviewed favours physical structure plus learning.** This review did not establish a validated end-to-end weather-and-terrain-to-stratigraphy model for Banff. That is a research gap, not proof that such a model cannot be trained; synthetic pretraining and subsequent independent field validation remain candidate development paths.
- **Validation must be temporal.** Leave-one-season-out testing is standard ([Viallon-Galinier et al., 2023](https://tc.copernicus.org/articles/17/2245/2023/tc-17-2245-2023.pdf)); random splits leak storm-level information.
- **Spatial transfer is weak.** ML corrections learned at one station should not be assumed to hold elsewhere ([Pomarol Moya et al., 2025](https://egusphere.copernicus.org/preprints/2025/egusphere-2025-1845/)).

---

## 10. Where an LLM agent fits

No peer-reviewed work was found describing an LLM agent that predicts snowpack structure; recent LLM forecasting research concerns general forecasting and weather-event benchmarks ([CLLMate, EMNLP 2025](https://aclanthology.org/2025.emnlp-main.886.pdf)). The evidence above implies a clear division of labour:

- **Physics and statistics** produce every layer, property and probability.
- **The LLM agent** orchestrates the pipeline; checks data quality; runs scenario experiments (for example, "what if forecast precipitation is 30% low?"); explains why a layer formed by pointing to the weather that produced it; compares predictions with incoming observations; writes a structured briefing with explicit uncertainty; and proposes, but does not silently apply, model updates.

This matches the user's stated preference for accountable human judgment, versioned records and verification over opaque automation. The agent is decision support, not a forecaster.

---

## 11. Design implications

The research supports the following architecture, detailed in the README:

1. **Virtual stations** per site × elevation band × aspect (flat plus four 38° slopes), as in Canadian and Swiss operations ([Avalanche Journal](https://avalanchejournal.ca/inside-the-snowpack-model-dashboard/); [Techel et al., 2025](https://nhess.copernicus.org/articles/25/3333/2025/nhess-25-3333-2025.pdf)).
2. **Two chains**: a nowcast driven by quality-controlled station actuals, and a forecast chain that restarts from the nowcast state and runs corrected NWP forcing for 48–84 h.
3. **Forcing correction first**: station-based bias correction per variable and lead time, wet-bulb phase partitioning, and HS-based precipitation scaling.
4. **Small ensemble** (for example, 20–40 members) perturbing precipitation, temperature and radiation within documented error ranges ([Richter et al., 2020](https://nhess.copernicus.org/preprints/nhess-2019-433/nhess-2019-433.pdf)), reported as percentiles ([Hatvan et al., 2024](https://arc.lib.montana.edu/snow-science/objects/ISSW2024_P7.11.pdf)).
5. **Observation loop**: every new pit is aligned by DTW, scored, used to update error statistics, optionally to re-initialize (roughly two-weekly, quality-gated), and to weight ensemble members.
6. **Learned layer** limited to forcing bias correction, instability calibration (a local Mayer-style model), and weak-layer persistence — each versioned and evaluated leave-one-season-out against the uncorrected baseline.
7. **Date-tag layer tracking** as the primary unit of communication and verification ([Herla et al., 2024](https://nhess.copernicus.org/articles/24/2727/2024/)).

---

## 12. Evaluation framework

| Level | Metric | Baseline target |
|---|---|---|
| Forcing | Bias, RMSE, CRPS per variable and lead time vs stations | Beat raw NWP |
| Bulk | HS and HN24/HN72 error; daily HS-change correlation | Median correlation > 0.54 ([Horton & Haegeli, 2022](https://tc.copernicus.org/articles/16/3393/2022/tc-16-3393-2022.pdf)) |
| Profile | DTW similarity Φ vs observed pits; by layer class | Beat uncorrected SNOWPACK; Φ ≈ 0.5–0.6 is state of the art |
| Critical layers | POD, precision, PSS by layer type (SH, FC/DH, crusts) with date-tag matching | POD ≈ 75%, precision ≈ 40% ([Herla et al., 2024](https://nhess.copernicus.org/articles/24/2727/2024/)) |
| Stability | Calibration and ROC of p_unstable vs local test results | Reliability diagram near diagonal |
| Ensemble | Spread–skill, rank histograms | Reliable spread |

---

## 13. Limitations, gaps and risks

- **Observation sparsity and bias.** Pits are taken on safe, representative, often sheltered sites ([CAA OGRS 2024](https://cdn.ymaws.com/www.avalancheassociation.ca/resource/resmgr/standards_docs/ogrs2024web.pdf)); they are not random samples of avalanche terrain.
- **Wind.** 1D models cannot represent wind slabs spatially; the agent must say so rather than infer.
- **NWP archives.** Continuous historical HRDPS forecasts are not trivially available; Open-Meteo's historical forecast archive begins around 2022 and model versions change over time ([Open-Meteo](https://open-meteo.com/en/docs/historical-forecast-api)). CaSR v3.2 provides a 1980–2024 reanalysis at ~10 km for spin-up and back-casting, with persistent orographic biases in western mountains ([Khedhaouiria et al., 2026](https://hess.copernicus.org/articles/30/5971/2026/)).
- **Data governance.** InfoEx data sharing requires agreements beyond API access; the MVP should use data the operation owns.
- **Operational status.** Even mature chains carry warnings that they are based on unvalidated forecasts and models ([snowpack.avalanche.ca](https://snowpack.avalanche.ca/)). This system must remain advisory.
- **Knowledge gaps.** No published Rockies-specific recalibration of p_unstable; no validated LLM agent for this task; limited literature on assimilating full manual profiles (as opposed to HS or reflectance) into SNOWPACK ensembles.

---

## 14. Conclusion

The science supports building a self-improving snowpack agent for the Canadian Rockies, provided "self-improving" means disciplined correction of a physics model by observations — not a black box learning stratigraphy from scratch. SNOWPACK already captures the timing of most critical layers from weather; it over-produces weak layers and inherits large precipitation errors. The greatest gains will come from (1) correcting forcing with the user's own station archive, (2) re-anchoring the simulated snowpack with quality-controlled field profiles, and (3) calibrating instability against local test results. The LLM's role is to run this machinery transparently and explain it in forecaster language, with every claim traceable to data.
