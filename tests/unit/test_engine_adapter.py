"""Engine adapter I/O and failure handling.

Tests marked ``MOCK`` use fake executables to exercise error handling only.
They never satisfy end-to-end acceptance, which requires the real engine
(tests/integration).
"""

from __future__ import annotations

import stat
from pathlib import Path

import pandas as pd
import pytest

from snowagent.engine import grains
from snowagent.engine import snowpack as sp
from snowagent.engine.profiles import convert_profile
from snowagent.errors import EngineRunFailed, EngineUnavailable

FIX = Path(__file__).resolve().parents[1] / "fixtures"


def test_parse_real_engine_pro_sample_geometry_and_bounds():
    station, profs = sp.parse_pro(FIX / "engine_sample_slope35.pro")
    assert station["SlopeAngle"] == "35.00" and len(profs) == 2
    layers, diag, nulls, ident = convert_profile(profs[-1], 35.0, "U1")
    assert diag.n_layers == len(layers) > 50
    cos35 = 0.8191520442889918
    assert diag.hs_slope_normal_m == pytest.approx(diag.hs_vertical_m * cos35)
    assert diag.swe_kg_m2_per_horizontal_area == pytest.approx(diag.swe_kg_m2_per_slope_area / cos35)
    for lo, hi in zip(layers, layers[1:], strict=False):
        assert hi.bottom_vertical_m == pytest.approx(lo.top_vertical_m)
    assert layers[-1].top_vertical_m == pytest.approx(diag.hs_vertical_m)
    assert all(ly.deposition_time is not None and ly.lineage_id for ly in layers)
    assert any(n.field == "p_unstable" for n in nulls)  # not invented
    assert ident is None


def test_grain_code_decoding():
    assert grains.decode(772) == ("MFcr", "MF", True)
    assert grains.is_crust(772) and grains.is_crust(880)
    assert not grains.is_crust(452) and grains.melt_freeze_marker(452)  # faceting former crust
    assert grains.candidate_weak_layer_basis(660) and grains.candidate_weak_layer_basis(550)
    assert grains.candidate_weak_layer_basis(330) is None


def test_template_has_no_unfilled_placeholders_and_single_owner_keys():
    text = sp.EngineSettings().render("X1")
    assert "{" not in "".join(ln for ln in text.splitlines() if not ln.lstrip().startswith(";"))
    for key in ("PERP_TO_SLOPE = TRUE", "SNOW_REDISTRIBUTION = FALSE", "SNOW_EROSION = FALSE", "NUMBER_SLOPES = 1",
                "CANOPY = FALSE", "COMBINE_ELEMENTS = FALSE"):
        assert key in text


def test_missing_engine_reports_recovery_command(monkeypatch, tmp_path):
    monkeypatch.setattr(sp, "DEFAULT_BIN_CANDIDATES", ())
    monkeypatch.setenv("PATH", str(tmp_path))
    monkeypatch.delenv("SNOWPACK_BIN", raising=False)
    with pytest.raises(EngineUnavailable) as ei:
        sp.find_engine()
    assert "recovery_command" in ei.value.details


def test_engine_found_at_the_macos_build_prefix_without_snowpack_bin(monkeypatch, tmp_path):
    """scripts/build_snowpack.sh installs to ~/.local/snowpack on macOS; that binary needs no SNOWPACK_BIN."""
    exe = tmp_path / ".local" / "snowpack" / "bin" / "snowpack"
    exe.parent.mkdir(parents=True)
    exe.write_text("#!/bin/bash\necho 'Snowpack version MOCK'\n")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", str(tmp_path / "nothing"))
    monkeypatch.delenv("SNOWPACK_BIN", raising=False)
    home = tuple(c for c in sp.DEFAULT_BIN_CANDIDATES if c.startswith("~"))
    assert home == ("~/.local/snowpack/bin/snowpack",)
    monkeypatch.setattr(sp, "DEFAULT_BIN_CANDIDATES", home)  # without the Linux prefix this machine may have
    assert sp.find_engine().binary == str(exe)


def _fake_engine(tmp_path: Path, body: str) -> sp.EngineInfo:
    """MOCK executable: prints a version, then runs ``body`` for real invocations."""
    exe = tmp_path / "fake_snowpack"
    exe.write_text("#!/bin/bash\nif [ \"$1\" = \"-v\" ]; then echo 'Snowpack version MOCK'; exit 0; fi\n" + body)
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return sp.find_engine(str(exe))


def _inputs(rd: Path) -> None:
    (rd / "input").mkdir(parents=True)
    idx = pd.date_range("2025-11-01T01:00Z", periods=5, freq="h")
    df = pd.DataFrame({f: 1.0 for f in sp.SMET_FIELDS}, index=idx)
    sp.write_smet_forcing(rd / "input" / "S.smet", "S", 51, -115, 2000, df)
    sp.write_snowfree_sno(rd / "input" / "S.sno", "S", 51, -115, 2000, 0, 0, idx[0])


@pytest.mark.parametrize("body,match", [
    ("exit 3\n", "exited with code 3"),                      # MOCK: crash
    ("echo '[E] missing precipitation' >&2; exit 0\n", "reported errors"),  # MOCK: error line but exit 0
    ("exit 0\n", "produced no"),                             # MOCK: silent no-output
])
def test_mock_engine_failures_are_explicit(tmp_path, body, match):
    eng = _fake_engine(tmp_path, body)
    rd = tmp_path / "run"
    _inputs(rd)
    with pytest.raises(EngineRunFailed, match=match):
        sp.run_engine(eng, rd, "S", pd.Timestamp("2025-11-01T05:00Z"), sp.EngineSettings())
