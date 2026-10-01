"""DTW alignment similarity (Herla et al. 2021) via r/dtw_similarity.R (sarp.snowprofile.alignment 2.0.2).

Profiles are observed-profile dicts (height above ground, cm). Grain form is reduced to the classes the R
package knows: the IACS main class, with melt-freeze crusts (MFcr) and ice formations (IF) kept separate.
Layers without a grain class are dropped (the package requires one); the count is not altered otherwise.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[3] / "r" / "dtw_similarity.R"


def _gtype(ly: dict) -> str | None:
    g = ly.get("grain_form") or ly.get("grain_class")
    if not g:
        return None
    if g.startswith("MFcr"):
        return "MFcr"
    return g[:2]


def to_r_profile(o: dict) -> dict | None:
    layers = [{"top": ly["top_cm"], "gtype": _gtype(ly), "hardness": ly.get("hardness_index")}
              for ly in o.get("layers", []) if ly.get("top_cm") is not None and _gtype(ly)]
    hs = o.get("hs_cm") or (max(ly["top"] for ly in layers) if layers else None)
    if not layers or not hs:
        return None
    return {"hs": hs, "layers": layers}


def similarity(pairs: list[tuple[str, dict, dict]], timeout: int = 3600) -> dict[str, dict]:
    """{id: {"sim", "sim_rescaled", "error"}} for (id, reference, query) pairs."""
    payload = []
    for pid, ref, qry in pairs:
        r, q = to_r_profile(ref), to_r_profile(qry)
        if r and q:
            payload.append({"id": pid, "ref": r, "query": q})
    if not payload:
        return {}
    with tempfile.TemporaryDirectory() as d:
        fin, fout = Path(d) / "in.json", Path(d) / "out.json"
        fin.write_text(json.dumps(payload))
        subprocess.run(["Rscript", str(SCRIPT), str(fin), str(fout)], check=True, timeout=timeout,
                       capture_output=True, text=True)
        return {r["id"]: r for r in json.loads(fout.read_text())}
