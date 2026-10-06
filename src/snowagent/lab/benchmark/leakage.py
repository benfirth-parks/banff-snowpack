"""Leakage checks on a built case package (build guide "Leakage checks"; ADR-059). Run by the builder on every case
before it is put in place (a failed check fails the build) and by ``snowagent lab check-leakage``.

Visible times are hours relative to as-of (``*_rel_h``), so "source_recorded_at <= as_of" reads
``available_rel_h <= 0``. A case fails if:
- the manifest is invalid (missing or malformed hashes, naive or inconsistent timestamps), or a visible/hidden file is
  missing, unlisted or differs from its hash;
- any visible record was available after as-of (station hours, ERA5-filled values within their 5-day latency, pits,
  tests), or observed after as-of;
- the target pit, or any duplicate copy of it, appears anywhere in the visible files (keys, ids, free text);
- a forecast was issued or available after as-of, or reaches past the valid time; measured weather after as-of
  appears other than as the labelled stand-in (issued at as-of, snowpack variables withheld);
- the visible files are not anonymous: an identifier or absolute-time field, a datetime column, a date-like or
  known-id string, pit coordinates (owner, 2026-10-05: agents must not memorize snowpacks);
- in a leave-one-season-out training case, any visible record from the held-out season;
- the visible files do not load as a ``VisibleBenchmarkCase`` (the type-level guard).

Sealed-test hidden files are checked for presence and a recorded hash only: their content is not read back.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
from pydantic import ValidationError

from snowagent.lab.benchmark import package as pkg
from snowagent.lab.benchmark.loader import CaseIntegrityError, load_visible_case, read_manifest, verify_hashes
from snowagent.lab.schemas.benchmark import CaseManifest, ForecastSource, Split, SplitMode
from snowagent.lab.schemas.weather import WEATHER_VARIABLES
from snowagent.lab.storage.tables import read_table

TOL_H = 1e-6
DATE_LIKE = re.compile(r"(19|20)\d{2}[-/_.]?(0[1-9]|1[0-2])[-/_.]?(0[1-9]|[12]\d|3[01])")


class LeakageError(RuntimeError):
    """A case failed a leakage check; the case is not written."""

    def __init__(self, report: LeakageReport) -> None:
        self.report = report
        super().__init__(f"case {report.case_id} failed leakage checks: "
                         + "; ".join(f"{c['name']}: {c['detail']}" for c in report.failed))


@dataclass
class LeakageReport:
    case_id: str
    checks: list[dict] = field(default_factory=list)
    checked_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))

    def add(self, name: str, problems: list[str]) -> None:
        more = f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""
        self.checks.append({"name": name, "ok": not problems, "detail": "; ".join(problems[:5]) + more})

    @property
    def failed(self) -> list[dict]:
        return [c for c in self.checks if not c["ok"]]

    @property
    def status(self) -> str:
        return "pass" if self.checks and not self.failed else "fail"

    def to_dict(self) -> dict:
        return {"case_id": self.case_id, "status": self.status, "checked_at": self.checked_at, "checks": self.checks}


def _col(df: pd.DataFrame, name: str) -> pd.Series:
    return df[name] if name in df else pd.Series(dtype=object)


def _strings(frames: dict[str, pd.DataFrame], jsons: dict[str, str]) -> list[tuple[str, str]]:
    """(where, text) for every string cell of the visible tables and every visible JSON text."""
    out = []
    for name, df in frames.items():
        for col in df.columns:
            if df[col].dtype == object:
                out += [(f"{name}.{col}", s) for s in df[col].dropna().astype(str).unique()]
    out += list(jsons.items())
    return out


def _json_keys(obj, keys: set[str]) -> set[str]:
    if isinstance(obj, dict):
        for k, v in obj.items():
            keys.add(k)
            _json_keys(v, keys)
    elif isinstance(obj, list):
        for v in obj:
            _json_keys(v, keys)
    return keys


def check_case(case_dir: Path, era5_latency_h: float = 120.0, withheld: list[str] | None = None,
               expected_name: str | None = None) -> LeakageReport:
    """Run every check on the package at ``case_dir`` and return the report (never raises for a failed check).
    ``expected_name``: the case id the directory is named for (the builder checks a temporary directory)."""
    case_dir = Path(case_dir)
    report = LeakageReport(case_id=case_dir.name)
    try:
        m: CaseManifest = read_manifest(case_dir)
    except (OSError, ValidationError, ValueError) as exc:
        report.add("manifest_valid", [f"manifest invalid: {str(exc).splitlines()[0][:300]}"])
        return report
    report.case_id = m.case_id
    name = expected_name or case_dir.name
    report.add("manifest_valid", [] if m.case_id == name and m.case_key else
               [f"directory {name} != {m.case_id}" if m.case_id != name else "manifest has no case_key"])
    t = pd.Timestamp(m.as_of_time)

    problems = []
    for which, names, hashes in ((pkg.VISIBLE, pkg.VISIBLE_FILES, m.visible_hashes),
                                 (pkg.HIDDEN, pkg.HIDDEN_FILES, m.hidden_hashes)):
        d = case_dir / which
        present = {p.name for p in d.iterdir()} if d.is_dir() else set()
        problems += [f"{which}/{n} not hashed in the manifest" for n in sorted(present - set(hashes))]
        problems += [f"{which}/{n} expected but missing from the manifest" for n in names if n not in hashes]
        problems += [f"{which}/{n} listed but missing" for n in sorted(set(hashes) - present)]
    if not problems:
        try:
            verify_hashes(case_dir, m, pkg.VISIBLE)
            if m.split != Split.sealed_test:  # sealed truth is not read back, not even to hash it
                verify_hashes(case_dir, m, pkg.HIDDEN)
        except CaseIntegrityError as exc:
            problems.append(str(exc))
    report.add("hashes_complete_and_match", problems)
    if problems:
        return report

    vis = case_dir / pkg.VISIBLE
    frames = {n: read_table(vis / n) for n in pkg.VISIBLE_FILES if n.endswith(".parquet")}
    jsons = {n: (vis / n).read_text() for n in pkg.VISIBLE_FILES if n.endswith(".json")}
    head = json.loads(jsons[pkg.CASE])
    wo, wf = frames[pkg.WEATHER_OBSERVED], frames[pkg.WEATHER_FORECASTS]
    pits, obs = frames[pkg.PITS], frames[pkg.OBSERVATIONS]
    runs = json.loads(jsons[pkg.FORECAST_RUNS])
    horizon = m.horizon_hours

    # visible header agrees with the manifest
    doy = t.dayofyear + (t.hour * 60 + t.minute) / 1440.0
    problems = [f"case.json {k} {head.get(k)} != manifest {v}" for k, v in
                (("case_key", m.case_key), ("case_type", m.case_type.value), ("site_code", m.site_code.value),
                 ("forecast_source", m.forecast_source.value if m.forecast_source else None))
                if head.get(k) != v]
    if abs(float(head.get("horizon_hours", -1)) - horizon) > 1e-3 or abs(float(head.get("as_of_day_of_year", -1)) - doy) > 1e-3:
        problems.append("case.json horizon or as-of day of year differs from the manifest")
    report.add("visible_header_matches_manifest", problems)

    # availability: every visible record available at or before as-of
    problems = []
    if len(wo):
        n = int(((wo["available_rel_h"] > TOL_H) | wo["available_rel_h"].isna()).sum())
        problems += [f"{n} observed hours available after as-of"] if n else []
        n = int((wo["t_rel_h"] > TOL_H).sum())
        problems += [f"{n} observed hours after as-of"] if n else []
        if not wo["kind"].isin(["observed", "reanalysis"]).all():
            problems.append("weather_observed holds forecast or stand-in hours")
        for v in WEATHER_VARIABLES:
            era5 = wo[f"{v}_source"].astype(str).str.startswith("era5") & (wo["t_rel_h"] + era5_latency_h > TOL_H)
            if era5.any():
                problems.append(f"{int(era5.sum())} ERA5 {v} values within their {era5_latency_h:g} h latency")
    for label, df in (("pit", pits), ("test", obs)):
        if len(df):
            n = int(((df["available_rel_h"] > TOL_H) | df["available_rel_h"].isna()).sum())
            problems += [f"{n} {label}s available after as-of"] if n else []
    report.add("visible_records_available_at_as_of", problems)

    # target pit and its copies nowhere in the visible files
    ids = {m.target_profile_id, *m.target_copies}
    problems = [f"pit_key {k} maps to the target {v}" for k, v in m.pit_keys.items() if v in ids]
    problems += [f"{where} contains {i}" for where, s in _strings(frames, jsons) for i in ids if i in s]
    report.add("target_profile_not_visible", problems)

    # no pit or test observed after as-of; manifest mapping complete
    problems = []
    for label, df in (("pit", pits), ("test", obs)):
        if len(df):
            n = int((df["t_rel_h"] > TOL_H).sum())
            problems += [f"{n} {label}s observed after as-of"] if n else []
    if set(_col(pits, "pit_key")) != set(m.pit_keys):
        problems.append("visible pit keys differ from the manifest's pit_keys")
    report.add("no_profile_observed_after_as_of", problems)

    # forecasts issued (and available) at or before as-of, up to the valid time; stand-in labelled
    problems = []
    standin = m.forecast_source == ForecastSource.measured_standin
    if len(wf):
        if ((wf["issued_rel_h"] > TOL_H) | wf["issued_rel_h"].isna()).any():
            problems.append("forecast hours issued after as-of")
        if (wf["available_rel_h"] > TOL_H).any():
            problems.append("forecast hours available after as-of")
        if (wf["t_rel_h"] > horizon + TOL_H).any():
            problems.append("forecast hours past the valid time")
        if standin:
            if not (wf["kind"] == "perfect_forecast").all() or (wf["issued_rel_h"].abs() > TOL_H).any():
                problems.append("stand-in hours not labelled perfect_forecast issued at as-of")
            for v in withheld if withheld is not None else (m.forecast_standin or {}).get("withheld_variables", []):
                if wf[v].notna().any():
                    problems.append(f"stand-in holds withheld snowpack variable {v}")
        elif not (wf["kind"] == "forecast").all():
            problems.append("measured hours in a case whose forecast source is not measured_standin")
    problems += [f"forecast run {r['source_id']} issued {r['issued_rel_h']:+.1f} h" for r in runs
                 if r["issued_rel_h"] > TOL_H or r["available_rel_h"] > TOL_H]
    problems += [f"manifest forecast run {r.source_id} issued after as-of" for r in m.forecast_runs
                 if pd.Timestamp(r.issued_at) > t or pd.Timestamp(r.available_at) > t]
    if standin and runs:
        problems.append("a stand-in case lists forecast runs")
    if m.forecast_source == ForecastSource.archived_gfs and not runs:
        problems.append("an archived_gfs case without its run")
    report.add("forecasts_issued_before_as_of", problems)

    # anonymous: no identifier or absolute time, no date-like or known-id string, no pit coordinates
    problems = []
    for n, df in frames.items():
        bad = sorted(set(df.columns) & (pkg.FORBIDDEN_FIELDS | pkg.PIT_COORDINATES))
        problems += [f"{n} has field {c}" for c in bad]
        problems += [f"{n}.{c} is an absolute time" for c in df.columns if pd.api.types.is_datetime64_any_dtype(df[c])]
    for n, text in jsons.items():
        keys = _json_keys(json.loads(text), set()) & pkg.FORBIDDEN_FIELDS
        if n != pkg.SITE:
            keys |= _json_keys(json.loads(text), set()) & pkg.PIT_COORDINATES
        problems += [f"{n} has field {k}" for k in sorted(keys)]
    known = set(m.pit_keys.values()) | set(m.visible_profile_ids) | {m.case_id}
    for where, s in _strings(frames, jsons):
        if DATE_LIKE.search(s):
            problems.append(f"{where} holds a date-like string")
        problems += [f"{where} contains id {i}" for i in known if i in s]
    report.add("visible_package_anonymous", problems)

    # leave-one-season-out: a training case shows nothing from the held-out season
    problems = []
    if m.split_mode == SplitMode.loso and m.split == Split.training and m.holdout_season:
        off = int(m.holdout_season[:4]) - int(m.season[:4])
        if len(pits) and (pits["season_offset"] == off).any():
            problems.append(f"{int((pits['season_offset'] == off).sum())} pits of the held-out season "
                            f"{m.holdout_season} visible")
        if m.season == m.holdout_season:
            problems.append("a training case in the held-out season")
    report.add("loso_holdout_not_visible", problems)

    try:
        load_visible_case(case_dir)
        problems = []
    except (ValidationError, ValueError, KeyError) as exc:
        problems = [str(exc).splitlines()[0][:300]]
    report.add("visible_case_validates", problems)
    return report
