"""The lab's local training loop (milestone 4, ADR-066 to ADR-069): rounds of competitions on every training case,
the top agents kept and varied (``loop``), a prediction and engine cache so an unchanged genome is never re-run
(``cache``, ``evaluate``), cost estimates (``estimate``), lineage (``lineage``) and the leave-one-season-out
promotion check (``loso``). Research and decision support only, not an avalanche forecast."""
