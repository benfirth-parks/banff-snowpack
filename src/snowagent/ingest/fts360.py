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


def _iso_ms(t: pd.Timestamp) -> str:
    """Same form as JavaScript Date.toISOString(), which the API validates: 2026-09-29T00:00:00.000Z."""
    return t.tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%S.") + f"{t.microsecond // 1000:03d}Z"


def fetch_station(agency: int, station_key: str, hex_id: str, start: str, end: str, raw_dir: Path) -> list[dict]:
    headers = {"Authorization": f"Bearer {os.environ['FTS360_TOKEN']}"} if os.environ.get("FTS360_TOKEN") else {}
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest = raw_dir / "manifest.jsonl"
    out = []
    for a, b in month_windows(start, end):
        dest = raw_dir / station_key / f"{station_key}_{a:%Y-%m}.csv"
        complete = b < pd.Timestamp.now(tz="UTC") - pd.Timedelta(days=2)
        if dest.exists() and complete:
            out.append({"path": str(dest), "status": "exists"})
            continue
        params = {"stationIds": hex_id, "startDate": _iso_ms(a), "endDate": _iso_ms(b)}
        url = BASE.format(agency=agency)
        r = None
        for attempt in range(5):
            try:
                r = requests.get(url, params=params, headers=headers, timeout=120)
            except requests.RequestException as exc:  # dropped connection: back off, retry, then record
                last_exc = exc
                time.sleep(2 ** (attempt + 2))
                continue
            if r.status_code in (429, 502, 503, 504):
                time.sleep(2 ** (attempt + 2))
                continue
            break
        if r is None:
            rec = {"url": url, "status_code": None, "station": station_key, "window": [params["startDate"],
                   params["endDate"]], "retrieved_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                   "error": f"{type(last_exc).__name__}: {str(last_exc)[:150]}"}
            with open(manifest, "a") as fh:
                fh.write(json.dumps(rec) + "\n")
            out.append(rec)
            continue
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


# FTS360 column -> (canonical SI column, converter). Station loggers name sensors differently.
COLUMNS = {
    "Temp": ("ta_k", lambda v: v + 273.15), "TA": ("ta_k", lambda v: v + 273.15),
    "Rh": ("rh_frac", lambda v: v / 100.0),
    "Wspd": ("vw_ms", lambda v: v / 3.6), "Mx_Spd": ("vw_max_ms", lambda v: v / 3.6),  # logged in km/h
    "Dir": ("dw_deg", lambda v: v),
    "HS": ("hs_m", lambda v: v / 100.0), "SDcm": ("hs_m", lambda v: v / 100.0), "SD": ("hs_m", lambda v: v / 100.0),
    "PC": ("pc_cum_mm", lambda v: v),  # AB Env weighing-gauge cumulative precipitation
    "Rn_1": ("rain_1h_mm", lambda v: v),  # tipping bucket (rain only)
}
HS_SPIKE_M = 0.30  # departure from the centred 24 h median that marks a snow-depth value as a spike
RANGES = {"ta_k": (228.15, 308.15), "rh_frac": (0.0, 1.05), "vw_ms": (0.0, 40.0), "vw_max_ms": (0.0, 60.0),
          "dw_deg": (0.0, 360.0), "hs_m": (0.0, 6.0), "pc_cum_mm": (0.0, 5000.0), "rain_1h_mm": (0.0, 50.0)}


def parse_station(files: list[Path]) -> pd.DataFrame:
    """Raw monthly CSVs -> hourly SI table with a QC column per variable (ok/bad/missing); never filled.

    Sub-hourly stations (AB Env, 15 min) are taken at the top of the hour. Cumulative gauge precipitation
    becomes hourly increments; negative increments (gauge emptying/resets) and >25 mm/h are flagged bad.
    Wind units are km/h in FTS360 exports (checked: Simpson Upper median 10 km/h, max gust ~69 km/h).
    """
    frames = [pd.read_csv(f, dtype=str) for f in files if Path(f).stat().st_size > 0]
    frames = [f for f in frames if len(f)]
    if not frames:
        return pd.DataFrame()
    return parse_frame(pd.concat(frames, ignore_index=True))


def parse_frame(d: pd.DataFrame) -> pd.DataFrame:
    """FTS360-named columns with a UTC ``Date`` column -> hourly SI table with QC (see ``parse_station``).

    Shared by the FTS360 API files and the user's logger-database exports (``ingest.byk_export``), so both
    pass identical QC. A logger's hourly gauge increment (``H2O_Eq_1hr_mm``) is used only where no cumulative
    ``PC`` exists.
    """
    d = d.copy()
    d["time_utc"] = pd.to_datetime(d["Date"], utc=True)
    d = d.drop_duplicates("time_utc").set_index("time_utc").sort_index()
    d = d[d.index.minute == 0]
    out = pd.DataFrame(index=d.index)
    for col, (name, conv) in COLUMNS.items():
        if col in d and name not in out:
            txt = d[col].astype(str)
            v = pd.to_numeric(txt.where(~txt.str.contains("/")), errors="coerce")
            if v.notna().any():
                out[name] = conv(v)
    for name, (lo, hi) in RANGES.items():
        if name in out:
            bad = out[name].notna() & ~out[name].between(lo, hi)
            out[name + "_qc"] = "ok"
            out.loc[out[name].isna(), name + "_qc"] = "missing"
            out.loc[bad, name + "_qc"] = "bad"
    if "hs_m" in out:  # isolated spikes (sensor echo off snowfall/vegetation): suspect, never used for scoring
        valid = out["hs_m"].where(out["hs_m_qc"] == "ok")
        med = pd.Series(valid.to_numpy(), index=out.index).rolling("24h", center=True, min_periods=6).median()
        spike = (valid - med.to_numpy()).abs() > HS_SPIKE_M
        out.loc[spike.to_numpy(), "hs_m_qc"] = "suspect"
    if "pc_cum_mm" in out:
        inc = out["pc_cum_mm"].where(out["pc_cum_mm_qc"] == "ok").diff()
        gap = out.index.to_series().diff() != pd.Timedelta(hours=1)
        out["psum_1h_mm"] = inc.where(~gap)
        out["psum_1h_mm_qc"] = "ok"
        out.loc[out["psum_1h_mm"].isna(), "psum_1h_mm_qc"] = "missing"
        out.loc[(inc < -0.5) | (inc > 25), "psum_1h_mm_qc"] = "bad"
    elif "H2O_Eq_1hr_mm" in d:
        inc = pd.to_numeric(d["H2O_Eq_1hr_mm"], errors="coerce").reindex(out.index)
        out["psum_1h_mm"] = inc
        out["psum_1h_mm_qc"] = "ok"
        out.loc[inc.isna(), "psum_1h_mm_qc"] = "missing"
        out.loc[(inc < -0.5) | (inc > 25), "psum_1h_mm_qc"] = "bad"
    return out.reset_index()


def load_station(key: str, fts_raw: Path = Path("data/raw/fts360"),
                 byk_interim: Path = Path("data/interim/byk_export")) -> pd.DataFrame:
    """All QC'd hourly records of one station: FTS360 API files plus the logger-database export (ADR-030).

    Each source is QC'd on its own (the archives do not overlap: exports end 2020, the API starts 2021-05);
    where they would overlap the FTS360 value is kept.
    """
    parts = [parse_station(sorted((Path(fts_raw) / key).glob("*.csv")))]
    f = Path(byk_interim) / f"{key}.csv"
    if f.exists():
        parts.append(parse_frame(pd.read_csv(f, dtype=str)))
    parts = [x for x in parts if not x.empty]
    if not parts:
        return pd.DataFrame()
    d = pd.concat(parts, ignore_index=True).drop_duplicates("time_utc", keep="first")
    return d.sort_values("time_utc").reset_index(drop=True)
