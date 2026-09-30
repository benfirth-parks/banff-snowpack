"""Local-file weather adapter.

A weather series is a CSV plus a JSON sidecar (``<file>.meta.json``) validated as
:class:`snowagent.contracts.WeatherMeta`. Column units are declared by explicit
suffixes; unknown or duplicated units and naive/irregular timestamps are
rejected. Your own datasets replace the synthetic fixtures by providing the same
two files (or a new adapter that returns :class:`WeatherSeries`) without any
change to the forcing builder or engine.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from snowagent.contracts import WeatherKind, WeatherMeta
from snowagent.errors import DataLeakage, InvalidInput, InvalidTime, InvalidUnits

# canonical variable -> {suffix: converter to SI}
UNIT_SUFFIXES: dict[str, dict[str, tuple[str, callable]]] = {
    "ta": {"c": ("K", lambda v: v + 273.15), "k": ("K", lambda v: v)},
    "rh": {"pct": ("1", lambda v: v / 100.0), "frac": ("1", lambda v: v)},
    "vw": {"ms": ("m s-1", lambda v: v)},
    "dw": {"deg": ("deg", lambda v: v)},
    "iswr": {"wm2": ("W m-2", lambda v: v)},
    "ilwr": {"wm2": ("W m-2", lambda v: v)},
    "psum": {"mm": ("kg m-2", lambda v: v), "kgm2": ("kg m-2", lambda v: v)},
}
# columns recognised but not used as forcing in this milestone
PASSTHROUGH = re.compile(r"^(station_id|qc_flag|vw_max_ms|rswr_wm2|tss_c|tsg_c|hs_cm|hn24_cm|lead_h)$")
TIME_COLUMNS = ("valid_time_utc", "timestamp_utc")
REQUIRED = ("ta", "rh", "vw", "iswr", "psum")
OPTIONAL = ("dw", "ilwr")
PHYSICAL_RANGE = {"ta": (223.15, 308.15), "rh": (0.0, 1.05), "vw": (0.0, 60.0), "dw": (0.0, 360.0),
                  "iswr": (0.0, 1400.0), "ilwr": (100.0, 500.0), "psum": (0.0, 100.0)}
MAX_FILL_HOURS = 3


@dataclass
class WeatherSeries:
    meta: WeatherMeta
    data: pd.DataFrame  # SI, index = UTC end-of-interval valid time; columns ta rh vw dw iswr ilwr psum
    qc: pd.DataFrame  # same shape, 'ok' | 'filled' flags per value
    source_file: str
    sha256: str

    # -------------------------------------------------------------- availability
    def record_available_time(self) -> pd.Series:
        if self.meta.kind == WeatherKind.forecast:
            return pd.Series(pd.Timestamp(self.meta.available_time), index=self.data.index)
        return self.data.index.to_series() + pd.Timedelta(seconds=self.meta.availability_latency_s or 0)

    def available_by(self, issue_time: datetime) -> WeatherSeries:
        """Subset containing only records available at or before ``issue_time``."""
        issue = pd.Timestamp(issue_time)
        if self.meta.kind == WeatherKind.forecast:
            if pd.Timestamp(self.meta.available_time) > issue:
                raise DataLeakage(
                    f"forecast {self.meta.series_id} became available at {self.meta.available_time}, "
                    f"after issue time {issue.isoformat()}",
                    series_id=self.meta.series_id)
            return self
        keep = self.record_available_time() <= issue
        return WeatherSeries(self.meta, self.data[keep], self.qc[keep], self.source_file, self.sha256)

    def window(self, start: datetime, end: datetime) -> pd.DataFrame:
        s, e = pd.Timestamp(start), pd.Timestamp(end)
        return self.data[(self.data.index >= s) & (self.data.index <= e)]


def _parse_times(raw: pd.Series) -> pd.DatetimeIndex:
    bad = [v for v in raw.astype(str) if not re.search(r"(Z|[+-]00:?00)$", v.strip())]
    if bad:
        raise InvalidTime(f"timestamps must be explicit UTC (suffix Z or +00:00); e.g. {bad[0]!r}")
    return pd.DatetimeIndex(pd.to_datetime(raw, utc=True))


def load_weather(csv_path: Path) -> WeatherSeries:
    csv_path = Path(csv_path)
    meta_path = csv_path.with_name(csv_path.name + ".meta.json")
    if not meta_path.exists():
        raise InvalidInput(f"missing metadata sidecar {meta_path.name} (source, elevation, times, units)")
    meta = WeatherMeta.model_validate_json(meta_path.read_text())
    raw = pd.read_csv(csv_path, dtype=str)
    tcols = [c for c in TIME_COLUMNS if c in raw.columns]
    if len(tcols) != 1:
        raise InvalidTime(f"need exactly one time column of {TIME_COLUMNS}, found {tcols}")
    idx = _parse_times(raw[tcols[0]])
    if idx.has_duplicates:
        raise InvalidTime("duplicate timestamps")
    if not idx.is_monotonic_increasing:
        raise InvalidTime("timestamps must be strictly increasing")
    steps = np.unique(np.diff(idx.asi8) // 10**9)
    if len(steps) != 1 or int(steps[0]) != meta.accumulation_interval_s:
        raise InvalidTime(f"time step {steps.tolist()} s does not equal declared accumulation interval "
                          f"{meta.accumulation_interval_s} s (irregular or sub-hourly aggregation not supported yet)")
    if meta.accumulation_interval_s != 3600:
        raise InvalidTime("only hourly series are supported in this milestone")

    out: dict[str, np.ndarray] = {}
    for col in raw.columns:
        if col in tcols or PASSTHROUGH.match(col):
            continue
        var, _, suffix = col.partition("_")
        if var not in UNIT_SUFFIXES or suffix not in UNIT_SUFFIXES[var]:
            raise InvalidUnits(f"column {col!r} has no recognised variable/unit suffix "
                               f"(allowed: {', '.join(v + '_' + s for v in UNIT_SUFFIXES for s in UNIT_SUFFIXES[v])})")
        if var in out:
            raise InvalidUnits(f"variable {var!r} supplied in more than one unit; ambiguous")
        vals = pd.to_numeric(raw[col], errors="coerce").to_numpy(dtype=float)
        out[var] = UNIT_SUFFIXES[var][suffix][1](vals)
    missing = [v for v in REQUIRED if v not in out]
    if missing:
        raise InvalidInput(f"required variables missing: {missing}")
    df = pd.DataFrame(out, index=idx)
    for v in OPTIONAL:
        if v not in df:
            df[v] = np.nan
    qc = pd.DataFrame("ok", index=idx, columns=df.columns)
    for v, (lo, hi) in PHYSICAL_RANGE.items():
        bad = (df[v] < lo) | (df[v] > hi)
        if bad.any():
            raise InvalidInput(f"{v} outside physical range [{lo}, {hi}] (SI) at {int(bad.sum())} records; "
                               "check units or QC the source", first=str(df.index[bad][0]))
    df["rh"] = df["rh"].clip(upper=1.0)
    # gap handling: linear fill <= MAX_FILL_HOURS, flagged; longer gaps rejected (never silent)
    for v in list(REQUIRED) + [o for o in OPTIONAL if df[o].notna().any()]:
        isna = df[v].isna()
        if not isna.any():
            continue
        runs = (isna != isna.shift()).cumsum()[isna]
        longest = runs.value_counts().max()
        if longest > MAX_FILL_HOURS:
            raise InvalidInput(f"{v} has a gap of {longest} h (> {MAX_FILL_HOURS} h); supply data or a QC'd fill")
        qc.loc[isna, v] = "filled"
        df[v] = df[v].interpolate(limit=MAX_FILL_HOURS, limit_direction="both") if v != "psum" else df[v].fillna(0.0)
    sha = hashlib.sha256(csv_path.read_bytes() + meta_path.read_bytes()).hexdigest()
    return WeatherSeries(meta, df, qc, str(csv_path), sha)


def write_weather(csv_path: Path, meta: WeatherMeta, df: pd.DataFrame) -> None:
    """Write a series in the adapter format (SI -> declared suffixes)."""
    out = pd.DataFrame(index=df.index)
    out["valid_time_utc"] = df.index.strftime("%Y-%m-%dT%H:%M:%SZ")
    out["ta_c"] = (df["ta"] - 273.15).round(3)
    out["rh_pct"] = (df["rh"] * 100).round(2)
    out["vw_ms"] = df["vw"].round(3)
    out["dw_deg"] = df["dw"].round(1)
    out["iswr_wm2"] = df["iswr"].round(2)
    out["ilwr_wm2"] = df["ilwr"].round(2)
    out["psum_mm"] = df["psum"].round(4)
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(csv_path, index=False)
    Path(str(csv_path) + ".meta.json").write_text(meta.model_dump_json(indent=1))


def latest_forecast(paths: list[Path], issue_time: datetime) -> WeatherSeries:
    """Pick the most recent forecast run that was available by ``issue_time``."""
    runs = [load_weather(p) for p in paths]
    ok = [r for r in runs if r.meta.kind == WeatherKind.forecast
          and pd.Timestamp(r.meta.available_time) <= pd.Timestamp(issue_time)]
    if not ok:
        raise DataLeakage(f"no forecast run available by {issue_time}")
    return max(ok, key=lambda r: (r.meta.issue_time, r.meta.available_time))


ONE_HOUR = timedelta(hours=1)
