#!/usr/bin/env python3
"""Claude Code hook: turns tool calls and subagent starts/stops into events
of the active /odd run, for the live view and the costs.

It never blocks Claude: outside a run, or on any error, it does nothing and
exits 0. Commands are not logged (they may carry secrets): only the tool,
the file name or the description Claude gave.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import odd_core as core  # noqa: E402

LABEL_LIMIT = 70


def short(text, limit: int = LABEL_LIMIT) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def tool_label(tool: str, data: dict) -> str:
    """What a tool call is about, without its contents."""
    if tool == "Bash":
        return short(data.get("description") or "comando")
    for key in ("file_path", "notebook_path", "path"):
        if data.get(key):
            return f"{tool} {Path(str(data[key])).name}"
    if data.get("pattern"):
        return f"{tool} «{short(data['pattern'], 30)}»"
    if data.get("skill"):
        return f"Skill {data['skill']}"
    return tool


def to_event(payload: dict) -> dict | None:
    """Hook input -> event fields, or None when there is nothing to show."""
    name = payload.get("hook_event_name")
    tool = payload.get("tool_name", "")
    data = payload.get("tool_input") or {}
    actor = payload.get("agent_type") or "orquestador"
    common = {"agent_id": payload.get("agent_id"), "agent_type": payload.get("agent_type")}
    if name == "PreToolUse" and tool in ("Agent", "Task"):
        return {"kind": "dispatch", "from": actor, "to": data.get("subagent_type") or "agente",
                "label": short(data.get("description")), "model": data.get("model"), **common}
    if name == "PostToolUse" and tool in ("Agent", "Task"):
        return {"kind": "return", "from": data.get("subagent_type") or "agente", "to": actor,
                "label": short(data.get("description")), **common}
    if name == "PreToolUse":
        return {"kind": "tool", "from": actor, "to": "", "label": tool_label(tool, data), "tool": tool, **common}
    if name == "SubagentStart":
        return {"kind": "agent_start", "from": "orquestador", "to": payload.get("agent_type") or "agente",
                "label": "empieza", **common}
    if name == "SubagentStop":
        return {"kind": "agent_stop", "from": payload.get("agent_type") or "agente", "to": "orquestador",
                "label": "termina", "agent_transcript_path": payload.get("agent_transcript_path"), **common}
    return None


def handle(payload: dict) -> None:
    run = core.active_run()
    if run is None:
        return
    state = core.load_state(run)
    project = Path(state.get("project", "/nonexistent"))
    if payload.get("cwd") and not Path(payload["cwd"]).resolve().is_relative_to(project):
        return
    # The first event of the run says which conversation is the orchestrator.
    if not state.get("transcript_path") and payload.get("transcript_path") and not payload.get("agent_id"):
        state["transcript_path"] = payload["transcript_path"]
        state["session_id"] = payload.get("session_id")
        core.save_state(run, state)
    if os.environ.get("ODD_DEBUG"):
        with open(run / "hook-debug.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps({key: payload[key] for key in payload if key not in ("tool_input", "tool_response")}) + "\n")
    event = to_event(payload)
    if event:
        kind, actor, target, label = event.pop("kind"), event.pop("from"), event.pop("to"), event.pop("label")
        core.add_event(run, kind, actor, target, label, **event)


def main() -> None:
    try:
        handle(json.load(sys.stdin))
    except Exception as problem:  # noqa: BLE001 - a hook must never break the session
        if os.environ.get("ODD_DEBUG"):
            print(f"odd hook: {problem}", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    main()
