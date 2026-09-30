"""Run the upstream SNOWPACK MST96 (Weissfluhjoch 1995-96) example and compare with its reference.

The bundled reference output was produced by SNOWPACK 3.41 (2017), so formats differ
from the current build; we compare physically comparable series by column name.
"""

from __future__ import annotations

import bz2
import shutil
from pathlib import Path

import numpy as np

from snowagent.engine import snowpack as sp
from snowagent.errors import EngineRunFailed, EngineUnavailable

HS_CORR_MIN = 0.99
SWE_PEAK_REL_TOL = 0.05


def run_mst96_example(source: Path, output: Path) -> dict:
    source = Path(source)
    ini = source / "tests" / "res1exp" / "io_res1exp.ini"
    ref = source / "tests" / "res1exp" / "output_ref" / "MST96_res.met.bz2"
    if not ini.exists() or not ref.exists():
        raise EngineUnavailable(f"upstream example not found under {source}",
                                recovery_command=sp.RECOVERY_COMMAND)
    eng = sp.find_engine()
    output = Path(output)
    shutil.rmtree(output, ignore_errors=True)
    (output / "run" / "output").mkdir(parents=True)
    shutil.copytree(source / "tests" / "input", output / "input")
    shutil.copy(ini, output / "run" / "io_res1exp.ini")
    import subprocess

    proc = subprocess.run([eng.binary, "-c", "io_res1exp.ini", "-e", "1996-06-17T00:00"], cwd=output / "run",
                          capture_output=True, text=True, timeout=1800)
    (output / "run" / "engine.log").write_text(proc.stdout + "\n" + proc.stderr)
    met_path = output / "run" / "output" / "MST96_res.met"
    if proc.returncode != 0 or not met_path.exists():
        raise EngineRunFailed("upstream MST96 example failed", returncode=proc.returncode,
                              log_tail=(proc.stderr or proc.stdout)[-1000:])
    (output / "MST96_res_reference.met").write_bytes(bz2.decompress(ref.read_bytes()))
    new = sp.parse_met(met_path)
    old = sp.parse_met(output / "MST96_res_reference.met")
    hs_n = new["Modelled snow depth (vertical)"].reindex(old.index)
    hs_o = old["Modelled snow depth (vertical)"]
    swe_n, swe_o = new["SWE (of snowpack)"].max(), old["SWE (of snowpack)"].max()
    corr = float(np.corrcoef(hs_n.fillna(0), hs_o.fillna(0))[0, 1])
    rel = float(abs(swe_n - swe_o) / swe_o)
    meas = new["Measured snow depth HS"]
    ok = meas.notna() & (meas >= 0)
    return {
        "engine": eng.version_string,
        "hs_correlation_vs_reference": round(corr, 5),
        "hs_mean_abs_diff_cm": round(float((hs_n - hs_o).abs().mean()), 2),
        "swe_peak_new": round(float(swe_n), 1), "swe_peak_reference": round(float(swe_o), 1),
        "swe_peak_rel_diff": round(rel, 4),
        "hs_rmse_vs_measured_cm": round(float(np.sqrt(((new["Modelled snow depth (vertical)"] - meas)[ok] ** 2).mean())), 1),
        "reference_version": "SNOWPACK 3.41 (2017) bundled test output",
        "passed": bool(corr >= HS_CORR_MIN and rel <= SWE_PEAK_REL_TOL),
        "criteria": f"HS corr >= {HS_CORR_MIN}, peak SWE within {SWE_PEAK_REL_TOL:.0%}",
        "wall_output": str(output),
    }
