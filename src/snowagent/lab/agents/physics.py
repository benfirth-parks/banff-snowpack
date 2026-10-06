"""SNOWPACK physics genes (milestone 5, ADR-070): from a genome's ``snowpack_physics`` block to the engine run.

Two kinds of gene:

- **Engine keys** (``INI_KEYS``): written into the run's ``io.ini``. The map gene -> (section, key) is the allow-list:
  a physics gene that is neither an engine key nor a forcing gene below is refused (``PhysicsError``), and so is a
  key the template is asked to take that is not in the map. Every key and its values were verified in the installed
  SNOWPACK source (20261002.b324cbd; ADR-070 lists the files and lines).
- **Forcing genes** (``FORCING_GENES``): how the visible weather is given to the engine. The precipitation factor per
  plot multiplies the adopted gauge factor (ADR-024/038) on the hours that factor applies to; the rain-snow ramp sets
  the PSUM_PH the forcing builder supplies (with PSUM_PH given, the engine's THRESH_RAIN is a fallback it never
  uses); the wind multiplier scales measured wind speed (the engine's WIND_SCALING_FACTOR scales only the drift wind,
  and erosion is off).

A gene at its default writes nothing: the incumbent's ``io.ini`` and forcing are reproduced byte for byte, so the
default genome is the milestone-3/4 incumbent. ``EnginePhysics`` is per plot (a Bow Summit run does not depend on
Goat's Eye's precipitation gene) and normalised (``HN_DENSITY_FIXEDVALUE`` only counts when ``HN_DENSITY = FIXED``,
the parameterisation only when ``PARAMETERIZED``), so its ``key`` names exactly what can change the engine profile;
the engine cache is keyed by it (output-only mutants reuse profiles).
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field

from snowagent.lab.schemas.genome import GenomeSpec, default_spec

PHYSICS_BLOCK = "snowpack_physics"


class PhysicsError(ValueError):
    """A physics gene or engine key outside the allow-list."""


# gene -> (ini section, ini key). Verified in the installed source (ADR-070).
INI_KEYS: dict[str, tuple[str, str]] = {
    "sp_hn_density": ("SnowpackAdvanced", "HN_DENSITY"),
    "sp_hn_density_parameterization": ("SnowpackAdvanced", "HN_DENSITY_PARAMETERIZATION"),
    "sp_hn_density_fixed_kg_m3": ("SnowpackAdvanced", "HN_DENSITY_FIXEDVALUE"),
    "sp_viscosity_model": ("SnowpackAdvanced", "VISCOSITY_MODEL"),
    "sp_roughness_length_m": ("Snowpack", "ROUGHNESS_LENGTH"),
    "sp_hoar_thresh_ta_c": ("SnowpackAdvanced", "HOAR_THRESH_TA"),
    "sp_hoar_thresh_rh": ("SnowpackAdvanced", "HOAR_THRESH_RH"),
    "sp_hoar_thresh_vw_ms": ("SnowpackAdvanced", "HOAR_THRESH_VW"),
    "sp_hoar_density_buried_kg_m3": ("SnowpackAdvanced", "HOAR_DENSITY_BURIED"),
    "sp_hoar_min_size_buried_mm": ("SnowpackAdvanced", "HOAR_MIN_SIZE_BURIED"),
}
# What the incumbent runs with for each engine key: the template's value where it sets the key, else the engine's
# default (SnowpackConfig.cc, advancedConfig / [Snowpack]). The gene defaults in config/lab.yaml must equal these
# (tested), which is what makes "a default gene writes nothing" reproduce the incumbent.
INCUMBENT_INI: dict[str, str] = {
    "HN_DENSITY": "PARAMETERIZED",  # template
    "HN_DENSITY_PARAMETERIZATION": "LEHNING_NEW",  # engine default
    "HN_DENSITY_FIXEDVALUE": "100",  # engine default (100.)
    "VISCOSITY_MODEL": "DEFAULT",  # engine default
    "ROUGHNESS_LENGTH": "0.002",  # template
    "HOAR_THRESH_TA": "1.2",  # engine default
    "HOAR_THRESH_RH": "0.97",  # engine default
    "HOAR_THRESH_VW": "3.5",  # engine default
    "HOAR_DENSITY_BURIED": "125",  # engine default (125.)
    "HOAR_MIN_SIZE_BURIED": "2",  # engine default (2.)
}
PRECIP_GENE = "sp_precip_mult_{site}"  # one per lab site code (bow, goat, simp)
FORCING_GENES = ("sp_rain_snow_mid_c", "sp_rain_snow_width_k", "sp_wind_mult")
# the forcing builder's ramp (spatial_forcing.builder.ForcingConfig): all snow at 0.2 degC, all rain at 2.2 degC
INCUMBENT_RAIN_SNOW = (0.2, 2.2)


def _is_precip(gene: str) -> bool:
    return gene.startswith("sp_precip_mult_")


def check_spec(spec: GenomeSpec | None = None) -> None:
    """The ``snowpack_physics`` block holds only mapped engine keys and forcing genes (else ``PhysicsError``)."""
    s = spec or default_spec()
    for g in s.blocks.get(PHYSICS_BLOCK, {}):
        if g not in INI_KEYS and g not in FORCING_GENES and not _is_precip(g):
            raise PhysicsError(f"physics gene {g} maps to no verified engine key or forcing input")


def _fmt(v) -> str:
    if isinstance(v, str):
        return v
    return f"{float(v):.6g}"


@dataclass(frozen=True)
class EnginePhysics:
    """The physics of one engine run (one plot): ini overrides (section, key, value) and forcing modifiers.
    ``EnginePhysics()`` is the incumbent."""

    ini: tuple[tuple[str, str, str], ...] = ()
    precip_mult: float = 1.0
    wind_mult: float = 1.0
    rain_snow_c: tuple[float, float] | None = None  # (all snow at, all rain at) degC; None = the builder's ramp
    genes: dict = field(default_factory=dict, compare=False, hash=False)  # the genes it came from (display)

    def __post_init__(self) -> None:
        allowed = {(sec, key) for sec, key in INI_KEYS.values()}
        for sec, key, _v in self.ini:
            if (sec, key) not in allowed:
                raise PhysicsError(f"engine key [{sec}] {key} is not in the verified allow-list")

    @property
    def is_default(self) -> bool:
        return not self.ini and self.precip_mult == 1.0 and self.wind_mult == 1.0 and self.rain_snow_c is None

    def as_dict(self) -> dict:
        return {"ini": [list(x) for x in self.ini], "precip_mult": _fmt(self.precip_mult),
                "wind_mult": _fmt(self.wind_mult),
                "rain_snow_c": None if self.rain_snow_c is None else [_fmt(x) for x in self.rain_snow_c]}

    @property
    def key(self) -> str:
        """``default`` for the incumbent, else a hash of the normalised physics (what the engine cache is keyed by)."""
        if self.is_default:
            return "default"
        return hashlib.sha256(json.dumps(self.as_dict(), sort_keys=True).encode()).hexdigest()[:24]

    def forcing_config(self):
        from snowagent.spatial_forcing.builder import ForcingConfig

        if self.rain_snow_c is None:
            return ForcingConfig()
        return ForcingConfig(phase_t_snow_c=self.rain_snow_c[0], phase_t_rain_c=self.rain_snow_c[1])

    def apply_ini(self, text: str) -> str:
        """The rendered ``io.ini`` with the overrides: an existing key in its section is replaced, a missing one is
        appended to the section."""
        for sec, key, val in self.ini:
            text = set_ini_key(text, sec, key, val)
        return text


def set_ini_key(text: str, section: str, key: str, value: str) -> str:
    lines = text.split("\n")
    start = next((i for i, ln in enumerate(lines) if ln.strip() == f"[{section}]"), None)
    if start is None:
        raise PhysicsError(f"engine template has no [{section}] section")
    end = next((i for i in range(start + 1, len(lines)) if lines[i].strip().startswith("[")), len(lines))
    pat = re.compile(rf"^\s*{re.escape(key)}\s*=")
    hits = [i for i in range(start + 1, end) if pat.match(lines[i])]
    if len(hits) > 1:
        raise PhysicsError(f"engine template sets [{section}] {key} more than once")
    if hits:
        lines[hits[0]] = f"{key} = {value}"
    else:
        last = end
        while last > start + 1 and not lines[last - 1].strip():
            last -= 1  # keep the blank line before the next section
        lines.insert(last, f"{key} = {value}")
    return "\n".join(lines)


def engine_physics(genes: dict, site_code: str, spec: GenomeSpec | None = None) -> EnginePhysics:
    """The normalised physics of a genome's genes for one plot (``site_code`` BOW, GOAT, SIMP). Genes outside the
    physics block are ignored; a genome without the block is the incumbent."""
    s = spec or default_spec()
    block = s.blocks.get(PHYSICS_BLOCK, {})
    phys = {g: genes[g] for g in block if g in genes}
    if not phys:
        return EnginePhysics()
    check_spec(s)
    for g, v in phys.items():
        block[g].validate_value(v)  # out of range or not a choice: refused
    dflt = {g: gs.default for g, gs in block.items()}

    def val(g):
        return phys.get(g, dflt[g])

    def changed(g) -> bool:
        a, b = val(g), dflt[g]
        return a != b if isinstance(a, str) or isinstance(b, str) else f"{float(a):.12g}" != f"{float(b):.12g}"

    active = set(INI_KEYS)
    if val("sp_hn_density") == "FIXED":
        active.discard("sp_hn_density_parameterization")
    else:
        active.discard("sp_hn_density_fixed_kg_m3")
    ini = []
    for g in INI_KEYS:
        if g not in block or g not in active:
            continue
        if changed(g) or (g == "sp_hn_density_fixed_kg_m3" and changed("sp_hn_density")):
            sec, key = INI_KEYS[g]
            ini.append((sec, key, _fmt(val(g))))
    pg = PRECIP_GENE.format(site=str(site_code).lower())
    precip = float(val(pg)) if pg in block and changed(pg) else 1.0
    wind = float(val("sp_wind_mult")) if "sp_wind_mult" in block and changed("sp_wind_mult") else 1.0
    rs = None
    if any(g in block and changed(g) for g in ("sp_rain_snow_mid_c", "sp_rain_snow_width_k")):
        mid, width = float(val("sp_rain_snow_mid_c")), float(val("sp_rain_snow_width_k"))
        rs = (round(mid - width / 2, 9), round(mid + width / 2, 9))
    return EnginePhysics(ini=tuple(sorted(ini)), precip_mult=precip, wind_mult=wind, rain_snow_c=rs, genes=phys)


def has_physics(genes: dict, spec: GenomeSpec | None = None) -> bool:
    s = spec or default_spec()
    return any(g in genes for g in s.blocks.get(PHYSICS_BLOCK, {}))


def physics_keys(genes: dict, sites, spec: GenomeSpec | None = None) -> dict[str, str]:
    """site code -> physics key (what a genome's engine profiles are cached under)."""
    return {str(s): engine_physics(genes, str(s), spec).key for s in sites}


__all__ = ["FORCING_GENES", "INCUMBENT_INI", "INCUMBENT_RAIN_SNOW", "INI_KEYS", "PHYSICS_BLOCK", "EnginePhysics",
           "PhysicsError", "check_spec", "engine_physics", "has_physics", "physics_keys", "set_ini_key"]
