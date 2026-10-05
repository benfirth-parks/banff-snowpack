"""Lab configuration (config/lab.yaml) and the season key.

Sites name their study plot; coordinates, elevation and the season start (15 Sep) come from
config/plot_forcing.yaml, so the lab never holds a second copy of them. Split seasons may appear in one split only.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo

import pandas as pd
import yaml
from pydantic import Field, field_validator, model_validator

from snowagent.lab.schemas.benchmark import Split, SplitMode
from snowagent.lab.schemas.common import LabModel, SiteCode
from snowagent.lab.schemas.genome import GenomeSpec, default_spec
from snowagent.lab.schemas.run import ScoringWeights
from snowagent.lab.schemas.site import ReferenceScenario, Site
from snowagent.lab.schemas.weather import WEATHER_VARIABLES

DEFAULT_CONFIG = Path("config/lab.yaml")
SEASON_KEY = re.compile(r"^(\d{4})-(\d{4})$")


def _season_keys(v: list[str]) -> list[str]:
    for k in v:
        m = SEASON_KEY.match(str(k))
        if not m or int(m[2]) != int(m[1]) + 1:
            raise ValueError(f"season {k!r} is not a season key like 2021-2022")
    if len(set(v)) != len(v):
        raise ValueError(f"season listed twice in one split: {v}")
    return v


class Splits(LabModel):
    """Season assignment (ADR-059). ``mode``: ``all`` (owner's default, 2026-10-05: every season in ``all_seasons``
    is training data, nothing sealed), ``split`` (development / validation / sealed test, overlap blocked) or
    ``loso`` (leave one season out of ``all_seasons``: ``loso_holdout``, or the season named at build time)."""

    mode: SplitMode = SplitMode.all
    all_seasons: list[str] = Field(default_factory=list)  # modes all and loso
    loso_holdout: str | None = None  # mode loso: default held-out season
    provisional: bool = False  # mode split: recommended seasons the owner has not confirmed (shown as a warning)
    development_seasons: list[str] = Field(default_factory=list)
    validation_seasons: list[str] = Field(default_factory=list)
    sealed_test_seasons: list[str] = Field(default_factory=list)

    @field_validator("all_seasons", "development_seasons", "validation_seasons", "sealed_test_seasons")
    @classmethod
    def _keys(cls, v: list[str]) -> list[str]:
        return _season_keys(v)

    @model_validator(mode="after")
    def _no_overlap(self) -> Splits:
        names = ("development_seasons", "validation_seasons", "sealed_test_seasons")
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                both = sorted(set(getattr(self, a)) & set(getattr(self, b)))
                if both:
                    raise ValueError(f"split overlap: {both} in both {a} and {b}; a season may be in one split only")
        if self.loso_holdout is not None and self.loso_holdout not in self.all_seasons:
            raise ValueError(f"loso_holdout {self.loso_holdout} is not in all_seasons")
        return self

    def seasons(self) -> dict[str, list[str]]:
        """Split name -> seasons of mode ``split`` (``development``, ``validation``, ``sealed_test``)."""
        return {name: list(getattr(self, f"{name}_seasons")) for name in ("development", "validation", "sealed_test")}

    def is_empty(self) -> bool:
        if self.mode == SplitMode.split:
            return not any(self.seasons().values())
        return not self.all_seasons

    def split_of(self, season: str) -> str | None:
        """Mode ``split``: development, validation or sealed_test (None: in no split)."""
        for name in ("development", "validation", "sealed_test"):
            if season in getattr(self, f"{name}_seasons"):
                return name
        return None

    def holdout(self, holdout: str | None = None) -> str | None:
        """The held-out season of mode loso (``holdout`` overrides the configured one)."""
        if self.mode != SplitMode.loso:
            return None
        h = holdout or self.loso_holdout
        if h is None:
            raise ValueError("mode loso needs a holdout season (splits.loso_holdout or --holdout)")
        if h not in self.all_seasons:
            raise ValueError(f"holdout season {h} is not in all_seasons")
        return h

    def assign(self, season: str, holdout: str | None = None) -> Split | None:
        """The split of a season under the configured mode (None: the season is not used)."""
        if self.mode == SplitMode.split:
            s = self.split_of(season)
            return Split(s) if s else None
        if season not in self.all_seasons:
            return None
        if self.mode == SplitMode.loso and season == self.holdout(holdout):
            return Split.holdout
        return Split.training

    def case_set(self, holdout: str | None = None) -> str:
        """Directory under benchmark/ for this mode: ``all``, ``split`` or ``loso_<season>``."""
        return f"loso_{self.holdout(holdout)}" if self.mode == SplitMode.loso else self.mode.value

    def mode_seasons(self, holdout: str | None = None) -> dict[str, list[str]]:
        """Split -> seasons under the configured mode (for the UI, CLI and run manifests)."""
        if self.mode == SplitMode.split:
            return self.seasons()
        if self.mode == SplitMode.loso:
            h = self.holdout(holdout)
            return {"training": [s for s in self.all_seasons if s != h], "holdout": [h]}
        return {"training": list(self.all_seasons)}

    def warn_provisional(self) -> bool:
        return self.mode == SplitMode.split and self.provisional


class AvailabilitySettings(LabModel):
    """When a record counts as available to a case (build guide "Availability rule"). No source records a
    publication time, so each is the observation (or issue) time plus a configured delay (ADR-059)."""

    profile_delay_h: float = Field(default=24.0, ge=0)  # pit observed -> pit available
    profile_delay_provisional: bool = True  # pending the owner's answer on when pits are published
    weather_latency_h: float = Field(default=1.0, ge=0)  # station hour observed -> available
    gfs_latency_h: float = Field(default=5.0, ge=0)  # GFS run initial (issue) time -> available
    era5_latency_h: float = Field(default=120.0, ge=0)  # ERA5T ~5 days behind real time (ADR-033): backfilled values


class ForecastCaseSettings(LabModel):
    """How a ``forecast_h72`` case picks its as-of time and archived run (ADR-059, changed by ADR-060).

    ``as_of_rule``:
    - ``run_reaches_valid`` (default since milestone 3): among the archived runs available before the pit whose
      leads reach the pit time (issue + max lead >= pit), take the one named by ``run_choice``; as_of = that run's
      availability time (issue + ``gfs_latency_h``), so the forecast covers the whole horizon (no tail gap).
      ``longest_lead``: the earliest such run (lead to the pit up to the run's max lead, 72 h in the archive);
      ``latest``: the last one. Without such a run the case is a labelled measured stand-in with
      as_of = pit - ``horizon_h``.
    - ``fixed_horizon`` (milestone 2): as_of = pit - ``horizon_h``; the latest run available at as_of and issued
      within ``max_run_age_h`` (its leads may end before the pit: a case warning)."""

    as_of_rule: Literal["run_reaches_valid", "fixed_horizon"] = "run_reaches_valid"
    run_choice: Literal["longest_lead", "latest"] = "longest_lead"
    horizon_h: float = Field(default=72.0, gt=0)  # fixed_horizon, and stand-in cases: as_of = pit time - horizon
    max_run_age_h: float = Field(default=24.0, gt=0)  # fixed_horizon: the run must be issued this close to as_of
    search_window_h: float = Field(default=240.0, gt=0)  # run_reaches_valid: runs issued at most this long before
    gfs_dir: str = "archive/forecasts/gfs"  # relative to the source checkout (read only)


class StandinSettings(LabModel):
    """Measured weather given as a forecast issued at as-of: next_pit cases, and forecast cases without an archived
    run (owner, 2026-10-05). Labelled ``measured_standin`` everywhere so scores can be split by it."""

    min_coverage: float = Field(default=0.5, ge=0, le=1)  # share of hours as_of..valid with T and precipitation
    # Measured variables that describe the snowpack being predicted, not the weather forcing it: withheld from the
    # stand-in (a weather forecast does not know them).
    withheld: list[str] = Field(default_factory=lambda: ["snow_depth_m", "swe_mm"])

    @field_validator("withheld")
    @classmethod
    def _vars(cls, v: list[str]) -> list[str]:
        unknown = sorted(set(v) - set(WEATHER_VARIABLES))
        if unknown:
            raise ValueError(f"standin.withheld names unknown weather variables {unknown}")
        return v


class BenchmarkSettings(LabModel):
    availability: AvailabilitySettings = Field(default_factory=AvailabilitySettings)
    forecast_h72: ForecastCaseSettings = Field(default_factory=ForecastCaseSettings)
    standin: StandinSettings = Field(default_factory=StandinSettings)


class WeatherImportSettings(LabModel):
    """``lab import`` weather: station values first; with ``era5_backfill`` an hour/variable no station supplied
    (missing or failed QC) takes the ERA5 nearest-cell value, flagged ``filled`` and sourced ``era5`` (owner,
    2026-10-05: "FTS360, else ERA5 backfill"; ADR-059). Values stay at the station or the ERA5 cell height."""

    era5_backfill: bool = True
    era5_dir: str = "data/interim/era5"  # relative to the source checkout (read only)


class LabConfig(LabModel):
    display_timezone: str
    season_start: str  # MM-DD, from config/plot_forcing.yaml
    sites: dict[SiteCode, Site]
    scoring_weights: ScoringWeights
    splits: Splits
    benchmark: BenchmarkSettings = Field(default_factory=BenchmarkSettings)
    weather: WeatherImportSettings = Field(default_factory=WeatherImportSettings)
    genome: GenomeSpec = Field(default_factory=default_spec)  # the gene allow-list (ADR-061)
    plot_forcing_config: str  # path it was read from (provenance)
    observations_config: str = "config/observations.yaml"  # holds the ADR-050 exclude switch (provenance)

    @field_validator("display_timezone")
    @classmethod
    def _tz(cls, v: str) -> str:
        ZoneInfo(v)  # raises for an unknown zone
        return v

    @field_validator("season_start")
    @classmethod
    def _start(cls, v: str) -> str:
        if not re.fullmatch(r"\d{2}-\d{2}", v):
            raise ValueError(f"season_start {v!r} must be MM-DD")
        return v

    def config_hash(self) -> str:
        """sha256 of the configuration as loaded (plot coordinates included), stable across key order."""
        payload = self.model_dump(mode="json", exclude={"plot_forcing_config", "observations_config"})
        return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()

    def site_by_plot(self, plot_id: str) -> Site | None:
        return next((s for s in self.sites.values() if s.plot_id == plot_id), None)


def load_lab_config(path: Path = DEFAULT_CONFIG) -> LabConfig:
    path = Path(path)
    raw = yaml.safe_load(path.read_text()) or {}
    pf_path = path.parent / raw.get("plot_forcing_config", "plot_forcing.yaml")
    pf = yaml.safe_load(pf_path.read_text())
    sites = {}
    for code, s in (raw.get("sites") or {}).items():
        plot = pf["plots"].get(s["plot_id"])
        if plot is None:
            raise ValueError(f"site {code}: plot {s['plot_id']!r} is not in {pf_path}")
        sites[code] = Site(code=code, plot_id=s["plot_id"], display_name=s.get("display_name", plot["name"]),
                           latitude=plot["lat"], longitude=plot["lon"], elevation_m=plot["elevation_m"],
                           timezone=raw["display_timezone"],
                           reference_scenario=ReferenceScenario(**s["reference_scenario"]),
                           wind_stations=list(s.get("wind") or []))
    return LabConfig(display_timezone=raw["display_timezone"], season_start=str(pf.get("season_start", "09-15")),
                     sites=sites, scoring_weights=ScoringWeights(**raw["scoring"]["weights"]),
                     splits=Splits(**(raw.get("splits") or {})),
                     benchmark=BenchmarkSettings(**(raw.get("benchmark") or {})),
                     weather=WeatherImportSettings(**(raw.get("weather") or {})),
                     genome=GenomeSpec(**raw["genome"]) if raw.get("genome") else default_spec(),
                     plot_forcing_config=str(pf_path),
                     observations_config=str(path.parent / raw.get("observations_config", "observations.yaml")))


def _utc(t: datetime | pd.Timestamp | str) -> pd.Timestamp:
    ts = pd.Timestamp(t)
    if ts.tzinfo is None:
        raise ValueError(f"naive time {t!r}: give a UTC offset")
    return ts.tz_convert("UTC")


def season_key(t: datetime | pd.Timestamp | str, season_start: str = "09-15") -> str:
    """Season of a time, 'YYYY-YYYY', the season starting at 00 UTC on ``season_start`` (MM-DD; the project's
    15 Sep, as in config/plot_forcing.yaml). 2023-09-14T23:00Z -> '2022-2023'; 2023-09-15T00:00Z -> '2023-2024'."""
    ts = _utc(t)
    month, day = (int(x) for x in season_start.split("-"))
    start = ts.year if (ts.month, ts.day) >= (month, day) else ts.year - 1
    return f"{start}-{start + 1}"


def season_bounds(key: str, season_start: str = "09-15") -> tuple[pd.Timestamp, pd.Timestamp]:
    """[start, end) of a season key in UTC."""
    m = SEASON_KEY.match(key)
    if not m:
        raise ValueError(f"not a season key: {key!r}")
    y = int(m[1])
    return pd.Timestamp(f"{y}-{season_start}", tz="UTC"), pd.Timestamp(f"{y + 1}-{season_start}", tz="UTC")


def season_keys(times: pd.Series, season_start: str = "09-15") -> pd.Series:
    """``season_key`` for a series of UTC times (vectorised)."""
    t = pd.to_datetime(times, utc=True)
    month, day = (int(x) for x in season_start.split("-"))
    after = (t.dt.month > month) | ((t.dt.month == month) & (t.dt.day >= day))
    start = t.dt.year.where(after, t.dt.year - 1)
    return start.astype("Int64").astype(str) + "-" + (start + 1).astype("Int64").astype(str)
