"""pipeline.py — Proxy for kr-dart-pipeline.

The ETL logic has moved to the kr-dart-pipeline standalone package.
This module keeps the krff-shell interface stable.
"""

from __future__ import annotations


def run_pipeline(*args, **kwargs):
    """Delegate to kr_dart_pipeline.run(). Falls back to 02_Pipeline/ if package not installed."""
    try:
        from kr_dart_pipeline import run
        return run(*args, **kwargs)
    except ImportError:
        # Fallback: legacy sys.path import from 02_Pipeline/ (for development without package install)
        import sys
        from pathlib import Path
        import importlib
        _pipeline_dir = str(Path(__file__).parent.parent / "02_Pipeline")
        if _pipeline_dir not in sys.path:
            sys.path.insert(0, _pipeline_dir)
        _impl = importlib.import_module("pipeline")
        return _impl.run(*args, **kwargs)


__all__ = ["run_pipeline"]
