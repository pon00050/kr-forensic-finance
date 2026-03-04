"""
conftest.py — pytest configuration for kr-forensic-finance tests.

Adds 02_Pipeline to sys.path so test files can import extract_dart,
transform, etc. directly without installing the package.
"""

import sys
from pathlib import Path

# Allow tests to import pipeline modules by name (extract_dart, transform, etc.)
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "02_Pipeline"))
