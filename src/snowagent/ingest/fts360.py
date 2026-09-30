"""FTS360 station records (README §6 primary actuals) via the FTS360 data API.

Same request the user's Rockies Weather Explorer makes: GET
https://fts360api.com/data/v1/agencies/<agency>/records/csv?stationIds=<hex>&startDate=<iso>&endDate=<iso>
with ``Authorization: Bearer <token>``. The token is never stored in code or config: it comes from the
environment's credential (the network proxy adds the header for fts360api.com) or, if set, the
FTS360_TOKEN environment variable.

Raw CSV responses are written unchanged, one file per station and calendar month, and logged in the
manifest (URL, time, sha256). Column names vary by station (e.g. ATCAvg vs TA), so parsing to SI is a
separate step after inspecting the headers.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import requests

BASE = "https://fts360api.com/data/v1/agencies/{agency}/records/csv"


def month_windows(start: str, end: str) -> list[tuple[pd.Timestamp, pd.Timestamp]]:
    s, e = pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC")
    edges = [s] + [t for t in pd.date_range(s.normalize(), e, freq="MS", tz="UTC") if t > s] + [e]
    return [(a, b) for a, b in zip(edges, edges[1:], strict=False) if b > a]


def fetch_station(agency: int, station_key: str, hex_id: str, start: str, end: str, raw_dir: Path) -> list[dict]:
    headers = {"Authorization": f"Bearer {os.environ['FTS360_TOKEN']}"} if os.environ.get("FTS360_TOKEN") else {}
    manifest = raw_dir / "manifest.jsonl"
    out = []
    for a, b in month_windows(start, end):
        dest = raw_dir / station_key / f"{station_key}_{a:%Y-%m}.csv"
        complete = b < pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=2)
        if dest.exists() and complete:
            out.append({"path": str(dest), "status": "exists"})
            continue
        params = {"stationIds": hex_id, "startDate": a.isoformat().replace("+00:00", "Z"),
                  "endDate": b.isoformat().replace("+00:00", "Z")}
        url = BASE.format(agency=agency)
        for attempt in range(3):
            r = requests.get(url, params=params, headers=headers, timeout=120)
            if r.status_code in (429, 502, 503, 504):
                time.sleep(2 ** (attempt + 2))
                continue
            break
        rec = {"url": r.url, "status_code": r.status_code, "retrieved_utc": datetime.now(UTC).isoformat(timespec="seconds"),
               "station": station_key, "window": [params["startDate"], params["endDate"]]}
        if r.status_code == 401 or r.status_code == 403:
            raise PermissionError(f"FTS360 {r.status_code}: credential missing or not accepted")
        if r.ok:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(r.content)
            rec |= {"path": str(dest), "bytes": len(r.content), "sha256": hashlib.sha256(r.content).hexdigest(),
                    "complete_window": complete}
        else:
            rec["error"] = r.text[:200]
        with open(manifest, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        out.append(rec)
    return out
