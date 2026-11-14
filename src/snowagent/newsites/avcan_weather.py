"""Avalanche Canada weather API station records (README §6: "Additional mountain stations"; ADR-108).

``GET https://weather.prod.avalanche.ca/stations/{id}/measurements?fromDate=&toDate=`` (open, no credential).
Used for stations that are not in the FTS360 feed, first of all Fidelity (Glacier National Park, station 93,
FID_60), whose hourly record on this API starts in mid-2023.

Raw JSON replies are written unchanged, one file per station and calendar month, and logged in a manifest (URL,
time, sha256, rows), like the FTS360 files. ``convert`` turns them into an FTS360-named CSV per station
(``<out_dir>/<key>/<key>.csv``) so the same QC (``ingest.fts360.parse_frame``) applies and ``load_station`` reads
them from the new-sites station folder. Units on this API: air temperature
deg C, relative humidity %, wind km/h, snow height cm, hourly precipitation mm.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import time
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import requests

BASE = "https://weather.prod.avalanche.ca/stations/{sid}/measurements"
# Lowest humidity accepted as a reading. Fidelity's humidity field repeats the air temperature (e.g. -8 with
# airTempAvg -8) or reads 0-6 %; neither is a humidity at a snow plot, so those values are left out (counted).
RH_MIN_PCT = 10.0


def month_windows(start: str, end: str) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    s, e = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    edges = [s] + [t for t in pd.date_range(s.normalize(), e, freq="MS", tz="UTC") if t > s] + [e]
    return [(a, b) for a, b in zip(edges, edges[1:], strict=False) if b > a]


def fetch_station(station_key: str, station_id: int, start: str, end: str, raw_dir: Path,
                  get=requests.get, sleep=time.sleep) -> list[dict]:
    """Monthly raw JSON files of one station. Complete months already on disk are skipped; a reply with fewer rows
    than the month's existing file never replaces it (raw data are immutable; the record carries a warning)."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest = raw_dir / "manifest.jsonl"
    out = []
    for a, b in month_windows(start, end):
        dest = raw_dir / station_key / f"{station_key}_{a:%Y-%m}.json"
        complete = b < pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=2)
        if dest.exists() and complete:
            out.append({"path": str(dest), "status": "exists"})
            continue
        url = BASE.format(sid=station_id)
        params = {"fromDate": a.strftime("%Y-%m-%dT%H:%M:%SZ"), "toDate": b.strftime("%Y-%m-%dT%H:%M:%SZ")}
        rec = {"url": url, "params": params, "station": station_key,
               "retrieved_utc": datetime.now(UTC).isoformat(timespec="seconds")}
        r = None
        for attempt in range(4):
            try:
                r = get(url, params=params, timeout=120)
            except requests.RequestException as exc:
                rec["error"] = f"{type(exc).__name__}: {str(exc)[:150]}"
                sleep(2 ** (attempt + 2))
                continue
            if r.status_code in (429, 502, 503, 504):
                sleep(2 ** (attempt + 2))
                continue
            break
        if r is not None:
            rec["status_code"] = r.status_code
            if r.ok:
                rows = len(r.json())
                rec |= {"bytes": len(r.content), "sha256": hashlib.sha256(r.content).hexdigest(), "rows": rows,
                        "complete_window": complete}
                rec.pop("error", None)
                old = len(json.loads(dest.read_bytes())) if dest.exists() else None
                if old is not None and rows < old:
                    rec |= {"kept_existing": str(dest), "warning": f"{station_key} {a:%Y-%m}: reply had {rows} rows, "
                            f"fewer than the {old} in the existing file; existing file kept"}
                elif rows or not dest.exists():
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_bytes(r.content)
                    rec["path"] = str(dest)
            else:
                rec["error"] = r.text[:200]
        with open(manifest, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        out.append(rec)
    return out


def sync_archive(raw_dir: Path, archive_dir: Path) -> dict:
    """Raw monthly JSON <-> ``archive_dir`` as gzip (unchanged bytes), both ways, so a fresh checkout (or Ben's Mac)
    has the history without fetching: archived months missing from ``raw_dir`` are restored; raw months missing from
    the archive, or with more rows than the archived copy, are archived. Nothing is ever shortened."""
    n_in = n_out = 0
    for gz in sorted(Path(archive_dir).glob("*/*.json.gz")):
        dest = Path(raw_dir) / gz.parent.name / gz.name[:-3]
        if not dest.exists():
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(gzip.decompress(gz.read_bytes()))
            n_in += 1
    for f in sorted(Path(raw_dir).glob("*/*.json")):
        dest = Path(archive_dir) / f.parent.name / (f.name + ".gz")
        raw = f.read_bytes()
        if dest.exists():
            old = gzip.decompress(dest.read_bytes())
            if old == raw or len(json.loads(raw)) <= len(json.loads(old)):
                continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
        n_out += 1
    return {"restored": n_in, "archived": n_out}


def to_fts_frame(rows: list[dict]) -> tuple[pd.DataFrame, dict]:
    """API rows -> a frame with FTS360 column names (``Date``, ``TA``, ``Rh``, ``Wspd``, ``Mx_Spd``, ``Dir``, ``SD``,
    ``H2O_Eq_1hr_mm``) for ``fts360.parse_frame``, and counts of what was left out."""
    d = pd.DataFrame(rows)
    if d.empty:
        return pd.DataFrame(), {"rows": 0}
    out = pd.DataFrame({"Date": d["measurementDateTime"]})
    for src, dst in (("airTempAvg", "TA"), ("windSpeedAvg", "Wspd"), ("windSpeedGust", "Mx_Spd"),
                     ("windDirAvg", "Dir"), ("snowHeight", "SD"), ("hourlyPrecip", "H2O_Eq_1hr_mm")):
        if src in d:
            out[dst] = pd.to_numeric(d[src], errors="coerce")
    notes = {"rows": len(d)}
    if "relativeHumidity" in d:
        rh = pd.to_numeric(d["relativeHumidity"], errors="coerce")
        low = rh < RH_MIN_PCT
        notes["rh_left_out"] = int(low.sum())
        out["Rh"] = rh.where(~low)
    return out, notes


def convert(raw_dir: Path, out_dir: Path, keys: list[str] | None = None) -> dict:
    """Raw monthly JSON -> ``<out_dir>/<key>/<key>.csv`` (FTS360 names, UTC) per station; returns a summary."""
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {}
    for sdir in sorted(p for p in Path(raw_dir).iterdir() if p.is_dir()):
        if keys and sdir.name not in keys:
            continue
        rows = [r for f in sorted(sdir.glob("*.json")) for r in json.loads(f.read_text())]
        frame, notes = to_fts_frame(rows)
        if frame.empty:
            continue
        frame = frame.drop_duplicates("Date").sort_values("Date")
        (out_dir / sdir.name).mkdir(parents=True, exist_ok=True)
        frame.to_csv(out_dir / sdir.name / f"{sdir.name}.csv", index=False)
        summary[sdir.name] = notes | {"first": str(frame["Date"].iloc[0]), "last": str(frame["Date"].iloc[-1])}
    return summary
