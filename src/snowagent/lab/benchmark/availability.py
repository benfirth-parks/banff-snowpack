"""The availability rule (build guide): a record is visible to a case only if ``source_recorded_at <= as_of``.

No source records when a pit or value was published, so the case builder sets ``source_recorded_at`` to the
observation (or issue) time plus a configured delay and marks the record ``assumed_delay`` (ADR-059):
pits observed + ``profile_delay_h`` (24 h, provisional pending the owner's answer), station hours observed +
``weather_latency_h`` (1 h), ERA5-filled values + ``era5_latency_h`` (5 days), GFS runs issued + ``gfs_latency_h``
(5 h). The processed tables are not changed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from snowagent.lab.schemas.common import AvailabilityAssumption, QualityFlag
from snowagent.lab.schemas.weather import SEVERITY, WEATHER_VARIABLES
from snowagent.lab.settings import AvailabilitySettings

_RANK = {f.value: i for i, f in enumerate(SEVERITY)}
_FLAG = {i: f for f, i in _RANK.items()}


def stamp(df: pd.DataFrame, time_col: str, delay_h: float) -> pd.DataFrame:
    """Copy with ``source_recorded_at`` = ``time_col`` + delay and the assumed-delay mark."""
    out = df.copy()
    out["source_recorded_at"] = pd.to_datetime(out[time_col], utc=True) + pd.Timedelta(hours=delay_h)
    out["availability_assumption"] = AvailabilityAssumption.assumed_delay.value
    return out


def rules_text(a: AvailabilitySettings) -> dict[str, str]:
    """Per visible table, how its availability was set (recorded in every manifest)."""
    pit = f"observed_at + {a.profile_delay_h:g} h" + (" (provisional, owner to confirm)" if a.profile_delay_provisional
                                                      else "")
    return {"permitted_pits": pit, "permitted_observations": pit + " (the pit's availability)",
            "weather_observed": f"observed_at + {a.weather_latency_h:g} h; ERA5-filled values observed_at + "
                                f"{a.era5_latency_h:g} h",
            "weather_forecasts": f"archived GFS: issued_at + {a.gfs_latency_h:g} h; measured stand-in: issued_at = "
                                 "as_of by convention"}


def recompute_quality(df: pd.DataFrame) -> pd.Series:
    """Worst flag over the variables with a value (as ``schemas.weather.record_quality``), vectorised."""
    if not len(df):
        return pd.Series(dtype=object)
    ranks = np.full(len(df), -1)
    for var in WEATHER_VARIABLES:
        r = df[f"{var}_qc"].map(_RANK).fillna(-1).to_numpy(dtype=int)
        ranks = np.maximum(ranks, r)
    return pd.Series([_FLAG.get(int(r), QualityFlag.missing.value) for r in ranks], index=df.index, dtype=object)
