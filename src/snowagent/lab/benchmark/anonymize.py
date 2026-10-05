"""Anonymous visible tables (owner, 2026-10-05: "we need to ensure agents just dont memorize these snowpacks").

Times become hours relative to the case's as-of time plus the UTC day of year (solar geometry needs it), never a
calendar date or year. Pits become ``pit_01``.. (oldest first) with a season offset (0 = the case's season); their
profile ids, observer, source file, coordinates, flags, free text (notes, comments, raw fields) and observer layer
tags ("Nov crust", "121106 Rain Crust") are dropped; a layer of concern marked from an observer tag keeps the bare
basis ``observer_tag``. Stability tests keep their type and result only. The real ids stay in the manifest
(``pit_keys``) and the hidden package.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from snowagent.lab.schemas.weather import WEATHER_VARIABLES

WEATHER_COLUMNS = ["t_rel_h", "day_of_year", "kind", "source_id", "issued_rel_h", "available_rel_h",
                   "availability_assumption",
                   *[c for v in WEATHER_VARIABLES for c in (v, f"{v}_source", f"{v}_qc")], "quality_flag"]
PIT_COLUMNS = ["pit_key", "t_rel_h", "day_of_year", "season_offset", "available_rel_h", "availability_assumption",
               "aspect_deg", "slope_deg", "terrain_class", "profile_quality", "snow_depth_m", "profile_depth_m",
               "temperatures_json"]
LAYER_COLUMNS = ["pit_key", "layer_index", "top_depth_m", "bottom_depth_m", "grain_primary", "grain_secondary",
                 "grain_size_mm", "grain_size_max_mm", "hardness", "hardness_index", "wetness", "density_kg_m3",
                 "temperature_c", "critical_class", "is_layer_of_concern", "concern_basis_json", "confidence",
                 "uncertain_fields_json"]
OBSERVATION_COLUMNS = ["pit_key", "observation_type", "t_rel_h", "available_rel_h", "payload_json"]
TEST_PAYLOAD_KEYS = ("type", "result", "score", "fracture_character", "shear_quality", "depth_m")


def rel_hours(t: pd.Series | pd.Timestamp, as_of: pd.Timestamp):
    return (pd.to_datetime(t, utc=True) - as_of) / pd.Timedelta(hours=1)


def day_of_year(t: pd.Series) -> pd.Series:
    t = pd.to_datetime(t, utc=True)
    return t.dt.dayofyear + (t.dt.hour * 60 + t.dt.minute) / 1440.0


def weather_table(df: pd.DataFrame, as_of: pd.Timestamp) -> pd.DataFrame:
    """Canonical weather rows (with ``source_recorded_at`` set) -> anonymous visible rows."""
    out = pd.DataFrame(index=df.index)
    out["t_rel_h"] = rel_hours(df["observed_at"], as_of).astype(float)
    out["day_of_year"] = day_of_year(df["observed_at"]).astype(float)
    out["kind"] = df["kind"].astype(object)
    out["source_id"] = df["source_id"].astype(object)
    issued = pd.to_datetime(df["issued_at"], utc=True) if "issued_at" in df else pd.Series(pd.NaT, index=df.index)
    out["issued_rel_h"] = rel_hours(issued, as_of).astype(float)
    out["available_rel_h"] = rel_hours(df["source_recorded_at"], as_of).astype(float)
    out["availability_assumption"] = df["availability_assumption"].astype(object)
    for v in WEATHER_VARIABLES:
        out[v] = df[v].astype(float)
        out[f"{v}_source"] = df[f"{v}_source"].astype(object)
        out[f"{v}_qc"] = df[f"{v}_qc"].astype(object)
    out["quality_flag"] = df["quality_flag"].astype(object)
    return out[WEATHER_COLUMNS].reset_index(drop=True)


def _season_start_year(season: str) -> int:
    return int(str(season)[:4])


def _basis(text: str) -> str:
    items = json.loads(text or "[]")
    return json.dumps(["observer_tag" if b.startswith("observer_tag") else b for b in items])


def _payload(text: str) -> str:
    p = json.loads(text or "{}")
    return json.dumps({k: p.get(k) for k in TEST_PAYLOAD_KEYS if k in p})


def pit_tables(prof: pd.DataFrame, layers: pd.DataFrame, obs: pd.DataFrame, as_of: pd.Timestamp, case_season: str
               ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, str]]:
    """Visible pits (stamped canonical profile rows), their layers and tests -> anonymous tables and the
    ``pit_key -> profile_id`` mapping (manifest only)."""
    prof = prof.sort_values(["observed_at", "profile_id"]).reset_index(drop=True)
    width = max(2, len(str(len(prof))))
    keys = {pid: f"pit_{i + 1:0{width}d}" for i, pid in enumerate(prof["profile_id"])}
    p = pd.DataFrame({"pit_key": prof["profile_id"].map(keys)})
    p["t_rel_h"] = rel_hours(prof["observed_at"], as_of).astype(float)
    p["day_of_year"] = day_of_year(prof["observed_at"]).astype(float)
    p["season_offset"] = (prof["season"].map(_season_start_year) - _season_start_year(case_season)).astype(int)
    p["available_rel_h"] = rel_hours(prof["source_recorded_at"], as_of).astype(float)
    p["availability_assumption"] = prof["availability_assumption"].astype(object)
    for c in ("aspect_deg", "slope_deg", "snow_depth_m", "profile_depth_m"):
        p[c] = pd.to_numeric(prof[c], errors="coerce").astype(float)
    for c in ("terrain_class", "profile_quality"):
        p[c] = prof[c].astype(object)
    p["temperatures_json"] = prof["temperatures_json"].astype(object) if len(prof) else pd.Series(dtype=object)
    lay = layers[layers["profile_id"].isin(keys)].sort_values(["profile_id", "top_depth_m", "bottom_depth_m"])
    lt = pd.DataFrame({"pit_key": lay["profile_id"].map(keys).astype(object)})
    lt["layer_index"] = lay.groupby("profile_id").cumcount().astype(int) if len(lay) else pd.Series(dtype=int)
    for c in LAYER_COLUMNS[2:]:
        if c == "concern_basis_json":
            lt[c] = lay[c].map(_basis) if len(lay) else pd.Series(dtype=object)
        elif c in lay:
            lt[c] = lay[c]
        else:
            lt[c] = np.nan
    if len(obs):
        ot = pd.DataFrame({"pit_key": obs["profile_id"].map(keys).astype(object),
                           "observation_type": obs["observation_type"].astype(object),
                           "t_rel_h": rel_hours(obs["observed_at"], as_of).astype(float),
                           "available_rel_h": rel_hours(obs["source_recorded_at"], as_of).astype(float),
                           "payload_json": obs["payload_json"].map(_payload)})
    else:
        ot = pd.DataFrame({c: pd.Series(dtype=float if c.endswith("_h") else object) for c in OBSERVATION_COLUMNS})
    return (p[PIT_COLUMNS].reset_index(drop=True), lt[LAYER_COLUMNS].reset_index(drop=True),
            ot[OBSERVATION_COLUMNS].reset_index(drop=True), {v: k for k, v in keys.items()})
