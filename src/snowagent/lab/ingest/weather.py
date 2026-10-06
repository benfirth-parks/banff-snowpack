"""QC'd station records (``ingest.fts360.load_station``: FTS360 API files, logger exports, dashboard history) ->
the lab's canonical hourly weather per site (ADR-056).

Each variable takes its stations in the order the plot's forcing recipe lists them (config/plot_forcing.yaml:
``ta``, ``rh``, ``psum``, ``hs_check``, ``swe_check``; wind from config/lab.yaml). Per hour the first station whose
value is QC "ok" supplies it; with none ok, the first "suspect" value is kept with its flag; a "bad" value is not
used (null, flagged bad, the station named; the raw file keeps it); otherwise null and "missing". Apart from the
optional ERA5 backfill below no source fills a gap, and no value is moved to the plot elevation: values are as
measured at the named station (or the ERA5 cell). Radiation and pressure are not measured at these plots. With an ERA5 backfill (``fill``; config/lab.yaml ``weather``, owner
2026-10-05: "FTS360, else ERA5 backfill") an hour and variable no station supplied (missing, or failed QC) takes the
ERA5 nearest-cell value, flagged ``filled`` and sourced ``era5_cell_<height>m``: named, never silent, and the
station's bad value stays in the raw file. Without a fill those values stay null. The hourly index runs from the
first to the last hour any of the site's stations reported, so gaps are explicit rows; with a fill and ``start``
(the start of the reanalysis seasons, ADR-076) it begins at ``start`` when that is earlier, so the seasons before
the stations get ERA5 hours (filled) and explicit missing rows where the ERA5 cache has no month.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import numpy as np
import pandas as pd

from snowagent.lab.schemas.common import AvailabilityAssumption, QualityFlag
from snowagent.lab.schemas.site import Site
from snowagent.lab.schemas.weather import SEVERITY, WEATHER_VARIABLES, WeatherRecord

SOURCE_ID = "plot_stations"
# canonical variable -> (plot_forcing.yaml recipe key, or "wind" for config/lab.yaml; station column; converter)
RECIPES: dict[str, tuple[str, str, Callable[[pd.Series], pd.Series]]] = {
    "air_temperature_k": ("ta", "ta_k", lambda s: s),
    "relative_humidity_frac": ("rh", "rh_frac", lambda s: s),
    "precipitation_mm": ("psum", "psum_1h_mm", lambda s: s),
    "wind_speed_ms": ("wind", "vw_ms", lambda s: s),
    "wind_direction_deg": ("wind", "dw_deg", lambda s: s),
    "snow_depth_m": ("hs_check", "hs_m", lambda s: s),
    "swe_mm": ("swe_check", "swe_mm", lambda s: s),
}
StationLoader = Callable[[str], pd.DataFrame]


def station_loader(source_root: Path) -> StationLoader:
    """``load_station`` over a checkout's data directories (read only)."""
    from snowagent.ingest.fts360 import load_station

    root = Path(source_root)

    def load(key: str) -> pd.DataFrame:
        return load_station(key, root / "data/raw/fts360", root / "data/interim/byk_export",
                            root / "data/interim/fts_dashboard")

    return load


def recipe_stations(site: Site, plot: dict) -> dict[str, list[str]]:
    """Canonical variable -> station keys in priority order."""
    return {var: list(site.wind_stations if key == "wind" else plot.get(key) or [])
            for var, (key, _c, _f) in RECIPES.items()}


def station_files(source_root: Path, stations: set[str]) -> list[Path]:
    """The raw and interim files ``load_station`` reads for these stations (for hashing and the immutability check)."""
    root = Path(source_root)
    out: list[Path] = []
    for key in sorted(stations):
        out += sorted((root / "data/raw/fts360" / key).glob("*.csv"))
        out += [f for f in (root / "data/interim/byk_export" / f"{key}.csv",
                            root / "data/interim/fts_dashboard" / f"{key}.csv") if f.exists()]
    return out


def site_weather(site: Site, plot: dict, load: StationLoader, provenance_id: str,
                 fill: Callable[[pd.DatetimeIndex], tuple[pd.DataFrame, str]] | None = None,
                 start: pd.Timestamp | None = None) -> tuple[pd.DataFrame, dict]:
    """Canonical hourly table of one site (one row per hour; columns as ``WeatherRecord``, with ``<var>_source`` and
    ``<var>_qc`` flattened) and a summary. ``fill(index)`` returns the backfill series (canonical variable columns)
    and its source label; ``start`` (with a fill only) moves the first hour back to it."""
    recipes = recipe_stations(site, plot)
    data: dict[str, pd.DataFrame] = {}
    for key in sorted({k for ks in recipes.values() for k in ks}):
        d = load(key)
        if not d.empty:
            data[key] = d.set_index("time_utc")
    starts = [d.index.min() for d in data.values()]
    ends = [d.index.max() for d in data.values()]
    if starts and fill is not None and start is not None:
        starts.append(pd.Timestamp(start).tz_convert("UTC").ceil("h"))
    idx = (pd.date_range(min(starts), max(ends), freq="h", name="observed_at") if data
           else pd.DatetimeIndex([], tz="UTC", name="observed_at"))
    out = pd.DataFrame(index=idx)
    summary: dict = {"stations": recipes, "hours": len(idx), "variables": {}}
    filled, label = fill(idx) if fill is not None and len(idx) else (None, None)
    if label:
        summary["backfill"] = label
    for var in WEATHER_VARIABLES:
        value = pd.Series(np.nan, index=idx)
        source = pd.Series(None, index=idx, dtype=object)
        qc = pd.Series(QualityFlag.missing.value, index=idx, dtype=object)
        _key, col, conv = RECIPES.get(var, (None, None, None))
        for flag in ("ok", "suspect", "bad") if var in RECIPES else ():
            for key in recipes[var]:
                d = data.get(key)
                if d is None or col not in d:
                    continue
                v = conv(d[col].reindex(idx).astype(float))
                f = d[f"{col}_qc"].reindex(idx) if f"{col}_qc" in d else pd.Series("ok", index=idx)
                take = (qc == QualityFlag.missing.value) & (f == flag)
                if flag != "bad":  # a bad value is named and flagged, the value itself is not used
                    take &= v.notna()
                    value[take] = v[take]
                source[take] = key
                qc[take] = flag
        if filled is not None and var in filled:
            take = qc.isin([QualityFlag.missing.value, QualityFlag.bad.value]) & filled[var].notna()
            value[take] = filled[var][take]
            source[take] = label
            qc[take] = QualityFlag.filled.value
        out[var], out[f"{var}_source"], out[f"{var}_qc"] = value, source, qc
        if var in RECIPES or filled is not None:
            summary["variables"][var] = {k: int(n) for k, n in qc.value_counts().items()}
    sev = {f.value: i for i, f in enumerate(SEVERITY)}
    qcols = [f"{v}_qc" for v in WEATHER_VARIABLES]
    ranks = out[qcols].apply(lambda c: c.map(sev)).max(axis=1)
    inv = {i: f for f, i in sev.items()}
    out["quality_flag"] = ranks.map(inv).fillna(QualityFlag.missing.value) if len(out) else pd.Series(dtype=object)
    out = out.reset_index()
    out.insert(0, "site_code", site.code.value)
    out.insert(2, "source_id", SOURCE_ID)
    out.insert(3, "kind", "observed")
    out.insert(4, "issued_at", pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns, UTC]"))
    out.insert(5, "source_recorded_at", pd.Series(pd.NaT, index=out.index, dtype="datetime64[ns, UTC]"))
    out.insert(6, "availability_assumption", AvailabilityAssumption.observed_at.value)
    out["provenance_id"] = provenance_id
    if len(out):
        summary["first_hour"], summary["last_hour"] = str(idx.min()), str(idx.max())
    return out, summary


def record_from_row(row: dict) -> WeatherRecord:
    """One canonical table row -> ``WeatherRecord`` (validation of the stored form)."""
    def val(x):
        return None if x is None or (isinstance(x, float) and np.isnan(x)) or x is pd.NaT else x

    sources = {v: row[f"{v}_source"] for v in WEATHER_VARIABLES if val(row.get(f"{v}_source")) is not None}
    qc = {v: row[f"{v}_qc"] for v in WEATHER_VARIABLES if row.get(f"{v}_qc") != QualityFlag.missing.value}
    return WeatherRecord(
        site_code=row["site_code"], observed_at=row["observed_at"], source_id=row["source_id"], kind=row["kind"],
        issued_at=val(row.get("issued_at")), source_recorded_at=val(row.get("source_recorded_at")),
        availability_assumption=row["availability_assumption"], sources=sources, qc=qc,
        quality_flag=row["quality_flag"], provenance_id=row["provenance_id"],
        **{v: val(row.get(v)) for v in WEATHER_VARIABLES})


def validate_frame(df: pd.DataFrame) -> int:
    """Validate every row as a ``WeatherRecord`` (raises on the first invalid one); returns the row count."""
    for row in df.to_dict("records"):
        record_from_row(row)
    return len(df)
