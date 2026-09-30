# Data-intake checklist

Send raw files unchanged; they are kept immutable. For each item, units and time zone must be stated,
not inferred.

## Terrain
- [ ] DEM (GeoTIFF or ESRI ASCII), CRS (EPSG), vertical datum/units, no-data value. Cover the domain
      plus ~10-15 km buffer for horizons.
- [ ] Domain boundary polygon (GeoJSON/shapefile) with CRS.
- [ ] Land cover / canopy (forest, open, rock, glacier, water) on or reprojectable to the DEM grid, with legend.
- [ ] Preferred unit resolution to start (default 1 km) and any areas of special interest.

## Stations (received: Simpson Lower, Bow Summit, Sunshine Village - AB Env -> Goat's Eye)
- [ ] Confirm coordinate datum (WGS84?) and elevation datum.
- [ ] Study-plot coordinates/elevation/slope/aspect (Goat's Eye; plots for Simpson Lower and Bow Summit?).
- [ ] Sensor heights (wind, T/RH), heated/weighing gauge type, radiation sensors present, shelter/exposure notes.

## Historical weather (per station)
- [ ] Hourly (or sub-hourly) files; timestamp time zone and interval convention (start/end of interval).
- [ ] Variables with units: TA, RH, VW (+gust, DW), ISWR (+RSWR), ILWR, PSUM, HS, TSS/TSG if available.
- [ ] Source QC flags and known outages/sensor changes.
- [ ] Coverage: at least from each season's snow-free start (Sep/Oct) through spring.

## Forecasts (for no-leakage evaluation)
- [ ] Archived NWP runs with model name/version, init time, time available to you, lead/valid times,
      grid point location and elevation, accumulation intervals.

## Field snow profiles (training/calibration/evaluation only — never required at runtime)
- [ ] Format (CAAML v6 XML/JSON or flat JSON like `templates/profile.example.json`), with a small sample first.
- [ ] Per profile: location, elevation, slope, aspect, observation time **and time zone**, observer,
      full-depth vs test profile, HS, layers (top/bottom, grain form/size, hardness, moisture, density),
      temperatures, stability tests with depths, date tags.
- [ ] Mapping of profile sites to study plots/stations; whether sites are sheltered study plots or slopes.
- [ ] Which seasons/locations may be used for training vs held out.

## Transfer
- Preferred: a separate private GitHub repository for data (split files < 100 MB or use Git LFS);
  it can be attached to this session and cloned. Alternatively a Google Drive folder.
