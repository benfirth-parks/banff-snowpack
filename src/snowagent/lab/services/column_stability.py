"""SK38 for the lab's own column models (ADR-147): SNOWPACK's skier stability index, computed by SNOWPACK's own
published formula from the elements of a model that does not run SNOWPACK (the column model, and the process
switches behind the site's main model Carver Daniels).

The stability check (ADR-127/144) gave models without SNOWPACK only the four-sign count of the layers the site
shows. That count is coarse: SNOWPACK's own layers merged the way the site merges them score 40% instead of 45%,
and the observed pits themselves score 42%. SNOWPACK's best number, SK38, needs no grain size: shear strength from
density and grain type, the weight of the snow above and the stress a skier adds. Every input is in the column's
elements, so this module computes it there, element by element, exactly as SNOWPACK 3.x does
(``Stability::checkStability`` and ``StabilityAlgorithms``, strength model ``DEFAULT``):

- **Shear strength** (kPa) from the density relative to ice, by grain type (Jamieson and Johnston 1998, 2001; Abe
  2004): PP 2.85 r^1.13, DF 8.75 r^1.54, RG 7.39 r^1.20, MF 21.0 r^1.24, FC and DH 18.5 r^2.11, a melt-freeze crust
  4 kPa, surface hoar by Jamieson's 2003 regression (age, load, depth, thickness, temperature; at least 0.1). The
  strength at an element's top is the smaller of its own and that of the element above. A normal-load correction
  ``phi`` (Lehning et al. 2004) for PP, DF, RG and MF.
- **Stresses** reduced to a 38 degree slope: normal ``sig_n`` = load x cos^2(38), shear ``sig_s`` = sig_n x tan(38).
- **Skier** (Foehn 1987: 85 kg on 1.7 m skis, peak stress at 54.3 degrees) at the element's depth below the ski
  penetration ``Pk`` = 0.8 x 43.3 / mean density of the top 30 cm (at most the depth of a supporting crust 3 cm or
  thicker, denser than 500 kg m-3, in the top 30 cm).
- SK38 = (strength + phi x sig_n) / (sig_s + skier stress), between 0.05 and 6; 6 above the penetration depth.

**Depth alone** (``depth_only``): the same formula on a column of one uniform snow (rounded grains at the column's
mean density), the column's own depth. It shows how much of a hit rate comes from where a skier's stress peaks
rather than from the model's layering: test columns mostly break 20 to 40 cm down, which is where SK38 is smallest
in any column. A model's layering skill is its hit rate above this, not above chance.

Nothing here is fitted: the constants are SNOWPACK's, copied from its source (``StabilityAlgorithms.cc``). The module
sits in ``lab/services``, outside the prediction-code hash, so no forecast changes. Decision support only, not an
avalanche forecast.
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np

PSI_REF = math.radians(38.0)  # reference slope (Stability::psi_ref)
ALPHA_MAX = math.radians(54.3)  # angle of peak skier stress at 38 degrees (Schweizer, SLF IB 712)
MAX_STABILITY = 6.0  # Stability::max_stability
RHO_ICE = 917.0
G = 9.81
SKIER_LOAD_N_M = 85.0 * G / 1.7  # 85 kg on 1.7 m skis
PK_DEPTH_M = 0.3  # the penetration depth uses the mean density of the top 30 cm
MIN_CRUST_M = 0.03  # Stability::min_thick_crust
CRUST_RHO = 500.0
COLUMN_FAMILIES = {"column", "process", "process_melt"}  # families whose forecast is a ``column.Column``
IACS_CLASS = {"PP": 1, "DF": 2, "RG": 3, "FC": 4, "DH": 5, "SH": 6, "MF": 7, "MFcr": 7}


def shear_strength(cls_: str, rho: float, sig_n: float, *, age_d: float = 0.0, depth_m: float = 0.0,
                   hs_m: float = 0.0, thick_m: float = 0.0, temp_c: float = 0.0) -> tuple[float, float]:
    """Shear strength (kPa) and normal-load correction ``phi`` of one element (``setShearStrengthDEFAULT``)."""
    r = max(rho, 1.0) / RHO_ICE
    if cls_ == "MFcr":
        return 4.0, 0.0  # SNOWPACK's melt-freeze hack: Sig_c2 = 4 kPa
    if cls_ == "PP":
        s = 2.85 * r ** 1.13
        return s, 0.08 * s + 0.056 + 0.022 * sig_n
    if cls_ == "DF":
        s = 8.75 * r ** 1.54
        return s, 0.08 * s + 0.224
    if cls_ == "RG":
        s = 7.39 * r ** 1.20
        return s, 0.08 * s + 0.224
    if cls_ == "MF":
        s = 21.0 * r ** 1.24
        return s, 0.08 * s + 0.224
    if cls_ == "SH":  # sh_mod 2: Jamieson's regression without grain size (14 Feb 2003)
        s = (0.429 + 0.0138 * age_d + 1.12 * sig_n / math.cos(PSI_REF) ** 2 - 0.596 * depth_m + 0.0785 * hs_m
             - 27.1 * thick_m + 0.0202 * temp_c)
        return max(0.1, s), 0.0
    return 18.5 * r ** 2.11, 0.0  # FC, DH (no phi, as Jamieson and Johnston 1998)


def penetration_depth(dz: np.ndarray, rho: np.ndarray, cls_: list[str]) -> float:
    """Ski penetration (m), elements from the surface down (``compPenetrationDepth``)."""
    z = np.concatenate([[0.0], np.cumsum(dz)[:-1]])  # top depth of each element
    top = z < PK_DEPTH_M
    if not top.any() or dz[top].sum() <= 0:
        return 0.0
    rho_pk = float(np.average(rho[top], weights=dz[top]))
    pk = 0.8 * 43.3 / max(rho_pk, 1.0)
    crust_top, thick, run_start = None, 0.0, None
    for i in np.flatnonzero(top):
        if cls_[i] == "MFcr" and rho[i] > CRUST_RHO:
            if run_start is None:
                run_start, thick = z[i], 0.0
            thick += dz[i]
            if thick > MIN_CRUST_M:
                crust_top = run_start
                break
        else:
            run_start, thick = None, 0.0
    hs = float(dz.sum())
    return min(pk, hs if crust_top is None else crust_top)


def skier_stress_kpa(layer_depth_m: float) -> float:
    """Foehn's skier shear stress (kPa) at this depth below the penetration, reduced to 38 degrees."""
    d = 2.0 * SKIER_LOAD_N_M * math.cos(ALPHA_MAX) * math.sin(ALPHA_MAX) ** 2 * math.sin(ALPHA_MAX + PSI_REF)
    return d / (math.pi * layer_depth_m * math.cos(PSI_REF)) / 1000.0


def sk38(dz: np.ndarray, rho: np.ndarray, cls_: list[str], *, age_h: np.ndarray | None = None,
         temp_c: np.ndarray | None = None) -> list[float]:
    """SK38 at the top of each element, elements from the surface down (``Stability::checkStability``)."""
    n = len(dz)
    if n == 0:
        return []
    hs = float(dz.sum())
    pk = penetration_depth(dz, rho, cls_)
    out: list[float] = []
    upper = 1001.0  # strength_upper: the element above's strength
    load_above = 0.0
    depth = 0.0
    for i in range(n):
        mass = rho[i] * dz[i]
        load = (load_above + 0.5 * mass) * G  # Pa: the snow above and half the element (its middle)
        sig_n = load * math.cos(PSI_REF) ** 2 / 1000.0
        sig_s = sig_n * math.tan(PSI_REF)
        s, phi = shear_strength(cls_[i], float(rho[i]), sig_n,
                                age_d=float(age_h[i]) / 24.0 if age_h is not None else 0.0, depth_m=depth, hs_m=hs,
                                thick_m=float(dz[i]), temp_c=float(temp_c[i]) if temp_c is not None else 0.0)
        sc2 = min(s, upper)
        upper = s
        below = depth - pk
        if below > 1e-6:
            out.append(max(0.05, min((sc2 + phi * sig_n) / (sig_s + skier_stress_kpa(below)), MAX_STABILITY)))
        else:
            out.append(MAX_STABILITY)
        load_above += mass
        depth += float(dz[i])
    return out


def depth_only(dz: np.ndarray, rho: np.ndarray) -> list[float]:
    """SK38 of a column of one uniform snow (rounded grains at the mean density) with these element depths: what
    depth alone gives (ADR-147)."""
    if len(dz) == 0:
        return []
    mean = float(np.average(rho, weights=dz)) if dz.sum() > 0 else 250.0
    return sk38(dz, np.full(len(dz), mean), ["RG"] * len(dz))


def add_sk38_baseline(layers: list[dict]) -> None:
    """``depth_only`` on a stability profile's layers that carry a density (SNOWPACK's elements, ADR-147)."""
    if not layers or any(ly.get("density") is None for ly in layers):
        return
    dz = np.array([ly["bottom_m"] - ly["top_m"] for ly in layers])
    rho = np.array([ly["density"] for ly in layers], dtype=float)
    for ly, v in zip(layers, depth_only(dz, rho), strict=True):
        ly["depth_only"] = v


def column_of(case, genome):
    """The column a column-family agent forecasts for this case (the same run as its forecast)."""
    from snowagent.lab.agents import column as cm
    from snowagent.lab.agents import process as pr

    fam = genome.family.value
    if fam == "column":
        col, _ = cm.simulate(case, dict(genome.genes))
        return col
    if fam in ("process", "process_melt"):
        g = pr.genes_of(genome)
        col, _ = cm.simulate(case, g, pr.ProcessExt(g))
        return col
    raise ValueError(f"family {fam} has no column")


def column_elements(col) -> list[dict]:
    """The column's elements (surface first) as stability-profile layers with SK38 and the depth-alone baseline."""
    n = len(col)
    if n == 0:
        return []
    idx = list(range(n - 1, -1, -1))  # the column lists elements from the ground up
    dz = np.array([col.dz[i] for i in idx], dtype=float)
    rho = np.array([col.rho[i] for i in idx], dtype=float)
    cls_ = [col.cls[i] for i in idx]
    sk = sk38(dz, rho, cls_, age_h=np.array([col.age[i] for i in idx]), temp_c=np.array([col.T[i] for i in idx]))
    base = depth_only(dz, rho)
    out, z = [], 0.0
    for k in range(n):
        out.append({"top_m": round(z, 4), "bottom_m": round(z + dz[k], 4), "grain_size_mm": None,
                    "grain_class": IACS_CLASS.get(cls_[k]), "grain": cls_[k], "hardness": None,
                    "density": round(float(rho[k]), 1), "sk38": sk[k], "depth_only": base[k], "crack": None,
                    "rta": None})
        z += dz[k]
    return out


def column_profile(case_dir: Path, genome) -> list[dict]:
    """A column-family agent's elements for a case, with SK38 (ADR-147)."""
    from snowagent.lab.benchmark.loader import load_visible_case

    return column_elements(column_of(load_visible_case(case_dir), genome))


__all__ = ["COLUMN_FAMILIES", "add_sk38_baseline", "column_elements", "column_of", "column_profile", "depth_only",
           "penetration_depth", "shear_strength", "sk38", "skier_stress_kpa"]
