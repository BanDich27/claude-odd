#!/usr/bin/env python3
"""Adds the /odd hooks to a Claude Code settings file, keeping what is there.

    install_hooks.py <settings.json>

Running it twice changes nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

COMMAND = 'python3 "$CLAUDE_PROJECT_DIR/.claude/skills/odd/scripts/hook.py"'
HOOKS = {
    "PreToolUse": "*",
    "PostToolUse": "Agent|Task",
    "SubagentStart": None,
    "SubagentStop": None,
}


def merge(settings: dict) -> dict:
    hooks = settings.setdefault("hooks", {})
    for event, matcher in HOOKS.items():
        groups = hooks.setdefault(event, [])
        if any(hook.get("command") == COMMAND for group in groups for hook in group.get("hooks", [])):
            continue
        group = {"hooks": [{"type": "command", "command": COMMAND, "timeout": 5}]}
        if matcher:
            group["matcher"] = matcher
        groups.append(group)
    return settings


def main() -> None:
    path = Path(sys.argv[1])
    settings = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merge(settings), indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
