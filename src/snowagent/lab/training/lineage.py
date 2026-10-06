"""Lineage of evolved agents (ADR-066): every genome's parents, operator and changed genes, read from the committed
rounds of every training run (``rounds/rNN/population.json``), and its ancestry back to the initial genomes."""

from __future__ import annotations

import json
from pathlib import Path

from snowagent.lab.storage.paths import LabPaths
from snowagent.lab.training.loop import training_root


def lineage_index(paths: LabPaths, run_id: str | None = None) -> dict[str, dict]:
    """genome hash -> its birth record (the first round it appeared in, across runs), with run and round."""
    root = training_root(paths)
    out: dict[str, dict] = {}
    runs = [root / run_id] if run_id else (sorted(root.iterdir()) if root.is_dir() else [])
    for run in runs:
        rounds = sorted((run / "rounds").glob("r[0-9][0-9]*")) if (run / "rounds").is_dir() else []
        for rd in rounds:
            f = rd / "population.json"
            if not f.is_file():
                continue
            for p in json.loads(f.read_text()):
                rec = p["lineage"]
                h = rec["genome_hash"]
                if h not in out:
                    out[h] = rec | {"run_id": run.name, "genome": p["genome"]}
    return out


def resolve(index: dict[str, dict], ref: str) -> str:
    """A genome hash, a unique hash prefix, an agent id (``<family>-<10 hex>``) or a unique label."""
    ref = ref.strip()
    if ref in index:
        return ref
    labels = [h for h, r in index.items() if r.get("label") == ref]
    if len(labels) == 1:
        return labels[0]
    if "-" in ref:
        ref = ref.rsplit("-", 1)[1]
    hits = [h for h in index if h.startswith(ref)] if ref else []
    if len(hits) == 1:
        return hits[0]
    raise KeyError(f"{'no' if not hits else 'several'} genomes match {ref!r}")


def ancestry(index: dict[str, dict], genome_hash: str, max_depth: int = 200) -> list[dict]:
    """The genome and its ancestors, depth first (each record gains ``depth``); unknown parents are listed as such."""
    out: list[dict] = []
    seen: set[str] = set()

    def walk(h: str, depth: int) -> None:
        if depth > max_depth:
            return
        rec = index.get(h)
        if rec is None:
            out.append({"genome_hash": h, "depth": depth, "unknown": True})
            return
        out.append(rec | {"depth": depth, "repeat": h in seen})
        if h in seen:
            return
        seen.add(h)
        for p in rec.get("parents", []):
            walk(p, depth + 1)

    walk(genome_hash, 0)
    return out


def _fmt(v) -> str:
    return f"{v:.4g}" if isinstance(v, float) else str(v)


def format_ancestry(chain: list[dict]) -> list[str]:
    lines = []
    for rec in chain:
        pad = "  " * rec["depth"] + ("└ " if rec["depth"] else "")
        if rec.get("unknown"):
            lines.append(f"{pad}{rec['genome_hash'][:10]} (not in any committed round)")
            continue
        head = (f"{pad}{rec['agent_id']} {rec['label']}  round {rec['round']} of {rec['run_id']}, "
                f"{rec['operator']}")
        if rec["parents"]:
            head += " of " + " x ".join(rec.get("parent_agent_ids") or [p[:10] for p in rec["parents"]])
        if rec.get("repeat"):
            lines.append(head + " (see above)")
            continue
        lines.append(head)
        changed = rec.get("changed_genes") if rec["parents"] else rec.get("changed_vs_default")
        what = "vs first parent" if rec["parents"] else "vs family default"
        if changed:
            items = ", ".join(f"{g} {_fmt(a)} -> {_fmt(b)}" + (" (2nd parent)" if g in rec.get(
                "from_second_parent", []) else "") for g, (a, b) in changed.items())
            lines.append(f"{'  ' * rec['depth']}    changed {what}: {items}")
        elif not rec["parents"]:
            lines.append(f"{'  ' * rec['depth']}    the family default")
    return lines


def lineage_for(paths: LabPaths, ref: str, run_id: str | None = None) -> tuple[dict, list[dict]]:
    index = lineage_index(paths, run_id)
    h = resolve(index, ref)
    return index[h], ancestry(index, h)


def lineage_paths(paths: LabPaths) -> list[Path]:
    root = training_root(paths)
    return sorted(root.glob("*/rounds/r*/population.json")) if root.is_dir() else []
