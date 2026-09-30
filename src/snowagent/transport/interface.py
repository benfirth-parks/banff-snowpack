"""Lateral snow-transport coupling contract (design only; NOT active in this milestone).

The first milestone runs independent columns and reports ``transport_status:
unresolved``. A future adapter (e.g. an Alpine3D-derived solver) must implement
:class:`TransportAdapter` and pass :func:`check_conservation` before any run may
report ``resolved``. Exposure indices alone must never be turned into deposition.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class UnitTransfer:
    """Mass moved during one coupling step, kg per unit (not per m2), >= 0."""

    source_unit: str
    target_unit: str | None  # None = exported across the domain boundary
    mass_kg: float
    source_layer_lineage: list[str] = field(default_factory=list)
    deposited_density_kg_m3: float | None = None  # must be specified for deposition
    deposited_grain_form: str | None = None


@dataclass(frozen=True)
class StepBudget:
    snowfall_kg: float
    rain_kg: float
    melt_export_kg: float
    sublimation_kg: float  # includes blowing-snow sublimation
    boundary_export_kg: float
    boundary_import_kg: float
    storage_change_kg: float


class TransportAdapter(Protocol):
    name: str
    version: str

    def erodible_mass(self, unit_id: str) -> float:
        """Only available eligible surface snow; never negative."""

    def step(self, wind_field: dict, dt_s: float) -> tuple[list[UnitTransfer], StepBudget]:
        """Compute transfers; must not create atmospheric snowfall."""


def check_conservation(b: StepBudget, rel_tol: float = 1e-6, abs_tol_kg: float = 1.0) -> float:
    """Domain storage change must equal inputs minus exports; transport is internal only."""
    expected = (b.snowfall_kg + b.rain_kg - b.melt_export_kg - b.sublimation_kg
                - b.boundary_export_kg + b.boundary_import_kg)
    residual = b.storage_change_kg - expected
    if abs(residual) > max(abs_tol_kg, rel_tol * (abs(b.snowfall_kg) + abs(b.rain_kg))):
        raise ValueError(f"transport budget does not close: residual {residual:.3f} kg")
    return residual


def check_transfers(transfers: list[UnitTransfer], erodible: dict[str, float]) -> None:
    taken: dict[str, float] = {}
    for t in transfers:
        if t.mass_kg < 0:
            raise ValueError("negative transfer")
        if t.target_unit is not None and t.deposited_density_kg_m3 is None:
            raise ValueError("deposition must specify transported-snow density")
        taken[t.source_unit] = taken.get(t.source_unit, 0.0) + t.mass_kg
    for uid, m in taken.items():
        if m > erodible.get(uid, 0.0) + 1e-9:
            raise ValueError(f"unit {uid} eroded {m} kg > available {erodible.get(uid, 0.0)} kg")


STATUS = "unresolved"
