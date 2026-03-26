"""
conftest.py — pytest configuration for krff-shell tests.

Adds the kr_dart_pipeline package directory to sys.path so test files can use
bare module names (import extract_dart, import transform, etc.) that resolve to
the installed kr_dart_pipeline package.

kr_dart_pipeline is appended (not prepended) so the project root stays first in
sys.path and root-level modules like cli.py are found before any same-named
modules inside kr_dart_pipeline.
"""

import sys
from pathlib import Path

import kr_dart_pipeline as _krdp

# Append kr_dart_pipeline package dir for bare-name imports used in tests
# (e.g. `import extract_dart as ed`, `import transform`)
_krdp_pkg_dir = str(Path(_krdp.__file__).parent)
if _krdp_pkg_dir not in sys.path:
    sys.path.append(_krdp_pkg_dir)
