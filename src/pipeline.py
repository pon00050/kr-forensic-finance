import sys
import importlib
from pathlib import Path

_pipeline_dir = str(Path(__file__).parent.parent / "02_Pipeline")
if _pipeline_dir not in sys.path:
    sys.path.insert(0, _pipeline_dir)

_impl = importlib.import_module("pipeline")  # resolves 02_Pipeline/pipeline.py
run_pipeline = _impl.run                     # re-export as run_pipeline

__all__ = ["run_pipeline"]
