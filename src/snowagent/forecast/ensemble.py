"""Reproducible scenario perturbations of source forcing.

Member 0 is the unperturbed control. Other members get spatially coherent
(domain-wide) perturbations: a constant log-normal precipitation factor, and
AR(1) temporally autocorrelated offsets for air temperature, shortwave (relative)
and longwave. Spread is SCENARIO uncertainty, not calibrated probability.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from snowagent.contracts import EnsembleConfig, WhatIf


def _ar1(rng: np.random.Generator, n: int, phi: float, sigma: float) -> np.ndarray:
    if sigma == 0:
        return np.zeros(n)
    innov = rng.normal(0.0, sigma * np.sqrt(1 - phi**2), n)
    out = np.empty(n)
    out[0] = rng.normal(0.0, sigma)
    for i in range(1, n):
        out[i] = phi * out[i - 1] + innov[i]
    return out


def member_perturbations(cfg: EnsembleConfig, n_hours: int) -> list[dict[str, np.ndarray | float]]:
    """Deterministic given (seed, members, n_hours)."""
    members: list[dict[str, np.ndarray | float]] = [
        {"ta_offset_k": np.zeros(n_hours), "psum_factor": 1.0, "iswr_factor": np.ones(n_hours),
         "ilwr_offset": np.zeros(n_hours)}]
    for m in range(1, cfg.members):
        rng = np.random.default_rng([cfg.seed, m])
        members.append({
            "ta_offset_k": _ar1(rng, n_hours, cfg.ar1_hourly, cfg.ta_sigma_k),
            "psum_factor": float(np.exp(rng.normal(-0.5 * cfg.psum_log_sigma**2, cfg.psum_log_sigma))),
            "iswr_factor": np.clip(1.0 + _ar1(rng, n_hours, cfg.ar1_hourly, cfg.iswr_rel_sigma), 0.0, 2.0),
            "ilwr_offset": _ar1(rng, n_hours, cfg.ar1_hourly, cfg.ilwr_sigma_wm2),
        })
    return members


def apply(src: pd.DataFrame, pert: dict, what_if: WhatIf) -> pd.DataFrame:
    out = src.copy()
    out["ta"] = out["ta"] + pert["ta_offset_k"] + what_if.ta_offset_k
    out["psum"] = out["psum"] * pert["psum_factor"] * what_if.psum_factor
    out["iswr"] = (out["iswr"] * pert["iswr_factor"] * what_if.iswr_factor).clip(lower=0)
    out["ilwr"] = out["ilwr"] + pert["ilwr_offset"]
    return out


def summarize(pert: dict) -> dict[str, float]:
    return {
        "ta_offset_mean_k": float(np.mean(pert["ta_offset_k"])),
        "ta_offset_sd_k": float(np.std(pert["ta_offset_k"])),
        "psum_factor": float(pert["psum_factor"]),
        "iswr_factor_mean": float(np.mean(pert["iswr_factor"])),
        "ilwr_offset_mean_wm2": float(np.mean(pert["ilwr_offset"])),
    }
