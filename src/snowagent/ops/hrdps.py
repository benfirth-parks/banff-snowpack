"""Archived HRDPS 2.5 km forecasts (ECCC MSC Datamart, hpfx.collab.science.gc.ca, about 7 weeks kept) at points.

The Datamart keeps each day's runs for a few weeks only (hpfx about 52 days, dd.weather.gc.ca about 30), so every
00 UTC run must be extracted while it is still there; the extracted point values are the long archive (ADR-116).
Each variable and lead is its own GRIB2 file (one message, rotated lat-lon grid, 0.0225 deg); the whole file is
read, the point values kept, and for every file the URL and the sha256 of its bytes recorded (so any value can be
re-derived exactly while the file is still published), not the fields themselves.

Point values: inverse-distance weights over the 4 nearest grid points (eccodes ``find_nearest``), plus the model
surface height (HGT, lead 0) at the same weights for lapse-rate adjustment downstream. Accumulations are kept as
HRDPS reports them (total and snow precipitation in kg m-2, radiation in J m-2, both since the run start); de-accumulation
happens downstream. HRDPS runs reach 48 h; the 48-72 h tail of a 72 h forecast comes from GFS (ADR-116).

This module only archives (part of the daily update); ``forecast.hrdps_point`` reads the archive.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import requests

BASES = ("https://hpfx.collab.science.gc.ca", "https://dd.weather.gc.ca")
MAX_LEAD = 48
WORKERS = 4  # parallel downloads; the Datamart started resetting connections at 12 (2026-11-18)
RETRY_WAIT_S = 10.0
FIELDS = {  # file variable/level token -> output column
    "TMP_AGL-2m": "tmp2m_k", "RH_AGL-2m": "rh2m_pct", "WIND_AGL-10m": "wind10m_ms", "WDIR_AGL-10m": "wdir10m_deg",
    "APCP_Sfc": "apcp_kgm2", "DSWRF_Sfc": "dswrf_jm2", "DLWRF_Sfc": "dlwrf_jm2", "PRES_Sfc": "pres_pa",
    "WEASN_Sfc": "snow_kgm2",
}
ACCUM = {"apcp_kgm2", "dswrf_jm2", "dlwrf_jm2", "snow_kgm2"}  # not in the lead-0 file


def file_url(base: str, run: datetime, lead: int, token: str) -> str:
    ymd, hh = run.strftime("%Y%m%d"), run.strftime("%H")
    return (f"{base}/{ymd}/WXO-DD/model_hrdps/continental/2.5km/{hh}/{lead:03d}/"
            f"{ymd}T{hh}Z_MSC_HRDPS_{token}_RLatLon0.0225_PT{lead:03d}H.grib2")


def _get(run: datetime, lead: int, token: str, session: requests.Session) -> tuple[str, bytes] | None:
    for base in BASES:
        url = file_url(base, run, lead, token)
        for attempt in range(3):
            try:
                r = session.get(url, timeout=120)
            except requests.RequestException:
                time.sleep(RETRY_WAIT_S * (attempt + 1))  # the Datamart resets connections when busy: back off
                continue
            if r.status_code == 404:
                break
            if r.ok and r.content[:4] == b"GRIB":
                return url, r.content
    return None


_NEAR: dict[tuple, list[tuple[list[int], list[float]]]] = {}  # grid geometry + points -> (indices, weights)


def _point_values(data: bytes, points: dict[str, tuple[float, float]]) -> dict[str, float]:
    import eccodes

    gid = eccodes.codes_new_from_message(data)
    try:
        geom = tuple(eccodes.codes_get(gid, k) for k in (
            "Ni", "Nj", "latitudeOfFirstGridPointInDegrees", "longitudeOfFirstGridPointInDegrees",
            "latitudeOfSouthernPoleInDegrees", "longitudeOfSouthernPoleInDegrees")) + tuple(points.items())
        if geom not in _NEAR:  # every file of a run shares one grid: the nearest-point search runs once
            near = [eccodes.codes_grib_find_nearest(gid, lat, lon, npoints=4) for lat, lon in points.values()]
            _NEAR[geom] = [([n["index"] for n in nn], [1.0 / max(n["distance"], 1e-3) ** 2 for n in nn])
                           for nn in near]
        values = eccodes.codes_get_values(gid)
        return {k: float(sum(w * values[i] for i, w in zip(idx, ws, strict=True)) / sum(ws))
                for k, (idx, ws) in zip(points, _NEAR[geom], strict=True)}
    finally:
        eccodes.codes_release(gid)


def extract_run(run: datetime, points: dict[str, tuple[float, float]], max_lead: int = MAX_LEAD,
                workers: int = WORKERS) -> tuple[list[dict], list[dict]]:
    """Return (rows, provenance) for one HRDPS cycle; a file not published leaves its value out (null)."""
    jobs = [(lead, tok, col) for lead in range(0, max_lead + 1) for tok, col in FIELDS.items()
            if not (lead == 0 and col in ACCUM)] + [(0, "HGT_Sfc", "model_elev_m")]
    session = requests.Session()

    def one(job):
        lead, tok, col = job
        got = _get(run, lead, tok, session)
        if got is None:
            return job, None, None
        url, data = got
        return job, _point_values(data, points), {"url": url, "sha256": hashlib.sha256(data).hexdigest()}

    vals: dict[int, dict[str, dict[str, float]]] = {lead: {k: {} for k in points} for lead in range(max_lead + 1)}
    elev: dict[str, float] = {}
    prov = []
    with ThreadPoolExecutor(workers) as ex:
        for (lead, _tok, col), v, p in ex.map(one, jobs):
            if v is None:
                continue
            prov.append({**p, "lead_h": lead, "column": col})
            for k, x in v.items():
                if col == "model_elev_m":
                    elev[k] = x
                else:
                    vals[lead][k][col] = x
    rows = []
    for lead in range(max_lead + 1):
        for k in points:
            if lead == 0:  # accumulations start at zero
                vals[0][k].update({c: 0.0 for c in ACCUM})
            rows.append({"run_utc": run.isoformat(), "lead_h": lead, "point": k, **vals[lead][k],
                         "model_elev_m": elev.get(k)})
    return rows, sorted(prov, key=lambda p: (p["lead_h"], p["column"]))


def run_complete(path: Path, points: Iterable[str], max_lead: int = MAX_LEAD) -> bool:
    """True when an extracted run holds every point, reaches ``max_lead`` and has temperature and precipitation at
    every lead (a run extracted while the Datamart was still publishing it is redone)."""
    import pandas as pd

    if not Path(path).exists():
        return False
    try:
        d = pd.read_csv(path)
    except (ValueError, OSError):
        return False
    if not len(d) or not set(points) <= set(d["point"]) or d["lead_h"].max() < max_lead:
        return False
    return bool(d[["tmp2m_k", "apcp_kgm2"]].notna().all().all()) if {"tmp2m_k", "apcp_kgm2"} <= set(d) else False


def write_run(rows: list[dict], prov: list[dict], out_dir: Path, run: datetime) -> Path:
    import pandas as pd

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"hrdps_{run.strftime('%Y%m%d%H')}"
    pd.DataFrame(rows).to_csv(out_dir / f"{stem}.csv", index=False)
    (out_dir / f"{stem}.provenance.json").write_text(json.dumps(
        {"retrieved_utc": datetime.now(UTC).isoformat(timespec="seconds"), "files": prov}, indent=0))
    return out_dir / f"{stem}.csv"
