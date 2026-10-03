"""Deploy checks for the site tool (ADR-045): refuse a deploy of an incomplete site folder."""

import json
import os
import socket
from pathlib import Path

import pandas as pd

NOW = pd.Timestamp("2026-10-03T06:00:00Z")
PLOTS = ("goats_eye", "simpson", "bow_summit")
SEASONS = ["2024-2025", "2025-2026", "2026-2027"]


def _site(web: Path, seasons: dict[str, list[str]] | None = None, now: pd.Timestamp = NOW,
          index_age_h: float = 1.0, status_age_h: float = 1.0) -> dict:
    """A deployable site folder: static files, a data file per season (and a forecasts file for the last one),
    data/sites.json in the layout of web.build.write_index, and data/status.json."""
    from snowagent.ops.deploy import STATIC_FILES

    seasons = {p: SEASONS for p in PLOTS} if seasons is None else seasons
    (web / "data").mkdir(parents=True, exist_ok=True)
    for name in STATIC_FILES:
        (web / name).write_text("static")
    idx = {"label": "x", "generated_utc": (now - pd.Timedelta(hours=index_age_h)).isoformat(timespec="seconds"),
           "sites": []}
    for plot, ss in seasons.items():
        (web / "data" / plot).mkdir(exist_ok=True)
        entries = []
        for s in ss:
            (web / "data" / plot / f"{s}.json").write_text(json.dumps({"site": plot, "season": s}))
            fc = None
            if s == ss[-1]:
                (web / "data" / plot / f"{s}_forecasts.json").write_text(json.dumps({"issues": []}))
                fc = f"data/{plot}/{s}_forecasts.json"
            entries.append({"season": s, "mode": "measured", "file": f"data/{plot}/{s}.json", "forecasts": fc,
                            "public": None, "pits": 0, "live": None})
        idx["sites"].append({"id": plot, "name": plot, "seasons": entries})
    (web / "data" / "sites.json").write_text(json.dumps(idx))
    (web / "data" / "status.json").write_text(json.dumps(
        {"generated_utc": (now - pd.Timedelta(hours=status_age_h)).isoformat(timespec="seconds")}))
    return idx


def _has(problems: list[str], *parts: str) -> bool:
    return any(all(p in x for p in parts) for x in problems)


def test_data_paths_must_stay_under_data():
    from snowagent.ops.deploy import data_path

    assert data_path("data/goats_eye/2025-2026_forecasts.json") == "data/goats_eye/2025-2026_forecasts.json"
    assert data_path("data/status.json") == "data/status.json"
    for bad in ("data/../secret.json", "/etc/x.json", "data/goats_eye/x.txt", "https://example.org/data/x.json",
                "data/.hidden.json", "data//x.json", "goats_eye/x.json", None, 3):
        assert data_path(bad) is None, bad


def test_check_deploy_passes_a_complete_folder(tmp_path):
    from snowagent.ops.deploy import check_deploy

    web = tmp_path / "web"
    ref = tmp_path / "deployed_sites.json"
    ref.write_text(json.dumps(_site(web)))
    res = check_deploy(web, ref, now=NOW, lock=tmp_path / "update.lock")
    assert res["ok"] is True and res["problems"] == []
    assert res["seasons"] == {p: 3 for p in PLOTS} and res["data_files_checked"] == 12
    assert res["status_generated_utc"] == "2026-10-03T05:00:00+00:00" and res["reference"] == str(ref)
    assert check_deploy(web, None, now=NOW, lock=tmp_path / "update.lock")["ok"] is True  # reference optional


def test_check_deploy_refuses_a_folder_that_would_drop_seasons_or_files(tmp_path):
    from snowagent.ops.deploy import check_deploy

    deployed = _site(tmp_path / "deployed", index_age_h=0.5)  # the live site: three seasons per plot
    deployed["sites"][1]["seasons"][2]["public"] = "data/simpson/2026-2027_public.json"
    ref = tmp_path / "deployed_sites.json"
    ref.write_text(json.dumps(deployed))
    web = tmp_path / "web"  # a fresh container that built only the live season of Goat's Eye
    _site(web, {"goats_eye": ["2026-2027"], "simpson": SEASONS, "bow_summit": SEASONS}, status_age_h=30)
    (web / "data" / "simpson" / "2025-2026.json").unlink()
    (web / "data" / "bow_summit" / "2024-2025.json").write_text('{"season": ')  # cut short
    (web / "app.js").unlink()
    res = check_deploy(web, ref, now=NOW, lock=tmp_path / "update.lock")
    p = res["problems"]
    assert res["ok"] is False and res["seasons"]["goats_eye"] == 1
    assert _has(p, "goats_eye: seasons of the deployed site missing here: 2024-2025, 2025-2026 (1 here, 3 deployed)")
    assert _has(p, "data/simpson/2025-2026.json: missing")
    assert _has(p, "data/bow_summit/2024-2025.json: not valid JSON")
    assert _has(p, "simpson 2026-2027: public file on the deployed site, none here")
    assert _has(p, "data/status.json", "30.0 h ago", "update build")
    assert _has(p, "data/sites.json: generated 2026-10-03T05:00:00+00:00, older than the deployed one")
    assert _has(p, "app.js: missing")
    assert len(p) == 7


def test_check_deploy_flags_missing_sites_bad_paths_bad_status_and_a_running_update(tmp_path):
    from snowagent.ops.deploy import check_deploy

    web = tmp_path / "web"
    idx = _site(web, {"goats_eye": SEASONS, "simpson": []})
    idx["sites"][0]["seasons"][0]["file"] = "data/../../secrets.json"
    (web / "data" / "sites.json").write_text(json.dumps(idx))
    (web / "data" / "status.json").write_text(json.dumps({"season": "2026-2027"}))
    lock = tmp_path / "update.lock"
    held = {"pid": os.getpid(), "host": socket.gethostname(), "command": "build",
            "started_utc": (NOW - pd.Timedelta(minutes=10)).isoformat(timespec="seconds")}
    lock.write_text(json.dumps(held))
    p = check_deploy(web, None, now=NOW, lock=lock)["problems"]
    assert _has(p, "bow_summit: not in data/sites.json") and _has(p, "simpson: no seasons in data/sites.json")
    assert _has(p, "goats_eye 2024-2025 file: 'data/../../secrets.json' is not a data/*.json path")
    assert _has(p, "data/status.json: no valid generated_utc")
    assert _has(p, "an update run holds the lock (build started", f"pid {os.getpid()}")
    assert len(p) == 5
    lock.write_text(json.dumps({**held, "started_utc": (NOW - pd.Timedelta(hours=4)).isoformat()}))  # stale
    assert not _has(check_deploy(web, None, now=NOW, lock=lock)["problems"], "holds the lock")
    (web / "data" / "sites.json").unlink()
    assert _has(check_deploy(web, None, now=NOW, lock=lock)["problems"], "data/sites.json: missing")


def test_check_deploy_cli_exits_2_and_lists_the_problems(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from snowagent.cli import app
    from snowagent.ops import update

    monkeypatch.setattr(update, "LOCK_FILE", tmp_path / "update.lock")
    web, ref = tmp_path / "web", tmp_path / "deployed_sites.json"
    ref.write_text(json.dumps(_site(web, now=pd.Timestamp.now(tz="UTC"))))
    r = CliRunner().invoke(app, ["update", "check-deploy", "--web", str(web), "--reference", str(ref)])
    assert r.exit_code == 0 and json.loads(r.stdout)["ok"] is True
    (web / "data" / "goats_eye" / "2024-2025.json").unlink()
    r = CliRunner().invoke(app, ["update", "check-deploy", "--web", str(web), "--reference", str(ref)])
    assert r.exit_code == 2 and json.loads(r.stdout)["problems"] == ["data/goats_eye/2024-2025.json: missing"]
    assert "check-deploy: data/goats_eye/2024-2025.json: missing" in r.stderr
    r = CliRunner().invoke(app, ["update", "check-deploy", "--web", str(web), "--max-age-h", "0.0001"])
    assert r.exit_code == 2 and "at most 0.0001 h" in r.stderr
