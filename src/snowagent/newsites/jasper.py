"""Jasper avalanche-program loggers in the FTS360 feed (Big Bend, Tangle Ridge; ADR-108) -> Banff column names.

The raw monthly CSVs (``data/raw/fts360/<key>/``, archived in ``archive/fts360/<key>/``) stay unchanged. This writes
one table per station with the names ``ingest.fts360.parse_frame`` reads (``Temp``, ``Rh``, ``Wspd``, ``Mx_Spd``,
``Dir``, ``SD``, ``PC``) and a JSON note of what it changed, so the Banff QC applies as it is.

Two things differ from the Banff loggers and are handled here:

- **Temperatures without their sign.** From late November 2021 to June 2023 the feed's copy of both loggers holds
  whole numbers only and almost nothing below zero: -12.4 deg C arrives as 12 (December means of +15 deg C at
  2125 m). Those readings are written as missing (``///``, which ``parse_frame`` reads as no value) and every
  such hour is listed in the note. Normal records carry decimals on ~90% of readings. Humidity, wind, snow depth
  and the gauge total are whole numbers in those months too, but cannot be negative, so they are kept.
- **Readings at :58.** Big Bend logged at 58 minutes past the hour until February 2019; a reading within five
  minutes before the hour is taken as that hour when the hour has no reading of its own.

Units, as in Banff's FTS360 copies: deg C, %, km/h, degrees, cm, cumulative mm (``Precip``). Big Bend's wind
matches Avalanche Canada's station 115 for the same hours.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

RENAME = {"ATCAvg": "Temp", "RHAvg": "Rh", "AvgWS": "Wspd", "PeakWS": "Mx_Spd", "AvgWD": "Dir", "SD": "SD",
          "Precip": "PC"}
SIGN_LOST_MIN_VALUES = 24  # readings a 3-day window needs before it can be judged
SIGN_LOST_WHOLE_FRAC = 0.8  # normal records: ~10% whole numbers; sign-lost records: ~100%
SNAP_MINUTES = 5


def _numbers(s: pd.Series) -> pd.Series:
    txt = s.astype(str)
    return pd.to_numeric(txt.where(~txt.str.contains("/")), errors="coerce")


def sign_lost(temps: pd.Series) -> pd.Series:
    """Readings (``temps``: numbers on a UTC hourly index) that came through rounded and without their sign: every
    hour whose centred 3-day window holds at least ``SIGN_LOST_MIN_VALUES`` readings, at least
    ``SIGN_LOST_WHOLE_FRAC`` of them whole numbers and under 10% negative; plus the whole, non-negative readings
    within 36 hours of such a window (the edges of a spell)."""
    f = pd.DataFrame({"n": temps.notna().astype(float),
                      "whole": (temps.notna() & (temps % 1 == 0)).astype(float),
                      "neg": (temps < 0).astype(float)}, index=temps.index)
    w = f.rolling("72h", center=True).sum()
    lost = (w["n"] >= SIGN_LOST_MIN_VALUES) & (w["whole"] >= SIGN_LOST_WHOLE_FRAC * w["n"]) & (w["neg"] < 0.1 * w["n"])
    near = lost.astype(float).rolling("73h", center=True).max() > 0
    return (lost | (near & (f["whole"] > 0) & (temps >= 0))).fillna(False)


def to_banff_frame(raw: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """One station's raw rows (all months) -> Banff-named rows on whole UTC hours, and a note of the changes."""
    d = raw.copy()
    t = pd.to_datetime(d["Date"], utc=True)
    on_hour = t.dt.minute == 0
    late = t.dt.minute >= 60 - SNAP_MINUTES
    t = t.where(~late, t.dt.ceil("h"))
    keep = on_hour | late
    d, t = d[keep], t[keep]
    d = d.assign(_t=t, _exact=on_hour[keep].to_numpy()).sort_values(["_t", "_exact"])
    d = d.drop_duplicates("_t", keep="last")  # an hour's own reading wins over a snapped one
    out = pd.DataFrame({"Date": d["_t"].dt.strftime("%Y-%m-%dT%H:%M:%SZ").to_numpy()})
    for src, dst in RENAME.items():
        if src in d:
            out[dst] = d[src].to_numpy()
    note = {"rows": int(len(out)), "snapped_from_minute_55_59": int((~d["_exact"]).sum())}
    if "Temp" in out:
        temps = _numbers(out["Temp"])
        temps.index = pd.DatetimeIndex(d["_t"].to_numpy())
        lost = sign_lost(temps).to_numpy()
        out.loc[lost, "Temp"] = "///"
        hours = out.loc[lost, "Date"]
        note |= {"temperature_sign_lost_hours": int(lost.sum()),
                 "temperature_sign_lost_first": hours.iloc[0] if len(hours) else None,
                 "temperature_sign_lost_last": hours.iloc[-1] if len(hours) else None}
    return out, note


def convert(raw_dir: Path, out_dir: Path, keys: list[str]) -> dict:
    """``raw_dir/<key>/*.csv`` -> ``out_dir/<key>/<key>.csv`` and ``<key>.json`` (the note) per station."""
    summary = {}
    for key in keys:
        files = [f for f in sorted((Path(raw_dir) / key).glob("*.csv")) if f.stat().st_size > 0]
        frames = [x for x in (pd.read_csv(f, dtype=str) for f in files) if len(x)]
        if not frames:
            continue
        out, note = to_banff_frame(pd.concat(frames, ignore_index=True))
        dest = Path(out_dir) / key
        dest.mkdir(parents=True, exist_ok=True)
        out.to_csv(dest / f"{key}.csv", index=False)
        note |= {"first": out["Date"].iloc[0], "last": out["Date"].iloc[-1], "source_files": len(files)}
        (dest / f"{key}.json").write_text(json.dumps(note, indent=1))
        summary[key] = note
    return summary
