"""`snowagent lab prepare` (ADR-075): the ERA5 months the lab reads, resumable fetching with per-month failures, and
the fresh-clone steps run in the repository root without overwriting anything."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
from typer.testing import CliRunner

from snowagent.lab.services import prepare as prep
from snowagent.lab.settings import load_lab_config
from tests.unit.test_lab_benchmark import CONFIG

REPO_CONFIG = Path(__file__).resolve().parents[2] / "config" / "lab.yaml"


def test_era5_months_are_september_to_june_of_every_configured_season_up_to_now():
    cfg = load_lab_config(REPO_CONFIG)
    seasons = prep.lab_seasons(cfg)
    months = prep.era5_months(cfg, today=date(2100, 1, 1))
    assert len(months) == 10 * len(seasons)
    assert months[0] == (int(seasons[0][:4]), 9) and months[-1] == (int(seasons[-1][5:]), 6)
    assert not any(m in (7, 8) for _y, m in months)
    now = prep.era5_months(cfg, today=date(2026, 10, 5))  # a season still to come is not fetched
    assert now[-1] == (2026, 10) and all(ym <= (2026, 10) for ym in now)


def test_fetch_skips_cached_months_and_reports_failures(tmp_path):
    cfg = load_lab_config(REPO_CONFIG)
    months = prep.era5_months(cfg)
    assert months
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


def _git(*args, cwd):
    import subprocess

    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_era5_bundle_adds_missing_months_from_the_branch_and_never_overwrites(tmp_path):
    remote, clone = tmp_path / "remote", tmp_path / "clone"
    (remote / "era5").mkdir(parents=True)
    (remote / "era5" / "era5_box_200001.npz").write_bytes(b"bundle")
    (remote / "era5" / "era5_box_200002.npz").write_bytes(b"bundle")
    (remote / "era5" / "era5_box_200002.json").write_text("{}")
    (remote / "era5" / "notes.sh").write_text("not a month")  # only box files are taken
    (remote / "README.md").write_text("bundle")
    _git("init", "-q", "-b", prep.ERA5_BUNDLE_BRANCH, cwd=remote)
    _git("add", ".", cwd=remote)
    _git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "bundle", cwd=remote)
    clone.mkdir()
    _git("init", "-q", cwd=clone)
    _git("remote", "add", "origin", str(remote), cwd=clone)
    era5_dir = clone / "data" / "interim" / "era5"
    era5_dir.mkdir(parents=True)
    (era5_dir / "era5_box_200001.npz").write_bytes(b"mine")
    import contextlib

    with contextlib.chdir(clone):
        added = prep.restore_era5_bundle(Path("data/interim/era5"), log=lambda _m: None)
    assert added == 1
    assert (era5_dir / "era5_box_200001.npz").read_bytes() == b"mine"
    assert (era5_dir / "era5_box_200002.npz").read_bytes() == b"bundle"
    assert (era5_dir / "era5_box_200002.json").is_file() and not (era5_dir / "notes.sh").exists()


def test_era5_bundle_missing_falls_back_to_the_mirror(tmp_path):
    import contextlib

    _git("init", "-q", cwd=tmp_path)
    msgs = []
    with contextlib.chdir(tmp_path):
        assert prep.restore_era5_bundle(Path("era5"), remote=str(tmp_path / "nowhere"), log=msgs.append) == 0
    assert "fetching from the mirror" in msgs[-1]
