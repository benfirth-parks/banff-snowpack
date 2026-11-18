"""Turn forecaster notes into proposed rules with Claude (ADR-115).

The model reads pseudonymised notes plus the rules already on file, and proposes rules in the fixed shape of
``RuleDraft``: a plain-words heuristic and a bounded multiplier on a weak layer's presence probability. It cannot add
layers or properties. Every proposal is checked again on load (``FeedbackRule``) and waits for Ben's approval.

Needs the optional ``anthropic`` package and an API key in the environment (ANTHROPIC_API_KEY); never in code.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import BaseModel

from snowagent.lab.feedback.notes import ForecasterNote, for_model
from snowagent.lab.feedback.rules import (
    MAX_FACTOR,
    MIN_FACTOR,
    NEAR_SURFACE_M,
    PLOTS,
    FeedbackRule,
    RuleDraft,
    from_draft,
)

MODEL = "claude-opus-5-5"

SYSTEM = f"""You help a snowpack research team learn from avalanche forecasters' notes on a SNOWPACK model.

Each note is about the modelled snow profile at one study plot ({", ".join(PLOTS)}): whether the forecaster agrees,
what it is about, and what they think the model gets wrong or misses. Writers are pseudonyms.

Propose rules that capture recurring, physically sensible patterns in the notes. A rule may only do one thing: \
multiply the model's probability that layers of some classes (surface_hoar, facets, depth_hoar, crust) are present, \
in one depth zone (near_surface = middle of the layer within {NEAR_SURFACE_M:.2f} m of the surface, buried = deeper, \
or any), at some plots (empty list = all plots). The factor must be between {MIN_FACTOR} and {MAX_FACTOR}; below 1 \
means the model over-forecasts such layers, above 1 means it under-forecasts them.

Be conservative:
- Propose a rule only when at least two notes, ideally from different writers or days, point the same way. Cite \
their keys in evidence.
- Prefer factors close to 1 unless notes strongly agree.
- Ignore notes that only describe a single pit result, or that are about things a rule cannot express (depth, \
timing, wind loading of slopes); you may mention them in your summary.
- Do not repeat a rule already on file; propose a changed version only if new notes clearly call for it.
- Propose no rules if the notes do not support any. An empty list is a good answer."""


class Distilled(BaseModel):
    rules: list[RuleDraft]
    summary: str


def build_prompt(notes: list[ForecasterNote], existing: list[FeedbackRule], salt: str) -> str:
    on_file = "\n".join(f"- {r.rule_id} [{r.status}] {r.statement} (classes {', '.join(c.value for c in r.classes)}, "
                        f"zone {r.zone.value}, plots {r.plots or 'all'}, factor {r.factor})" for r in existing)
    return (f"Rules already on file:\n{on_file or '(none)'}\n\n"
            f"Forecaster notes, one JSON object per line:\n{for_model(notes, salt)}\n\n"
            "Propose rules as described, and give a short summary of what the notes say overall.")


def distil(notes: list[ForecasterNote], existing: list[FeedbackRule], salt: str, client=None,
           now: datetime | None = None) -> tuple[list[FeedbackRule], list[str], str]:
    """Proposed rules, the drafts that failed checks (as messages), and the model's summary."""
    if not notes:
        return [], [], "No notes yet."
    if client is None:
        import anthropic

        client = anthropic.Anthropic()
    response = client.beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": "high"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": build_prompt(notes, existing, salt)}],
        output_format=Distilled,
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("the model declined to distil these notes")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("the model's answer was cut off; distil fewer notes at a time")
    out: Distilled = response.parsed_output
    times = {n.key: n.received_at for n in notes}
    now = now or datetime.now(UTC)
    held = {r.rule_id for r in existing}
    rules, problems = [], []
    for d in out.rules:
        try:
            r = from_draft(d, times, now)
        except ValueError as exc:
            problems.append(f"{d.statement[:80]}: {exc}")
            continue
        if r.rule_id not in held:
            rules.append(r)
    return rules, problems, out.summary


__all__ = ["MODEL", "SYSTEM", "Distilled", "build_prompt", "distil"]
