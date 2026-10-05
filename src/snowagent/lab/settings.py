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
from zoneinfo import ZoneInfo

import pandas as pd
import yaml
from pydantic import Field, field_validator, model_validator

from snowagent.lab.schemas.common import LabModel, SiteCode
from snowagent.lab.schemas.run import ScoringWeights
from snowagent.lab.schemas.site import ReferenceScenario, Site

DEFAULT_CONFIG = Path("config/lab.yaml")
SEASON_KEY = re.compile(r"^(\d{4})-(\d{4})$")


class Splits(LabModel):
    development_seasons: list[str] = Field(default_factory=list)
    validation_seasons: list[str] = Field(default_factory=list)
    sealed_test_seasons: list[str] = Field(default_factory=list)

    @field_validator("development_seasons", "validation_seasons", "sealed_test_seasons")
    @classmethod
    def _keys(cls, v: list[str]) -> list[str]:
        for k in v:
            m = SEASON_KEY.match(str(k))
            if not m or int(m[2]) != int(m[1]) + 1:
                raise ValueError(f"season {k!r} is not a season key like 2021-2022")
        if len(set(v)) != len(v):
            raise ValueError(f"season listed twice in one split: {v}")
        return v

    @model_validator(mode="after")
    def _no_overlap(self) -> Splits:
        names = ("development_seasons", "validation_seasons", "sealed_test_seasons")
        for i, a in enumerate(names):
            for b in names[i + 1:]:
                both = sorted(set(getattr(self, a)) & set(getattr(self, b)))
                if both:
                    raise ValueError(f"split overlap: {both} in both {a} and {b}; a season may be in one split only")
        return self

    def split_of(self, season: str) -> str | None:
        for name in ("development", "validation", "sealed_test"):
            if season in getattr(self, f"{name}_seasons"):
                return name
        return None


class LabConfig(LabModel):
    display_timezone: str
    season_start: str  # MM-DD, from config/plot_forcing.yaml
    sites: dict[SiteCode, Site]
    scoring_weights: ScoringWeights
    splits: Splits
    plot_forcing_config: str  # path it was read from (provenance)

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
        payload = self.model_dump(mode="json", exclude={"plot_forcing_config"})
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
                     splits=Splits(**(raw.get("splits") or {})), plot_forcing_config=str(pf_path))


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
