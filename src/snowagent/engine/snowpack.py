"""Isolated adapter around the SNOWPACK executable.

The adapter writes SMET forcing and ``.sno`` state files, renders a verified ini
template, runs the binary in a private run directory and parses its outputs.
It never fabricates output: a non-zero exit, a missing output file or an
unparseable profile raises :class:`EngineRunFailed`.
"""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from snowagent.errors import EngineRunFailed, EngineUnavailable

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TEMPLATE = REPO_ROOT / "config" / "snowpack" / "terrain_column.ini"
# The default prefixes of scripts/build_snowpack.sh: Linux /opt/snowpack, macOS ~/.local/snowpack (no sudo; the
# home directory is resolved when the engine is looked up).
DEFAULT_BIN_CANDIDATES = ("/opt/snowpack/bin/snowpack", "~/.local/snowpack/bin/snowpack")
RECOVERY_COMMAND = "bash scripts/build_snowpack.sh   # or: docker build -f docker/snowpack.Dockerfile -t snowagent/snowpack ."
NODATA = -999.0

SMET_FIELDS = ("TA", "RH", "VW", "DW", "ISWR", "ILWR", "PSUM", "PSUM_PH", "TSG")


# --------------------------------------------------------------------------- discovery


@dataclass(frozen=True)
class EngineInfo:
    binary: str
    version: str
    libsnowpack: str
    meteoio: str

    @property
    def version_string(self) -> str:
        return f"SNOWPACK {self.version} (libsnowpack {self.libsnowpack}, MeteoIO {self.meteoio})"


def find_engine(explicit: str | None = None) -> EngineInfo:
    """Locate the SNOWPACK binary: explicit arg, $SNOWPACK_BIN, known prefix, $PATH."""
    candidates: list[str] = []
    if explicit:
        candidates.append(explicit)
    if os.environ.get("SNOWPACK_BIN"):
        candidates.append(os.environ["SNOWPACK_BIN"])
    candidates.extend(os.path.expanduser(c) for c in DEFAULT_BIN_CANDIDATES)
    which = shutil.which("snowpack")
    if which:
        candidates.append(which)
    for c in candidates:
        if c and Path(c).is_file() and os.access(c, os.X_OK):
            return EngineInfo(binary=c, **_query_version(c))
    raise EngineUnavailable(
        "SNOWPACK binary not found. Engine execution is blocked; no profiles can be produced.",
        searched=candidates,
        recovery_command=RECOVERY_COMMAND,
    )


def _query_version(binary: str) -> dict[str, str]:
    try:
        out = subprocess.run([binary, "-v"], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise EngineUnavailable(f"SNOWPACK binary at {binary} cannot execute: {exc}",
                                recovery_command=RECOVERY_COMMAND) from exc
    text = out.stdout + out.stderr

    def grab(pattern: str) -> str:
        m = re.search(pattern, text)
        return m.group(1) if m else "unknown"

    version = grab(r"Snowpack version (\S+)")
    if version == "unknown":
        raise EngineUnavailable(f"Could not read SNOWPACK version from {binary}: {text[:300]!r}",
                                recovery_command=RECOVERY_COMMAND)
    return {"version": version, "libsnowpack": grab(r"Libsnowpack (\S+)"), "meteoio": grab(r"MeteoIO (\S+)")}


# --------------------------------------------------------------------------- configuration


@dataclass(frozen=True)
class EngineSettings:
    utm_zone: str = "11U"
    ts_days_between: float = 1.0 / 24.0
    prof_days_between: float = 0.25
    met_height_m: float = 2.0
    wind_height_m: float = 10.0
    timeout_s: int = 1800
    template_path: Path = DEFAULT_TEMPLATE
    calculation_step_min: float = 15.0
    # restart-state backups (<id>.sno<YYYYMMDDHHMM>): every SNOW_DAYS_BETWEEN days from start + FIRST_BACKUP days.
    # Defaults = no backups within a season (engine default FIRST_BACKUP 400).
    snow_days_between: float = 3650.0
    first_backup: float = 400.0

    def render(self, station_id: str) -> str:
        text = Path(self.template_path).read_text()
        values = {
            "station_id": station_id,
            "utm_zone": self.utm_zone,
            "ts_days_between": f"{self.ts_days_between:.10f}",
            "prof_days_between": f"{self.prof_days_between:.10f}",
            "met_height_m": f"{self.met_height_m:g}",
            "wind_height_m": f"{self.wind_height_m:g}",
            "calculation_step_min": f"{self.calculation_step_min:g}",
            "psum_period_s": f"{self.calculation_step_min * 60:g}",
            "snow_days_between": f"{self.snow_days_between:g}",
            "first_backup": f"{self.first_backup:g}",
        }
        for key, value in values.items():
            text = text.replace("{" + key + "}", value)
        leftover = re.findall(r"^[^;]*\{(\w+)\}", text, flags=re.M)
        if leftover:
            raise ValueError(f"unfilled engine template placeholders: {leftover}")
        return text

    def config_hash(self) -> str:
        """Hash of the rendered template independent of the per-unit station id."""
        return sha256_text(self.render("STATION"))[:16]


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# --------------------------------------------------------------------------- file writers


def _fmt_time(t: pd.Timestamp | datetime) -> str:
    return pd.Timestamp(t).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%S")


def write_smet_forcing(path: Path, station_id: str, lat: float, lon: float, altitude_m: float,
                       df: pd.DataFrame) -> None:
    """Write hourly forcing. ``df`` is indexed by UTC end-of-interval timestamps and has
    SI columns TA[K] RH[1] VW[m/s] DW[deg] ISWR[W/m2] ILWR[W/m2] PSUM[kg/m2/interval]
    PSUM_PH[0 solid..1 liquid] TSG[K]."""
    missing = [f for f in SMET_FIELDS if f not in df.columns]
    if missing:
        raise ValueError(f"forcing missing fields {missing}")
    if df.index.tz is None:
        raise ValueError("forcing index must be tz-aware UTC")
    lines = [
        "SMET 1.1 ASCII",
        "[HEADER]",
        f"station_id = {station_id}",
        f"station_name = {station_id}",
        f"latitude = {lat:.6f}",
        f"longitude = {lon:.6f}",
        f"altitude = {altitude_m:.1f}",
        f"nodata = {NODATA:g}",
        "tz = 0",
        "fields = timestamp " + " ".join(SMET_FIELDS),
        "[DATA]",
    ]
    vals = df[list(SMET_FIELDS)].to_numpy(dtype=float)
    vals = np.where(np.isfinite(vals), vals, NODATA)
    for t, row in zip(df.index, vals, strict=True):
        lines.append(_fmt_time(t) + " " + " ".join(f"{v:.5f}" for v in row))
    path.write_text("\n".join(lines) + "\n")


def write_snowfree_sno(path: Path, station_id: str, lat: float, lon: float, altitude_m: float,
                       slope_deg: float, azimuth_deg: float, profile_date: datetime) -> None:
    """Explicit snow-free initial state (zero snow layers, no soil layers)."""
    text = f"""SMET 1.1 ASCII
[HEADER]
station_id       = {station_id}
station_name     = {station_id}
latitude         = {lat:.6f}
longitude        = {lon:.6f}
altitude         = {altitude_m:.1f}
nodata           = -999
tz               = 0
ProfileDate      = {_fmt_time(profile_date)}
HS_Last          = 0.0000
SlopeAngle       = {slope_deg:.2f}
SlopeAzi         = {azimuth_deg:.2f}
nSoilLayerData   = 0
nSnowLayerData   = 0
SoilAlbedo       = 0.09
BareSoil_z0      = 0.200
CanopyHeight     = 0.00
CanopyLeafAreaIndex = 0.00
CanopyDirectThroughfall = 1.00
ErosionLevel     = 0
TimeCountDeltaHS = 0.000000
fields           = timestamp Layer_Thick  T  Vol_Frac_I  Vol_Frac_W  Vol_Frac_V  Vol_Frac_S Rho_S Conduc_S HeatCapac_S  rg  rb  dd  sp  mk mass_hoar ne CDot metamo
[DATA]
"""
    path.write_text(text)


def read_sno_header(path: Path) -> dict[str, str]:
    header: dict[str, str] = {}
    for line in path.read_text().splitlines():
        if line.strip() == "[DATA]":
            break
        if "=" in line:
            k, v = line.split("=", 1)
            header[k.strip()] = v.strip()
    return header


# --------------------------------------------------------------------------- execution


@dataclass
class RunOutputs:
    run_dir: Path
    pro: Path
    met: Path
    sno: Path
    log: Path
    returncode: int
    wall_s: float
    extra: dict[str, str] = field(default_factory=dict)


def run_engine(engine: EngineInfo, run_dir: Path, station_id: str, end: datetime,
               settings: EngineSettings, restart: bool = False) -> RunOutputs:
    """Run SNOWPACK in ``run_dir`` (which must contain input/<id>.smet and input/<id>.sno)."""
    inp = run_dir / "input"
    for f in (inp / f"{station_id}.smet", inp / f"{station_id}.sno"):
        if not f.exists():
            raise EngineRunFailed(f"missing engine input {f}")
    (run_dir / "output").mkdir(exist_ok=True)
    ini = run_dir / "io.ini"
    ini.write_text(settings.render(station_id))
    cmd = [engine.binary, "-c", "io.ini", "-e", pd.Timestamp(end).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M")]
    if restart:
        cmd.append("-r")
    log = run_dir / "engine.log"
    t0 = datetime.now(UTC)
    try:
        proc = subprocess.run(cmd, cwd=run_dir, capture_output=True, text=True, timeout=settings.timeout_s)
    except subprocess.TimeoutExpired as exc:
        raise EngineRunFailed(f"SNOWPACK timed out after {settings.timeout_s}s", run_dir=str(run_dir)) from exc
    wall = (datetime.now(UTC) - t0).total_seconds()
    log.write_text(f"$ {' '.join(cmd)}\n--- stdout\n{proc.stdout}\n--- stderr\n{proc.stderr}\n")
    out = run_dir / "output"
    outputs = RunOutputs(run_dir, out / f"{station_id}_run.pro", out / f"{station_id}_run.met",
                         out / f"{station_id}_run.sno", log, proc.returncode, wall)
    # SNOWPACK prints "[E] ..." lines to stdout and backtraces to stderr; collect both.
    errors = [ln for ln in (proc.stdout + "\n" + proc.stderr).splitlines() if ln.lstrip().startswith("[E]")]
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout)[-1500:]
        raise EngineRunFailed(f"SNOWPACK exited with code {proc.returncode}", run_dir=str(run_dir),
                              log_tail=tail, errors=errors[:10])
    # SNOWPACK can log a fatal data error and still exit 0 without simulating;
    # treat any error line as a failed run rather than trusting the exit code.
    if errors:
        raise EngineRunFailed("SNOWPACK reported errors", run_dir=str(run_dir), errors=errors[:10])
    for p in (outputs.pro, outputs.met, outputs.sno):
        if not p.exists() or p.stat().st_size == 0:
            raise EngineRunFailed(f"SNOWPACK produced no {p.name}", run_dir=str(run_dir))
    return outputs


# --------------------------------------------------------------------------- parsers


PRO_CODES = {
    "0501": "height_cm",
    "0502": "density",
    "0503": "temperature_c",
    "0504": "element_id",
    "0505": "deposition",
    "0506": "lwc_pct",
    "0508": "dendricity",
    "0509": "sphericity",
    "0511": "bond_size_mm",
    "0512": "grain_size_mm",
    "0513": "grain_type",
    "0514": "surface_hoar",
    "0515": "ice_pct",
    "0520": "temp_gradient",
    "0533": "sk38",
    "0534": "hardness",
}


@dataclass
class ProProfile:
    time: pd.Timestamp
    data: dict[str, list[str]]


def parse_pro(path: Path) -> tuple[dict[str, str], list[ProProfile]]:
    """Parse a SNOWPACK .pro file into per-time raw arrays (strings, converted later)."""
    station: dict[str, str] = {}
    profiles: list[ProProfile] = []
    section = None
    current: ProProfile | None = None
    with open(path) as fh:
        for raw in fh:
            line = raw.strip()
            if not line:
                continue
            if line.startswith("["):
                section = line
                continue
            if section == "[STATION_PARAMETERS]" and "=" in line:
                k, v = line.split("=", 1)
                station[k.strip()] = v.strip()
            elif section == "[DATA]":
                code, _, rest = line.partition(",")
                if code == "0500":
                    t = pd.Timestamp(datetime.strptime(rest.strip(), "%d.%m.%Y %H:%M:%S")).tz_localize("UTC")
                    current = ProProfile(t, {})
                    profiles.append(current)
                elif current is not None and code in PRO_CODES:
                    parts = rest.split(",")
                    current.data[PRO_CODES[code]] = parts[1:]  # first entry is the count
    if not profiles:
        raise EngineRunFailed(f"no profiles in {path}")
    return station, profiles


def parse_met(path: Path) -> pd.DataFrame:
    """Parse SNOWPACK .met time series into a DataFrame keyed by column names."""
    lines = path.read_text().splitlines()
    try:
        hdr_i = next(i for i, ln in enumerate(lines) if ln.startswith("ID,Date"))
        data_i = next(i for i, ln in enumerate(lines) if ln.strip() == "[DATA]")
    except StopIteration as exc:
        raise EngineRunFailed(f"unrecognised .met format in {path}") from exc
    cols = [c.strip() for c in lines[hdr_i].split(",")]
    rows = [ln.split(",")[: len(cols)] for ln in lines[data_i + 1:] if ln.strip()]
    df = pd.DataFrame(rows, columns=cols[: len(rows[0])] if rows else cols)
    df["Date"] = pd.to_datetime(df["Date"], format="%d.%m.%Y %H:%M:%S").dt.tz_localize("UTC")
    df = df.set_index("Date").drop(columns=["ID"])
    df = df.loc[:, [c for c in df.columns if c not in ("", "-")]]
    df = df.loc[:, ~df.columns.duplicated()]
    return df.apply(pd.to_numeric, errors="coerce").replace(NODATA, np.nan)
