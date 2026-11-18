"""Rules distilled from forecaster notes, and the one bounded way they may touch a forecast (ADR-115).

A rule never adds, removes or reshapes a layer (CLAUDE.md principle 1). It may only multiply SNOWPACK's probability
that a weak layer or crust is present, by a factor between ``MIN_FACTOR`` and ``MAX_FACTOR``, for layers of the named
classes in one depth zone at the named plots. Every rule starts as ``proposed``; only Ben's approval makes it usable,
and an approved rule still changes nothing on the site until it has passed the 945-pit check, the locked winters and
the blind test, like any other model change.

Leakage: a rule carries ``usable_from``, the receive time of the newest note it rests on. It may touch a case only if
the case's cut-off is after ``usable_from``, or the case comes from before forecaster notes existed
(``NOTES_ERA_START``), where no note can know the pit.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from snowagent.lab.feedback.notes import feedback_dir

MIN_FACTOR, MAX_FACTOR = 0.5, 1.5
NEAR_SURFACE_M = 0.30
NOTES_ERA_START = datetime(2026, 11, 1, tzinfo=UTC)  # first winter with the feedback box
RULES_FILE = "rules.json"
PLOTS = ("bow_summit", "simpson", "goats_eye", "parker_ridge", "fidelity")


class RuleClass(StrEnum):
    surface_hoar = "surface_hoar"
    facets = "facets"
    depth_hoar = "depth_hoar"
    crust = "crust"


class Zone(StrEnum):
    near_surface = "near_surface"  # middle of the layer within NEAR_SURFACE_M of the snow surface
    buried = "buried"  # deeper than that
    any = "any"


class RuleDraft(BaseModel):
    """What the language model may propose. Bounds are checked again on load."""

    model_config = ConfigDict(extra="forbid")

    statement: str = Field(description="The heuristic in one plain sentence a forecaster would recognise.")
    plots: list[str] = Field(description="Plot ids it applies to; empty means every plot.")
    classes: list[RuleClass] = Field(description="Layer classes whose presence probability it changes.")
    zone: Zone
    factor: float = Field(description=f"Multiplier on presence probability, {MIN_FACTOR} to {MAX_FACTOR}.")
    evidence: list[str] = Field(description="Keys of the notes that support it.")
    reasoning: str = Field(description="Why the notes support this rule, and how much they agree.")


class FeedbackRule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rule_id: str
    statement: str
    plots: list[str] = Field(default_factory=list)
    classes: list[RuleClass]
    zone: Zone = Zone.any
    factor: float
    evidence: list[str]
    usable_from: datetime
    reasoning: str = ""
    status: Literal["proposed", "approved", "rejected"] = "proposed"
    proposed_at: datetime
    decided_at: datetime | None = None

    @field_validator("factor")
    @classmethod
    def _bounded(cls, v: float) -> float:
        if not MIN_FACTOR <= v <= MAX_FACTOR or v == 1.0:
            raise ValueError(f"factor must be in [{MIN_FACTOR}, {MAX_FACTOR}] and not 1.0, got {v}")
        return v

    @field_validator("plots")
    @classmethod
    def _known_plots(cls, v: list[str]) -> list[str]:
        bad = [p for p in v if p not in PLOTS]
        if bad:
            raise ValueError(f"unknown plots {bad}")
        return sorted(set(v))

    @field_validator("classes")
    @classmethod
    def _some_class(cls, v: list[RuleClass]) -> list[RuleClass]:
        if not v:
            raise ValueError("a rule must name at least one layer class")
        return sorted(set(v))

    def usable_at(self, cutoff: datetime) -> bool:
        if cutoff.tzinfo is None:
            cutoff = cutoff.replace(tzinfo=UTC)
        return cutoff < NOTES_ERA_START or cutoff > self.usable_from

    def matches(self, plot: str, critical: str, mid_depth_m: float) -> bool:
        if self.plots and plot not in self.plots:
            return False
        if critical not in {c.value for c in self.classes}:
            return False
        near = mid_depth_m <= NEAR_SURFACE_M
        return self.zone == Zone.any or (self.zone == Zone.near_surface) == near


def rule_id(statement: str, evidence: list[str]) -> str:
    return "R-" + hashlib.sha256(json.dumps([statement, sorted(evidence)]).encode()).hexdigest()[:8]


def from_draft(d: RuleDraft, note_times: dict[str, datetime], now: datetime) -> FeedbackRule:
    """A proposed rule from a draft. Evidence must name notes we hold; ``usable_from`` is the newest of them."""
    known = [k for k in d.evidence if k in note_times]
    if not known:
        raise ValueError(f"rule {d.statement!r} cites no known note")
    return FeedbackRule(rule_id=rule_id(d.statement, known), statement=d.statement.strip(), plots=d.plots,
                        classes=d.classes, zone=d.zone, factor=round(float(d.factor), 3), evidence=sorted(known),
                        usable_from=max(note_times[k] for k in known), reasoning=d.reasoning.strip(),
                        proposed_at=now)


def load_rules(folder: Path | None = None) -> list[FeedbackRule]:
    path = (folder or feedback_dir()) / RULES_FILE
    if not path.is_file():
        return []
    return [FeedbackRule.model_validate(r) for r in json.loads(path.read_text())["rules"]]


def save_rules(rules: list[FeedbackRule], folder: Path | None = None) -> Path:
    folder = folder or feedback_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / RULES_FILE
    body = {"rules": [json.loads(r.model_dump_json()) for r in sorted(rules, key=lambda r: (r.proposed_at, r.rule_id))]}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(body, indent=2) + "\n")
    tmp.replace(path)
    return path


def decide(rules: list[FeedbackRule], rid: str, status: Literal["approved", "rejected"],
           now: datetime) -> list[FeedbackRule]:
    if not any(r.rule_id == rid for r in rules):
        raise KeyError(rid)
    return [r.model_copy(update={"status": status, "decided_at": now}) if r.rule_id == rid else r for r in rules]


def rulebook_hash(rules: list[FeedbackRule]) -> str:
    """Hash of the approved rules, for prediction traceability (CLAUDE.md principle 2)."""
    core = [r.model_dump(mode="json", exclude={"reasoning", "proposed_at", "decided_at", "status"})
            for r in sorted(rules, key=lambda r: r.rule_id) if r.status == "approved"]
    return hashlib.sha256(json.dumps(core, sort_keys=True).encode()).hexdigest()[:12]


def adjust_layers(layers: list, rules: list[FeedbackRule], plot: str, cutoff: datetime) -> list[str]:
    """Multiply ``prob`` of matching layers in place (duck-typed: ``top``, ``bottom`` depths from the surface in m,
    ``critical``, ``prob``, ``extra``). Only approved rules usable at ``cutoff`` act; several matching rules multiply
    but the total stays within [MIN_FACTOR, MAX_FACTOR]. Returns the ids of rules that acted."""
    live = [r for r in rules if r.status == "approved" and r.usable_at(cutoff)]
    used: set[str] = set()
    for ly in layers:
        crit = getattr(ly.critical, "value", ly.critical)
        hits = [r for r in live if r.matches(plot, crit, (ly.top + ly.bottom) / 2)]
        if not hits:
            continue
        f = 1.0
        for r in hits:
            f *= r.factor
        f = min(MAX_FACTOR, max(MIN_FACTOR, f))
        ly.prob = min(1.0, max(0.0, ly.prob * f))
        ly.extra = {**(ly.extra or {}), "feedback_rules": [r.rule_id for r in hits]}
        used.update(r.rule_id for r in hits)
    return sorted(used)


__all__ = ["FeedbackRule", "MAX_FACTOR", "MIN_FACTOR", "NOTES_ERA_START", "RuleClass", "RuleDraft", "Zone",
           "adjust_layers", "decide", "from_draft", "load_rules", "rule_id", "rulebook_hash", "save_rules"]
