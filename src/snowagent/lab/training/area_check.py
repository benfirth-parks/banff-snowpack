"""Out-of-area check during training (ADR-117; owner 2026-11-18: "Score only, never train").

After each round's selection, the agents tested on the locked winters are also scored on the cases of a second lab
whose plot lies outside the training area: the Jasper test set (``config/lab_jasper.yaml``, data root
``data/lab_jasper`` beside the run's own data root). Like the locked winters (ADR-083) these scores are recorded and
shown, never used to choose an agent or to stop a run, and the analogue library stays the training run's (an agent
knows Banff's pits only). The scoring weights are the run's: the Jasper config extends Banff's, which the loop checks.

A run stores the area's config, data root and case ids in its plan, so a resume scores the same cases (cases built
after the run started are left out; a case gone missing is skipped and counted).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from snowagent.lab.competition.runner import select_cases
from snowagent.lab.settings import LabConfig, load_lab_config
from snowagent.lab.storage.paths import LabPaths
from snowagent.lab.training.evaluate import CaseRef, case_refs

AREAS = {"jasper": {"name": "Parker Ridge (Jasper)", "config": "lab_jasper.yaml", "data_root": "lab_jasper"}}
CASE_SET = "all"
NOTE = ("pits from outside the training area, scored after selection: never used to choose agents or to stop the "
        "run (ADR-117)")


@dataclass
class Area:
    key: str
    name: str
    config_path: Path
    paths: LabPaths
    cfg: LabConfig
    refs: list[CaseRef]
    missing: int = 0  # case ids of the plan no longer found (a resume after a rebuild)

    @property
    def seasons(self) -> list[str]:
        return sorted({r.manifest.season for r in self.refs})


def resolve(key: str, data_root: Path, config: Path | None = None, area_root: Path | None = None
            ) -> tuple[Path, Path]:
    """The area lab's config (``config/<file>`` of the checkout holding ``data_root``) and data root (beside
    ``data_root``: ``data/lab`` -> ``data/lab_jasper``), unless given."""
    if key not in AREAS:
        raise ValueError(f"unknown area check {key!r}: {', '.join(AREAS)}")
    spec = AREAS[key]
    root = Path(data_root).resolve()
    if area_root is None:
        area_root = root.parent / spec["data_root"]
    if config is None:
        config = next((d / "config" / spec["config"] for d in [root, *root.parents]
                       if (d / "config" / spec["config"]).is_file()), Path("config") / spec["config"])
    return Path(config), Path(area_root)


def load_area(key: str, config: Path, data_root: Path, case_ids: list[str] | None = None) -> Area:
    cfg = load_lab_config(config)
    paths = LabPaths(Path(data_root))
    cases = select_cases(paths, CASE_SET, case_ids=case_ids) if paths.benchmark.is_dir() else []
    refs = case_refs(cases)
    missing = len(set(case_ids or []) - {r.case_id for r in refs}) if case_ids is not None else 0
    return Area(key, AREAS[key]["name"], Path(config), paths, cfg, refs, missing)


def status(key: str, data_root: Path, config: Path | None = None) -> dict:
    """For the app: the area lab's case count and seasons ({"cases": 0} when it is not prepared yet)."""
    try:
        cpath, root = resolve(key, data_root, config)
        if not cpath.is_file():
            return {"cases": 0, "seasons": [], "config": str(cpath), "data_root": str(root)}
        a = load_area(key, cpath, root)
    except (OSError, ValueError) as exc:
        return {"cases": 0, "seasons": [], "error": f"{type(exc).__name__}: {exc}"[:300]}
    return {"cases": len(a.refs), "seasons": a.seasons, "name": a.name, "config": str(a.config_path),
            "data_root": str(a.paths.root)}


def plan_entry(area: Area) -> dict:
    return {"lab": area.key, "name": area.name, "config": str(Path(area.config_path).resolve()),
            "data_root": str(Path(area.paths.root).resolve()), "config_hash": area.cfg.config_hash(),
            "case_set": CASE_SET, "case_ids": sorted(r.case_id for r in area.refs), "seasons": area.seasons,
            "note": NOTE}
