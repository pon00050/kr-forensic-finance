import sys
from pathlib import Path

_pipeline_dir = str(Path(__file__).parent.parent / "02_Pipeline")


def run_pipeline(*args, **kwargs):
    """Lazy proxy for 02_Pipeline/pipeline.run(). Defers heavy imports until called."""
    import importlib
    if _pipeline_dir not in sys.path:
        sys.path.insert(0, _pipeline_dir)
    _impl = importlib.import_module("pipeline")
    return _impl.run(*args, **kwargs)


__all__ = ["run_pipeline"]
