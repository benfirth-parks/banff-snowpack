"""Case builder (build guide "Benchmark-case design"; ADR-059).

One case per usable pit and case type (owner, 2026-10-05: "the historical weather forecasts and weather actuals
before every observed pit for all seasons"):

- ``forecast_h72`` (the training case): as_of = the availability time of the archived GFS run whose leads reach the
  pit (the earliest such run by default, ADR-060; ``forecast_h72.as_of_rule: fixed_horizon`` restores milestone 2's
  as_of = pit - 72 h with the latest run available then). Visible: measured weather of the season up to as_of
  (station, else ERA5 backfill once ERA5 is available), earlier pits available by then, and that run to the pit.
  Where no archived run reaches the pit, as_of = pit - 72 h and measured weather from as_of to the pit stands in for
  the forecast, labelled ``measured_standin`` (``forecast_source`` in the manifest).
- ``next_pit``: as_of = availability time of the previous permitted pit with layers at the same plot in the same
  season (the anchor); the measured stand-in runs from as_of to the pit.

Targets: every pit at the site except duplicates, pits on the owner's review list when the ADR-050 switch is on,
pits with neither layers nor snow depth, and pits in seasons the split mode does not use. A pit without layers but
with HS is a depth-only target. Every exclusion is reported with its reason. The visible package is anonymous
(``anonymize``). Each case is written to a temporary directory, checked (``leakage.check_case``) and only then put
in place under ``benchmark/<case set>/<split>/<case_id>``: a failed check fails the build.

Each manifest records where the case's measured weather came from (``weather_source``: station, mixed or era5_only,
with the station share of temperature and precipitation hours; ADR-076): the seasons before the plot stations
(``splits.reanalysis_seasons``) run on ERA5 alone, under the same availability and leakage rules.
"""

from __future__ import annotations

import json
import shutil
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from pydantic import ValidationError

from snowagent.lab.benchmark import package as pkg
from snowagent.lab.benchmark.anonymize import pit_tables, weather_table
from snowagent.lab.benchmark.availability import recompute_quality, rules_text, stamp
from snowagent.lab.benchmark.gfs import GfsArchive, candidate_runs, forecast_frame, reaching_run, run_info
from snowagent.lab.benchmark.leakage import DATE_LIKE, LeakageError, LeakageReport, check_case
from snowagent.lab.schemas.benchmark import (
    CaseManifest,
    CaseType,
    ForecastRun,
    ForecastSource,
    Split,
    TargetScope,
    VisibleForecastRun,
    WeatherSource,
)
from snowagent.lab.schemas.common import AvailabilityAssumption, QualityFlag
from snowagent.lab.schemas.run import InputFile, RunKind, RunManifest
from snowagent.lab.schemas.weather import WEATHER_VARIABLES
from snowagent.lab.services.data import profile_from_rows
from snowagent.lab.settings import LabConfig, season_bounds
from snowagent.lab.storage.paths import LabPaths
from snowagent.lab.storage.provenance import data_hash, git_commit, input_files, new_run_id, software_version
from snowagent.lab.storage.registry import RunRegistry
from snowagent.lab.storage.tables import read_table, write_table

BUILDER_VERSION = "4"  # 3: as_of from the run whose leads reach the pit (ADR-060); 4: weather_source (ADR-076)
CASE_SUFFIX = {CaseType.forecast_h72: "H72", CaseType.next_pit: "NP"}
STANDIN_SOURCE = "measured_standin"
WEATHER_SOURCE_VARIABLES = ("air_temperature_k", "precipitation_mm")  # decide weather_source (ADR-076)
STATION_SHARE_MIN = 0.9  # station: at least this share of the hours of each from a plot station
_EMPTY = pd.DataFrame(columns=["site_code", "observed_at", *[c for v in WEATHER_VARIABLES
                                                              for c in (v, f"{v}_source", f"{v}_qc")],
                               "kind", "source_id", "issued_at", "source_recorded_at", "availability_assumption",
                               "quality_flag", "provenance_id"])


class CaseBuildError(RuntimeError):
    """The build cannot run (no processed tables, bad filter)."""


def new_case_key() -> str:
    """Random 16-hex case key that never looks like a date.

    The leakage check rejects any date-like string in the visible package, and a random hex key matches that
    pattern about once in 7,000 draws, which would fail a clean build. Re-draw until it does not match.
    """
    while True:
        key = uuid.uuid4().hex[:16]
        if not DATE_LIKE.search(key):
            return key

@dataclass
class Inputs:
    """The processed tables (read once per build) and the GFS archive."""

    profiles: pd.DataFrame
    layers: pd.DataFrame
    observations: pd.DataFrame
    weather: dict[str, pd.DataFrame]  # site code -> the site's hourly rows, sorted
    gfs: GfsArchive
    source_root: Path
    exclude_flagged: bool
    table_files: list[InputFile] = field(default_factory=list)

    @classmethod
    def load(cls, paths: LabPaths, config: LabConfig, source_root: Path, exclude_flagged: bool | None = None,
             gfs_dir: Path | None = None) -> Inputs:
        if not paths.profiles.exists():
            raise CaseBuildError(f"no processed profiles under {paths.root}; run `snowagent lab import` first")
        if exclude_flagged is None:
            from snowagent.obs.observed import exclude_flagged_pits

            exclude_flagged = exclude_flagged_pits(Path(config.observations_config))
        prof = read_table(paths.profiles)
        prof["review_reasons"] = prof["review_reasons_json"].map(json.loads)
        files = [p for p in (paths.profiles, paths.layers, paths.observations, paths.weather) if p.exists()]
        gdir = Path(gfs_dir) if gfs_dir else Path(source_root) / config.benchmark.forecast_h72.gfs_dir
        w = read_table(paths.weather) if paths.weather.exists() else pd.DataFrame(columns=["site_code", "observed_at"])
        weather = {str(k): g.sort_values("observed_at").reset_index(drop=True) for k, g in w.groupby("site_code")}
        return cls(profiles=prof, layers=read_table(paths.layers), observations=read_table(paths.observations),
                   weather=weather, gfs=GfsArchive(gdir), source_root=Path(source_root),
                   exclude_flagged=bool(exclude_flagged), table_files=input_files(files, paths.root))


@dataclass
class Candidate:
    case_type: CaseType
    site_code: str
    target: dict  # processed profile row
    season: str
    split: Split | None = None
    as_of: pd.Timestamp | None = None
    anchor: dict | None = None
    runs: list[pd.Timestamp] = field(default_factory=list)
    forecast_source: ForecastSource | None = None
    scope: TargetScope = TargetScope.full_profile
    reason: str | None = None  # exclusion reason (None: a case is built)
    detail: str = ""


# --------------------------------------------------------------------------------------------- selection


def copies_of(profiles: pd.DataFrame, pid: str) -> list[str]:
    """Every record linked to ``pid`` by ``duplicate_of`` (either direction, transitively), ``pid`` excluded."""
    edges: dict[str, set[str]] = {}
    for a, b in profiles.dropna(subset=["duplicate_of"])[["profile_id", "duplicate_of"]].itertuples(index=False):
        edges.setdefault(a, set()).add(b)
        edges.setdefault(b, set()).add(a)
    seen, todo = {pid}, [pid]
    while todo:
        for n in edges.get(todo.pop(), set()) - seen:
            seen.add(n)
            todo.append(n)
    return sorted(seen - {pid})


def permitted_pool(inputs: Inputs, site: str) -> pd.DataFrame:
    """Pits at the site that may be shown to agents at all: not duplicates, not flagged (switch on)."""
    p = inputs.profiles[inputs.profiles["site_code"] == site]
    keep = p["duplicate_of"].isna()
    if inputs.exclude_flagged:
        keep &= p["review_reasons"].map(len) == 0
    return p[keep]


@lru_cache(maxsize=8)
def _plots(plot_forcing_config: str) -> dict:
    return yaml.safe_load(Path(plot_forcing_config).read_text())["plots"]


def gfs_point(config: LabConfig, site: str) -> str | None:
    """The plot's archived GFS point (config/plot_forcing.yaml ``gfs_point``)."""
    return (_plots(config.plot_forcing_config).get(config.sites[site].plot_id) or {}).get("gfs_point")


def _hours(start: pd.Timestamp, end: pd.Timestamp) -> int:
    return max(int((end.floor("h") - start.floor("h")) / pd.Timedelta(hours=1)), 0)


def standin_coverage(weather: pd.DataFrame | None, as_of: pd.Timestamp, valid: pd.Timestamp) -> float:
    """Share of the hours from as_of to the valid time with both a measured temperature and precipitation
    (``weather``: the site's rows)."""
    n = _hours(as_of, valid)
    if n == 0 or weather is None or weather.empty:
        return 0.0
    w = weather[(weather["observed_at"] > as_of) & (weather["observed_at"] <= valid)]
    return min(float((w["air_temperature_k"].notna() & w["precipitation_mm"].notna()).sum()) / n, 1.0)


def candidates(inputs: Inputs, config: LabConfig, case_types: list[CaseType], sites: list[str],
               holdout: str | None = None) -> list[Candidate]:
    """One candidate per (case type, pit at the site), each with its as_of and forecast source, or its exclusion
    reason."""
    a = config.benchmark.availability
    fc = config.benchmark.forecast_h72
    runs = inputs.gfs.runs() if CaseType.forecast_h72 in case_types else pd.DatetimeIndex([], tz="UTC")
    out: list[Candidate] = []
    for site in sites:
        prof = inputs.profiles[inputs.profiles["site_code"] == site].sort_values("observed_at")
        pool = permitted_pool(inputs, site)
        pool_avail = pd.to_datetime(pool["observed_at"], utc=True) + pd.Timedelta(hours=a.profile_delay_h)
        point = gfs_point(config, site)
        for row in prof.to_dict("records"):
            T = pd.Timestamp(row["observed_at"])
            for ct in case_types:
                c = Candidate(ct, site, row, season=row["season"], split=config.splits.assign(row["season"], holdout))
                if row.get("duplicate_of"):
                    c.reason, c.detail = "duplicate", f"duplicate_of {row['duplicate_of']}"
                elif inputs.exclude_flagged and row["review_reasons"]:
                    c.reason, c.detail = "flagged_review_list", ", ".join(row["review_reasons"])
                elif row["n_layers"] == 0 and pd.isna(row["snow_depth_m"]):
                    c.reason = "no_layers_no_snow_depth"
                elif c.split is None:
                    c.reason, c.detail = "season_not_in_split_mode", f"{row['season']} ({config.splits.mode.value})"
                if c.reason is None and row["n_layers"] == 0:
                    c.scope = TargetScope.depth_only
                if c.reason is None and ct == CaseType.forecast_h72:
                    c.as_of = T - pd.Timedelta(hours=fc.horizon_h)
                    if fc.as_of_rule == "fixed_horizon":
                        c.runs = candidate_runs(runs, c.as_of, a.gfs_latency_h, fc.max_run_age_h) if point else []
                    else:  # ADR-060: the run whose leads reach the pit; as_of = its availability time
                        hit = reaching_run(inputs.gfs, runs, T, point, a.gfs_latency_h, fc.search_window_h,
                                           fc.run_choice) if point else None
                        if hit is not None:
                            c.runs = [hit[0]]
                            c.as_of = hit[0] + pd.Timedelta(hours=a.gfs_latency_h)
                    c.forecast_source = ForecastSource.archived_gfs if c.runs else ForecastSource.measured_standin
                elif c.reason is None and ct == CaseType.next_pit:
                    s0, _s1 = season_bounds(row["season"], config.season_start)
                    ok = ((pool["season"] == row["season"]) & (pool["n_layers"] > 0) & (pool_avail < T)
                          & (pd.to_datetime(pool["observed_at"], utc=True) >= s0))
                    if not ok.any():
                        c.reason = "no_previous_pit_in_season"
                        c.detail = "no permitted pit with layers available before this pit in its season"
                    else:
                        anchor = pool[ok].assign(_av=pool_avail[ok]).sort_values(["_av", "observed_at"]).iloc[-1]
                        c.anchor = anchor.drop(labels="_av").to_dict()
                        c.as_of = pd.Timestamp(anchor["_av"])
                        c.forecast_source = ForecastSource.measured_standin
                out.append(c)
    min_cov = config.benchmark.standin.min_coverage
    for c in out:  # the stand-in needs measured weather; checked here so the exclusion is reported with the others
        if c.reason is None and c.forecast_source == ForecastSource.measured_standin:
            cov = standin_coverage(inputs.weather.get(c.site_code), c.as_of, pd.Timestamp(c.target["observed_at"]))
            if cov < min_cov:
                c.reason = "standin_weather_coverage_below_min"
                c.detail = f"no archived forecast; measured coverage {cov:.2f} < {min_cov:g}"
    return out


def weather_provenance(weather: pd.DataFrame | None, start: pd.Timestamp, end: pd.Timestamp
                       ) -> tuple[WeatherSource, dict[str, float]]:
    """Weather source of a case from the site's measured hours in [start, end] (season start to the pit, or to as-of
    when an archived forecast follows): per variable (temperature, precipitation) the share of the hours with a value
    whose value came from a plot station rather than ERA5, and ``station`` (both shares >= ``STATION_SHARE_MIN``),
    ``era5_only`` (no station value of either) or ``mixed`` (ADR-076)."""
    shares = {}
    for v in WEATHER_SOURCE_VARIABLES:
        if weather is None or weather.empty:
            shares[v] = 0.0
            continue
        w = weather[(weather["observed_at"] >= start) & (weather["observed_at"] <= end)]
        valued = w[v].notna()
        src = w[f"{v}_source"]
        station = valued & src.notna() & ~src.astype(str).str.startswith("era5")
        shares[v] = round(float(station.sum()) / int(valued.sum()), 4) if valued.any() else 0.0
    if all(x >= STATION_SHARE_MIN for x in shares.values()):
        return WeatherSource.station, shares
    if not any(x > 0 for x in shares.values()):
        return WeatherSource.era5_only, shares
    return WeatherSource.mixed, shares


# --------------------------------------------------------------------------------------------- visible tables


def visible_weather(weather: pd.DataFrame, start: pd.Timestamp, as_of: pd.Timestamp, latency_h: float,
                    era5_latency_h: float) -> tuple[pd.DataFrame, int]:
    """The site's measured hours from ``start`` available at as_of (observed + latency), with ERA5-filled values
    still within their latency withheld (null, missing); returns the rows and the number of values withheld."""
    w = weather[(weather["observed_at"] >= start) & (weather["observed_at"] <= as_of)]
    w = stamp(w, "observed_at", latency_h)
    w = w[w["source_recorded_at"] <= as_of].reset_index(drop=True)
    recent = w["observed_at"] + pd.Timedelta(hours=era5_latency_h) > as_of
    withheld = 0
    for v in WEATHER_VARIABLES:
        m = recent & w[f"{v}_source"].astype(str).str.startswith("era5")
        if m.any():
            withheld += int(m.sum())
            w.loc[m, v] = np.nan
            w.loc[m, f"{v}_source"] = None
            w.loc[m, f"{v}_qc"] = QualityFlag.missing.value
    if withheld:
        w["quality_flag"] = recompute_quality(w)
    return w, withheld


def standin_weather(weather: pd.DataFrame, as_of: pd.Timestamp, valid: pd.Timestamp, latency_h: float,
                    withheld: list[str]) -> pd.DataFrame:
    """The site's measured hours not visible at as_of, up to the valid time, as a forecast issued at as_of by
    convention, with the snowpack variables withheld."""
    w = weather[(weather["observed_at"] <= valid)
                & (weather["observed_at"] + pd.Timedelta(hours=latency_h) > as_of)].copy()
    w["kind"] = "perfect_forecast"
    w["source_id"] = STANDIN_SOURCE
    w["issued_at"] = as_of
    w["source_recorded_at"] = as_of
    w["availability_assumption"] = AvailabilityAssumption.perfect_forecast.value
    for var in withheld:
        w[var] = np.nan
        w[f"{var}_source"] = None
        w[f"{var}_qc"] = QualityFlag.missing.value
    w["quality_flag"] = recompute_quality(w)
    return w.reset_index(drop=True)


@dataclass
class VisiblePits:
    profiles: pd.DataFrame  # stamped canonical rows (before anonymising)
    layers: pd.DataFrame
    observations: pd.DataFrame
    excluded: dict[str, int]


def visible_pits(inputs: Inputs, site: str, as_of: pd.Timestamp, target_ids: set[str], delay_h: float,
                 holdout_season: str | None = None) -> VisiblePits:
    """Permitted pits of the site (all seasons, minus a held-out season) available at as_of, their layers and tests,
    and the count of the site's records not shown, by reason."""
    site_prof = inputs.profiles[inputs.profiles["site_code"] == site]
    t_obs = pd.to_datetime(site_prof["observed_at"], utc=True)
    excluded: dict[str, int] = {}
    is_target = site_prof["profile_id"].isin(target_ids)
    dup = site_prof["duplicate_of"].notna() & ~is_target
    flagged = ((site_prof["review_reasons"].map(len) > 0) & ~dup & ~is_target if inputs.exclude_flagged
               else pd.Series(False, index=site_prof.index))
    rest = ~is_target & ~dup & ~flagged
    after = rest & (t_obs > as_of)
    not_yet = rest & (t_obs <= as_of) & (t_obs + pd.Timedelta(hours=delay_h) > as_of)
    held = rest & ~after & ~not_yet & (site_prof["season"] == holdout_season) if holdout_season else (
        pd.Series(False, index=site_prof.index))
    for reason, mask in (("target_or_copy", is_target), ("duplicate", dup), ("flagged_review_list", flagged),
                         ("observed_after_as_of", after), ("not_yet_available_at_as_of", not_yet),
                         ("holdout_season", held)):
        if int(mask.sum()):
            excluded[f"profiles:{reason}"] = int(mask.sum())
    keep = rest & ~after & ~not_yet & ~held
    prof = stamp(site_prof[keep].drop(columns=["review_reasons"]), "observed_at", delay_h).reset_index(drop=True)
    ids = set(prof["profile_id"])
    layers = inputs.layers[inputs.layers["profile_id"].isin(ids)].reset_index(drop=True)
    o = inputs.observations
    site_obs = o[o["site_code"] == site] if len(o) else o
    obs = site_obs[site_obs["profile_id"].isin(ids)] if len(site_obs) else site_obs
    if len(site_obs) > len(obs):
        excluded["tests:pit_not_visible"] = len(site_obs) - len(obs)
    obs = stamp(obs, "observed_at", delay_h).reset_index(drop=True) if len(obs) else obs.reset_index(drop=True)
    return VisiblePits(prof, layers, obs, excluded)


# --------------------------------------------------------------------------------------------- one case


def case_id_for(c: Candidate) -> str:
    """The dated, human-readable id (manifest and directory only; never in the visible files)."""
    return f"{c.site_code}_{pd.Timestamp(c.target['observed_at']):%Y%m%dT%H%M}Z_{CASE_SUFFIX[c.case_type]}"


def _write_json(path: Path, obj) -> None:
    path.write_text(json.dumps(obj, indent=1, default=str))


def _manifest(case_id: str, fields: dict) -> CaseManifest:
    """The manifest, or a ``LeakageError`` when its own checks refuse it (e.g. the target among the visible pits)."""
    try:
        return CaseManifest(**fields)
    except ValidationError as exc:
        report = LeakageReport(case_id=case_id)
        report.add("manifest_valid", [str(exc).splitlines()[1 if len(str(exc).splitlines()) > 1 else 0][:300]])
        raise LeakageError(report) from exc


def _doy(t: pd.Timestamp) -> float:
    return round(t.dayofyear + (t.hour * 60 + t.minute) / 1440.0, 6)


def _empty_weather(weather: pd.DataFrame) -> pd.DataFrame:
    return stamp(weather.iloc[0:0], "observed_at", 0)


def build_case(c: Candidate, inputs: Inputs, config: LabConfig, paths: LabPaths, case_id: str, case_set: str,
               holdout: str | None, run_id: str, created: datetime) -> dict:
    """Write one case package (checked before it is put in place); returns its summary row."""
    a = config.benchmark.availability
    site = config.sites[c.site_code]
    T = pd.Timestamp(c.target["observed_at"])
    as_of = c.as_of
    horizon = float((T - as_of) / pd.Timedelta(hours=1))  # exact: forecast hours are checked against it
    s0, _s1 = season_bounds(c.season, config.season_start)
    target_id = c.target["profile_id"]
    copies = copies_of(inputs.profiles, target_id)
    withheld_vars = list(config.benchmark.standin.withheld)
    warnings: list[str] = []
    if config.splits.warn_provisional():
        warnings.append("season split is provisional (owner to confirm)")
    if a.profile_delay_provisional:
        warnings.append(f"pit availability assumed {a.profile_delay_h:g} h after observation (provisional, owner to "
                        "confirm); no publication time is recorded")
    warnings.append("station, ERA5 and forecast availability times are assumed delays, not recorded publication times")

    loso_holdout = holdout if c.split == Split.training else None
    vp = visible_pits(inputs, c.site_code, as_of, {target_id, *copies}, a.profile_delay_h, loso_holdout)
    site_w = inputs.weather.get(c.site_code, _EMPTY)
    wo, era5_withheld = visible_weather(site_w, s0, as_of, a.weather_latency_h, a.era5_latency_h)
    excluded = dict(vp.excluded)
    if era5_withheld:
        excluded["weather_observed:era5_values_within_latency"] = era5_withheld
    fc = _empty_weather(site_w)
    runs: list[ForecastRun] = []
    standin_meta = None
    gfs_files: list[Path] = []
    if c.forecast_source == ForecastSource.archived_gfs:
        point = gfs_point(config, c.site_code)
        for issued in c.runs:  # latest available first; the first that has the plot's point
            df = inputs.gfs.read(issued)
            if point in set(df["point"]):
                fc, elev = forecast_frame(df, point, c.site_code, issued, a.gfs_latency_h, inputs.gfs.path(issued).name)
                fc = fc[fc["observed_at"] <= T]
                max_lead = float(df["lead_h"].max())
                runs = [run_info(inputs.gfs, issued, point, a.gfs_latency_h, elev, max_lead, inputs.source_root)]
                gfs_files.append(inputs.gfs.path(issued))
                end = issued + pd.Timedelta(hours=max_lead)
                if end < T:
                    warnings.append(f"forecast run ends {(T - end) / pd.Timedelta(hours=1):.1f} h before the valid "
                                    f"time (archived runs reach {max_lead:g} h; as_of rule fixed_horizon)")
                break
        if not runs:  # no candidate run carries the plot's point: fall back to the labelled stand-in
            c.forecast_source = ForecastSource.measured_standin
    if c.forecast_source == ForecastSource.measured_standin:
        fc = standin_weather(site_w, as_of, T, a.weather_latency_h, withheld_vars)
        standin_meta = {"source_id": STANDIN_SOURCE, "issued_at": as_of.isoformat(), "hours": _hours(as_of, T),
                        "rows": len(fc), "coverage": round(standin_coverage(site_w, as_of, T), 3),
                        "withheld_variables": withheld_vars,
                        "note": "measured weather after as_of given as a forecast issued at as_of by convention; "
                                "snowpack variables withheld"}
        if c.case_type == CaseType.forecast_h72:
            warnings.append("no archived forecast for this case: measured weather stands in (measured_standin)")
        if standin_meta["coverage"] < 0.9:
            warnings.append(f"measured stand-in covers {standin_meta['coverage']:.0%} of the hours")
    if c.scope == TargetScope.depth_only:
        warnings.append("target pit has no placed layers: scored for snow depth only")
    wsource, wshare = weather_provenance(site_w, s0, as_of if c.forecast_source == ForecastSource.archived_gfs else T)
    pits, layers, tests, pit_keys = pit_tables(vp.profiles, vp.layers, vp.observations, as_of, c.season)
    case_key = new_case_key()

    tmp = paths.benchmark / f".tmp-{case_id}-{uuid.uuid4().hex[:8]}"
    vis, hid = tmp / pkg.VISIBLE, tmp / pkg.HIDDEN
    vis.mkdir(parents=True)
    hid.mkdir(parents=True)
    try:
        head = {"case_key": case_key, "case_type": c.case_type.value, "site_code": c.site_code,
                "as_of_day_of_year": _doy(as_of), "horizon_hours": horizon,
                "forecast_source": c.forecast_source.value, "availability_warnings": warnings}
        _write_json(vis / pkg.CASE, head)
        (vis / pkg.SITE).write_text(site.model_dump_json(indent=1))
        (vis / pkg.TERRAIN).write_text(site.reference_scenario.model_dump_json(indent=1))
        write_table(weather_table(wo, as_of), vis / pkg.WEATHER_OBSERVED)
        write_table(weather_table(fc, as_of), vis / pkg.WEATHER_FORECASTS)
        _write_json(vis / pkg.FORECAST_RUNS, [VisibleForecastRun(
            source_id=r.source_id, issued_rel_h=round((pd.Timestamp(r.issued_at) - as_of) / pd.Timedelta(hours=1), 3),
            available_rel_h=round((pd.Timestamp(r.available_at) - as_of) / pd.Timedelta(hours=1), 3),
            surface_elevation_m=r.surface_elevation_m, max_lead_h=r.max_lead_h).model_dump() for r in runs])
        write_table(pits, vis / pkg.PITS)
        write_table(layers, vis / pkg.LAYERS)
        write_table(tests, vis / pkg.OBSERVATIONS)

        tl = inputs.layers[inputs.layers["profile_id"] == target_id]
        truth = profile_from_rows({k: v for k, v in c.target.items() if k != "review_reasons"}, tl.to_dict("records"))
        (hid / pkg.TRUTH_PROFILE).write_text(truth.model_dump_json(indent=1))
        write_table(tl.reset_index(drop=True), hid / pkg.TRUTH_LAYERS)
        o = inputs.observations
        to = o[o["profile_id"] == target_id] if len(o) else o
        write_table(to.reset_index(drop=True), hid / pkg.TRUTH_OBSERVATIONS)
        _write_json(hid / pkg.VERIFICATION, {"case_id": case_id, "season": c.season, "target_profile_id": target_id,
                                              "target_copies": copies, "target_scope": c.scope.value,
                                              "valid_time": T.isoformat(),
                                              "observation_ids": list(to["observation_id"]) if len(to) else []})

        gfs_inputs = input_files(gfs_files, inputs.source_root) if gfs_files else []
        manifest = _manifest(case_id, dict(
            case_id=case_id, case_type=c.case_type, site_code=c.site_code, season=c.season, split=c.split,
            as_of_time=as_of.to_pydatetime(), valid_time=T.to_pydatetime(), horizon_hours=horizon,
            scenario=site.reference_scenario, target_profile_id=target_id,
            visible_hashes=pkg.hash_dir(vis, pkg.VISIBLE_FILES), hidden_hashes=pkg.hash_dir(hid, pkg.HIDDEN_FILES),
            availability_assumption=AvailabilityAssumption.assumed_delay, warnings=warnings, created_at=created,
            builder_version=BUILDER_VERSION, case_key=case_key, case_set=case_set, split_mode=config.splits.mode,
            holdout_season=holdout, target_scope=c.scope, target_copies=copies,
            anchor_profile_id=c.anchor["profile_id"] if c.anchor else None, forecast_source=c.forecast_source,
            forecast_runs=runs, forecast_standin=standin_meta, weather_source=wsource, weather_station_share=wshare,
            availability_rules=rules_text(a),
            availability_provisional=a.profile_delay_provisional, split_provisional=config.splits.warn_provisional(),
            pit_keys=pit_keys, visible_profile_ids=sorted(pit_keys.values()),
            visible_counts={"weather_observed": len(wo), "weather_forecasts": len(fc), "permitted_pits": len(pits),
                            "permitted_layers": len(layers), "permitted_observations": len(tests)},
            excluded_counts=excluded, build_run_id=run_id, config_hash=config.config_hash(),
            data_hash=data_hash(inputs.table_files + gfs_inputs)))
        (tmp / pkg.MANIFEST).write_text(manifest.model_dump_json(indent=1))
        report = check_case(tmp, a.era5_latency_h, withheld_vars, expected_name=case_id)
        if report.failed:
            raise LeakageError(report)
        _write_json(tmp / pkg.CHECKS, report.to_dict())
        final = paths.benchmark / case_set / c.split.value / case_id
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            shutil.rmtree(final)
        tmp.rename(final)
    finally:
        if tmp.exists():
            shutil.rmtree(tmp)
    return {"case_id": case_id, "site_code": c.site_code, "split": c.split.value, "case_type": c.case_type.value,
            "season": c.season, "as_of_time": as_of.isoformat(), "valid_time": T.isoformat(),
            "horizon_hours": horizon, "forecast_source": c.forecast_source.value, "weather_source": wsource.value,
            "target_scope": c.scope.value, "leakage_check": report.status, "visible_pits": len(pits), "gfs": [f.name for f in gfs_files],
            "profile_ids": sorted(pit_keys.values()) + [target_id]}


# --------------------------------------------------------------------------------------------- the build


def build_cases(paths: LabPaths, config: LabConfig, source_root: Path = Path("."), sites: list[str] | None = None,
                case_types: list[str] | None = None, profile_ids: list[str] | None = None,
                holdout: str | None = None, splits: list[str] | None = None, exclude_flagged: bool | None = None,
                gfs_dir: Path | None = None, prune: bool = True) -> dict:
    """Build every case matching the filters under the configured split mode; returns the build report (also written
    to ``benchmark/<case set>/build_report.json`` and recorded as a ``case_build`` run). ``holdout``: the held-out
    season of mode loso. ``prune`` removes earlier cases of the case set matching the filters that this build did
    not produce (never with ``profile_ids``)."""
    t0 = time.time()
    created = datetime.now(UTC)
    run_id = new_run_id("case_build", created, str(paths.root))
    hold = config.splits.holdout(holdout)
    case_set = config.splits.case_set(holdout)
    inputs = Inputs.load(paths, config, source_root, exclude_flagged, gfs_dir)
    site_list = sites or [s.value for s in config.sites]
    types = [CaseType(t) for t in (case_types or [t.value for t in CaseType])]
    split_set = {Split(s) for s in splits} if splits else None
    cands = candidates(inputs, config, types, site_list, hold)
    if profile_ids:
        cands = [c for c in cands if c.target["profile_id"] in set(profile_ids)]
        if not cands:
            raise CaseBuildError(f"no pit {profile_ids} at {site_list}")
    built, exclusions, used = [], [], set()
    ids: dict[str, int] = {}
    for c in cands:
        if split_set is not None and c.split is not None and c.split not in split_set:
            continue
        if c.reason is not None:
            exclusions.append({"site_code": c.site_code, "case_type": c.case_type.value,
                               "profile_id": c.target["profile_id"], "observed_at": str(c.target["observed_at"]),
                               "season": c.season, "split": c.split.value if c.split else None,
                               "reason": c.reason, "detail": c.detail})
            continue
        cid = case_id_for(c)
        ids[cid] = ids.get(cid, 0) + 1
        if ids[cid] > 1:
            cid = f"{cid}-{ids[cid]}"
        row = build_case(c, inputs, config, paths, cid, case_set, hold, run_id, created)
        used.update(row.pop("profile_ids"))
        built.append(row)
    removed = 0
    if prune and not profile_ids:
        keep = {r["case_id"] for r in built}
        suffixes = tuple(f"_{CASE_SUFFIX[t]}" for t in types)
        for sp in (split_set or set(Split)):
            d = paths.benchmark / case_set / sp.value
            for p in (d.iterdir() if d.is_dir() else []):
                if ((p / pkg.MANIFEST).exists() and p.name not in keep and p.name.split("-")[0].endswith(suffixes)
                        and p.name.split("_")[0] in site_list):
                    shutil.rmtree(p)
                    removed += 1
    report = _report(run_id, created, config, inputs, built, exclusions, removed, case_set, hold,
                     {"sites": site_list, "case_types": [t.value for t in types], "profile_ids": profile_ids or [],
                      "splits": sorted(s.value for s in split_set) if split_set else []})
    gfs_used = sorted({inputs.gfs.directory / g for r in built for g in r["gfs"]})
    gfs_inputs = input_files(list(gfs_used), inputs.source_root) if gfs_used else []
    manifest = RunManifest(
        run_id=run_id, kind=RunKind.case_build, status="ok", created_at=created, finished_at=datetime.now(UTC),
        config_hash=config.config_hash(), data_hash=data_hash(inputs.table_files + gfs_inputs),
        software_version=software_version(), git_commit=git_commit(Path(__file__).parent),
        splits=config.splits.mode_seasons(holdout), case_ids=[r["case_id"] for r in built],
        profile_ids_used=sorted(used), inputs=inputs.table_files + gfs_inputs,
        outputs=[str(paths.benchmark / case_set / r["split"] / r["case_id"]) for r in built],
        counts={"cases": len(built), "excluded": len(exclusions), "removed_stale": removed,
                "archived_gfs": sum(r["forecast_source"] == "archived_gfs" for r in built),
                "measured_standin": sum(r["forecast_source"] == "measured_standin" for r in built),
                **{f"weather_{w.value}": sum(r["weather_source"] == w.value for r in built) for w in WeatherSource}},
        warnings=report["warnings"], runtime_s=round(time.time() - t0, 1))
    RunRegistry(paths.registry).record(manifest)
    paths.manifests.mkdir(parents=True, exist_ok=True)
    (paths.manifests / f"{run_id}.json").write_text(manifest.model_dump_json(indent=1))
    report["runtime_s"] = manifest.runtime_s
    (paths.benchmark / case_set).mkdir(parents=True, exist_ok=True)
    _write_json(paths.benchmark / case_set / "build_report.json", report)
    (paths.outputs / "reports").mkdir(parents=True, exist_ok=True)
    _write_json(paths.outputs / "reports" / f"{run_id}.json", report)
    return report


def _report(run_id: str, created: datetime, config: LabConfig, inputs: Inputs, built: list[dict],
            exclusions: list[dict], removed: int, case_set: str, holdout: str | None, filters: dict) -> dict:
    a = config.benchmark.availability
    counts: dict[str, dict[str, dict[str, int]]] = {}
    per_season: dict[str, dict[str, dict[str, int]]] = {}
    for r in built:
        d = counts.setdefault(r["site_code"], {}).setdefault(r["split"], {})
        d[r["case_type"]] = d.get(r["case_type"], 0) + 1
        s = per_season.setdefault(r["case_type"], {}).setdefault(f"{r['site_code']} {r['season']}",
                                                                  {"archived_gfs": 0, "measured_standin": 0})
        s[r["forecast_source"]] += 1
        s[r["weather_source"]] = s.get(r["weather_source"], 0) + 1
    by_reason: dict[str, int] = {}
    for e in exclusions:
        k = f"{e['case_type']}:{e['reason']}"
        by_reason[k] = by_reason.get(k, 0) + 1
    warnings = []
    if config.splits.warn_provisional():
        warnings.append("season split is provisional (owner to confirm)")
    if a.profile_delay_provisional:
        warnings.append(f"pit availability = observed + {a.profile_delay_h:g} h (provisional, owner to confirm)")
    return {"run_id": run_id, "created_at": created.isoformat(timespec="seconds"), "case_set": case_set,
            "split_mode": config.splits.mode.value, "holdout_season": holdout, "filters": filters,
            "exclude_flagged_pits": inputs.exclude_flagged, "splits": config.splits.mode_seasons(holdout),
            "split_provisional": config.splits.warn_provisional(), "availability_rules": rules_text(a),
            "cases": len(built), "case_counts": counts, "cases_per_plot_season": per_season,
            "forecast_sources": {t: {src: sum(r["forecast_source"] == src for r in built if r["case_type"] == t)
                                     for src in ("archived_gfs", "measured_standin")}
                                 for t in sorted({r["case_type"] for r in built})},
            "weather_sources": {t: {w.value: sum(r["weather_source"] == w.value for r in built
                                                 if r["case_type"] == t) for w in WeatherSource}
                                for t in sorted({r["case_type"] for r in built})},
            "depth_only_targets": sum(r["target_scope"] == TargetScope.depth_only.value for r in built),
            "leakage": {"pass": sum(r["leakage_check"] == "pass" for r in built),
                        "fail": sum(r["leakage_check"] != "pass" for r in built)},
            "exclusion_counts": dict(sorted(by_reason.items())), "exclusions": exclusions,
            "removed_stale_cases": removed, "case_list": built, "warnings": warnings}
