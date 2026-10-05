"""Directory layout of the lab's generated data (default ``data/lab``, gitignored). Everything under it is derived
and can be deleted and rebuilt with ``snowagent lab init`` and ``snowagent lab import``; the inputs it is built from
(``data/raw``, ``data/interim``, ``archive/``, ``profiles/``) are only read."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATA_ROOT = Path("data/lab")


@dataclass(frozen=True)
class LabPaths:
    root: Path = DEFAULT_DATA_ROOT

    @property
    def processed(self) -> Path:
        return self.root / "processed"

    @property
    def weather(self) -> Path:
        return self.processed / "weather" / "weather_hourly.parquet"

    @property
    def profiles(self) -> Path:
        return self.processed / "profiles" / "profiles.parquet"

    @property
    def layers(self) -> Path:
        return self.processed / "profiles" / "layers.parquet"

    @property
    def observations(self) -> Path:
        return self.processed / "observations" / "observations.parquet"

    @property
    def benchmark(self) -> Path:
        return self.root / "benchmark"

    @property
    def outputs(self) -> Path:
        return self.root / "outputs"

    @property
    def manifests(self) -> Path:
        return self.root / "manifests"

    @property
    def registry(self) -> Path:
        return self.root / "registry.sqlite"

    def directories(self) -> list[Path]:
        return [self.weather.parent, self.profiles.parent, self.observations.parent, self.manifests,
                *(self.benchmark / s for s in ("development", "validation", "sealed_test")),
                *(self.outputs / s for s in ("predictions", "reports", "exports"))]
