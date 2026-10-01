"""User-provided logger-database exports of the FTS360 station network, 2014-2020 (README §6 "User's FTS360
archive"; ADR-030).

Three files from the user (2026-10-01) are archived unchanged (non-zip files gzipped) in ``archive/byk_export``
(kept in git, like ``archive/fts360``) with a manifest (sha256 of the received bytes, original name, receipt time):

- ``2018-11-06 - All stations for Liam Kenny.CSV`` (zipped): hourly (gauges 15 min) Dec 2014 - Nov 2018,
  columns Temp, Wspd, Dir, Mx_Spd, Mx_Dir, H2O_Eq_1hr_mm, HS; 6999 = missing; no humidity.
- ``simpson_lower.CSV``: Simpson Lower full logger table Jan 2015 - Mar 2020 (adds Rh).
- ``Bow Summit Precip Gauge2019-06-13_08-33-04.XML`` (zipped, MS Access export): 15 min, Mar 2016 - Jun 2019,
  cumulative PC and gauge TA.

Times are local standard time (MST, UTC-7) without daylight-saving shifts: verified by the diurnal temperature
cycle and by exact agreement (after +7 h) of the same stations in the user's FTS dashboard export with the
FTS360 API. The converted files (``data/interim/byk_export/<station_key>.csv``) keep FTS360 column names and
a UTC ``Date`` column, so ``ingest.fts360.parse_frame`` applies the same QC to both archives.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import shutil
import xml.etree.ElementTree as ET
import zipfile
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

UTC_OFFSET_H = 7  # MST -> UTC
SENTINELS = (6999.0, -999.0)  # logger "no data" codes (6999 all fields; -999 wind direction only)
KEEP = ["Temp", "TA", "Rh", "Wspd", "Dir", "Mx_Spd", "HS", "PC", "H2O_Eq_1hr_mm"]
STATIONS = {  # logger table name -> station key used in config/external_sources.yaml
    "Avi - BYK Bosworth Lower": "bosworth_lower",
    "Avi - BYK Bosworth Upper": "bosworth_upper",
    "Avi - BYK Bow Summit": "bow_summit",
    "Avi - BYK Bow Summit Precip Gauge": "bow_summit_precip_ab_env",
    "Avi__BYK_Bow_Summit_Precip_Gauge": "bow_summit_precip_ab_env",
    "Avi - BYK Lookout": "lookout",
    "Avi - BYK Simpson Lower": "simpson_lower",
    "Avi__BYK_Simpson_Lower": "simpson_lower",
    "Avi - BYK Simpson Upper": "simpson_upper",
    "Avi - BYK Stanley Lower": "stanley_lower",
    "Avi - BYK Sunshine Village": "sunshine_village_ab_env",
    "Avi - BYK Vulture Peak": "vulture",
    "Avi - BYK Whymper": "whymper",
}


def archive(sources: list[Path], raw_dir: Path = Path("archive/byk_export")) -> list[dict]:
    """Copy user files unchanged into ``raw_dir`` (idempotent by sha256) and log them in the manifest."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    manifest = raw_dir / "manifest.jsonl"
    known = {json.loads(x)["sha256"] for x in manifest.read_text().splitlines()} if manifest.exists() else set()
    out = []
    for src in sources:
        sha = hashlib.sha256(Path(src).read_bytes()).hexdigest()
        name = Path(src).name.split("-", 1)[1] if Path(src).name[:8].isalnum() and Path(src).name[8:9] == "-" \
            else Path(src).name  # strip the upload tool's 8-hex prefix
        dest = raw_dir / (name if name.lower().endswith(".zip") else name + ".gz")
        rec = {"path": str(dest), "original_name": name, "sha256": sha, "bytes": Path(src).stat().st_size,
               "received_utc": datetime.now(UTC).isoformat(timespec="seconds"), "provider": "user upload"}
        if sha in known:
            out.append({**rec, "status": "exists"})
            continue
        if dest.suffix == ".gz":
            dest.write_bytes(gzip.compress(Path(src).read_bytes(), mtime=0))
        else:
            shutil.copyfile(src, dest)
        with open(manifest, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
        out.append({**rec, "status": "archived"})
    return out


def _local_to_utc(s: pd.Series, fmt: str | None = None) -> pd.Series:
    t = pd.to_datetime(s.str.replace("/", "-", regex=False), format=fmt)
    return (t + pd.Timedelta(hours=UTC_OFFSET_H)).dt.tz_localize("UTC")


def _clean(d: pd.DataFrame, station_col: str = "StationName") -> pd.DataFrame:
    cols = [c for c in KEEP if c in d]
    out = d[[station_col, "time_utc", *cols]].copy()
    for c in cols:
        v = pd.to_numeric(out[c], errors="coerce")
        out[c] = v.mask(v.isin(SENTINELS))
    return out.rename(columns={station_col: "logger"})


def read_csv_export(data: bytes) -> pd.DataFrame:
    d = pd.read_csv(io.BytesIO(data), dtype=str, low_memory=False)
    d["time_utc"] = _local_to_utc(d["DateTime"])
    return _clean(d)


def read_xml_export(data: bytes) -> pd.DataFrame:
    """MS Access XML export: one element per record named after the logger table."""
    rows, table = [], None
    for _, el in ET.iterparse(io.BytesIO(data)):
        if el.tag == "DateTimeNum" or el.tag in KEEP or el.tag == "dataroot":
            continue
        if any(c.tag == "DateTimeNum" for c in el):
            table = el.tag
            rows.append({c.tag: c.text for c in el if c.tag == "DateTimeNum" or c.tag in KEEP})
            el.clear()
    d = pd.DataFrame(rows)
    d["StationName"] = table
    d["time_utc"] = _local_to_utc(d["DateTimeNum"].str.replace("T", " "))
    return _clean(d)


def read_raw(raw_dir: Path = Path("archive/byk_export")) -> pd.DataFrame:
    """All archived exports as one long table (logger, time_utc, raw FTS-named columns)."""
    frames = []
    for f in sorted(raw_dir.iterdir()):
        if f.suffix == ".gz":
            data, kind = gzip.decompress(f.read_bytes()), Path(f.stem).suffix.lower()
            if kind == ".csv":
                frames.append(read_csv_export(data).assign(file=f.stem))
            elif kind == ".xml":
                frames.append(read_xml_export(data).assign(file=f.stem))
        elif f.suffix.lower() == ".zip":
            with zipfile.ZipFile(f) as z:
                for n in z.namelist():
                    if n.lower().endswith(".csv"):
                        frames.append(read_csv_export(z.read(n)).assign(file=f.name))
                    elif n.lower().endswith(".xml"):
                        frames.append(read_xml_export(z.read(n)).assign(file=f.name))
        elif f.suffix.lower() == ".csv":
            frames.append(read_csv_export(f.read_bytes()).assign(file=f.name))
        elif f.suffix.lower() == ".xml":
            frames.append(read_xml_export(f.read_bytes()).assign(file=f.name))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def convert(raw_dir: Path = Path("archive/byk_export"), out_dir: Path = Path("data/interim/byk_export")
            ) -> dict[str, dict]:
    """One FTS-named CSV per station key. Where files overlap, each value is taken from the file with the most
    columns for that station (the full logger tables), then filled from the others; overlaps are checked."""
    d = read_raw(raw_dir)
    d["key"] = d["logger"].map(STATIONS)
    unknown = sorted(d.loc[d["key"].isna(), "logger"].unique())
    out_dir.mkdir(parents=True, exist_ok=True)
    summary: dict[str, dict] = {"_unmapped_loggers": {"names": unknown}} if unknown else {}
    for key, g in d.dropna(subset=["key"]).groupby("key"):
        parts = []
        for fname, x in g.groupby("file"):
            x = x.drop(columns=["logger", "key", "file"]).dropna(axis=1, how="all")
            parts.append((x.shape[1], fname, x.drop_duplicates("time_utc").set_index("time_utc")))
        parts.sort(key=lambda t: -t[0])
        merged = parts[0][2]
        disagree = 0
        for _, _, x in parts[1:]:
            common = merged.index.intersection(x.index)
            for c in set(merged.columns) & set(x.columns):
                a, b = merged.loc[common, c], x.loc[common, c]
                disagree += int(((a - b).abs() > 1e-6).sum())
            merged = merged.combine_first(x)
        merged = merged.sort_index()
        res = merged.reset_index().rename(columns={"time_utc": "Date"})
        res["Date"] = res["Date"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        res.to_csv(out_dir / f"{key}.csv", index=False)
        summary[key] = {"files": [p[1] for p in parts], "rows": len(res), "start": res["Date"].iloc[0],
                        "end": res["Date"].iloc[-1], "columns": [c for c in res.columns if c != "Date"],
                        "overlap_disagreements": disagree}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1, default=lambda o: int(o)
                                                     if isinstance(o, np.integer) else str(o)))
    return summary
