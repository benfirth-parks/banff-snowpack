# weather_history/

Station weather files supplied by the user. `raw/` is immutable: files are stored exactly as received.

| File | Received | sha256 | Content |
|---|---|---|---|
| raw/ALL_STATIONS_2026.BYK.zip | 2026-09-30 (chat upload) | eeaa1853754c3f55d9d6c0837c4912685e61a5657cb310b7eec455e4fdc45457 | `ALL STATIONS 2026.BYK.csv`: FTS360 export, 25 Parks stations (BYK, JNP, LLYK, Waterton, Yoho EnvCan), hourly, 2023-12-30 to 2026-09-16 |

Notes (verified 2026-09-30):
- Timestamps are naive **MST (UTC-7)**: shifted by -7 h they match the FTS360 API (UTC) exactly for Simpson
  Lower, Jan-Mar 2024 (2,153 h, mean |dT| = 0.0 C).
- Variables: `Temp`, `Min_Temp_24hr`, `Mx_Temp_24hr` (C), `HS`, `HN24`, `HN_1hr` (cm), plus logger diagnostics
  (DataQual, FreqOffset, LoggerSn, ModIndex, SigStrength, Vb). No wind, humidity, precipitation or radiation;
  the FTS360 API (`snowagent ingest fts360`) has those for most stations.
- Simpson Upper has temperature only (no HS) in this export.
