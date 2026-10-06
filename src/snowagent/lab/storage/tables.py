"""Parquet read/write. pyarrow (the ``lab`` extra) is needed only here and imported when a table is touched."""

from __future__ import annotations

from pathlib import Path

import pandas as pd


class LabExtraMissing(ImportError):
    """A lab dependency is not installed."""


def require(module: str) -> None:
    import importlib

    try:
        importlib.import_module(module)
    except ImportError as exc:
        raise LabExtraMissing(f"{module} is not installed; install the lab extra: pip install -e '.[lab]'") from exc


def write_table(df: pd.DataFrame, path: Path) -> Path:
    """Write atomically (temporary file, then rename) so a failed import never leaves half a table."""
    require("pyarrow")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".parquet.tmp")
    df.to_parquet(tmp, index=False, engine="pyarrow")
    tmp.replace(path)
    return path


def read_table(path: Path, columns: list[str] | None = None, filters: list | None = None) -> pd.DataFrame:
    """The table, or an empty frame when it has not been written yet (the UI's empty state)."""
    if not Path(path).exists():
        return pd.DataFrame(columns=columns or [])
    require("pyarrow")
    return pd.read_parquet(path, columns=columns, filters=filters, engine="pyarrow")
