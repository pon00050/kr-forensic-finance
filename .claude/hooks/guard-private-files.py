#!/usr/bin/env python3
"""Claude Code PreToolUse hook: block git-add of private files.

Reads tool invocation JSON from stdin. If the Bash command is a `git add`
that references any private path, exits with code 2 to block the action.
"""
import json
import sys
import re

PRIVATE_PATTERNS = [
    "KNOWN_ISSUES.md",
    "CHANGELOG.md",
    "PRODUCT_VISION.md",
    "company_dives",
    "NETWORK_FINDINGS.md",
    "00_Reference",
    "PHASE_1_5_PLAN.md",
    "CLAUDE.md",
]

def main():
    try:
        data = json.load(sys.stdin)
        command = data.get("tool_input", {}).get("command", "")
    except (json.JSONDecodeError, AttributeError):
        sys.exit(0)

    # Only check git add commands
    if not re.match(r"\s*git\s+add", command):
        sys.exit(0)

    for pattern in PRIVATE_PATTERNS:
        if pattern in command:
            print(
                f"BLOCKED: '{pattern}' is a private file/directory and must not "
                f"be staged for commit. See CLAUDE.md privacy rules.",
                file=sys.stderr,
            )
            sys.exit(2)

    sys.exit(0)

if __name__ == "__main__":
    main()
