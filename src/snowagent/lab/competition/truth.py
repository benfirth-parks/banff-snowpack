"""The scorer's only door to the withheld pits (ADR-064).

A competition (and the M4 evolution loop) reads truth only for the scoring splits of the case set's split mode:
mode ``all`` -> training, ``loso`` -> training and holdout, ``split`` -> development and validation. Sealed-test
truth is never read here: this module never passes an unseal phrase, and refuses before ``load_hidden_truth`` is
called. The analogue library may draw only on the LIBRARY splits (training, or development in mode split), never
on holdout, validation or sealed-test pits.
"""

from __future__ import annotations

from pathlib import Path

from snowagent.lab.benchmark.loader import SealedTruthError, load_hidden_truth
from snowagent.lab.schemas.benchmark import CaseManifest, HiddenTruth, Split, SplitMode

SCORING_SPLITS: dict[SplitMode, frozenset[Split]] = {
    SplitMode.all: frozenset({Split.training}),
    SplitMode.loso: frozenset({Split.training, Split.holdout}),
    SplitMode.split: frozenset({Split.development, Split.validation}),
}
LIBRARY_SPLITS: dict[SplitMode, frozenset[Split]] = {
    SplitMode.all: frozenset({Split.training}),
    SplitMode.loso: frozenset({Split.training}),
    SplitMode.split: frozenset({Split.development}),
}


class TruthNotScorable(SealedTruthError):
    """The case's truth may not be read by a competition (sealed test, or a split its mode does not score)."""


def scorable(manifest: CaseManifest) -> bool:
    mode = manifest.split_mode or SplitMode.all
    return manifest.split != Split.sealed_test and manifest.split in SCORING_SPLITS[mode]


def library_ok(manifest: CaseManifest) -> bool:
    mode = manifest.split_mode or SplitMode.all
    return manifest.split != Split.sealed_test and manifest.split in LIBRARY_SPLITS[mode]


def scoring_truth(case_dir: Path, manifest: CaseManifest) -> HiddenTruth:
    """The withheld pit of a scorable case; ``TruthNotScorable`` (before any file is opened) otherwise."""
    if not scorable(manifest):
        raise TruthNotScorable(f"{manifest.case_id}: split {manifest.split.value} is not scored in mode "
                               f"{(manifest.split_mode or SplitMode.all).value}; its truth is not read")
    return load_hidden_truth(case_dir)
