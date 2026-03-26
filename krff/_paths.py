"""src/_paths.py — Canonical project path constants."""

from __future__ import annotations

import os
from pathlib import Path

_default_root = Path(__file__).resolve().parents[1]
PROJECT_ROOT = Path(os.environ.get("KRFF_PROJECT_ROOT", str(_default_root)))
PROCESSED_DIR = Path(os.environ.get("KRFF_DATA_DIR", str(PROJECT_ROOT / "01_Data" / "processed")))
DATABASE_URL: str | None = os.environ.get("DATABASE_URL")
