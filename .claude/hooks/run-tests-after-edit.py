#!/usr/bin/env python3
"""PostToolUse hook: run pytest after .py file edits."""
import json
import subprocess
import sys

try:
    data = json.load(sys.stdin)
    file_path = data.get("tool_input", {}).get("file_path", "")
except (json.JSONDecodeError, AttributeError):
    sys.exit(0)

if not file_path.endswith(".py"):
    sys.exit(0)

# Skip test files to avoid infinite loops
if "test_" in file_path or file_path.endswith("_test.py"):
    sys.exit(0)

result = subprocess.run(
    ["uv", "run", "python", "-m", "pytest", "tests/", "-x", "-q"],
    capture_output=True,
    text=True,
    timeout=120,
)

if result.returncode != 0:
    print(f"TESTS FAILED after editing {file_path}", file=sys.stderr)
    output = result.stdout
    print(output[-500:] if len(output) > 500 else output, file=sys.stderr)
    sys.exit(2)

sys.exit(0)
