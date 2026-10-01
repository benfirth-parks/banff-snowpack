# FTS360 logger-database exports from the user (2014-2020)

Received 2026-10-01 as uploads; archived unchanged (`simpson_lower.CSV` gzipped). `manifest.jsonl` records the
sha256 of the received bytes. Local standard time (MST, UTC-7, no DST); 6999 = no data, -999 = no wind direction.
Conversion to per-station FTS-named CSVs: `snowagent ingest byk` (no arguments re-converts this folder into
data/interim/byk_export); QC: `snowagent.ingest.fts360.load_station` (ADR-030).

| file | content |
|---|---|
| 2018-11-06_-_All_Stations_for_Liam_Kenny.zip | 11 BYK stations, hourly (gauges 15 min), Dec 2014 - Nov 2018; Temp, wind, HS, H2O_Eq_1hr_mm; no humidity |
| simpson_lower.CSV.gz | Simpson Lower full logger table, Jan 2015 - Mar 2020 (adds Rh) |
| Bow_Summit_Precip_Gauge2019-06-13_08-33-04.zip | Bow Summit precipitation gauge, MS Access XML, 15 min, Mar 2016 - Jun 2019 (PC, TA) |

Not archived (checked, nothing new): `simpson_low_2019-08-15.csv/.XML/.xsd` (subsets of simpson_lower.CSV,
identical values), the `.xsd` schemas, and the FTS Power BI dashboard (`FTS_Data_Visitor_Safety-2026-05-26.pbix`:
same records as the FTS360 API from 2023-12-30; it also contains station credentials, so it is not stored).
