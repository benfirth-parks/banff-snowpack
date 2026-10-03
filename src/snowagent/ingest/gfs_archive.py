"""Archived GFS 0.25 deg forecasts (AWS Open Data, s3://noaa-gfs-bdp-pds, 2021-01 onward) at points.

Only the needed GRIB messages are fetched, by HTTP byte range from each file's .idx. The public archive is
the immutable raw source; we keep the extracted point values plus, for every message, the URL, byte range
and sha256 of the bytes read (so any value can be re-derived exactly), not the global fields themselves.

Point values: bilinear from the 4 surrounding grid points, plus the model surface height (HGT:surface) at
the same weights so downstream lapse-rate adjustment to station/terrain elevation has its reference.
Accumulations and averages are kept as GFS reports them (APCP resets every 6 h, fluxes are running
averages since the last 6-h boundary); de-accumulation happens downstream, not here.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

import requests

BASE = "https://noaa-gfs-bdp-pds.s3.amazonaws.com"
FIELDS = {  # idx "VAR:LEVEL" prefix -> output column
    "TMP:2 m above ground": "tmp2m_k", "RH:2 m above ground": "rh2m_pct", "UGRD:10 m above ground": "u10_ms",
    "VGRD:10 m above ground": "v10_ms", "APCP:surface": "apcp_kgm2", "DSWRF:surface": "dswrf_wm2",
    "DLWRF:surface": "dlwrf_wm2", "PRES:surface": "pres_pa", "HGT:surface": "model_elev_m",
    "CSNOW:surface": "csnow",
}


def _idx(url: str) -> list[tuple[int, int | None, str]]:
    lines = requests.get(url + ".idx", timeout=60).text.strip().splitlines()
    parts = [ln.split(":") for ln in lines]
    out = []
    for i, p in enumerate(parts):
        start = int(p[1])
        end = int(parts[i + 1][1]) - 1 if i + 1 < len(parts) else None
        out.append((start, end, ":".join(p[3:6])))
    return out


def _weights(lat: float, lon: float) -> tuple[list[tuple[int, int]], list[float]]:
    lon = lon % 360
    i0, j0 = int((90 - lat) // 0.25), int(lon // 0.25)
    fy, fx = ((90 - lat) - i0 * 0.25) / 0.25, (lon - j0 * 0.25) / 0.25
    idx = [(i0, j0), (i0, j0 + 1), (i0 + 1, j0), (i0 + 1, j0 + 1)]
    w = [(1 - fy) * (1 - fx), (1 - fy) * fx, fy * (1 - fx), fy * fx]
    return idx, w


def extract_run(run: datetime, leads: list[int], points: dict[str, tuple[float, float]]) -> tuple[list[dict], list[dict]]:
    """Return (rows, provenance) for one GFS cycle."""
    import eccodes

    ymd, hh = run.strftime("%Y%m%d"), run.strftime("%H")
    rows, prov = [], []
    wts = {k: _weights(*v) for k, v in points.items()}
    for lead in leads:
        url = f"{BASE}/gfs.{ymd}/{hh}/atmos/gfs.t{hh}z.pgrb2.0p25.f{lead:03d}"
        vals: dict[str, dict[str, float]] = {k: {} for k in points}
        for start, end, desc in _idx(url):
            key = ":".join(desc.split(":")[:2])
            col = FIELDS.get(key)
            if col is None or (col in ("apcp_kgm2", "dswrf_wm2", "dlwrf_wm2", "csnow") and "ave" not in desc
                               and "acc" not in desc) or (col == "apcp_kgm2" and "day acc" in desc):
                continue  # keep the 6-h-window accumulation/average message only
            rng = f"bytes={start}-{'' if end is None else end}"
            data = requests.get(url, headers={"Range": rng}, timeout=120).content
            prov.append({"url": url, "range": rng, "desc": desc, "sha256": hashlib.sha256(data).hexdigest()})
            gid = eccodes.codes_new_from_message(data)
            ni, nj = eccodes.codes_get(gid, "Ni"), eccodes.codes_get(gid, "Nj")
            field = eccodes.codes_get_values(gid).reshape(nj, ni)
            eccodes.codes_release(gid)
            for k, (ij, w) in wts.items():
                vals[k][col] = float(sum(wi * field[i, j % ni] for (i, j), wi in zip(ij, w, strict=True)))
                vals[k][col + "_desc"] = desc.split(":")[-1]
        for k in points:
            rows.append({"run_utc": run.isoformat(), "lead_h": lead, "point": k, **vals[k]})
    return rows, prov


def run_complete(path: Path, points: Iterable[str], max_lead: int) -> bool:
    """True when an extracted run's CSV holds every requested point and reaches ``max_lead``; an earlier partial or
    test extract (fewer points or leads), a missing or an unreadable file is not complete and is redone."""
    import pandas as pd

    if not Path(path).exists():
        return False
    try:
        d = pd.read_csv(path, usecols=["lead_h", "point"])
    except (ValueError, OSError):  # empty, truncated or without the columns
        return False
    return len(d) > 0 and set(points) <= set(d["point"]) and d["lead_h"].max() >= max_lead


def write_run(rows: list[dict], prov: list[dict], out_dir: Path, run: datetime) -> Path:
    import pandas as pd

    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"gfs_{run.strftime('%Y%m%d%H')}"
    pd.DataFrame(rows).to_csv(out_dir / f"{stem}.csv", index=False)
    (out_dir / f"{stem}.provenance.json").write_text(json.dumps(
        {"retrieved_utc": datetime.now(UTC).isoformat(timespec="seconds"), "messages": prov}, indent=0))
    return out_dir / f"{stem}.csv"
