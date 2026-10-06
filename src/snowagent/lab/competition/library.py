"""The analogue library (harness side; ADR-062).

Built once per competition from the cases whose truth a library may hold (``truth.library_ok``: training, or
development in mode split; never holdout, validation or sealed-test pits). The harness keeps each entry's season and
gives an agent, for each case, only the entries of OTHER seasons, as an anonymous ``AnalogueLibrary`` (no season,
date or id): an analogue agent cannot draw on the season it is scored on, so it cannot memorise its pits.
"""

from __future__ import annotations

from pathlib import Path

from snowagent.lab.agents.analogue import AnalogueEntry, AnalogueLayer, AnalogueLibrary, digest
from snowagent.lab.benchmark.loader import load_visible_case, read_manifest
from snowagent.lab.competition.scoring import truth_depth
from snowagent.lab.competition.truth import library_ok, scoring_truth
from snowagent.lab.schemas.benchmark import TargetScope
from snowagent.lab.schemas.common import LabModel


class SeasonEntry(LabModel):
    season: str  # harness side only: never passed to an agent
    entry: AnalogueEntry


def library_entry(case_dir: Path) -> SeasonEntry | None:
    """The case as a library entry, or None when its truth may not feed a library."""
    m = read_manifest(case_dir)
    if not library_ok(m):
        return None
    truth = scoring_truth(case_dir, m).truth_profile
    layers = [AnalogueLayer(top_depth_m=ly.top_depth_m, bottom_depth_m=ly.bottom_depth_m, grain=ly.grain_primary,
                            hardness_index=ly.hardness_index) for ly in truth.layers] \
        if m.target_scope == TargetScope.full_profile else []
    return SeasonEntry(season=m.season, entry=AnalogueEntry(digest=digest(load_visible_case(case_dir)),
                                                            truth_hs_m=truth_depth(truth), truth_layers=layers))


def library_for(entries: list[SeasonEntry], season: str) -> AnalogueLibrary:
    """What an agent scored on a case of ``season`` may see: the entries of every other season."""
    return AnalogueLibrary(entries=[e.entry for e in entries if e.season != season])
