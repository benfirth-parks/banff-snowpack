"""The site's season timeline (ADR-114): the block of web/app.js run in node with the new-snow check block."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "web" / "app.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def _block(name: str, end: str) -> str:
    m = re.search(
        rf"// -+ {re.escape(name)}\n(.*?)// -+ {re.escape(end)}", APP.read_text(), re.S
    )
    assert m, f"{name} block not found in web/app.js"
    return m.group(1)


def _run(expr: str):
    code = _block("new-snow check (ADR-104)", "end new-snow check") + _block(
        "season timeline (ADR-114)", "end season timeline"
    )
    out = subprocess.run(
        [NODE, "-e", code + f"\nconsole.log(JSON.stringify({expr}));"],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def _row(top, bot, grain, flags=0):
    return [top, bot, grain, 2.0, 150, -5.0, 0.0, flags]


PROFILES = [
    {"t": "2026-01-10T00", "hs": 50, "L": [_row(50, 20, "PP"), _row(20, 0, "FC", 2)]},
    {"t": "2026-01-10T06", "hs": 60, "L": [_row(60, 55, "SH", 2), _row(55, 0, "RG")]},
    {
        "t": "2026-01-11T06",
        "hs": 130,
        "L": [_row(130, 120, "FC", 2), _row(120, 0, "RG")],
    },
]
PIT = {"id": "p1", "t": "2026-01-10T20:00", "hs": 100.0, "L": [[100, 0, "RG", 2, 250]]}
H = 3600e3


def test_columns_span_to_the_next_profile():
    cols = _run(f"timelineCols({json.dumps(PROFILES)}, [], false)")
    t0 = cols[0]["t"]
    assert [(c["t"] - t0) / H for c in cols] == [0, 6, 30]
    assert [(c["t1"] - c["t"]) / H for c in cols] == [
        6,
        24,
        24,
    ]  # the last keeps the step before it
    assert [c["L"] for c in cols] == [
        p["L"] for p in PROFILES
    ]  # layers unchanged without the check


def test_new_snow_check_only_after_the_pit_update():
    cols = _run(f"timelineCols({json.dumps(PROFILES)}, {json.dumps([PIT])}, true)")
    assert cols[1]["L"][0][7] == 2  # before the pit's update: unchanged
    assert cols[2]["L"][0][7] == 6  # after it, a weak layer above 90 cm is unlikely


def test_column_and_layer_lookup():
    cols = json.dumps(PROFILES)
    find = f"(() => {{ const c = timelineCols({cols}, [], false); const t0 = c[0].t;"
    assert _run(
        find
        + " return [-1, 0, 5.9, 6, 29, 53.9, 54].map((h) => { const x = colAt(c, t0 + h * 3600e3); "
        "return x ? (x.t - t0) / 3600e3 : null; }); })()"
    ) == [None, 0, 0, 6, 6, 30, None]
    assert _run(f"layerAt({json.dumps(PROFILES[0]['L'])}, 30)")[2] == "PP"
    assert _run(f"layerAt({json.dumps(PROFILES[0]['L'])}, 70)") is None
