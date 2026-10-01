"""User-provided logger-database exports of the FTS360 station network, 2014-2026 (README §6 "User's FTS360
archive"; ADR-030, ADR-036).

Three files from the user (2026-10-01) are archived unchanged (non-zip files gzipped) in ``archive/byk_export``
(kept in git, like ``archive/fts360``) with a manifest (sha256 of the received bytes, original name, receipt time):

- ``2018-11-06 - All stations for Liam Kenny.CSV`` (zipped): hourly (gauges 15 min) Dec 2014 - Nov 2018,
  columns Temp, Wspd, Dir, Mx_Spd, Mx_Dir, H2O_Eq_1hr_mm, HS; 6999 = missing; no humidity.
- ``simpson_lower.CSV``: Simpson Lower full logger table Jan 2015 - Mar 2020 (adds Rh).
- ``Bow Summit Precip Gauge2019-06-13_08-33-04.XML`` (zipped, MS Access export): 15 min, Mar 2016 - Jun 2019,
  cumulative PC and gauge TA.

Later (ADR-036): the full logger record tables of Bow Summit (Dec 2014 -), Simpson Lower and Upper (Jan 2015 -) and
Sunshine Village (Aug 2015 -) from the user's per-station Power BI reports, extracted without the reports' station
table (credentials) to ``archive/byk_export/station_tables`` with a manifest (source sha256, rows, time span).

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
KEEP = ["Temp", "TA", "Rh", "Wspd", "Dir", "Mx_Spd", "HS", "SD", "PC", "SW", "H2O_Eq_1hr_mm"]
# one canonical FTS360 name per variable and station, so files with different logger names do not shadow each other
CANONICAL = {"sunshine_village_ab_env": {"Temp": "TA", "HS": "SD"}}
DROP = {"sunshine_village_ab_env": ["H2O_Eq_1hr_mm"]}  # cumulative PC is used where present
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
    # full logger tables exported from the Visitor Safety Power BI station reports (2014/2015 - 2026)
    "Avi__BYK_Bow_Summit": "bow_summit",
    "Avi__BYK_Simpson_Upper": "simpson_upper",
    "Avi__BYK_Sunshine_Village": "sunshine_village_ab_env",
}
STATION_TABLES = "station_tables"  # subfolder with record tables extracted from per-station .pbix (no credentials)
TABLE_VARIABLES = ["Temp", "TA", "TA2", "Rh", "Wspd", "Dir", "Mx_Spd", "Mx_Dir", "SDcm", "SD", "HS", "PC", "SW",
                   "H2O_Eq_1hr_mm", "H2O_Eq_Total", "Total_Precip_mm", "Rn_1", "Rn_Total", "HN_1hr", "Snow_1hr"]


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


def extract_station_table(pbix: Path, out_dir: Path = Path("archive/byk_export") / STATION_TABLES) -> dict:
    """One-off: a per-station Power BI report's logger record table to a credential-free CSV (ADR-036; needs the
    optional ``pbixray``). Only the table named after the logger is read; the report's StationsTable (logger
    credentials) is never read out. Output: ``<out_dir>/<station key>.csv.gz`` plus a manifest line."""
    try:
        from pbixray import PBIXRay
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("extracting a .pbix needs `pip install pbixray`; the archived tables are enough to "
                           "convert") from exc
    model = PBIXRay(str(pbix))
    names = [t for t in model.tables if t in STATIONS]
    if len(names) != 1:
        raise ValueError(f"expected exactly one known logger table in {pbix}, found {names}")
    d = model.get_table(names[0])
    # measurements only (with the logger's pre-cleaning "_Raw_" copies); administrative columns (who edited a
    # record, logger serials, battery/radio diagnostics) are left out
    keep = [c for c in TABLE_VARIABLES if c in d]
    d = d[["DateTimeStr", "DateTimeNum", *keep, *[f"{c}_Raw_" for c in keep if f"{c}_Raw_" in d]]].copy()
    d["DateTimeNum"] = pd.to_datetime(d["DateTimeNum"]).dt.round("s").dt.strftime("%Y-%m-%d %H:%M:%S")
    d = d.sort_values("DateTimeNum", kind="stable")
    buf = io.StringIO()
    d.to_csv(buf, index=False)
    out_dir.mkdir(parents=True, exist_ok=True)
    key = STATIONS[names[0]]
    out = out_dir / f"{'sunshine_village' if key == 'sunshine_village_ab_env' else key}.csv.gz"
    out.write_bytes(gzip.compress(buf.getvalue().encode(), mtime=0))
    raw = Path(pbix).read_bytes()
    rec = {"path": str(out), "logger_table": names[0], "source_file": Path(pbix).name,
           "source_sha256": hashlib.sha256(raw).hexdigest(), "rows": len(d), "columns": list(d.columns),
           "first": d["DateTimeNum"].iloc[0], "last": d["DateTimeNum"].iloc[-1],
           "extracted_utc": datetime.now(UTC).isoformat(timespec="seconds"),
           "note": "record table only; StationsTable (credentials) not extracted"}
    with open(out_dir / "manifest.jsonl", "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    return rec


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
    tables = raw_dir / STATION_TABLES
    if tables.exists():
        man = {json.loads(x)["path"].split("/")[-1]: json.loads(x) for x in
               (tables / "manifest.jsonl").read_text().splitlines()} if (tables / "manifest.jsonl").exists() else {}
        for f in sorted(tables.glob("*.csv.gz")):
            d = pd.read_csv(f, low_memory=False)
            d = d[[c for c in d.columns if not c.endswith("_Raw_")]]
            d["StationName"] = man.get(f.name, {}).get("logger_table", f.name)
            d["time_utc"] = _local_to_utc(d["DateTimeNum"].astype(str))
            frames.append(_clean(d).assign(file=f"{STATION_TABLES}/{f.name}"))
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _canonical(x: pd.DataFrame, key: str) -> pd.DataFrame:
    """Rename logger aliases to the station's canonical name; where a file carries both (the Sunshine table has
    Temp and TA, HS and SD, identical where both are set), keep the canonical value and fill from the alias."""
    for alias, name in CANONICAL.get(key, {}).items():
        if alias in x:
            x = x.assign(**{name: x[name].combine_first(x[alias]) if name in x else x[alias]}).drop(columns=alias)
    return x


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
            x = _canonical(x.drop(columns=["logger", "key", "file"]), key)
            x = x.drop(columns=[c for c in DROP.get(key, []) if c in x]).dropna(axis=1, how="all")
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
        tmp = out_dir / f".{key}.csv.tmp"
        res.to_csv(tmp, index=False)
        tmp.replace(out_dir / f"{key}.csv")  # atomic: readers never see a partial file
        summary[key] = {"files": [p[1] for p in parts], "rows": len(res), "start": res["Date"].iloc[0],
                        "end": res["Date"].iloc[-1], "columns": [c for c in res.columns if c != "Date"],
                        "overlap_disagreements": disagree}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1, default=lambda o: int(o)
                                                     if isinstance(o, np.integer) else str(o)))
    return summary
