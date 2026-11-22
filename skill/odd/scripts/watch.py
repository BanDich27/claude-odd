#!/usr/bin/env python3
"""Live view of an /odd run in the terminal: the orchestrator, the agents,
the messages travelling between them, the phase, the time and the cost.

    python3 watch.py [--run <run folder>]      # q or Ctrl+C to close

It only reads the run folder (events.jsonl, state.json); it never writes.
"""

from __future__ import annotations

import argparse
import json
import os
import select
import shutil
import signal
import sys
import termios
import time
import tty
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import odd_core as core  # noqa: E402

FPS = 12
TRAVEL_SECONDS = 1.1
COST_EVERY = 5.0
LOG_LINES = 12
SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
ROLES = ["odd-planner", "odd-implementer", "odd-validator", "odd-sonar-fixer", "odd-docs"]
COLORS = {"orquestador": 214, "odd-planner": 141, "odd-implementer": 75, "odd-validator": 114,
          "odd-sonar-fixer": 203, "odd-docs": 180}
OTHER_COLORS = [44, 177, 222, 109]

RESET, BOLD, DIM = "\033[0m", "\033[1m", "\033[2m"


def fg(code: int) -> str:
    return f"\033[38;5;{code}m"


def display(name: str) -> str:
    return name.removeprefix("odd-")


def clip(text: str, width: int) -> str:
    text = str(text)
    return text if len(text) <= width else text[: max(width - 1, 0)] + "…"


class Screen:
    """A grid of cells (character + style) drawn in one write per frame."""

    def __init__(self, width: int, height: int):
        self.width, self.height = width, height
        self.cells = [[(" ", "")] * width for _ in range(height)]

    def put(self, row: int, col: int, text: str, style: str = "") -> None:
        if not 0 <= row < self.height:
            return
        for i, char in enumerate(text):
            if 0 <= col + i < self.width:
                self.cells[row][col + i] = (char, style)

    def box(self, row: int, col: int, width: int, height: int, style: str) -> None:
        self.put(row, col, "╭" + "─" * (width - 2) + "╮", style)
        for r in range(row + 1, row + height - 1):
            self.put(r, col, "│", style)
            self.put(r, col + width - 1, "│", style)
        self.put(row + height - 1, col, "╰" + "─" * (width - 2) + "╯", style)

    def render(self) -> str:
        lines = []
        for row in self.cells:
            line, current = [], None
            for char, style in row:
                if style != current:
                    line.append(RESET + style)
                    current = style
                line.append(char)
            lines.append("".join(line) + RESET)
        return "\033[H" + "\n".join(lines)


class View:
    def __init__(self, run: Path):
        self.run = run
        self.state: dict = {}
        self.events: list[dict] = []
        self.offset = 0
        self.agents: list[str] = list(ROLES)
        self.status: dict[str, str] = {}
        self.last: dict[str, str] = {}
        self.models: dict[str, str] = {}
        self.packets: list[dict] = []
        self.cost = 0.0
        self.cost_at = 0.0
        self.started = time.time()

    # --- data ---------------------------------------------------------------

    def color(self, name: str) -> int:
        if name not in COLORS:
            COLORS[name] = OTHER_COLORS[len(COLORS) % len(OTHER_COLORS)]
        return COLORS[name]

    def read(self) -> None:
        self.state = core.load_state(self.run) or self.state
        try:
            with open(self.run / "events.jsonl", encoding="utf-8") as handle:
                handle.seek(self.offset)
                chunk = handle.read()
                self.offset = handle.tell()
        except OSError:
            return
        fresh = self.offset > 0 and len(self.events) > 0
        for line in chunk.splitlines():
            try:
                event = json.loads(line)
            except ValueError:
                continue
            self.events.append(event)
            self.apply(event, animate=fresh)
        if time.time() - self.cost_at > COST_EVERY and self.state.get("started_at"):
            try:
                self.cost = core.costs(self.state, self.events)["total_usd"]
            except (KeyError, ValueError, OSError):
                pass
            self.cost_at = time.time()

    def apply(self, event: dict, animate: bool) -> None:
        kind, source, target = event.get("kind"), event.get("from", ""), event.get("to", "")
        for name in (source, target):
            if name and name != "orquestador" and kind in ("dispatch", "return", "agent_start", "agent_stop", "tool") \
                    and name not in self.agents:
                self.agents.append(name)
        if kind in ("dispatch", "agent_start"):
            self.status[target] = "working"
            if event.get("model"):
                self.models[target] = event["model"]
            if kind == "dispatch":
                self.last[target] = event.get("label", "")
                if animate:
                    self.packets.append({"from": "orquestador", "to": target, "t": time.time(),
                                         "label": event.get("label", ""), "glyph": "●"})
        elif kind in ("return", "agent_stop"):
            self.status[source] = "done"
            if kind == "return" and animate:
                self.packets.append({"from": source, "to": "orquestador", "t": time.time(),
                                     "label": "resultado", "glyph": "◆"})
        elif kind == "tool":
            self.last[source] = event.get("label", "")
        elif kind == "model":
            role = target if target.startswith("odd-") else f"odd-{target}"
            self.models[role] = event.get("label", "").split("→")[-1].strip()

    # --- drawing ------------------------------------------------------------

    def layout(self, width: int) -> dict:
        count = len(self.agents)
        slot = max((width - 2) // count, 12)
        centers = {name: 1 + slot * i + slot // 2 for i, name in enumerate(self.agents)}
        return {"slot": slot, "centers": centers, "orch": width // 2}

    def path(self, layout: dict, agent: str, rows: dict) -> list[tuple[int, int]]:
        """Cells from the orchestrator down the bus to the agent."""
        orch, target = layout["orch"], layout["centers"][agent]
        points = [(rows["stem"], orch)]
        step = 1 if target >= orch else -1
        points += [(rows["bus"], col) for col in range(orch, target + step, step)]
        points += [(rows["drop"], target)]
        return points

    def draw(self) -> str:
        width, height = shutil.get_terminal_size((100, 32))
        screen = Screen(width, height)
        layout = self.layout(width)
        now = time.time()
        spin = SPINNER[int(now * FPS) % len(SPINNER)]

        # Title and clock.
        elapsed = int(now - core.parse_time(self.state["started_at"]).timestamp()) if self.state.get("started_at") \
            else int(now - self.started)
        right = f"⏱ {elapsed // 60:02d}:{elapsed % 60:02d}   ${self.cost:.2f}   {self.state.get('mode', '')}"
        screen.put(0, 1, "◆ ODD ", BOLD + fg(214))
        screen.put(0, 7, clip(self.state.get("task", "…"), max(width - len(right) - 10, 10)), BOLD)
        screen.put(0, width - len(right) - 1, right, fg(250))

        # Pipeline of phases.
        current = self.state.get("phase")
        order = [key for key, _ in core.PHASES]
        position = order.index(current) if current in order else len(order)
        col = 1
        for index, (key, label) in enumerate(core.PHASES):
            if key == current:
                mark, style = f"{spin} {label}", BOLD + fg(214)
            elif index < position:
                mark, style = f"✓ {label}", fg(114)
            else:
                mark, style = f"○ {label}", DIM
            screen.put(2, col, mark, style)
            col += len(mark) + 1
            if col < width - 3 and key != core.PHASES[-1][0]:
                screen.put(2, col, "─", DIM)
                col += 2

        # Orchestrator box.
        busy = any(s == "working" for s in self.status.values())
        orch_w = 26
        top = 4
        ocol = layout["orch"] - orch_w // 2
        screen.box(top, ocol, orch_w, 4, fg(214))
        screen.put(top + 1, ocol + 2, "ORQUESTADOR", BOLD + fg(214))
        screen.put(top + 2, ocol + 2, clip("esperando agentes" if busy else self.last.get("orquestador", "coordinando"),
                                           orch_w - 4), fg(250))
        rows = {"stem": top + 4, "bus": top + 5, "drop": top + 6}
        screen.put(rows["stem"], layout["orch"], "│", DIM)
        centers = sorted(layout["centers"].values())
        for c in range(min(centers + [layout["orch"]]), max(centers + [layout["orch"]]) + 1):
            screen.put(rows["bus"], c, "─", DIM)
        screen.put(rows["bus"], layout["orch"], "┴", DIM)

        # Agent boxes.
        box_top, box_w = rows["drop"] + 1, max(layout["slot"] - 2, 10)
        for name in self.agents:
            center = layout["centers"][name]
            screen.put(rows["bus"], center, "┬", DIM)
            screen.put(rows["drop"], center, "│", DIM)
            status = self.status.get(name, "idle")
            color = self.color(name)
            style = fg(color) + (BOLD if status == "working" else "") if status != "idle" else DIM
            left = center - box_w // 2
            screen.box(box_top, left, box_w, 5, style)
            screen.put(box_top + 1, left + 2, clip(display(name).upper(), box_w - 4), style + BOLD)
            badge = {"working": spin + " trabajando", "done": "✓ listo", "idle": "· en espera"}[status]
            screen.put(box_top + 2, left + 2, clip(self.models.get(name, "—"), box_w - 4), fg(250) if status != "idle" else DIM)
            screen.put(box_top + 3, left + 2, clip(badge if status != "working" else self.last.get(name, badge), box_w - 4),
                       fg(color) if status != "idle" else DIM)

        # Messages on their way.
        alive = []
        for packet in self.packets:
            progress = (now - packet["t"]) / TRAVEL_SECONDS
            if progress >= 1:
                continue
            alive.append(packet)
            agent = packet["to"] if packet["from"] == "orquestador" else packet["from"]
            if agent not in layout["centers"]:
                continue
            points = self.path(layout, agent, rows)
            if packet["from"] != "orquestador":
                points = list(reversed(points))
            index = min(int(progress * len(points)), len(points) - 1)
            color = fg(self.color(agent)) + BOLD
            for trail in range(1, 3):
                if index - trail >= 0:
                    r, c = points[index - trail]
                    screen.put(r, c, "•", fg(self.color(agent)) + DIM)
            r, c = points[index]
            screen.put(r, c, packet["glyph"], color)
            label = clip(packet["label"], 28)
            screen.put(rows["stem"], min(layout["orch"] + 2, width - len(label) - 1), label, color)
        self.packets = alive

        # Activity log.
        log_top = box_top + 6
        screen.put(log_top, 1, "ACTIVIDAD", BOLD + fg(250))
        shown = [e for e in self.events if e.get("kind") not in ("render",)][-min(LOG_LINES, max(height - log_top - 2, 1)):]
        for i, event in enumerate(shown):
            source = event.get("from", "")
            target = event.get("to", "")
            arrow = f" → {display(target)}" if target and event.get("kind") in ("dispatch", "return") else ""
            line = f"{event.get('ts', '')[11:19]}  "
            screen.put(log_top + 1 + i, 1, line, DIM)
            who = f"{display(source)}{arrow}"
            screen.put(log_top + 1 + i, 1 + len(line), clip(who, 30), fg(self.color(source)))
            screen.put(log_top + 1 + i, 33 + len(line), clip(event.get("label", ""), width - 36 - len(line)), fg(250))

        if self.state.get("phase") == "done":
            screen.put(height - 1, 1, f"Terminado: {self.state.get('status', '')} · q para cerrar", BOLD + fg(114))
        else:
            screen.put(height - 1, 1, "q para cerrar", DIM)
        return screen.render()


def find_run(path: str | None) -> Path:
    if path:
        return Path(path)
    for _ in range(600):  # wait up to a minute for a run to start
        run = core.active_run()
        if run:
            return run
        time.sleep(0.1)
    sys.exit("odd: no hay ninguna ejecución activa")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run")
    args = parser.parse_args()
    view = View(find_run(args.run))
    old = termios.tcgetattr(sys.stdin) if sys.stdin.isatty() else None
    sys.stdout.write("\033[?1049h\033[?25l\033[2J")
    signal.signal(signal.SIGWINCH, lambda *_: sys.stdout.write("\033[2J"))
    try:
        if old:
            tty.setcbreak(sys.stdin.fileno())
        while True:
            view.read()
            sys.stdout.write(view.draw())
            sys.stdout.flush()
            ready, _, _ = select.select([sys.stdin] if old else [], [], [], 1 / FPS)
            if ready and os.read(sys.stdin.fileno(), 1) in (b"q", b"Q"):
                break
    except KeyboardInterrupt:
        pass
    finally:
        if old:
            termios.tcsetattr(sys.stdin, termios.TCSADRAIN, old)
        sys.stdout.write(RESET + "\033[?25h\033[?1049l")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
