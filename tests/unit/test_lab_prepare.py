"""`snowagent lab prepare` (ADR-075): the ERA5 months the lab reads, resumable fetching with per-month failures, and
the fresh-clone steps run in the repository root without overwriting anything."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from snowagent.lab.services import prepare as prep
from snowagent.lab.settings import load_lab_config
from tests.unit.test_lab_benchmark import CONFIG

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "lab.yaml"


def test_era5_months_are_september_to_june_of_every_configured_season():
    cfg = load_lab_config(REPO_CONFIG)
    months = prep.era5_months(cfg)
    seasons = prep.lab_seasons(cfg)
    assert len(months) == 10 * len(seasons)
    assert months[0] == (int(seasons[0][:4]), 9) and months[-1] == (int(seasons[-1][5:]), 6)
    assert not any(m in (7, 8) for _y, m in months)


def test_fetch_skips_cached_months_and_reports_failures(tmp_path):
    cfg = load_lab_config(REPO_CONFIG)
    months = prep.era5_months(cfg)
    (tmp_path / f"era5_box_{months[0][0]}{months[0][1]:02d}.npz").write_bytes(b"x")
    asked = []

    def fake(task):
        y, m, out = task
        asked.append((y, m))
        if (y, m) == months[-1]:
            return y, m, "FileNotFoundError: not on the mirror yet"
        (Path(out) / f"era5_box_{y}{m:02d}.npz").write_bytes(b"x")
        return y, m, None

    rep = prep.fetch_era5(cfg, tmp_path, workers=1, log=lambda _m: None, fetch=fake)
    assert months[0] not in asked and len(asked) == len(months) - 1
    assert rep["cached"] == 1 and rep["fetched"] == len(months) - 2 and len(rep["failed"]) == 1
    asked.clear()
    rep = prep.fetch_era5(cfg, tmp_path, workers=1, log=lambda _m: None, fetch=fake)  # a rerun retries the failure
    assert asked == [months[-1]] and rep["cached"] == len(months) - 1


def test_prepare_runs_in_the_repository_root_and_keeps_existing_profiles(tmp_path, monkeypatch):
    import snowagent.obs.observed as observed
    import snowagent.ops.update as update

    cfg = load_lab_config(CONFIG)
    calls = []
    monkeypatch.setattr(update, "bootstrap", lambda: calls.append(Path.cwd()) or {"fts360_restored": 0})
    monkeypatch.setattr(observed, "build_observed", lambda t, p: ([], {"profiles": 0}))
    monkeypatch.setattr(observed, "write_observed", lambda obs, out: (out.parent.mkdir(parents=True, exist_ok=True),
                                                                       out.write_text("")))
    monkeypatch.setattr(observed, "summarise_observed", lambda obs: pd.DataFrame())
    rep = prep.prepare(tmp_path, cfg, era5=False, log=lambda _m: None)
    assert calls == [tmp_path] and rep["observed_profiles"] == {"profiles": 0}
    assert (tmp_path / prep.OBSERVED).is_file() and "skipped" in rep["era5"]
    monkeypatch.setattr(observed, "build_observed", lambda t, p: (_ for _ in ()).throw(AssertionError("rebuilt")))
    assert prep.prepare(tmp_path, cfg, era5=False, log=lambda _m: None)["observed_profiles"] == "present"


def test_cli_prepare_refuses_outside_the_repository_root(tmp_path, monkeypatch):
    from snowagent.cli import app

    monkeypatch.chdir(tmp_path)
    r = CliRunner().invoke(app, ["lab", "prepare", "--no-era5", "--config", str(REPO_CONFIG)])
    assert r.exit_code == 2 and "repository root" in r.output
