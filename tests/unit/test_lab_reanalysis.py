"""Seasons before the plot stations as training cases (ADR-076): the season switch, the weather import before the
stations, each case's weather provenance (station / mixed / era5_only) and its filters, the unchanged leakage checks
on an ERA5-only case (ERA5 keeps its 120 h latency), and `lab prepare` fetching only the months those cases read."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("pyarrow", reason="lab extra not installed (pip install -e '.[lab]')")

from snowagent.lab.benchmark import builder  # noqa: E402
from snowagent.lab.benchmark.leakage import LeakageError, check_case  # noqa: E402
from snowagent.lab.benchmark.loader import case_dirs, load_visible_case, read_manifest  # noqa: E402
from snowagent.lab.competition.runner import EngineSpec, build_leaderboard, run_competition, select_cases  # noqa: E402
from snowagent.lab.genome import default_genome  # noqa: E402
from snowagent.lab.schemas.genome import AgentFamily  # noqa: E402
from snowagent.lab.services import prepare as prep  # noqa: E402
from snowagent.lab.settings import Splits, load_lab_config  # noqa: E402
from snowagent.lab.storage.paths import LabPaths  # noqa: E402
from snowagent.lab.storage.tables import read_table, write_table  # noqa: E402
from tests.unit import lab_fixtures  # noqa: E402
from tests.unit.test_lab_benchmark import CONFIG, _config  # noqa: E402

OLD = [f"{y}-{y + 1}" for y in range(1997, 2015)]
ERA5 = "era5_cell_2244m"


# --------------------------------------------------------------------------------------------- the switch


def test_switch_adds_the_reanalysis_seasons_to_the_lab(tmp_path):
    cfg = load_lab_config(CONFIG)
    s = cfg.splits
    assert s.include_reanalysis_seasons and s.reanalysis_seasons == OLD
    assert s.all_seasons[:18] == OLD and s.all_seasons[18:] == [f"{y}-{y + 1}" for y in range(2015, 2026)]
    assert s.assign("2006-2007").value == "training" and s.development_seasons[:18] == OLD
    assert prep.lab_seasons(cfg)[0] == "1997-1998"
    # off: exactly the station seasons, the older pits are history only
    off = _config(tmp_path, include_reanalysis_seasons=False).splits
    assert off.all_seasons[0] == "2015-2016" and len(off.all_seasons) == 11 and off.assign("2006-2007") is None
    # loso may hold out an older season only while the switch is on
    assert Splits(mode="loso", all_seasons=["2020-2021"], reanalysis_seasons=["2006-2007"],
                  loso_holdout="2006-2007").holdout() == "2006-2007"
    with pytest.raises(ValueError, match="not in all_seasons"):
        Splits(mode="loso", all_seasons=["2020-2021"], reanalysis_seasons=["2006-2007"],
               include_reanalysis_seasons=False, loso_holdout="2006-2007")
    # loading twice (a dumped config) lists each season once; a malformed season key is refused
    again = Splits(**s.model_dump())
    assert again.all_seasons == s.all_seasons
    with pytest.raises(ValueError, match="season key"):
        Splits(reanalysis_seasons=["2006-2008"])


# --------------------------------------------------------------------------------------------- import


def test_import_reaches_back_to_the_first_cached_era5_month_before_the_stations(tmp_path):
    from snowagent.lab.services.data import era5_start, import_data, load_weather
    from tests.unit.test_lab_import import FIX
    import shutil

    source = tmp_path / "checkout"
    shutil.copytree(FIX / "fts360", source / "data/raw/fts360")
    d = lab_fixtures.write_era5(source, ["2006-12", "2024-01"])
    cfg = load_lab_config(CONFIG)
    files = sorted(d.glob("era5_box_*.npz"))
    assert era5_start(cfg, files) == pd.Timestamp("2006-12-01", tz="UTC")
    # a cache that starts after the reanalysis seasons, or the switch off: the stations' first hour, as before
    assert era5_start(cfg, [f for f in files if "2024" in f.name or "_z" in f.name]) is None
    assert era5_start(_config(tmp_path, include_reanalysis_seasons=False), files) is None
    paths = LabPaths(tmp_path / "lab")
    report = import_data(source, paths, cfg, ("weather",))
    assert report["sites"]["BOW"]["weather_first_hour"].startswith("2006-12-01")
    w = load_weather(paths, "BOW").set_index("observed_at")
    dec = w.loc["2006-12"]
    assert len(dec) == 31 * 24 and (dec["air_temperature_k_source"] == "era5_cell_2100m").all()
    assert (dec["precipitation_mm_qc"] == "filled").all() and dec["snow_depth_m"].isna().all()
    gap = w.loc["2007-01"]  # no ERA5 month in the cache: explicit missing rows, never invented
    assert len(gap) and gap["air_temperature_k"].isna().all() and (gap["air_temperature_k_qc"] == "missing").all()
