"""The "How agents change" page's reading of a run (ADR-133): shifts, trimmed runs, the live round, family trees,
score weights and adding a run archived on another computer."""

from __future__ import annotations

import io
import json
import tarfile
from pathlib import Path

import pytest

from snowagent.lab.schemas.genome import default_spec
from snowagent.lab.services.genomes import (
    ArchiveError,
    _last_round_started,
    agent_index,
    drift_table,
    family_tree,
    gene_info,
    import_run_archive,
    run_spec,
    shift,
    step_text,
    total_drift,
    value_history,
    weights_in_force,
)
from snowagent.lab.storage.paths import LabPaths


def test_shift_is_a_share_of_the_allowed_range_and_a_choice_counts_one():
    num = {"kind": "float", "min": 0.0, "max": 10.0, "standard": 5.0}
    assert shift(7.5, num) == 0.25 and shift(0.0, num) == -0.5 and shift(5.0, num) == 0.0
    ch = {"kind": "choice", "min": None, "max": None, "standard": "on"}
    assert shift("off", ch) == 1.0 and shift("on", ch) == 0.0


def test_gene_info_names_every_setting_and_a_trimmed_run_changes_only_its_list():
    spec = default_spec()
    full = gene_info(spec, "process")
    run_switches = full["gene"].isin(["proc_sw_crustgate", "col_old_facets", "proc_sw_duration", "proc_facet_excess_cap", "proc_sw_phase", "proc_sw_catch", "proc_phase_shift_k"]) | full["gene"].str.startswith("proc_cg_")  # ADR-141, ADR-142, ADR-143, ADR-152
    assert full.loc[~run_switches, "evolves"].all() and not full.loc[run_switches, "evolves"].any() and full["setting"].str.len().gt(0).all() and full["topic"].str.len().gt(0).all()
    trimmed = run_spec(spec, {"trimmed_genes": {"process": ["col_pp_hours", "proc_sw_surface"]}})
    t = gene_info(trimmed, "process").set_index("gene")
    assert set(t.index[t["evolves"]]) == {"col_pp_hours", "proc_sw_surface"}
    col = gene_info(spec, "column").set_index("gene")
    assert not col.at["col_old_facets", "evolves"]  # a run switch (ADR-128) never evolves


def _rounds():
    spec = default_spec()
    std = {g: gs.default for g, (_b, gs) in spec.family_genes("process").items()}
    a = {"hash": "a" * 64, "label": "process-default", "name": "process-default", "family": "process",
         "role": "initial", "operator": "initial", "parents": [], "changed": {}, "genes": std, "rank": 1}
    b = {"hash": "b" * 64, "label": "r02-m01-process", "name": "Omar Balmoral", "family": "process", "role": "child",
         "operator": "mutation", "parents": ["a" * 64], "changed": {"col_pp_hours": [24.0, 36.0]},
         "genes": std | {"col_pp_hours": 36.0, "proc_sw_surface": "off"}, "rank": 1}
    return [{"round": 1, "scored": True, "best": a["hash"], "agents": [a]},
            {"round": 2, "scored": True, "best": b["hash"], "agents": [b, a | {"rank": 2, "role": "survivor"}]},
            {"round": 3, "scored": False, "best": None, "agents": [b | {"rank": None}]}]


def test_drift_value_history_and_family_tree_of_a_run():
    spec = default_spec()
    info = gene_info(spec, "process")
    rounds = _rounds()
    dt = drift_table(rounds, info)
    assert list(dt.columns) == [1, 2]  # the round being tested has no best yet
    assert set(dt.index) == {"new snow breaking down: hours", "switch: near-surface facets in clear cold spells"}
    assert dt.loc["switch: near-surface facets in clear cold spells", 2] == 1.0
    td = total_drift(rounds, info).set_index("round")
    assert td.at[1, "drift"] == 0 and td.at[2, "settings moved"] == 2
    vh = value_history(rounds, "col_pp_hours")
    assert len(vh) == 4 and vh["best"].sum() == 2 and not vh[vh["round"] == 3]["scored"].any()
    tree = family_tree(rounds, "b" * 64)
    assert [t["name"] for t in tree] == ["Omar Balmoral", "process-default"]
    text = step_text(tree[0], info, agent_index(rounds))
    assert text == "small random change of process-default: new snow breaking down: hours 24 → 36"


def test_weights_in_force_compares_the_runs_weights_with_todays():
    w = weights_in_force({"scoring_weights": {"snow_depth": 0.2, "layer_structure": 0.3}},
                         {"snow_depth": 0.1, "layer_structure": 0.35}).set_index("part of the score")
    assert w.at["Snow depth", "this run"] == 20 and w.at["Snow depth", "today"] == 10
    assert w.at["Layering", "today"] == 35


def test_last_round_started_reads_a_long_event_log_from_its_end(tmp_path):
    f = tmp_path / "events.jsonl"
    with open(f, "w") as fh:
        fh.write(json.dumps({"ev": "round_started", "round": 1, "agents": []}) + "\n")
        for i in range(30000):
            fh.write(json.dumps({"ev": "case_scored", "i": i, "pad": "x" * 40}) + "\n")
        fh.write(json.dumps({"ev": "round_started", "round": 2, "agents": []}) + "\n")
        for i in range(30000):
            fh.write(json.dumps({"ev": "case_scored", "i": i, "pad": "x" * 40}) + "\n")
    assert _last_round_started(f, chunk=1 << 16)["round"] == 2
    assert _last_round_started(tmp_path / "none.jsonl") is None


def _archive(tmp_path: Path, files: dict[str, bytes], link: str | None = None) -> Path:
    out = tmp_path / "run.tar.gz"
    with tarfile.open(out, "w:gz") as tf:
        for name, data in files.items():
            ti = tarfile.TarInfo(name)
            ti.size = len(data)
            tf.addfile(ti, io.BytesIO(data))
        if link:
            ti = tarfile.TarInfo(link)
            ti.type, ti.linkname = tarfile.SYMTYPE, "/etc/passwd"
            tf.addfile(ti)
    return out


def test_a_run_archive_is_added_and_anything_unsafe_or_not_a_run_is_refused(tmp_path):
    paths = LabPaths(tmp_path / "lab")
    good = {"cloud1-x/run.json": b"{}", "cloud1-x/rounds/r01/round.json": b"{}",
            "cloud1-x/status.json": json.dumps({"state": "running", "pid": 1}).encode()}
    run_id = import_run_archive(paths, _archive(tmp_path, good))
    d = paths.outputs / "training" / run_id
    assert run_id == "cloud1-x" and (d / "rounds" / "r01" / "round.json").is_file()
    assert json.loads((d / "status.json").read_text())["state"] == "stopped"  # not going on this computer
    with pytest.raises(ArchiveError, match="already here"):
        import_run_archive(paths, _archive(tmp_path, good))
    for bad, why in [({"a/run.json": b"{}", "b/rounds/r01/x": b""}, "one run folder"),
                     ({"a/../../evil": b"x", "a/run.json": b"{}"}, "unsafe"),
                     ({"c/cache/x.parquet": b"x"}, "not a training run")]:
        with pytest.raises(ArchiveError, match=why):
            import_run_archive(paths, _archive(tmp_path, bad))
    with pytest.raises(ArchiveError, match="link"):
        import_run_archive(paths, _archive(tmp_path, {"e/run.json": b"{}", "e/rounds/r01/x": b""}, link="e/l"))
    (tmp_path / "junk.tar.gz").write_bytes(b"not a tar")
    with pytest.raises(ArchiveError, match="not a run archive"):
        import_run_archive(paths, tmp_path / "junk.tar.gz")
