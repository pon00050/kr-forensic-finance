"""constants.py — Re-exports from kr-forensic-core.

All thresholds and flag names now live in kr-forensic-core to allow
kr-anomaly-scoring and other downstream repos to import them without
depending on krff-shell.
"""

from __future__ import annotations

from kr_forensic_core.constants import *  # noqa: F401, F403
from kr_forensic_core.constants import __all__  # noqa: F401
