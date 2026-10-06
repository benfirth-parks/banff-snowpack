"""Benchmark harness (build guide "Benchmark-case design"; ADR-059): case packages, the availability rule, leakage
checks and the loaders.

A case package lives at ``<data root>/benchmark/<split>/<case_id>/``::

    manifest.json      evaluator side: names the target pit, hashes every file, records assumptions
    checks.json        the leakage-check result written when the case was built
    visible/           the only directory an agent's inputs are read from (``loader.load_visible_case``)
    hidden/            the withheld pit (``loader.load_hidden_truth``; sealed-test cases need an explicit unseal)

Agents receive a ``VisibleBenchmarkCase`` and nothing else: no path, manifest or hidden object is reachable from it.
"""
