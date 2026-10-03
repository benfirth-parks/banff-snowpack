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
    first = check_deploy(web, None, now=NOW, lock=tmp_path / "update.lock", no_reference=True)  # a first deploy
    assert first["ok"] is True and first["no_reference"] is True and res["no_reference"] is False


def test_check_deploy_without_a_reference_refuses_a_folder_holding_only_the_live_season(tmp_path):
    from snowagent.ops.deploy import check_deploy

    web = tmp_path / "web"  # restore-web failed (site unreachable), then update build indexed the live season
    _site(web, {p: ["2026-2027"] for p in PLOTS})
    lock = tmp_path / "update.lock"
    res = check_deploy(web, None, now=NOW, lock=lock)  # the deployed index could not be downloaded either
    assert res["ok"] is False and res["seasons"] == {p: 1 for p in PLOTS}
    assert len(res["problems"]) == 1 and res["problems"][0].startswith("no reference: download the deployed site")
    assert check_deploy(web, None, now=NOW, lock=lock, no_reference=True)["ok"] is True  # only when told so


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
    p = check_deploy(web, None, now=NOW, lock=lock, no_reference=True)["problems"]
    assert _has(p, "bow_summit: not in data/sites.json") and _has(p, "simpson: no seasons in data/sites.json")
    assert _has(p, "goats_eye 2024-2025 file: 'data/../../secrets.json' is not a data/*.json path")
    assert _has(p, "data/status.json: no valid generated_utc")
    assert _has(p, "an update run holds the lock (build started", f"pid {os.getpid()}")
    assert len(p) == 5
    lock.write_text(json.dumps({**held, "started_utc": (NOW - pd.Timedelta(hours=4)).isoformat()}))  # stale
    assert not _has(check_deploy(web, None, now=NOW, lock=lock, no_reference=True)["problems"], "holds the lock")
    (web / "data" / "sites.json").unlink()
    assert _has(check_deploy(web, None, now=NOW, lock=lock, no_reference=True)["problems"], "data/sites.json: missing")


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
    (web / "data" / "goats_eye" / "2024-2025.json").write_text("{}")
    r = CliRunner().invoke(app, ["update", "check-deploy", "--web", str(web)])  # no reference: refused
    assert r.exit_code == 2 and "check-deploy: no reference" in r.stderr
    r = CliRunner().invoke(app, ["update", "check-deploy", "--web", str(web), "--no-reference"])
    assert r.exit_code == 0 and json.loads(r.stdout)["no_reference"] is True


BASE = "https://site.example"


class _Remote:
    """The deployed site, served from a folder by a fake HTTP getter (no network); counts the URLs asked for."""

    def __init__(self, root: Path, broken: dict[str, bytes | Exception] | None = None):
        self.root, self.broken, self.calls = root, broken or {}, []

    def __call__(self, url: str) -> bytes:
        self.calls.append(url)
        assert url.startswith(BASE + "/data/")
        path = url.removeprefix(BASE + "/")
        if path in self.broken:
            b = self.broken[path]
            if isinstance(b, Exception):
                raise b
            return b
        f = self.root / path
        if not f.is_file():
            raise RuntimeError(f"404 Client Error: Not Found for url: {url}")
        return f.read_bytes()


def test_restore_web_downloads_every_listed_file_keeps_local_ones_and_writes_the_index_last(tmp_path):
    from snowagent.ops.deploy import check_deploy, restore_web

    remote = _Remote(tmp_path / "remote")
    deployed = _site(remote.root, now=pd.Timestamp.now(tz="UTC"))
    web = tmp_path / "web"
    (web / "data" / "goats_eye").mkdir(parents=True)
    for name in ("index.html", "app.js", "styles.css", "netlify.toml"):  # in the checkout
        (web / name).write_text("static")
    mine = web / "data" / "goats_eye" / "2026-2027.json"  # e.g. left by an earlier, interrupted restore
    mine.write_text('{"local": true}')
    res = restore_web(BASE + "/", web / "data", get=remote)
    assert res["ok"] is True and res["failed_steps"] == [] and res["sites_json"] == "written"
    assert res["restored"] == 12 and res["kept"] == 1 and res["seasons"] == {p: 3 for p in PLOTS}
    assert remote.calls[0] == BASE + "/data/sites.json" and remote.calls[-1] == BASE + "/data/status.json"
    assert len(remote.calls) == 13  # the index, 11 data files (one kept), status.json
    assert json.loads((web / "data" / "sites.json").read_text()) == deployed
    assert mine.read_text() == '{"local": true}'  # never replaced without --force
    assert (web / "data" / "bow_summit" / "2026-2027_forecasts.json").read_bytes() == \
        (remote.root / "data" / "bow_summit" / "2026-2027_forecasts.json").read_bytes()
    assert not list(web.rglob("*.part"))
    ref = tmp_path / "deployed_sites.json"
    ref.write_text(json.dumps(deployed))
    assert check_deploy(web, ref, lock=tmp_path / "update.lock")["ok"] is True

    remote.calls.clear()  # the index exists now: nothing to do
    again = restore_web(BASE, web / "data", get=remote)
    assert again["ok"] is True and "skipped" in again and remote.calls == []

    forced = restore_web(BASE, web / "data", force=True, get=remote)  # --force replaces local files
    assert forced["ok"] is True and forced["restored"] == 13 and forced["kept"] == 0
    assert json.loads(mine.read_text()) == {"site": "goats_eye", "season": "2026-2027"}


def test_restore_web_never_writes_a_partial_file_or_the_index_after_a_failure_and_resumes(tmp_path):
    from snowagent.ops.deploy import restore_web

    remote = _Remote(tmp_path / "remote")
    idx = _site(remote.root)
    idx["sites"][2]["seasons"][0]["public"] = "../../outside.json"  # never fetched or written
    (remote.root / "data" / "sites.json").write_text(json.dumps(idx))
    remote.broken = {"data/simpson/2025-2026.json": ConnectionError("connection reset"),
                     "data/goats_eye/2024-2025.json": b"<html>Page not found</html>"}  # a 200 that is not JSON
    out = tmp_path / "web" / "data"
    res = restore_web(BASE, out, get=remote)
    assert res["ok"] is False and res["sites_json"].startswith("not written") and not (out / "sites.json").exists()
    steps = {f["step"]: f["error"] for f in res["failed_steps"]}
    assert steps["restore:data/simpson/2025-2026.json"] == "ConnectionError: connection reset"
    assert steps["restore:data/goats_eye/2024-2025.json"].startswith("JSONDecodeError")
    assert "is not a data/*.json path" in steps["restore:index"] and len(steps) == 3
    assert not (out / "simpson" / "2025-2026.json").exists() and not (out / "goats_eye" / "2024-2025.json").exists()
    assert res["restored"] == 11 and not list(tmp_path.rglob("*.part")) and not (tmp_path / "outside.json").exists()
    assert all("outside" not in u for u in remote.calls)

    idx["sites"][2]["seasons"][0]["public"] = None  # index fixed
    (remote.root / "data" / "sites.json").write_text(json.dumps(idx))
    remote.broken, remote.calls = {}, []
    res = restore_web(BASE, out, get=remote)  # resumes: only the two missing files are fetched
    assert res["ok"] is True and res["restored"] == 2 and res["kept"] == 11 and (out / "sites.json").exists()
    assert sorted(remote.calls) == sorted(BASE + "/" + p for p in ("data/sites.json", "data/simpson/2025-2026.json",
                                                                   "data/goats_eye/2024-2025.json"))


def test_restore_web_without_the_index_restores_nothing(tmp_path):
    import pytest

    from snowagent.ops.deploy import restore_web

    remote = _Remote(tmp_path / "remote")  # nothing deployed there
    res = restore_web(BASE, tmp_path / "data", get=remote)
    assert res["ok"] is False and res["failed_steps"][0]["step"] == "restore:data/sites.json"
    assert "404" in res["failed_steps"][0]["error"] and remote.calls == [BASE + "/data/sites.json"]
    assert not (tmp_path / "data").exists()
    remote.broken = {"data/sites.json": b'{"label": "x"}'}
    assert "no list of sites" in restore_web(BASE, tmp_path / "data", get=remote)["failed_steps"][0]["error"]
    with pytest.raises(ValueError):
        restore_web("file:///etc", tmp_path / "data", get=remote)


def test_restore_web_cli_uses_the_lock_and_the_run_log(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from snowagent.cli import app
    from snowagent.ops import deploy, update

    monkeypatch.setattr(update, "LOCK_FILE", tmp_path / "update.lock")
    monkeypatch.setattr(update, "RUN_LOG", tmp_path / "runs.jsonl")
    remote = _Remote(tmp_path / "remote", {"data/status.json": RuntimeError("503 Server Error")})
    _site(remote.root)
    monkeypatch.setattr(deploy, "http_get", remote)
    out = tmp_path / "web" / "data"
    args = ["update", "restore-web", "--base-url", BASE, "--out", str(out)]
    r = CliRunner().invoke(app, args)
    assert r.exit_code == 2 and json.loads(r.stdout)["failed_steps"][0]["step"] == "restore:data/status.json"
    remote.broken = {}
    r = CliRunner().invoke(app, args)
    assert r.exit_code == 0 and json.loads(r.stdout)["sites_json"] == "written" and (out / "status.json").exists()
    first, second = (json.loads(x) for x in (tmp_path / "runs.jsonl").read_text().splitlines())
    assert first["command"] == "restore-web" and first["exit_code"] == 2 and first["counts"]["restored"] == 12
    assert second["exit_code"] == 0 and second["counts"] == {"restored": 1, "kept": 12}
    assert not (tmp_path / "update.lock").exists()
