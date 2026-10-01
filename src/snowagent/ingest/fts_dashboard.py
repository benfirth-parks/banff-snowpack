"""Visitor Safety FTS dashboard history (Power BI, Oct 2015 - Jul 2026) as a third station archive (ADR-034).

The user's dashboard combines pairs of FTS loggers per area ("Sunshine-Lookout" = Sunshine AB Env low + Lookout
high, ...). Its record table ("Data Historic") is extracted once, without the station table (which carries
station credentials), to ``archive/fts_dashboard/data_historic.csv.gz`` with the source file's sha256; the
.pbix itself stays out of git. Timestamps are MST (UTC-7), as in the logger exports.

Column-to-station mapping, verified against the FTS360 API (2021-26) and the logger exports (2015-18) after the
+7 h shift: identical values for every mapped temperature, humidity, wind and Bow/Simpson snow-depth column;
Sunshine temperature 94%, snow depth 91%, pillow 94% identical (the dashboard samples/cleans those slightly
differently); gauge totals r = 1.00 with 79-94% of hours identical. Lowest precedence: FTS360 API, then the
logger exports, then this; used where the others have no record (mainly Nov 2018 - May 2021).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd

ARCHIVE = Path("archive/fts_dashboard")
UTC_OFFSET_H = 7
# (dashboard station, column) -> (station key, FTS360 column name understood by fts360.parse_frame)
MAPPING = {
    ("Sunshine-Lookout", "Temp_Low"): ("sunshine_village_ab_env", "TA"),
    ("Sunshine-Lookout", "HS"): ("sunshine_village_ab_env", "SD"),
    ("Sunshine-Lookout", "H2O_Total"): ("sunshine_village_ab_env", "PC"),
    ("Sunshine-Lookout", "SW"): ("sunshine_village_ab_env", "SW"),
    ("Sunshine-Lookout", "Temp_High"): ("lookout", "Temp"),
    ("Sunshine-Lookout", "Rh_High"): ("lookout", "Rh"),
    ("Sunshine-Lookout", "Wspd"): ("lookout", "Wspd"),
    ("Sunshine-Lookout", "Dir"): ("lookout", "Dir"),
    ("Sunshine-Lookout", "Mx_Spd"): ("lookout", "Mx_Spd"),
    ("Bow-Vulture", "Temp_Low"): ("bow_summit", "Temp"),
    ("Bow-Vulture", "HS"): ("bow_summit", "HS"),
    ("Bow-Vulture", "H2O_Total"): ("bow_summit_precip_ab_env", "PC"),
    ("Bow-Vulture", "Temp_High"): ("vulture", "Temp"),
    ("Bow-Vulture", "Rh_High"): ("vulture", "Rh"),
    ("Bow-Vulture", "Wspd"): ("vulture", "Wspd"),
    ("Bow-Vulture", "Dir"): ("vulture", "Dir"),
    ("Bow-Vulture", "Mx_Spd"): ("vulture", "Mx_Spd"),
    ("Simpson-Vermillion", "Temp_Low"): ("simpson_lower", "Temp"),
    ("Simpson-Vermillion", "Rh_Low"): ("simpson_lower", "Rh"),
    ("Simpson-Vermillion", "HS"): ("simpson_lower", "HS"),
    ("Simpson-Vermillion", "Temp_High"): ("simpson_upper", "Temp"),
    ("Simpson-Vermillion", "Rh_High"): ("simpson_upper", "Rh"),
    ("Simpson-Vermillion", "Wspd"): ("simpson_upper", "Wspd"),
    ("Simpson-Vermillion", "Dir"): ("simpson_upper", "Dir"),
    ("Simpson-Vermillion", "Mx_Spd"): ("simpson_upper", "Mx_Spd"),
    ("Simpson-Vermillion", "H2O_Total"): ("vermilion", "PC"),
    ("Bosworth", "Temp_Low"): ("bosworth_lower", "Temp"),
    ("Bosworth", "HS"): ("bosworth_lower", "HS"),
    ("Bosworth", "Temp_High"): ("bosworth_upper", "Temp"),
    ("Bosworth", "Rh_High"): ("bosworth_upper", "Rh"),
    ("Bosworth", "Wspd"): ("bosworth_upper", "Wspd"),
    ("Stanley-Whymper", "Temp_Low"): ("stanley_lower", "Temp"),
    ("Stanley-Whymper", "HS"): ("stanley_lower", "HS"),
    ("Stanley-Whymper", "Temp_High"): ("whymper", "Temp"),
    ("Stanley-Whymper", "Rh_High"): ("whymper", "Rh"),
    ("Stanley-Whymper", "Wspd"): ("whymper", "Wspd"),
}


def extract(pbix: Path, archive: Path = ARCHIVE) -> dict:
    """One-off: the dashboard's record table to a credential-free CSV (needs the optional ``pbixray``)."""
    try:
        from pbixray import PBIXRay
    except ImportError as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("extracting a .pbix needs `pip install pbixray`; the archived CSV is enough to convert")\
            from exc
    sha = hashlib.sha256(Path(pbix).read_bytes()).hexdigest()
    d = PBIXRay(str(pbix)).get_table("Data Historic")
    for c in ("DateTime", "LocalTime", "Time", "MonthDay"):
        if c in d:
            d[c] = pd.to_datetime(d[c]).dt.round("s").dt.strftime("%Y-%m-%d %H:%M:%S")
    d = d.sort_values(["Station", "DateTime"])
    buf = io.StringIO()
    d.to_csv(buf, index=False)
    archive.mkdir(parents=True, exist_ok=True)
    out = archive / "data_historic.csv.gz"
    out.write_bytes(gzip.compress(buf.getvalue().encode(), mtime=0))
    rec = {"path": str(out), "source_file": Path(pbix).name, "source_sha256": sha, "table": "Data Historic",
           "rows": len(d), "extracted_utc": datetime.now(UTC).isoformat(timespec="seconds"),
           "note": "StationsTable (station credentials) deliberately not extracted"}
    with open(archive / "manifest.jsonl", "a") as fh:
        fh.write(json.dumps(rec) + "\n")
    return rec


def convert(archive: Path = ARCHIVE, out_dir: Path = Path("data/interim/fts_dashboard")) -> dict[str, dict]:
    """Per-station CSVs with FTS360 column names and a UTC ``Date`` column (QC happens in fts360.parse_frame)."""
    d = pd.read_csv(Path(archive) / "data_historic.csv.gz", low_memory=False)
    d["Date"] = (pd.to_datetime(d["DateTime"]) + pd.Timedelta(hours=UTC_OFFSET_H)).dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    frames: dict[str, pd.DataFrame] = {}
    for (stn, col), (key, name) in MAPPING.items():
        if col not in d:
            continue
        sub = d.loc[d["Station"] == stn, ["Date", col]].dropna().drop_duplicates("Date").set_index("Date")[col]
        if sub.empty:
            continue
        frames.setdefault(key, pd.DataFrame())
        frames[key] = frames[key].join(sub.rename(name), how="outer") if not frames[key].empty \
            else sub.rename(name).to_frame()
    out_dir.mkdir(parents=True, exist_ok=True)
    summary = {}
    for key, f in frames.items():
        f = f.sort_index().reset_index()
        f.to_csv(out_dir / f"{key}.csv", index=False)
        summary[key] = {"rows": len(f), "start": f["Date"].iloc[0], "end": f["Date"].iloc[-1],
                        "columns": [c for c in f.columns if c != "Date"]}
    (out_dir / "summary.json").write_text(json.dumps(summary, indent=1))
    return summary
