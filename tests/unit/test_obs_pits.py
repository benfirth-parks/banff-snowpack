"""Pit files stay out of the public repository and are kept or restored outside git (ADR-165)."""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from snowagent.obs import pits

REPO = Path(__file__).resolve().parents[2]


def _git(root: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],
                          check=True, capture_output=True, text=True).stdout


def test_no_pit_file_is_tracked():
    if not (REPO / ".git").exists():
        pytest.skip("not a git checkout")
    tracked = _git(REPO, "ls-files", "--", "profiles/").split()
    assert tracked == [], "original pit files print observer names: keep them out of git (ADR-165)"
    assert "/profiles/" in (REPO / ".gitignore").read_text().splitlines()


def _pit(root: Path, name: str, data: bytes) -> None:
    p = root / "profiles" / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


def test_save_then_restore_is_byte_exact(tmp_path):
    a, b, store = tmp_path / "a", tmp_path / "b", tmp_path / "store"
    store.mkdir()
    _pit(a, "2024-2025/Bow Summit/x.jpg", b"\xff\xd8 image bytes")
    _pit(a, "2024-2025/Bow Summit/y.pro", b"Observer=AB\n")
    first = pits.save(a, store, say=lambda m: None)
    assert first["saved"] == 2 and Path(first["archive"]).name == "profiles.tar"
    assert pits.save(a, store, say=lambda m: None)["saved"] == 0  # nothing new, no archive
    _pit(a, "2025-2026/Simpson/z.pdf", b"%PDF")
    second = pits.save(a, store, now=datetime(2026, 11, 27, tzinfo=UTC), say=lambda m: None)
    assert second["files"] == ["profiles/2025-2026/Simpson/z.pdf"]
    assert Path(second["archive"]).name == "new-20261127T000000Z.tar"

    _pit(b, "2024-2025/Bow Summit/y.pro", b"kept as it is")
    rep = pits.restore(b, store, say=lambda m: None)
    assert rep["added"] == 2 and rep["files"] == 3
    assert (b / "profiles/2024-2025/Bow Summit/x.jpg").read_bytes() == b"\xff\xd8 image bytes"
    assert (b / "profiles/2024-2025/Bow Summit/y.pro").read_bytes() == b"kept as it is"  # never overwritten


def _repo_after_removal(tmp_path: Path) -> tuple[Path, str]:
    """A clone that tracked one pit file, then took profiles/ out of git (as a fast-forward would leave it)."""
    r = tmp_path / "repo"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _pit(r, "2020-2021/Goat's Eye/p.jpg", b"pit")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "pits")
    old = _git(r, "rev-parse", "HEAD").strip()
    (r / ".gitignore").write_text("/profiles/\n")
    _git(r, "rm", "-r", "-q", "--cached", "profiles")
    _git(r, "add", ".gitignore")
    _git(r, "commit", "-q", "-m", "untrack pits")
    (r / "profiles/2020-2021/Goat's Eye/p.jpg").unlink()
    return r, old


def test_keep_pits_after_an_update_removed_them(tmp_path):
    r, old = _repo_after_removal(tmp_path)
    assert pits.last_tracked_commit(r) is not None
    pits._KEPT.discard(str(r.resolve()))
    assert pits.keep_pits(r, say=lambda m: None) == 1
    assert (r / "profiles/2020-2021/Goat's Eye/p.jpg").read_bytes() == b"pit"
    assert _git(r, "status", "--porcelain") == ""  # back on disk, untracked and ignored
    assert pits.keep_pits(r, say=lambda m: None) == 0  # once per process


def test_restore_falls_back_to_git_history(tmp_path):
    r, _ = _repo_after_removal(tmp_path)
    rep = pits.restore(r, tmp_path / "no-store", say=lambda m: None)
    assert rep["added"] == 1 and rep["source"].startswith("git history")


def test_restore_reports_nothing_found(tmp_path):
    rep = pits.restore(tmp_path, tmp_path / "no-store", say=lambda m: None)
    assert rep == {"source": "none found", "added": 0, "files": 0}


def test_keep_pits_leaves_a_fresh_clone_alone(tmp_path):
    r, _ = _repo_after_removal(tmp_path)
    fresh = tmp_path / "fresh"
    _git(tmp_path, "clone", "-q", str(r), str(fresh))
    pits._KEPT.discard(str(fresh.resolve()))
    assert pits.keep_pits(fresh, say=lambda m: None) == 0  # CI and new containers: no 237 MB from history
    assert not (fresh / "profiles").exists()


def test_every_command_keeps_the_pits_first(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from snowagent import cli

    seen = []
    monkeypatch.setattr(pits, "keep_pits", lambda root, say=print: seen.append(root) or 0)
    res = CliRunner().invoke(cli.app, ["obs", "pits-save", "--store", str(tmp_path / "none")])
    assert res.exit_code == 0 and seen == [REPO]
