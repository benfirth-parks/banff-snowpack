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
WEAK_BLOCK = "snowpack_weak_layers"  # ADR-092: the weak-layer genes (genome schema 4); physics like the block above
PHYSICS_BLOCKS = (PHYSICS_BLOCK, WEAK_BLOCK)


def physics_block(spec: GenomeSpec) -> dict:
    """Every physics gene of the spec (both blocks), gene -> GeneSpec."""
    return {g: gs for b in PHYSICS_BLOCKS for g, gs in spec.blocks.get(b, {}).items()}


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
    # ADR-092: weak-layer genes
    "sp_hoar_density_surf_kg_m3": ("SnowpackAdvanced", "HOAR_DENSITY_SURF"),
    "sp_hoar_min_size_surf_mm": ("SnowpackAdvanced", "HOAR_MIN_SIZE_SURF"),
    "sp_atmospheric_stability": ("Snowpack", "ATMOSPHERIC_STABILITY"),
    "sp_vapour_transport": ("SnowpackAdvanced", "ENABLE_VAPOUR_TRANSPORT"),
    "sp_facet_dpdz_hpa_m": ("SnowpackAdvanced", "LAB_FACET_DPDZ"),
    "sp_facet_rate": ("SnowpackAdvanced", "LAB_FACET_RATE"),
    "sp_crust_facet": ("SnowpackAdvanced", "LAB_CRUST_FACET"),
}
# ADR-092: engine keys that exist only in an engine built with this repository's patch (scripts/snowpack-patches);
# an engine without it ignores them silently, so they are refused there (``require_patches``)
PATCHED_KEYS: dict[str, str] = {"LAB_FACET_DPDZ": "lab-facet-knobs", "LAB_FACET_RATE": "lab-facet-knobs",
                                "LAB_CRUST_FACET": "lab-facet-knobs"}
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
    "HOAR_DENSITY_SURF": "100",  # engine default (100.)
    "HOAR_MIN_SIZE_SURF": "0.5",  # engine default (0.5)
    "ATMOSPHERIC_STABILITY": "MO_SCHLOEGL_MULTI_OFFSET",  # template (terrain_column.ini)
    "ENABLE_VAPOUR_TRANSPORT": "false",  # engine default
    "LAB_FACET_DPDZ": "5",  # patched engine: Metamorphism::mm_tg_dpdz
    "LAB_FACET_RATE": "1",  # patched engine: no change
    "LAB_CRUST_FACET": "1",  # patched engine: no change
}
PRECIP_GENE = "sp_precip_mult_{site}"  # one per lab site code (bow, goat, simp)
FORCING_GENES = ("sp_rain_snow_mid_c", "sp_rain_snow_width_k", "sp_wind_mult", "sp_ilwr_offset_wm2",
                 "sp_ground_temp_c")
TA_GENE = "sp_ta_offset_{site}_k"  # ADR-092: one per lab site code, like the precipitation factor
# the forcing builder's ramp (spatial_forcing.builder.ForcingConfig): all snow at 0.2 degC, all rain at 2.2 degC
INCUMBENT_RAIN_SNOW = (0.2, 2.2)


def _is_precip(gene: str) -> bool:
    return gene.startswith("sp_precip_mult_") or gene.startswith("sp_ta_offset_")


def check_spec(spec: GenomeSpec | None = None) -> None:
    """The ``snowpack_physics`` block holds only mapped engine keys and forcing genes (else ``PhysicsError``)."""
    s = spec or default_spec()
    for g in physics_block(s):
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
    ta_offset_k: float = 0.0  # ADR-092: added to air temperature (measured and reanalysis hours)
    ilwr_offset_wm2: float = 0.0  # ADR-092: added to incoming longwave (measured and reanalysis hours)
    ground_temp_c: float | None = None  # ADR-092: TSG lower boundary; None = the builder's 0 degC
    genes: dict = field(default_factory=dict, compare=False, hash=False)  # the genes it came from (display)

    def __post_init__(self) -> None:
        allowed = {(sec, key) for sec, key in INI_KEYS.values()}
        for sec, key, _v in self.ini:
            if (sec, key) not in allowed:
                raise PhysicsError(f"engine key [{sec}] {key} is not in the verified allow-list")

    @property
    def is_default(self) -> bool:
        return (not self.ini and self.precip_mult == 1.0 and self.wind_mult == 1.0 and self.rain_snow_c is None
                and self.ta_offset_k == 0.0 and self.ilwr_offset_wm2 == 0.0 and self.ground_temp_c is None)

    def as_dict(self) -> dict:
        d = {"ini": [list(x) for x in self.ini], "precip_mult": _fmt(self.precip_mult),
             "wind_mult": _fmt(self.wind_mult),
             "rain_snow_c": None if self.rain_snow_c is None else [_fmt(x) for x in self.rain_snow_c]}
        # ADR-092 fields only when set, so the keys of earlier physics stay as they were
        if self.ta_offset_k != 0.0:
            d["ta_offset_k"] = _fmt(self.ta_offset_k)
        if self.ilwr_offset_wm2 != 0.0:
            d["ilwr_offset_wm2"] = _fmt(self.ilwr_offset_wm2)
        if self.ground_temp_c is not None:
            d["ground_temp_c"] = _fmt(self.ground_temp_c)
        return d

    @property
    def patches(self) -> frozenset[str]:
        """The engine patches this physics needs (ADR-092)."""
        return frozenset(PATCHED_KEYS[key] for _sec, key, _v in self.ini if key in PATCHED_KEYS)

    def adjust_forcing(self, data, measured: dict):
        """ADR-092: the air temperature and longwave offsets on a forcing frame (columns ``ta`` in K, ``ilwr``),
        on the rows ``measured[col]`` marks (not GFS hours); returns the frame (a copy when anything changes)."""
        if self.ta_offset_k == 0.0 and self.ilwr_offset_wm2 == 0.0:
            return data
        data = data.copy()
        if self.ta_offset_k != 0.0:
            m = measured["ta"]
            data.loc[m, "ta"] = data.loc[m, "ta"] + self.ta_offset_k
        if self.ilwr_offset_wm2 != 0.0:
            m = measured["ilwr"]
            data.loc[m, "ilwr"] = (data.loc[m, "ilwr"] + self.ilwr_offset_wm2).clip(lower=50.0)
        return data

    @property
    def key(self) -> str:
        """``default`` for the incumbent, else a hash of the normalised physics (what the engine cache is keyed by)."""
        if self.is_default:
            return "default"
        return hashlib.sha256(json.dumps(self.as_dict(), sort_keys=True).encode()).hexdigest()[:24]

    def forcing_config(self):
        from snowagent.spatial_forcing.builder import ForcingConfig

        kw = {}
        if self.rain_snow_c is not None:
            kw |= {"phase_t_snow_c": self.rain_snow_c[0], "phase_t_rain_c": self.rain_snow_c[1]}
        if self.ground_temp_c is not None:
            kw["ground_temperature_k"] = self.ground_temp_c + 273.15
        return ForcingConfig(**kw)

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
    block = physics_block(s)
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
    tg = TA_GENE.format(site=str(site_code).lower())
    ta = float(val(tg)) if tg in block and changed(tg) else 0.0
    lw = float(val("sp_ilwr_offset_wm2")) if "sp_ilwr_offset_wm2" in block and changed("sp_ilwr_offset_wm2") else 0.0
    gt = float(val("sp_ground_temp_c")) if "sp_ground_temp_c" in block and changed("sp_ground_temp_c") else None
    rs = None
    if any(g in block and changed(g) for g in ("sp_rain_snow_mid_c", "sp_rain_snow_width_k")):
        mid, width = float(val("sp_rain_snow_mid_c")), float(val("sp_rain_snow_width_k"))
        rs = (round(mid - width / 2, 9), round(mid + width / 2, 9))
    return EnginePhysics(ini=tuple(sorted(ini)), precip_mult=precip, wind_mult=wind, rain_snow_c=rs, ta_offset_k=ta,
                         ilwr_offset_wm2=lw, ground_temp_c=gt, genes=phys)


def engine_patches(binary: str) -> frozenset[str]:
    """The repository patches an engine was built with (ADR-092): ``share/banff-snowpack/patches.txt`` beside its
    ``bin/`` (written by scripts/build_snowpack.sh); none for an engine built without them."""
    from pathlib import Path

    f = Path(binary).resolve().parent.parent / "share" / "banff-snowpack" / "patches.txt"
    try:
        return frozenset(x.strip() for x in f.read_text().split() if x.strip())
    except OSError:
        return frozenset()


def require_patches(physics: EnginePhysics, binary: str) -> None:
    """``PhysicsError`` when the physics needs a patch the engine at ``binary`` lacks (it would ignore the key)."""
    missing = physics.patches - engine_patches(binary)
    if missing:
        raise PhysicsError(f"this agent's faceting settings need the SNOWPACK engine rebuilt with this repository's "
                           f"patches ({', '.join(sorted(missing))}): run bash scripts/build_snowpack.sh")


def has_physics(genes: dict, spec: GenomeSpec | None = None) -> bool:
    s = spec or default_spec()
    return any(g in genes for g in physics_block(s))


def physics_keys(genes: dict, sites, spec: GenomeSpec | None = None) -> dict[str, str]:
    """site code -> physics key (what a genome's engine profiles are cached under)."""
    return {str(s): engine_physics(genes, str(s), spec).key for s in sites}


__all__ = ["FORCING_GENES", "INCUMBENT_INI", "INCUMBENT_RAIN_SNOW", "INI_KEYS", "PATCHED_KEYS", "PHYSICS_BLOCK",
           "PHYSICS_BLOCKS", "WEAK_BLOCK", "physics_block",
           "TA_GENE", "EnginePhysics", "PhysicsError", "check_spec", "engine_patches", "engine_physics", "has_physics",
           "physics_keys", "require_patches", "set_ini_key"]
