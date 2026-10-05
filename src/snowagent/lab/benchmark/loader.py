"""Case loaders. Two doors, never mixed:

- ``load_visible_case``: the agent-facing loader. It opens ``visible/`` only (not the manifest, which names the
  target pit, and never ``hidden/``) and returns a ``VisibleBenchmarkCase``: anonymous, no path, no field for the
  target or any hidden object.
- ``load_hidden_truth``: the evaluator's loader. A sealed-test case's truth is refused unless the caller passes the
  typed confirmation ``UNSEAL <case_id>`` (CLI: ``snowagent lab case-truth --unseal``); hashes are verified.

Plus listing and read-only access to manifests and visible tables for the UI and CLI.
"""

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from snowagent.lab.benchmark import package as pkg
from snowagent.lab.schemas.benchmark import (
    CaseManifest,
    HiddenTruth,
    Split,
    VisibleBenchmarkCase,
    VisibleForecastRun,
    VisibleLayer,
    VisibleObservation,
    VisiblePit,
    VisibleWeatherHour,
)
from snowagent.lab.schemas.common import QualityFlag
from snowagent.lab.schemas.profile import ProfileTemperature, SnowProfile
from snowagent.lab.schemas.site import ReferenceScenario, Site
from snowagent.lab.schemas.weather import WEATHER_VARIABLES
from snowagent.lab.storage.paths import LabPaths
from snowagent.lab.storage.provenance import sha256_file
from snowagent.lab.storage.tables import read_table


class SealedTruthError(PermissionError):
    """A sealed-test case's hidden truth was requested without the explicit unseal confirmation."""


class CaseIntegrityError(ValueError):
    """A case file is missing or does not match the hash in its manifest."""


# --------------------------------------------------------------------------------------------- listing


def case_dirs(paths: LabPaths, case_set: str | None = None, split: str | None = None) -> list[Path]:
    """Case directories ``benchmark/<case_set>/<split>/<case_id>`` (all sets and splits when not given)."""
    root = paths.benchmark
    sets = [case_set] if case_set else case_sets(paths)
    out: list[Path] = []
    for cs in sets:
        for sp in [split] if split else [s.value for s in Split]:
            d = root / cs / sp
            if d.is_dir():
                out += sorted(p for p in d.iterdir() if (p / pkg.MANIFEST).is_file())
    return out


def case_sets(paths: LabPaths) -> list[str]:
    """Case sets with at least one built case or a build report (the import creates empty directories)."""
    root = paths.benchmark
    if not root.is_dir():
        return []
    return sorted(d.name for d in root.iterdir() if d.is_dir() and not d.name.startswith(".")
                  and ((d / "build_report.json").is_file() or any(d.glob(f"*/*/{pkg.MANIFEST}"))))


def find_case(paths: LabPaths, case_id: str, case_set: str | None = None) -> list[Path]:
    """Every built package of ``case_id`` (one per case set it was built in)."""
    return [d for d in case_dirs(paths, case_set) if d.name == case_id]


def read_manifest(case_dir: Path) -> CaseManifest:
    """The evaluator-side manifest (names the target pit: never passed to an agent)."""
    return CaseManifest.model_validate_json((Path(case_dir) / pkg.MANIFEST).read_text())


def read_checks(case_dir: Path) -> dict:
    f = Path(case_dir) / pkg.CHECKS
    return json.loads(f.read_text()) if f.is_file() else {"status": "not_checked", "checks": []}


def read_visible_table(case_dir: Path, name: str) -> pd.DataFrame:
    """One visible table as a frame (UI, leakage checks). Hidden files cannot be read through here."""
    if name not in pkg.VISIBLE_FILES or not name.endswith(".parquet"):
        raise ValueError(f"{name} is not a visible table")
    return read_table(Path(case_dir) / pkg.VISIBLE / name)


def read_visible_json(case_dir: Path, name: str):
    if name not in pkg.VISIBLE_FILES or not name.endswith(".json"):
        raise ValueError(f"{name} is not a visible JSON file")
    return json.loads((Path(case_dir) / pkg.VISIBLE / name).read_text())


# --------------------------------------------------------------------------------------------- rows -> records


def _clean(v):
    if v is None or v is pd.NaT:
        return None
    if isinstance(v, float) and np.isnan(v):
        return None
    return v.item() if hasattr(v, "item") else v


def hour_from_row(row: dict) -> VisibleWeatherHour:
    d = {k: _clean(row.get(k)) for k in ("t_rel_h", "day_of_year", "kind", "source_id", "issued_rel_h",
                                         "available_rel_h", "availability_assumption", "quality_flag")}
    sources = {v: row[f"{v}_source"] for v in WEATHER_VARIABLES if _clean(row.get(f"{v}_source")) is not None}
    qc = {v: row[f"{v}_qc"] for v in WEATHER_VARIABLES if row.get(f"{v}_qc") not in (None, QualityFlag.missing.value)}
    return VisibleWeatherHour(**d, sources=sources, qc=qc, **{v: _clean(row.get(v)) for v in WEATHER_VARIABLES})


def pits_from_tables(pits: pd.DataFrame, layers: pd.DataFrame) -> list[VisiblePit]:
    by: dict[str, list[VisibleLayer]] = {}
    for r in layers.sort_values(["pit_key", "layer_index"]).to_dict("records") if len(layers) else []:
        d = {k: _clean(v) for k, v in r.items() if k not in ("pit_key", "layer_index") and not k.endswith("_json")}
        by.setdefault(r["pit_key"], []).append(VisibleLayer(
            **d, concern_basis=json.loads(r["concern_basis_json"] or "[]"),
            uncertain_fields=json.loads(r["uncertain_fields_json"] or "[]")))
    out = []
    for r in pits.to_dict("records"):
        d = {k: _clean(v) for k, v in r.items() if not k.endswith("_json")}
        temps = [ProfileTemperature(**t) for t in json.loads(r.get("temperatures_json") or "[]")]
        out.append(VisiblePit(**d, temperatures=temps, layers=by.get(r["pit_key"], [])))
    return out


# --------------------------------------------------------------------------------------------- the two doors


def load_visible_case(case_dir: Path) -> VisibleBenchmarkCase:
    """The agent-facing loader: reads ``visible/`` only and returns the validated, anonymous case."""
    vis = Path(case_dir) / pkg.VISIBLE
    head = json.loads((vis / pkg.CASE).read_text())
    return VisibleBenchmarkCase(
        case_key=head["case_key"], case_type=head["case_type"], site_code=head["site_code"],
        as_of_day_of_year=head["as_of_day_of_year"], horizon_hours=head["horizon_hours"],
        forecast_source=head["forecast_source"],
        scenario=ReferenceScenario.model_validate_json((vis / pkg.TERRAIN).read_text()),
        site=Site.model_validate_json((vis / pkg.SITE).read_text()),
        weather_observed=[hour_from_row(r) for r in read_table(vis / pkg.WEATHER_OBSERVED).to_dict("records")],
        weather_forecasts=[hour_from_row(r) for r in read_table(vis / pkg.WEATHER_FORECASTS).to_dict("records")],
        forecast_runs=[VisibleForecastRun(**r) for r in json.loads((vis / pkg.FORECAST_RUNS).read_text())],
        permitted_pits=pits_from_tables(read_table(vis / pkg.PITS), read_table(vis / pkg.LAYERS)),
        permitted_observations=[
            VisibleObservation(**{k: _clean(v) for k, v in r.items() if k != "payload_json"},
                               payload=json.loads(r["payload_json"] or "{}"))
            for r in read_table(vis / pkg.OBSERVATIONS).to_dict("records")],
        availability_warnings=list(head.get("availability_warnings") or []))


def verify_hashes(case_dir: Path, manifest: CaseManifest, which: str) -> None:
    """Raise ``CaseIntegrityError`` when a ``visible`` or ``hidden`` file is missing or differs from its hash."""
    hashes = manifest.visible_hashes if which == pkg.VISIBLE else manifest.hidden_hashes
    for name, h in hashes.items():
        f = Path(case_dir) / which / name
        if not f.is_file():
            raise CaseIntegrityError(f"{manifest.case_id}: {which}/{name} is missing")
        if sha256_file(f) != h:
            raise CaseIntegrityError(f"{manifest.case_id}: {which}/{name} differs from its manifest hash")


def load_hidden_truth(case_dir: Path, unseal: str | None = None) -> HiddenTruth:
    """The evaluator's loader. A sealed-test case needs ``unseal == "UNSEAL <case_id>"`` (typed by a person);
    nothing in the benchmark build, the UI or the evolution loop passes it."""
    manifest = read_manifest(case_dir)
    if manifest.split == Split.sealed_test and unseal != pkg.unseal_phrase(manifest.case_id):
        raise SealedTruthError(f"{manifest.case_id} is a sealed-test case: its hidden truth is read only with an "
                               f"explicit unseal (type '{pkg.unseal_phrase(manifest.case_id)}')")
    verify_hashes(case_dir, manifest, pkg.HIDDEN)
    hid = Path(case_dir) / pkg.HIDDEN
    return HiddenTruth(case_id=manifest.case_id,
                       truth_profile=SnowProfile.model_validate_json((hid / pkg.TRUTH_PROFILE).read_text()),
                       verification=json.loads((hid / pkg.VERIFICATION).read_text()))
