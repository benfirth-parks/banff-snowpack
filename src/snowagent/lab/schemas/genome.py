"""Agent genome (milestone 3, ADR-061): an agent family plus that family's bounded genes, and nothing else.

The owner (2026-10-05): "each agent needs a 'genome'" and "we need to ensure agents just dont memorize these
snowpacks". So a genome is a few dozen numbers or categories from one allow-list, ``config/lab.yaml`` ``genome``:
blocks of genes, each gene with its kind (float, int or choice), range, unit and default, and per family the blocks
it carries. Validation rejects an unknown gene, a missing gene, a value out of range or of the wrong type, and a
genome larger than the size guard (``max_genes``, ``max_bytes``): no profile, layer, date or case data fits.

The genome hash identifies behaviour: family and genes only (canonical JSON, floats rounded to 12 significant
digits), not the label or lineage. Mutation and crossover are in ``snowagent.lab.genome``.
"""

from __future__ import annotations

import hashlib
import json
import re
from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import Field, ValidationInfo, field_validator, model_validator

from snowagent.lab.schemas.common import LabModel

GENOME_SCHEMA = "lab-genome-4"  # 3: SNOWPACK physics genes (milestone 5, ADR-070); 4: weak-layer genes (ADR-092)
# Blocks a genome of an earlier schema version does not carry: its records (milestone-3/4 runs, lineage) still
# validate, with their hashes unchanged, and such a genome runs the incumbent's physics. ``upgrade_genome`` (in
# ``snowagent.lab.genome``) gives it the new blocks at their defaults.
LEGACY_BLOCKS: dict[str, tuple[str, ...]] = {"lab-genome-2": ("snowpack_physics", "snowpack_weak_layers"),
                                             "lab-genome-3": ("snowpack_weak_layers",)}
DEFAULT_LAB_CONFIG = Path(__file__).resolve().parents[4] / "config" / "lab.yaml"
LABEL = re.compile(r"^[A-Za-z0-9_.:+-]{1,64}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")
GeneValue = float | int | str


class AgentFamily(StrEnum):
    persistence = "persistence"  # last available pit carried forward, adjusted for depth change
    weather_rule = "weather_rule"  # layers built from the visible weather by bounded rules
    analogue = "analogue"  # nearest past cases of other seasons by weather features
    snowpack = "snowpack"  # the SNOWPACK engine (the incumbent)
    hybrid = "hybrid"  # SNOWPACK structure blended with pit and rule adjustments


class GeneSpec(LabModel):
    """One allow-listed gene: kind, range (or choices), default, unit and meaning."""

    kind: Literal["float", "int", "choice"] = "float"
    min: float | None = None
    max: float | None = None
    choices: list[str] | None = None
    default: GeneValue
    unit: str
    doc: str

    @model_validator(mode="after")
    def _check(self) -> GeneSpec:
        if self.kind == "choice":
            if not self.choices or len(set(self.choices)) != len(self.choices):
                raise ValueError("a choice gene lists distinct choices")
            if self.default not in self.choices:
                raise ValueError(f"default {self.default!r} is not one of {self.choices}")
            return self
        if self.min is None or self.max is None or not self.min < self.max:
            raise ValueError("a numeric gene needs min < max")
        self.validate_value(self.default)
        return self

    def validate_value(self, v: Any) -> GeneValue:
        """The value as the gene's type, or ValueError (wrong type, out of range, not a choice)."""
        if self.kind == "choice":
            if not isinstance(v, str) or v not in (self.choices or []):
                raise ValueError(f"{v!r} is not one of {self.choices}")
            return v
        if isinstance(v, bool) or not isinstance(v, int | float):
            raise ValueError(f"{v!r} is not a number")
        if self.kind == "int":
            if float(v) != int(v):
                raise ValueError(f"{v!r} is not an integer")
            v = int(v)
        else:
            v = float(v)
            if v != v or v in (float("inf"), float("-inf")):
                raise ValueError("not a finite number")
        if not self.min <= v <= self.max:  # type: ignore[operator]
            raise ValueError(f"{v} is outside [{self.min:g}, {self.max:g}]")
        return v


class GenomeSpec(LabModel):
    """The allow-list (``config/lab.yaml`` ``genome``): gene blocks and each family's blocks."""

    max_genes: int = Field(default=64, ge=1, le=256)
    max_bytes: int = Field(default=4096, ge=256, le=65536)
    blocks: dict[str, dict[str, GeneSpec]]
    families: dict[AgentFamily, list[str]]

    @model_validator(mode="after")
    def _check(self) -> GenomeSpec:
        seen: dict[str, str] = {}
        for block, genes in self.blocks.items():
            for g in genes:
                if g in seen:
                    raise ValueError(f"gene {g} is in blocks {seen[g]} and {block}: gene names are unique")
                seen[g] = block
        missing = set(AgentFamily) - set(self.families)
        if missing:
            raise ValueError(f"genome.families lacks {sorted(missing)}")
        for fam, blocks in self.families.items():
            unknown = [b for b in blocks if b not in self.blocks]
            if unknown or len(set(blocks)) != len(blocks):
                raise ValueError(f"family {fam}: unknown or repeated blocks {unknown or blocks}")
            if sum(len(self.blocks[b]) for b in blocks) > self.max_genes:
                raise ValueError(f"family {fam} has more than max_genes {self.max_genes} genes")
        return self

    def family_genes(self, family: AgentFamily | str) -> dict[str, tuple[str, GeneSpec]]:
        """Gene -> (block, spec) of a family, in block order."""
        return {g: (b, s) for b in self.families[AgentFamily(family)] for g, s in self.blocks[b].items()}

    def spec_hash(self) -> str:
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()

    def for_version(self, schema_version: str) -> GenomeSpec:
        """The allow-list as a genome of ``schema_version`` saw it (without the blocks added later)."""
        drop = LEGACY_BLOCKS.get(schema_version, ())
        if not drop:
            return self
        return self.model_copy(update={"families": {f: [b for b in bl if b not in drop]
                                                    for f, bl in self.families.items()}})


@lru_cache(maxsize=4)
def _spec_from(path: str, _mtime_ns: int) -> GenomeSpec:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    if "genome" not in raw:
        raise ValueError(f"{path} has no genome section")
    return GenomeSpec(**raw["genome"])


def default_spec(path: Path = DEFAULT_LAB_CONFIG) -> GenomeSpec:
    """The repository's allow-list (``config/lab.yaml``); runs pass the loaded config's spec explicitly."""
    return _spec_from(str(path), Path(path).stat().st_mtime_ns)


def _canonical(v: GeneValue) -> GeneValue:
    return float(f"{v:.12g}") if isinstance(v, float) else v


class AgentGenome(LabModel):
    """A family and its genes. Validated against the spec passed as ``context={"spec": spec}`` (default: the
    repository's ``config/lab.yaml``)."""

    schema_version: Literal["lab-genome-2", "lab-genome-3", "lab-genome-4"] = GENOME_SCHEMA
    family: AgentFamily
    genes: dict[str, GeneValue]
    label: str | None = None  # display name (letters, digits, _.:+-), never part of the hash
    origin: Literal["default", "file", "mutation", "crossover"] = "default"
    parents: list[str] = Field(default_factory=list, max_length=2)  # genome hashes (lineage)

    @field_validator("genes", mode="before")
    @classmethod
    def _scalars(cls, v: Any) -> Any:
        if isinstance(v, dict):
            bad = [k for k, x in v.items() if isinstance(x, bool) or not isinstance(x, int | float | str)]
            if bad:
                raise ValueError(f"genes {bad} are not scalar numbers or choices (a genome holds no data)")
        return v

    @model_validator(mode="after")
    def _check(self, info: ValidationInfo) -> AgentGenome:
        spec = ((info.context or {}).get("spec") or default_spec()).for_version(self.schema_version)
        allowed = spec.family_genes(self.family)
        unknown = sorted(set(self.genes) - set(allowed))
        missing = sorted(set(allowed) - set(self.genes))
        if unknown:
            raise ValueError(f"{self.family} genome: unknown genes {unknown} (not in the allow-list)")
        if missing:
            raise ValueError(f"{self.family} genome: missing genes {missing}")
        clean = {}
        for g, (_block, s) in allowed.items():
            try:
                clean[g] = s.validate_value(self.genes[g])
            except ValueError as exc:
                raise ValueError(f"{self.family} genome: gene {g}: {exc}") from None
        object.__setattr__(self, "genes", clean)  # typed (int genes as int), in allow-list order
        if self.family == AgentFamily.hybrid and not any(
                clean[w] > 0 for w in ("snowpack_weight", "persistence_weight", "rule_weight")):
            raise ValueError("hybrid genome: at least one blend weight must be above 0")
        if self.label is not None and not LABEL.match(self.label):
            raise ValueError("label: 1-64 letters, digits or _.:+-")
        if any(not SHA256.match(p) for p in self.parents):
            raise ValueError("parents are genome hashes (sha256 hex)")
        if len(self.genes) > spec.max_genes:
            raise ValueError(f"genome has {len(self.genes)} genes, more than max_genes {spec.max_genes}")
        size = len(self.model_dump_json().encode())
        if size > spec.max_bytes:
            raise ValueError(f"genome is {size} bytes, more than max_bytes {spec.max_bytes} (a genome holds no data)")
        return self

    @property
    def genome_hash(self) -> str:
        """sha256 of the family and genes (canonical JSON); label and lineage excluded."""
        ident = {"schema_version": self.schema_version, "family": self.family.value,
                 "genes": {k: _canonical(v) for k, v in sorted(self.genes.items())}}
        return hashlib.sha256(json.dumps(ident, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

    @property
    def agent_id(self) -> str:
        """``<family>-<first 10 hex of the genome hash>``: the id scores and leaderboards use."""
        return f"{self.family.value}-{self.genome_hash[:10]}"

    @property
    def display_name(self) -> str:
        return self.label or self.agent_id

    def gene(self, name: str) -> GeneValue:
        return self.genes[name]
