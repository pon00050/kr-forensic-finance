import pandas as pd
from pathlib import Path


def run_beneish_screen(parquet_path: str | Path) -> pd.DataFrame:
    """Load already-computed Beneish scores. Produces no side effects."""
    return pd.read_parquet(parquet_path)


__all__ = ["run_beneish_screen"]
