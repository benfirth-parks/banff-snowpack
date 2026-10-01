# Visitor Safety FTS dashboard history (Power BI), Oct 2015 - Jul 2026

`data_historic.csv.gz`: the dashboard's "Data Historic" record table, extracted once with pbixray from
`FTS_Data_Visitor_Safety_Historical_Data_Power_BI_Report.pbix` (user upload 2026-10-01; sha256 in
`manifest.jsonl`). The dashboard's station table is NOT extracted (it carries station credentials) and the .pbix
is not stored in git. Timestamps (`DateTime`) are MST (UTC-7, no DST); `LocalTime` is MDT/MST.
Conversion per station: `snowagent ingest fts-dashboard` -> data/interim/fts_dashboard; mapping and checks:
`snowagent.ingest.fts_dashboard` (ADR-034).
