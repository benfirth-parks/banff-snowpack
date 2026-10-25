"""The site's new-snow check (ADR-104): the block of web/app.js run in node on hand-made pits and layers."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from snowagent.lab.agents.snowpack import NEW_SNOW_FRAC

APP = Path(__file__).resolve().parents[2] / "web" / "app.js"
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node not installed")


def _block() -> str:
    m = re.search(r"// -+ new-snow check \(ADR-104\)\n(.*?)// -+ end new-snow check", APP.read_text(), re.S)
    assert m, "new-snow check block not found in web/app.js"
    return m.group(1)


def _run(expr: str):
    out = subprocess.run([NODE, "-e", _block() + f"\nconsole.log(JSON.stringify({expr}));"],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def _row(top, bot, grain, flags):
    return [top, bot, grain, 2.0, 150, -5.0, 0.0, flags]


PIT = {"id": "p1", "t": "2026-01-10T20:00", "hs": 100.0, "L": [[100, 60, "RG", 2, 250], [60, 0, "FC", 3, 280]]}
LAYERS = [_row(130, 120, "FC", 2),   # middle 125 > 90: in new snow -> unlikely
          _row(128, 126, "RG", 0),   # not a weak-layer flag: unchanged
          _row(119, 111, "SH", 2),   # middle 115: unlikely
          _row(92, 86, "DH", 3),     # middle 89 < 90 (old snow): unchanged, crust bit kept
          _row(86, 0, "FC", 2)]


def test_same_share_as_the_lab():
    assert re.search(r"const NEW_SNOW_FRAC = ([0-9.]+);", _block()).group(1) == str(NEW_SNOW_FRAC)


def test_marks_weak_layers_above_the_last_pits_depth():
    rows = _run(f"markNewSnow({json.dumps(LAYERS)}, 100)")
    assert [r[7] for r in rows] == [6, 0, 6, 3, 2]
    assert [r[:7] for r in rows] == [r[:7] for r in LAYERS]  # nothing else changes


def test_forecast_uses_pits_before_its_issue():
    pits = json.dumps([PIT])
    assert _run(f'newSnowPit({pits}, new Date("2026-01-11T00:00:00Z"), true)') == {"pit": PIT, "hs": 100.0}
    assert _run(f'newSnowPit({pits}, new Date("2026-01-10T00:00:00Z"), true)') is None


def test_nowcast_waits_for_the_pit_update():
    pits = json.dumps([PIT])  # the update runs at 00 UTC the next day; the profile at that hour is from before it
    assert _run(f'newSnowPit({pits}, new Date("2026-01-11T00:00:00Z"), false)') is None
    assert _run(f'newSnowPit({pits}, new Date("2026-01-11T06:00:00Z"), false)')["pit"]["id"] == "p1"


def test_latest_pit_with_layers_and_depth_fallback():
    later_no_layers = {"id": "p2", "t": "2026-01-15T20:00", "hs": 140.0, "L": []}
    later_no_depth = {"id": "p3", "t": "2026-01-12T20:00", "hs": None, "L": [[118, 70, "RG", 2, 250], [70, 0, "FC", 3, 280]]}
    pits = json.dumps([later_no_layers, PIT, later_no_depth])
    got = _run(f'newSnowPit({pits}, new Date("2026-01-20T00:00:00Z"), true)')
    assert got["pit"]["id"] == "p3" and got["hs"] == 118
    assert _run('newSnowPit([], new Date("2026-01-20T00:00:00Z"), true)') is None
