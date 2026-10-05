"""Lab configuration: sites from config/plot_forcing.yaml, 15 Sep season key, split overlap blocked (ADR-055/056)."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest
import yaml
from pydantic import ValidationError

from snowagent.lab.settings import Splits, load_lab_config, season_bounds, season_key, season_keys

CONFIG = Path(__file__).resolve().parents[2] / "config"


def test_sites_read_from_plot_config_not_duplicated():
    cfg = load_lab_config(CONFIG / "lab.yaml")
    plots = yaml.safe_load((CONFIG / "plot_forcing.yaml").read_text())["plots"]
    lab = yaml.safe_load((CONFIG / "lab.yaml").read_text())
    assert set(cfg.sites) == {"BOW", "GOAT", "SIMP"}
    for code, plot in (("BOW", "bow_summit"), ("GOAT", "goats_eye"), ("SIMP", "simpson")):
        s = cfg.sites[code]
        assert s.plot_id == plot
        assert (s.latitude, s.longitude, s.elevation_m) == (plots[plot]["lat"], plots[plot]["lon"],
                                                            plots[plot]["elevation_m"])
        assert not {"lat", "lon", "latitude", "longitude", "elevation_m"} & set(lab["sites"][code])
    assert cfg.display_timezone == "America/Edmonton" and cfg.season_start == "09-15"
    assert sum(cfg.scoring_weights.model_dump().values()) == pytest.approx(1.0)
    assert len(cfg.config_hash()) == 64


def test_season_key_uses_15_september_start():
    assert season_key("2023-09-14T23:59:00Z") == "2022-2023"
    assert season_key("2023-09-15T00:00:00Z") == "2023-2024"
    assert season_key("2024-01-10T19:00:00+00:00") == "2023-2024"
    assert season_key("2023-10-01T00:00:00Z", season_start="10-01") == "2023-2024"
    with pytest.raises(ValueError, match="naive"):
        season_key("2023-09-15T00:00:00")
    s, e = season_bounds("2023-2024")
    assert s == pd.Timestamp("2023-09-15", tz="UTC") and e == pd.Timestamp("2024-09-15", tz="UTC")
    times = pd.Series(pd.to_datetime(["2023-09-14T23:00Z", "2023-09-15T00:00Z", "2024-03-01T00:00Z"], utc=True))
    assert list(season_keys(times)) == [season_key(t) for t in times]


def test_split_overlap_is_rejected():
    assert Splits().split_of("2020-2021") is None
    s = Splits(development_seasons=["2018-2019", "2019-2020"], validation_seasons=["2020-2021"],
               sealed_test_seasons=["2021-2022"])
    assert s.split_of("2020-2021") == "validation"
    with pytest.raises(ValidationError, match="split overlap"):
        Splits(development_seasons=["2018-2019", "2019-2020"], sealed_test_seasons=["2019-2020"])
    with pytest.raises(ValidationError, match="season key"):
        Splits(validation_seasons=["2019-2021"])
    with pytest.raises(ValidationError, match="twice"):
        Splits(validation_seasons=["2019-2020", "2019-2020"])


def test_config_with_overlapping_splits_does_not_load(tmp_path):
    lab = yaml.safe_load((CONFIG / "lab.yaml").read_text())
    lab["splits"] = {"development_seasons": ["2020-2021"], "validation_seasons": ["2020-2021"]}
    lab["plot_forcing_config"] = str(CONFIG / "plot_forcing.yaml")
    (tmp_path / "lab.yaml").write_text(yaml.safe_dump(lab))
    with pytest.raises(ValidationError, match="split overlap"):
        load_lab_config(tmp_path / "lab.yaml")
