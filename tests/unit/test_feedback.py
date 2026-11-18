"""Forecaster feedback (ADR-115): notes, leakage guard, bounded rules, distiller with a fake client."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from snowagent.lab.feedback.distil import Distilled, distil
from snowagent.lab.feedback.notes import (
    ForecasterNote,
    for_model,
    load_notes,
    merge_notes,
    save_notes,
    visible_notes,
    writer_code,
)
from snowagent.lab.feedback.rules import (
    FeedbackRule,
    RuleClass,
    RuleDraft,
    Zone,
    adjust_layers,
    decide,
    from_draft,
    load_rules,
    rulebook_hash,
    save_rules,
)

T = lambda *a: datetime(*a, tzinfo=UTC)  # noqa: E731


def note(key: str, when: datetime, name: str = "Ben F", plot: str = "bow_summit", **kw) -> ForecasterNote:
    return ForecasterNote(key=key, received_at=when, plot=plot, name=name, note=kw.pop("text", "SH missing"), **kw)


def rule(**kw) -> FeedbackRule:
    base = dict(rule_id="R-1", statement="Surface hoar under-forecast at Bow", plots=["bow_summit"],
                classes=[RuleClass.surface_hoar], zone=Zone.near_surface, factor=1.3, evidence=["a"],
                usable_from=T(2026, 12, 5), status="approved", proposed_at=T(2026, 12, 6))
    return FeedbackRule(**{**base, **kw})


@dataclass
class L:
    top: float
    bottom: float
    critical: str
    prob: float = 0.6
    extra: dict = field(default_factory=dict)


def test_notes_round_trip_and_keep_first_copy(tmp_path):
    a = note("k1", T(2026, 12, 1, 15))
    b = note("k2", T(2026, 12, 2, 15), agreement="disagree", topics=["weak_layer"])
    changed = note("k1", T(2026, 12, 1, 15), text="edited later")
    merged = merge_notes([a], [changed, b])
    assert [n.key for n in merged] == ["k1", "k2"] and merged[0].note == "SH missing"
    save_notes(merged, tmp_path)
    assert load_notes(tmp_path) == merged


def test_explorer_records_parse_and_unknown_fields_are_ignored():
    n = ForecasterNote.model_validate({"key": "2026-12-01/x", "version": 1, "received_at": "2026-12-01T22:00:00.000Z",
                                      "plot": "simpson", "agreement": "partly", "topics": ["crust"], "note": "n",
                                      "name": "AB", "model_run_t": "2026-12-01T12:00:00.000Z", "model_hs_cm": 80,
                                      "pit_shown_t": None, "later_field": 1})
    assert n.received_at == T(2026, 12, 1, 22) and n.pit_shown_t is None


def test_leakage_guard_uses_receive_time_strictly_before_cutoff():
    notes = [note("a", T(2026, 12, 1)), note("b", T(2026, 12, 3)), note("c", T(2026, 12, 3, 0, 0, 1))]
    assert [n.key for n in visible_notes(notes, T(2026, 12, 3))] == ["a"]
    assert [n.key for n in visible_notes(notes, datetime(2026, 12, 4))] == ["a", "b", "c"]  # naive cut-off = UTC


def test_names_never_reach_the_model():
    text = for_model([note("a", T(2026, 12, 1), name="Jane Doe")], salt="s")
    assert "Jane" not in text and writer_code("Jane Doe", "s") in text
    assert writer_code(" jane doe ", "s") == writer_code("Jane Doe", "s") != writer_code("Jane Doe", "t")


def test_rule_bounds_are_enforced():
    with pytest.raises(ValidationError):
        rule(factor=2.0)
    with pytest.raises(ValidationError):
        rule(factor=1.0)
    with pytest.raises(ValidationError):
        rule(plots=["everest"])
    with pytest.raises(ValidationError):
        rule(classes=[])


def test_rule_time_gate():
    r = rule()
    assert r.usable_at(T(2019, 1, 15))  # before forecaster notes existed
    assert not r.usable_at(T(2026, 12, 4))  # after notes began but before the rule's evidence
    assert not r.usable_at(T(2026, 12, 5))
    assert r.usable_at(T(2026, 12, 6))


def test_adjust_layers_only_touches_matching_layers_and_stays_bounded():
    layers = [L(0.02, 0.04, "surface_hoar"), L(0.5, 0.6, "surface_hoar"), L(0.05, 0.1, "facets"),
              L(0.01, 0.03, "surface_hoar")]
    rules = [rule(), rule(rule_id="R-2", factor=1.5, zone=Zone.any), rule(rule_id="R-3", status="proposed")]
    used = adjust_layers(layers[:3], rules, "bow_summit", T(2027, 1, 10))
    assert used == ["R-1", "R-2"]
    assert layers[0].prob == pytest.approx(0.9)  # 1.3 x 1.5 capped at 1.5
    assert layers[1].prob == pytest.approx(0.9)  # only R-2 (buried), x1.5
    assert layers[2].prob == 0.6 and not layers[2].extra
    assert adjust_layers([layers[3]], rules, "simpson", T(2027, 1, 10)) == []
    assert adjust_layers([layers[3]], rules, "bow_summit", T(2026, 12, 1)) == []  # not yet usable


def test_rules_save_decide_and_hash(tmp_path):
    p = rule(status="proposed")
    save_rules([p], tmp_path)
    loaded = load_rules(tmp_path)
    assert loaded == [p] and rulebook_hash(loaded) == rulebook_hash([])
    approved = decide(loaded, "R-1", "approved", T(2026, 12, 7))
    assert approved[0].status == "approved" and rulebook_hash(approved) != rulebook_hash([])
    with pytest.raises(KeyError):
        decide(loaded, "R-9", "approved", T(2026, 12, 7))


def test_draft_needs_known_evidence_and_takes_newest_time():
    d = RuleDraft(statement="Crusts too few at Simpson", plots=["simpson"], classes=[RuleClass.crust], zone=Zone.any,
                  factor=1.2, evidence=["a", "b", "ghost"], reasoning="two notes")
    r = from_draft(d, {"a": T(2026, 12, 1), "b": T(2026, 12, 9)}, T(2026, 12, 10))
    assert r.evidence == ["a", "b"] and r.usable_from == T(2026, 12, 9) and r.status == "proposed"
    with pytest.raises(ValueError):
        from_draft(d.model_copy(update={"evidence": ["ghost"]}), {"a": T(2026, 12, 1)}, T(2026, 12, 10))


class FakeClient:
    def __init__(self, out: Distilled, stop: str = "end_turn") -> None:
        self.calls = []
        msgs = SimpleNamespace(parse=self._parse)
        self.beta = SimpleNamespace(messages=msgs)
        self._resp = SimpleNamespace(stop_reason=stop, parsed_output=out)

    def _parse(self, **kw):
        self.calls.append(kw)
        return self._resp


def test_distil_with_fake_client_keeps_valid_drafts_and_reports_bad_ones():
    notes = [note("a", T(2026, 12, 1), name="Jane"), note("b", T(2026, 12, 2), name="Sam")]
    good = RuleDraft(statement="SH under-forecast near surface at Bow", plots=["bow_summit"],
                     classes=[RuleClass.surface_hoar], zone=Zone.near_surface, factor=1.2, evidence=["a", "b"],
                     reasoning="two writers")
    bad = good.model_copy(update={"statement": "Way too much", "factor": 3.0})
    client = FakeClient(Distilled(rules=[good, bad], summary="SH missed"))
    rules, problems, summary = distil(notes, [], "salt", client=client, now=T(2026, 12, 3))
    assert [r.statement for r in rules] == [good.statement] and len(problems) == 1 and summary == "SH missed"
    call = client.calls[0]
    assert call["model"] == "claude-opus-5-5" and "Jane" not in call["messages"][0]["content"]
    again, _, _ = distil(notes, rules, "salt", client=FakeClient(Distilled(rules=[good], summary="")))
    assert again == []  # already on file
    with pytest.raises(RuntimeError):
        distil(notes, [], "salt", client=FakeClient(Distilled(rules=[], summary=""), stop="refusal"))
    assert distil([], [], "salt", client=client)[0] == []
