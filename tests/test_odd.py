"""Tests of the /odd scripts: python3 -m unittest discover -s tests"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parent.parent / "skill" / "odd" / "scripts"
sys.path.insert(0, str(SCRIPTS))

import hook  # noqa: E402
import install_hooks  # noqa: E402
import odd_core as core  # noqa: E402
import odd_render  # noqa: E402
import oddctl  # noqa: E402


def git(cwd, *args):
    subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True)


class Sandbox(unittest.TestCase):
    """A git project with .claude/odd.json and a private ODD_HOME."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.home = root / "odd"
        self.project = root / "project"
        self.project.mkdir()
        git(self.project, "init", "-q", "-b", "main")
        git(self.project, "config", "user.email", "t@t")
        git(self.project, "config", "user.name", "t")
        (self.project / ".claude").mkdir()
        (self.project / ".claude" / "odd.json").write_text(json.dumps({
            "checks": [{"name": "ok", "command": ["true"]}],
            "screenshot": {"command": ["sh", "-c", "printf PNG > \"$0\"", "{out}"]},
        }))
        (self.project / "a.txt").write_text("one\n")
        git(self.project, "add", ".")
        git(self.project, "commit", "-q", "-m", "base")
        patcher = mock.patch.object(core, "ODD_HOME", self.home)
        patcher.start()
        self.addCleanup(patcher.stop)
        cwd = os.getcwd()
        os.chdir(self.project)
        self.addCleanup(os.chdir, cwd)

    def tearDown(self):
        self.tmp.cleanup()

    def odd(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            try:
                oddctl.main(list(args))
                code = 0
            except SystemExit as exit_:
                code = exit_.code or 0
        return code, out.getvalue()

    def start(self, *extra):
        code, out = self.odd("init", "--task", "Añadir un reloj", *extra)
        self.assertEqual(0, code)
        return Path(json.loads(out)["run"])


class SlugTest(unittest.TestCase):
    def test_ascii_and_short(self):
        self.assertEqual("anadir-reloj-al-juego-de", core.slugify("¡Añadir reloj al juego de ajedrez!"))
        self.assertEqual("tarea", core.slugify("¿?"))


class CostTest(unittest.TestCase):
    def test_prices_every_kind_of_token(self):
        usage = {"input_tokens": 1_000_000, "output_tokens": 1_000_000, "cache_read_input_tokens": 1_000_000,
                 "cache_creation_input_tokens": 2_000_000,
                 "cache_creation": {"ephemeral_1h_input_tokens": 1_000_000, "ephemeral_5m_input_tokens": 1_000_000}}
        # 2 + 10 + 0.10 + 2*1.25 + 2*2
        self.assertAlmostEqual(18.6, core.usage_cost("claude-sonnet-5-5", usage))
        self.assertIsNone(core.usage_cost("gpt-x", usage))

    def test_counts_each_message_once_and_only_after_start(self):
        with tempfile.NamedTemporaryFile("w", suffix=".jsonl", delete=False) as handle:
            line = {"type": "assistant", "timestamp": "2026-01-01T10:00:00Z",
                    "message": {"id": "m1", "model": "claude-opus-5-5", "usage": {"output_tokens": 100}}}
            handle.write(json.dumps(line) + "\n" + json.dumps(line) + "\n")
            old = dict(line, timestamp="2025-01-01T10:00:00Z", message=dict(line["message"], id="m0"))
            handle.write(json.dumps(old) + "\nnot json\n")
        rows = core.transcript_usage(handle.name, core.parse_time("2026-01-01T09:00:00Z"))
        os.unlink(handle.name)
        self.assertEqual(1, len(rows))

    def test_groups_by_agent(self):
        with tempfile.TemporaryDirectory() as folder:
            main = Path(folder) / "session.jsonl"
            sub = Path(folder) / "session" / "subagents" / "agent-abc.jsonl"
            sub.parent.mkdir(parents=True)
            entry = {"type": "assistant", "timestamp": "2026-01-01T10:00:00Z",
                     "message": {"id": "x", "model": "claude-haiku-5-5", "usage": {"input_tokens": 1_000_000}}}
            main.write_text(json.dumps(dict(entry, message=dict(entry["message"], model="claude-opus-5-5"))) + "\n")
            sub.write_text(json.dumps(entry) + "\n")
            state = {"started_at": "2026-01-01T09:00:00Z", "transcript_path": str(main)}
            events = [{"kind": "agent_stop", "agent_id": "abc", "agent_type": "odd-validator"}]
            result = core.costs(state, events)
        agents = {row["agent"]: row["usd"] for row in result["rows"]}
        self.assertEqual({"orquestador": 4.0, "odd-validator": 0.1}, agents)
        self.assertAlmostEqual(4.1, result["total_usd"])


class SonarTest(unittest.TestCase):
    def test_splits_issues_by_changed_files(self):
        issues = [{"component": "vc:ui/app.lua", "line": 3, "rule": "external_luacheck:211", "message": "unused"},
                  {"component": "vc:engine/game.lua", "line": 9, "rule": "r", "message": "m"}]
        mine, others = core.split_issues(issues, {"ui/app.lua"})
        self.assertEqual(["ui/app.lua"], [issue["file"] for issue in mine])
        self.assertEqual(["engine/game.lua"], [issue["file"] for issue in others])

    def test_token_from_env_first(self):
        with mock.patch.dict(os.environ, {"SONAR_TOKEN": "abc"}):
            self.assertEqual("abc", core.sonar_token())


class NumstatTest(unittest.TestCase):
    def test_binary_files_count_zero(self):
        files = core.parse_numstat("3\t1\tui/app.lua\n-\t-\tassets/x.png\nbroken")
        self.assertEqual([("ui/app.lua", 3, 1), ("assets/x.png", 0, 0)],
                         [(f["file"], f["added"], f["removed"]) for f in files])


class HookTest(Sandbox):
    def payload(self, **fields):
        return {"cwd": str(self.project), "session_id": "s1", "transcript_path": "/t/s1.jsonl", **fields}

    def test_does_nothing_without_a_run(self):
        hook.handle(self.payload(hook_event_name="PreToolUse", tool_name="Read", tool_input={"file_path": "/x/a"}))
        self.assertFalse(self.home.exists())

    def test_dispatch_tool_and_return(self):
        run = self.start()
        hook.handle(self.payload(hook_event_name="PreToolUse", tool_name="Agent",
                                 tool_input={"subagent_type": "odd-planner", "description": "Planificar", "model": "opus"}))
        hook.handle(self.payload(hook_event_name="PreToolUse", tool_name="Bash", agent_id="a1", agent_type="odd-planner",
                                 tool_input={"command": "SECRET=1 make", "description": "Ejecuta los tests"}))
        hook.handle(self.payload(hook_event_name="PostToolUse", tool_name="Agent",
                                 tool_input={"subagent_type": "odd-planner", "description": "Planificar"}))
        events = core.read_events(run)
        kinds = [(e["kind"], e["from"], e["to"]) for e in events[1:]]
        self.assertEqual([("dispatch", "orquestador", "odd-planner"), ("tool", "odd-planner", ""),
                          ("return", "odd-planner", "orquestador")], kinds)
        self.assertEqual("Ejecuta los tests", events[2]["label"])
        self.assertNotIn("SECRET", json.dumps(events))
        self.assertEqual("/t/s1.jsonl", core.load_state(run)["transcript_path"])

    def test_ignores_other_projects(self):
        run = self.start()
        hook.handle({"cwd": "/elsewhere", "hook_event_name": "PreToolUse", "tool_name": "Read", "tool_input": {}})
        self.assertEqual(1, len(core.read_events(run)))


class CtlTest(Sandbox):
    def test_init_creates_branch_state_and_marker(self):
        run = self.start("--auto")
        state = core.load_state(run)
        self.assertEqual("odd/anadir-un-reloj", state["branch"])
        self.assertEqual("auto", state["mode"])
        self.assertEqual(run, core.active_run())

    def test_init_refuses_dirty_tree_and_second_run(self):
        (self.project / "a.txt").write_text("changed\n")
        self.assertEqual(3, self.odd("init", "--task", "x")[0])
        self.start("--allow-dirty")
        self.assertEqual(4, self.odd("init", "--task", "y", "--allow-dirty")[0])

    def test_model_rejects_fable(self):
        self.start()
        self.assertEqual(1, self.odd("model", "implementer", "fable", "porque sí")[0])
        self.assertEqual(0, self.odd("model", "implementer", "sonnet", "pasos medium")[0])

    def test_loops_count(self):
        self.start()
        self.assertEqual("1", self.odd("loop", "validate")[1].strip())
        self.assertEqual("2", self.odd("loop", "validate")[1].strip())

    def test_checks_record_results(self):
        run = self.start()
        self.assertEqual(0, self.odd("check", "--all")[0])
        self.assertEqual(1, self.odd("check", "falla", "--", "false")[0])
        self.assertEqual([True, False], [c["passed"] for c in core.read_json(run / "checks.json")])

    def test_shot_rejects_odd_names_and_saves_png(self):
        run = self.start()
        self.assertEqual(1, self.odd("shot", "menu", "--name", "../x")[0])
        code, out = self.odd("shot", "menu")
        self.assertEqual(0, code)
        self.assertEqual(run / "shots" / "menu.png", Path(out.strip()))

    def test_finish_renders_report_and_clears_marker(self):
        run = self.start()
        (self.project / "b.txt").write_text("new\n")
        code, out = self.odd("finish", "--status", "ok", "--no-open")
        self.assertEqual(0, code)
        self.assertTrue((run / "report.html").exists())
        self.assertIsNone(core.active_run())
        self.assertIn("b.txt", (run / "report.html").read_text())


class RenderTest(Sandbox):
    def test_plan_escapes_agent_text_and_has_summaries(self):
        run = self.start()
        core.write_json(run / "sections" / "plan.json", {
            "title": "t", "summary": "Resumen <script>alert(1)</script>", "goal": "usa `make`",
            "steps": [{"id": 1, "title": "Paso", "files": ["ui/app.lua"], "complexity": "high"}],
            "acceptance": [{"id": "A1", "criterion": "c", "how": "test"}], "questions": ["¿Sí?"],
        })
        page = odd_render.render_plan(run, core.load_state(run))
        self.assertNotIn("<script>alert", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("<code>make</code>", page)
        self.assertEqual(page.count('class="tldr"'), page.count("<section"))
        self.assertIn("Preguntas abiertas", page)

    def test_pipeline_after_a_fix_loop_goes_back(self):
        state = {"phase": "implement"}
        events = [{"kind": "phase", "to": key} for key in ("plan", "approve", "implement", "checks", "validate",
                                                            "implement")]
        strip = odd_render.pipeline(state, events)
        self.assertEqual(2, strip.count('class="done"'))
        self.assertIn('class="now">Implementar', strip)

    def test_report_survives_missing_sections(self):
        run = self.start()
        page = odd_render.render_report(run, core.load_state(run))
        for title in ("Resumen", "Implementación", "Validación", "SonarQube", "Archivos modificados", "Modelos y costes"):
            self.assertIn(title, page)
        self.assertEqual(10, page.count('class="tldr"'))

    def test_report_inlines_screenshots(self):
        run = self.start()
        (run / "shots" / "game.png").write_bytes(b"PNG")
        core.write_json(run / "sections" / "validation-1.json", {
            "verdict": "pass", "summary": "ok", "screenshots": [{"file": "../../etc/passwd", "verdict": "pass"},
                                                                 {"file": "game.png", "verdict": "pass"}]})
        page = odd_render.render_report(run, core.load_state(run))
        self.assertIn("data:image/png;base64,UE5H", page)
        self.assertIn("Captura no encontrada", page)


class InstallHooksTest(unittest.TestCase):
    def test_merge_is_idempotent_and_keeps_existing(self):
        settings = {"hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "x"}]}]}}
        once = install_hooks.merge(json.loads(json.dumps(settings)))
        twice = install_hooks.merge(json.loads(json.dumps(once)))
        self.assertEqual(once, twice)
        self.assertEqual(2, len(once["hooks"]["PreToolUse"]))
        self.assertIn("SubagentStop", once["hooks"])


if __name__ == "__main__":
    unittest.main()
