"""Shared state of an /odd run: run folder, events, checks, changed files,
token costs and SonarQube results. Standard library only.

A run lives in a temporary folder (``$ODD_HOME`` or ``/tmp/odd``)::

    <run>/state.json        task, mode, branch, base commit, phase, models
    <run>/events.jsonl      what the agents and the orchestrator did, in order
    <run>/sections/*.json   what each agent reports (plan, implement, ...)
    <run>/checks.json       commands run by the orchestrator and their result
    <run>/shots/*.png       screenshots taken for the validation
    <run>/plan.html, report.html

``<ODD_HOME>/active.json`` points at the run in progress, so the hooks know
where to write; it is removed when the run finishes.
"""

from __future__ import annotations

import datetime as _dt
import glob
import json
import os
import re
import subprocess
import tempfile
import unicodedata
from pathlib import Path

ODD_HOME = Path(os.environ.get("ODD_HOME", "/tmp/odd"))

PHASES = [
    ("plan", "Planificar"),
    ("approve", "Aprobar"),
    ("implement", "Implementar"),
    ("checks", "Checks"),
    ("validate", "Validar"),
    ("sonar", "Sonar"),
    ("docs", "Docs"),
    ("report", "Informe"),
]

# USD per million tokens. Cache writes: 1.25x input (5 min), 2x input (1 h).
PRICES = {
    "claude-opus-5-5": {"input": 4.00, "output": 20.00, "cache_read": 0.20},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_read": 0.10},
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_read": 0.01},
    "claude-fable-5-2": {"input": 8.00, "output": 40.00, "cache_read": 0.20},
}

MAX_OUTPUT_LINES = 60


def now() -> str:
    return _dt.datetime.now().astimezone().isoformat(timespec="seconds")


def parse_time(text: str) -> _dt.datetime:
    """Reads an ISO time, with or without zone (``Z`` included)."""
    value = _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None:
        value = value.astimezone()
    return value


def slugify(text: str, words: int = 5) -> str:
    """Short ASCII name for folders and branches: "Añadir reloj al juego" -> "anadir-reloj-al-juego"."""
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    parts = re.findall(r"[a-z0-9]+", ascii_text.lower())
    return "-".join(parts[:words]) or "tarea"


# --- files ------------------------------------------------------------------


def read_json(path: Path, default=None):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def write_json(path: Path, data) -> None:
    """Writes atomically, so the watcher never reads half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def active_run() -> Path | None:
    marker = read_json(ODD_HOME / "active.json")
    if not marker:
        return None
    run = Path(marker.get("run", ""))
    return run if (run / "state.json").exists() else None


def load_state(run: Path) -> dict:
    return read_json(run / "state.json", {})


def save_state(run: Path, state: dict) -> None:
    write_json(run / "state.json", state)


def add_event(run: Path, kind: str, actor: str = "orquestador", target: str = "", label: str = "", **extra) -> dict:
    event = {"ts": now(), "kind": kind, "from": actor, "to": target, "label": label}
    event.update({key: value for key, value in extra.items() if value not in (None, "")})
    with open(run / "events.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    return event


def read_events(run: Path) -> list[dict]:
    events = []
    try:
        with open(run / "events.jsonl", encoding="utf-8") as handle:
            for line in handle:
                try:
                    events.append(json.loads(line))
                except ValueError:
                    continue
    except OSError:
        pass
    return events


# --- git --------------------------------------------------------------------


def git(project: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(["git", "-C", str(project), *args], capture_output=True, text=True, check=False)
    if check and result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)}: {result.stderr.strip()}")
    return result.stdout


def parse_numstat(text: str) -> list[dict]:
    """``git diff --numstat`` lines -> ``[{file, added, removed}]`` (binary files count as 0)."""
    files = []
    for line in text.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, removed, name = parts
        files.append({
            "file": name,
            "added": int(added) if added.isdigit() else 0,
            "removed": int(removed) if removed.isdigit() else 0,
            "status": "modificado",
        })
    return files


def changed_files(project: Path, base: str) -> list[dict]:
    """Files changed since ``base``, committed or not, plus new untracked ones."""
    files = parse_numstat(git(project, "diff", "--numstat", base))
    statuses = {}
    for line in git(project, "diff", "--name-status", base).splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            statuses[parts[-1]] = {"A": "nuevo", "D": "borrado"}.get(parts[0][:1], "modificado")
    for entry in files:
        entry["status"] = statuses.get(entry["file"], entry["status"])
    for name in git(project, "ls-files", "--others", "--exclude-standard").splitlines():
        try:
            with open(project / name, encoding="utf-8", errors="ignore") as handle:
                lines = sum(1 for _ in handle)
        except OSError:
            lines = 0
        files.append({"file": name, "added": lines, "removed": 0, "status": "nuevo"})
    return sorted(files, key=lambda entry: entry["file"])


# --- checks -----------------------------------------------------------------


def tail(text: str, lines: int = MAX_OUTPUT_LINES) -> str:
    return "\n".join(text.splitlines()[-lines:])


def run_check(run: Path, name: str, command: list[str], cwd: Path) -> dict:
    """Runs a command of the project and keeps its result in ``checks.json``."""
    started = _dt.datetime.now()
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
    entry = {
        "name": name,
        "command": " ".join(command),
        "exit": result.returncode,
        "passed": result.returncode == 0,
        "seconds": round((_dt.datetime.now() - started).total_seconds(), 1),
        "output": tail(result.stdout + ("\n" + result.stderr if result.stderr else "")),
        "ts": now(),
    }
    checks = read_json(run / "checks.json", [])
    checks.append(entry)
    write_json(run / "checks.json", checks)
    add_event(run, "check", "orquestador", name, f"{name}: {'OK' if entry['passed'] else 'falla'}",
              passed=entry["passed"])
    return entry


# --- costs ------------------------------------------------------------------


def price_of(model: str) -> dict | None:
    for prefix, price in PRICES.items():
        if model.startswith(prefix):
            return price
    return None


def usage_cost(model: str, usage: dict) -> float | None:
    price = price_of(model)
    if price is None:
        return None
    creation = usage.get("cache_creation") or {}
    write_1h = creation.get("ephemeral_1h_input_tokens", 0)
    write_5m = creation.get("ephemeral_5m_input_tokens", usage.get("cache_creation_input_tokens", 0) - write_1h)
    tokens = (
        usage.get("input_tokens", 0) * price["input"]
        + usage.get("output_tokens", 0) * price["output"]
        + usage.get("cache_read_input_tokens", 0) * price["cache_read"]
        + max(write_5m, 0) * price["input"] * 1.25
        + write_1h * price["input"] * 2
    )
    return tokens / 1_000_000


def transcript_usage(path: str, since: _dt.datetime) -> list[dict]:
    """Usage of every assistant message in a transcript after ``since``.

    Claude Code writes one line per content block, all with the same usage:
    each message is counted once, by its id.
    """
    seen, rows = set(), []
    try:
        handle = open(path, encoding="utf-8")
    except OSError:
        return rows
    with handle:
        for line in handle:
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            message = entry.get("message")
            if not isinstance(message, dict) or not message.get("usage") or entry.get("type") != "assistant":
                continue
            key = message.get("id") or entry.get("requestId") or entry.get("uuid")
            if key in seen:
                continue
            try:
                if parse_time(entry["timestamp"]) < since:
                    continue
            except (KeyError, ValueError):
                continue
            seen.add(key)
            rows.append({"model": message.get("model", "?"), "usage": message["usage"]})
    return rows


def agent_transcripts(state: dict, events: list[dict]) -> dict[str, str]:
    """Transcript of each subagent of the run -> agent type."""
    found = {}
    names = {}
    for event in events:
        agent_id, agent_type = event.get("agent_id"), event.get("agent_type")
        if agent_id and agent_type:
            names[agent_id] = agent_type
        path = event.get("agent_transcript_path")
        if path:
            found[path] = agent_type or "subagente"
    main = state.get("transcript_path")
    if main:
        folder = os.path.join(os.path.splitext(main)[0], "subagents")
        for path in glob.glob(os.path.join(folder, "*.jsonl")):
            if path not in found:
                stem = Path(path).stem.removeprefix("agent-")
                found[path] = names.get(stem, "subagente")
    return found


def costs(state: dict, events: list[dict]) -> dict:
    """Tokens and USD by agent and model since the run started."""
    since = parse_time(state["started_at"])
    sources = []
    if state.get("transcript_path"):
        sources.append((state["transcript_path"], "orquestador"))
    sources.extend(agent_transcripts(state, events).items())
    rows: dict[tuple, dict] = {}
    for path, actor in sources:
        for item in transcript_usage(path, since):
            usage = item["usage"]
            row = rows.setdefault((actor, item["model"]), {
                "agent": actor, "model": item["model"], "calls": 0, "input": 0, "output": 0,
                "cache_read": 0, "cache_write": 0, "usd": 0.0, "priced": True,
            })
            row["calls"] += 1
            row["input"] += usage.get("input_tokens", 0)
            row["output"] += usage.get("output_tokens", 0)
            row["cache_read"] += usage.get("cache_read_input_tokens", 0)
            row["cache_write"] += usage.get("cache_creation_input_tokens", 0)
            usd = usage_cost(item["model"], usage)
            if usd is None:
                row["priced"] = False
            else:
                row["usd"] += usd
    table = sorted(rows.values(), key=lambda row: -row["usd"])
    return {"rows": table, "total_usd": round(sum(row["usd"] for row in table), 4)}


# --- SonarQube --------------------------------------------------------------


def sonar_component_path(component: str) -> str:
    """``vector-chess:ui/app.lua`` -> ``ui/app.lua``."""
    return component.split(":", 1)[1] if ":" in component else component


def split_issues(issues: list[dict], changed: set[str]) -> tuple[list[dict], list[dict]]:
    """Open issues -> (in files this run changed, elsewhere)."""
    mine, others = [], []
    for issue in issues:
        item = {
            "file": sonar_component_path(issue.get("component", "")),
            "line": issue.get("line"),
            "rule": issue.get("rule", ""),
            "severity": issue.get("severity", ""),
            "message": issue.get("message", ""),
        }
        (mine if item["file"] in changed else others).append(item)
    return mine, others


def read_properties(path: Path) -> dict:
    values = {}
    try:
        with open(path, encoding="utf-8") as handle:
            for line in handle:
                if "=" in line and not line.lstrip().startswith("#"):
                    key, value = line.split("=", 1)
                    values[key.strip()] = value.strip()
    except OSError:
        pass
    return values


def sonar_token() -> str | None:
    """SONAR_TOKEN, or the first line of ~/.config/odd/sonar_token (chmod 600)."""
    token = os.environ.get("SONAR_TOKEN")
    if token:
        return token
    try:
        with open(Path.home() / ".config" / "odd" / "sonar_token", encoding="utf-8") as handle:
            return handle.readline().strip() or None
    except OSError:
        return None
