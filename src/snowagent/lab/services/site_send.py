"""Send to site (ADR-084): put an evolved agent on the public site as an experimental option, or take it off.

The lab app writes one small JSON file per agent (its name, genome and where it came from) to the ``site-agents``
branch of the repository and pushes it with the computer's own GitHub sign-in; the next daily update validates and
runs it (``snowagent.ops.site_agents``). Plumbing only: a temporary index, ``commit-tree`` and a plain (never forced)
push, so the checkout, its branch and its files are never touched. No credentials are read or stored here."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path

from snowagent.lab.services.names import nickname
from snowagent.lab.storage.paths import LabPaths
from snowagent.lab.training.loop import load_round, training_root
from snowagent.ops.site_agents import BRANCH, FOLDER, MAX_AGENTS, agent_file, branch_files, parse_agents

REF = f"refs/remotes/origin/{BRANCH}"


class SendError(RuntimeError):
    """A send or remove that did not happen, with a message for the app."""


def _env(index: Path | None = None) -> dict:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}  # a missing sign-in fails at once instead of waiting for input
    if index is not None:
        env["GIT_INDEX_FILE"] = str(index)
    return env


def _git(repo: Path, *args: str, index: Path | None = None, stdin: bytes | None = None, timeout: int = 120) -> str:
    r = subprocess.run(["git", *args], cwd=repo, input=stdin, capture_output=True, env=_env(index), timeout=timeout)
    if r.returncode != 0:
        raise SendError(f"git {args[0]} failed: {r.stderr.decode(errors='replace').strip()[:400]}")
    return r.stdout.decode().strip()


def agent_record(paths: LabPaths, run_id: str, r: int, agent_id: str, now=None) -> dict:
    """The site file for one agent of a committed round: its TV name, genome and its scores (training winters and,
    when the run had them, the locked test winters)."""
    name, genome, source = agent_parts(paths, run_id, r, agent_id)
    return agent_file(name, genome, source, now)


def agent_parts(paths: LabPaths, run_id: str, r: int, agent_id: str) -> tuple[str, dict, dict]:
    """(TV name, genome, source) of one agent of a committed round."""
    run_dir = training_root(paths) / run_id
    rd = load_round(run_dir, r)
    row = next((x for x in rd["leaderboard"]["ranked"] if x["agent_id"] == agent_id), None)
    if row is None:
        raise SendError(f"agent {agent_id} is not on round {r}'s leaderboard")
    genome = json.loads((run_dir / "genomes" / f"{row['genome_hash']}.json").read_text())
    locked = (rd["round"].get("locked_test") or {})
    by = {a["agent_id"]: a for a in locked.get("agents", [])}
    inc = locked.get("incumbent") or {}
    source = {"run_id": run_id, "round": r, "rank": row["rank"], "composite": row.get("composite"),
              "locked_composite": (by.get(agent_id) or {}).get("composite"),
              "locked_seasons": list(locked.get("seasons") or []),
              "incumbent_locked_composite": inc.get("composite")}
    return nickname(row["genome_hash"], row["label"]), genome, source


def fetch(repo: Path) -> None:
    """Bring the remote branch's latest state into ``REF`` (it may not exist yet: no agent was ever sent)."""
    if _remote_has_branch(repo):
        _git(repo, "fetch", "--quiet", "origin", f"+refs/heads/{BRANCH}:{REF}")
    else:
        _git_ok(repo, "update-ref", "-d", REF)


def _remote_has_branch(repo: Path) -> bool:
    return bool(_git(repo, "ls-remote", "--heads", "origin", BRANCH, timeout=60))


def on_site(repo: Path) -> list[dict]:
    """The agents on the branch now (the ones the next daily update will run), oldest first."""
    fetch(repo)
    agents, _ = parse_agents(branch_files(repo, ref=REF, fetch=False))
    return [{"id": aid, "name": a.name, "added_utc": a.added_utc, **a.source.model_dump()} for aid, a in agents]


def _commit(repo: Path, change, message: str) -> str:
    """Apply ``change(index)`` to the branch's tree in a temporary index, commit and push; returns the commit."""
    fetch(repo)
    parent = None
    if subprocess.run(["git", "rev-parse", "--verify", "--quiet", REF], cwd=repo, capture_output=True).returncode == 0:
        parent = _git(repo, "rev-parse", REF)
    with tempfile.TemporaryDirectory() as tmp:
        index = Path(tmp) / "index"
        if parent:
            _git(repo, "read-tree", parent, index=index)
        else:
            _git(repo, "read-tree", "--empty", index=index)
        change(index)
        tree = _git(repo, "write-tree", index=index)
    if parent and tree == _git(repo, "rev-parse", f"{parent}^{{tree}}"):
        return parent  # nothing to change
    ident = {} if _git_ok(repo, "config", "user.email") else {
        "GIT_AUTHOR_NAME": "Snowpack Agent Lab", "GIT_AUTHOR_EMAIL": "lab@localhost",
        "GIT_COMMITTER_NAME": "Snowpack Agent Lab", "GIT_COMMITTER_EMAIL": "lab@localhost"}
    args = ["commit-tree", tree, "-m", message] + (["-p", parent] if parent else [])
    r = subprocess.run(["git", *args], cwd=repo, capture_output=True, env={**_env(), **ident}, timeout=60)
    if r.returncode != 0:
        raise SendError(f"git commit-tree failed: {r.stderr.decode(errors='replace').strip()[:400]}")
    commit = r.stdout.decode().strip()
    try:
        _git(repo, "push", "--quiet", "origin", f"{commit}:refs/heads/{BRANCH}")
    except SendError as exc:
        raise SendError(f"{exc}\n\nIf this says the sign-in or authentication failed, sign in to GitHub once in "
                        "Terminal (see the guide, 'Send to site'), then try again.") from exc
    _git(repo, "update-ref", REF, commit)
    return commit


def _git_ok(repo: Path, *args: str) -> bool:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True).returncode == 0


def send(repo: Path, record: dict) -> str:
    """Add (or replace) an agent on the site branch; refused when ``MAX_AGENTS`` other agents are there already."""
    from snowagent.ops.site_agents import SiteAgent

    aid = SiteAgent.model_validate(record).checked_genome().agent_id
    current = on_site(repo)
    if aid not in {a["id"] for a in current} and len(current) >= MAX_AGENTS:
        raise SendError(f"The site already has {MAX_AGENTS} agents. Remove one first.")
    blob = _git(repo, "hash-object", "-w", "--stdin", stdin=(json.dumps(record, indent=1) + "\n").encode())

    def add(index: Path) -> None:
        _git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{FOLDER}/{aid}.json", index=index)

    return _commit(repo, add, f"Site agent: add {record['name']} ({aid})")


def remove(repo: Path, agent_id: str) -> str:
    """Take an agent off the site branch (the next daily update stops showing it)."""
    if agent_id not in {a["id"] for a in on_site(repo)}:
        raise SendError("That agent is not on the site.")

    def drop(index: Path) -> None:
        _git(repo, "update-index", "--force-remove", f"{FOLDER}/{agent_id}.json", index=index)

    return _commit(repo, drop, f"Site agent: remove {agent_id}")
