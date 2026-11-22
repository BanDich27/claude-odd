#!/usr/bin/env python3
"""Command line of an /odd run, used by the orchestrator (see SKILL.md).

    oddctl.py init --task "..." [--auto] [--allow-dirty] [--force]
    oddctl.py phase <phase> [--note TEXT]
    oddctl.py model <role> <opus|sonnet|haiku> <reason>
    oddctl.py loop <name>                 # counts a fix loop, prints its number
    oddctl.py event <kind> <from> <to> <label>
    oddctl.py check [--all | <name> -- <command...>]
    oddctl.py shot <screen> [--size WxH] [--name NAME]
    oddctl.py lint
    oddctl.py sonar
    oddctl.py files | cost
    oddctl.py render plan|report [--open]
    oddctl.py watch
    oddctl.py finish --status ok|failed|cancelled

Every command but ``init`` works on the active run.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import odd_core as core  # noqa: E402

ALLOWED_MODELS = {"opus", "sonnet", "haiku"}
SONAR_WAIT_SECONDS = 300


def fail(message: str, code: int = 1):
    print(f"odd: {message}", file=sys.stderr)
    sys.exit(code)


def project_root() -> Path:
    try:
        return Path(core.git(Path.cwd(), "rev-parse", "--show-toplevel").strip())
    except RuntimeError:
        fail("no es un repositorio git")


def load_config(project: Path) -> dict:
    config = core.read_json(project / ".claude" / "odd.json")
    if config is None:
        fail(f"falta {project / '.claude' / 'odd.json'} (copia examples/odd.json del repo del skill)")
    return config


def current_run() -> tuple[Path, dict]:
    run = core.active_run()
    if run is None:
        fail("no hay ninguna ejecución de /odd en marcha (oddctl.py init)")
    return run, core.load_state(run)


# --- commands ---------------------------------------------------------------


def cmd_init(args):
    project = project_root()
    load_config(project)
    previous = core.active_run()
    if previous and not args.force:
        fail(f"ya hay una ejecución activa en {previous}; usa --force para dejarla", 4)
    dirty = [line[3:] for line in core.git(project, "status", "--porcelain").splitlines()]
    if dirty and not args.allow_dirty:
        print(json.dumps({"dirty": dirty}, ensure_ascii=False))
        fail("hay cambios sin commitear; pásalos a un commit o stash, o usa --allow-dirty", 3)
    slug = core.slugify(args.task)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    run = core.ODD_HOME / f"{stamp}-{slug}"
    (run / "sections").mkdir(parents=True)
    (run / "shots").mkdir()
    base = core.git(project, "rev-parse", "HEAD").strip()
    branch = f"odd/{slug}"
    existing = set(core.git(project, "branch", "--list", "--format=%(refname:short)").split())
    suffix = 2
    while branch in existing:
        branch = f"odd/{slug}-{suffix}"
        suffix += 1
    core.git(project, "switch", "-c", branch)
    state = {
        "task": args.task, "slug": slug, "mode": "auto" if args.auto else "interactivo",
        "project": str(project), "branch": branch, "base": base,
        "started_at": core.now(), "phase": "plan", "status": "en curso",
        "models": {}, "loops": {}, "preexisting": dirty,
    }
    core.save_state(run, state)
    core.write_json(core.ODD_HOME / "active.json", {"run": str(run), "project": str(project)})
    core.add_event(run, "phase", "orquestador", "plan", "Planificar")
    print(json.dumps({"run": str(run), "branch": branch, "mode": state["mode"]}, ensure_ascii=False))


def cmd_phase(args):
    names = dict(core.PHASES)
    if args.phase not in names:
        fail(f"fase desconocida: {args.phase} (una de {', '.join(names)})")
    run, state = current_run()
    state["phase"] = args.phase
    core.save_state(run, state)
    core.add_event(run, "phase", "orquestador", args.phase, args.note or names[args.phase])


def cmd_model(args):
    if args.model not in ALLOWED_MODELS:
        fail(f"modelo no permitido: {args.model} (solo {', '.join(sorted(ALLOWED_MODELS))})")
    run, state = current_run()
    state.setdefault("model_log", []).append({"role": args.role, "model": args.model, "reason": args.reason,
                                              "ts": core.now()})
    state["models"][args.role] = args.model
    core.save_state(run, state)
    core.add_event(run, "model", "orquestador", args.role, f"{args.role} → {args.model}", reason=args.reason)


def cmd_loop(args):
    run, state = current_run()
    state["loops"][args.name] = state["loops"].get(args.name, 0) + 1
    core.save_state(run, state)
    core.add_event(run, "loop", "orquestador", args.name, f"vuelta {state['loops'][args.name]} de {args.name}")
    print(state["loops"][args.name])


def cmd_event(args):
    run, _ = current_run()
    core.add_event(run, args.kind, args.actor, args.target, args.label)


def cmd_check(args):
    run, state = current_run()
    project = Path(state["project"])
    if args.all:
        checks = load_config(project).get("checks", [])
        if not checks:
            fail("odd.json no define ningún check")
    elif args.name and args.command:
        command = args.command[1:] if args.command[0] == "--" else args.command
        checks = [{"name": args.name, "command": command}]
    else:
        fail("usa --all o <nombre> -- <comando>")
    passed = True
    for check in checks:
        entry = core.run_check(run, check["name"], check["command"], project)
        print(f"[{'OK' if entry['passed'] else 'FALLA'}] {check['name']} ({entry['seconds']} s)")
        if not entry["passed"]:
            print(entry["output"])
            passed = False
    sys.exit(0 if passed else 1)


def cmd_shot(args):
    run, state = current_run()
    project = Path(state["project"])
    config = load_config(project).get("screenshot")
    if not config:
        fail("odd.json no define cómo hacer capturas")
    name = args.name or (args.screen + (f"-{args.size}" if args.size else ""))
    if not all(part.isalnum() or part in "-_" for part in name):
        fail("el nombre de la captura solo puede llevar letras, números, - y _")
    out = run / "shots" / f"{name}.png"
    values = {"screen": args.screen, "out": str(out), "size": args.size or ""}
    command = [part.format(**values) for part in config["command"]]
    started = time.time()
    result = subprocess.run(command, cwd=project, capture_output=True, text=True, check=False,
                            timeout=config.get("timeout", 120))
    ok = result.returncode == 0 and out.exists()
    core.add_event(run, "shot", "orquestador", args.screen, f"captura {name}: {'OK' if ok else 'falla'}",
                   file=out.name if ok else None)
    if not ok:
        fail(f"la captura falló ({time.time() - started:.0f} s):\n{core.tail(result.stdout + result.stderr, 20)}")
    print(out)


def cmd_lint(_args):
    run, state = current_run()
    project = Path(state["project"])
    command = load_config(project).get("lint")
    if not command:
        fail("odd.json no define lint")
    entry = core.run_check(run, "lint", command, project)
    print(entry["output"] if not entry["passed"] or entry["output"].strip() else "lint OK")
    sys.exit(0 if entry["passed"] else 1)


def sonar_get(host: str, token: str, path: str, params: dict) -> dict:
    url = f"{host.rstrip('/')}{path}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url)
    request.add_header("Authorization", "Basic " + base64.b64encode(f"{token}:".encode()).decode())
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - local SonarQube URL from config
        return json.load(response)


def cmd_sonar(_args):
    run, state = current_run()
    project = Path(state["project"])
    config = load_config(project).get("sonar")
    if not config:
        fail("odd.json no define sonar")
    token = core.sonar_token()
    result = {"status": "omitido", "ts": core.now()}
    if not token:
        result["reason"] = "falta SONAR_TOKEN (variable o ~/.config/odd/sonar_token)"
        core.write_json(run / "sonar-raw.json", result)
        fail(result["reason"], 2)
    env = dict(os.environ, SONAR_TOKEN=token)
    scan = subprocess.run(config["command"], cwd=project, env=env, capture_output=True, text=True, check=False)
    if scan.returncode != 0:
        result["reason"] = "el escáner falló"
        result["output"] = core.tail(scan.stdout + scan.stderr, 30)
        core.write_json(run / "sonar-raw.json", result)
        fail(f"{result['reason']}:\n{result['output']}", 2)
    task = core.read_properties(project / ".scannerwork" / "report-task.txt")
    host = config.get("host") or task.get("serverUrl", "http://127.0.0.1:9000")
    key = config.get("project_key") or task.get("projectKey")
    try:
        deadline = time.time() + SONAR_WAIT_SECONDS
        while task.get("ceTaskId") and time.time() < deadline:
            status = sonar_get(host, token, "/api/ce/task", {"id": task["ceTaskId"]})["task"]["status"]
            if status in ("SUCCESS", "FAILED", "CANCELED"):
                break
            time.sleep(3)
        gate = sonar_get(host, token, "/api/qualitygates/project_status", {"projectKey": key})
        issues = sonar_get(host, token, "/api/issues/search",
                           {"componentKeys": key, "resolved": "false", "ps": 500})
    except (urllib.error.URLError, KeyError, ValueError) as problem:
        result["reason"] = f"no se pudo leer SonarQube: {problem}"
        core.write_json(run / "sonar-raw.json", result)
        fail(result["reason"], 2)
    changed = {entry["file"] for entry in core.changed_files(project, state["base"])}
    mine, others = core.split_issues(issues.get("issues", []), changed)
    result.update({
        "status": "hecho",
        "quality_gate": gate.get("projectStatus", {}).get("status", "?"),
        "issues_changed": mine, "issues_elsewhere": others,
        "dashboard": f"{host.rstrip('/')}/dashboard?id={urllib.parse.quote(key)}",
    })
    history = core.read_json(run / "sonar-history.json", [])
    history.append({"ts": result["ts"], "gate": result["quality_gate"], "changed": len(mine), "elsewhere": len(others)})
    core.write_json(run / "sonar-history.json", history)
    core.write_json(run / "sonar-raw.json", result)
    clean = result["quality_gate"] == "OK" and not mine
    core.add_event(run, "sonar", "orquestador", "sonar",
                   f"gate {result['quality_gate']} · {len(mine)} issues en lo cambiado", passed=clean)
    print(f"Quality gate: {result['quality_gate']} · issues en archivos cambiados: {len(mine)} · "
          f"en el resto: {len(others)}")
    for issue in mine:
        print(f"  {issue['file']}:{issue['line']} [{issue['rule']}] {issue['message']}")
    sys.exit(0 if clean else 1)


def cmd_files(_args):
    _, state = current_run()
    print(json.dumps(core.changed_files(Path(state["project"]), state["base"]), ensure_ascii=False, indent=2))


def cmd_cost(_args):
    run, state = current_run()
    print(json.dumps(core.costs(state, core.read_events(run)), ensure_ascii=False, indent=2))


def open_file(path: Path):
    explorer = shutil.which("explorer.exe")
    if explorer and shutil.which("wslpath"):
        windows = subprocess.run(["wslpath", "-w", str(path)], capture_output=True, text=True, check=False).stdout
        subprocess.run([explorer, windows.strip()], check=False)  # explorer.exe always exits 1
    elif shutil.which("xdg-open"):
        subprocess.run(["xdg-open", str(path)], check=False)


def cmd_render(args):
    import odd_render

    run, state = current_run()
    path = odd_render.render(run, state, args.kind)
    core.add_event(run, "render", "orquestador", args.kind, f"{args.kind}.html listo")
    if args.open:
        open_file(path)
    print(path)


def cmd_watch(_args):
    run, state = current_run()
    project = Path(state["project"])
    where = load_config(project).get("watch", "split")
    script = Path(__file__).resolve().parent / "watch.py"
    command = ["python3", str(script), "--run", str(run)]
    terminal = shutil.which("wt.exe")
    distro = os.environ.get("WSL_DISTRO_NAME")
    if where == "off" or not terminal or not distro:
        print("Abre el visor en otra terminal con:\n  " + " ".join(command))
        return
    action = {"split": ["split-pane", "-V", "--size", "0.45"], "tab": ["new-tab"], "window": []}[where]
    window = ["-w", "new"] if where == "window" else ["-w", "0"]
    subprocess.Popen([terminal, *window, *action, "--title", "ODD", "wsl.exe", "-d", distro,
                      "--cd", str(project), "--", *command],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    print("visor abierto")


def cmd_finish(args):
    import odd_render

    run, state = current_run()
    state["status"] = {"ok": "completado", "failed": "con fallos", "cancelled": "cancelado"}[args.status]
    state["finished_at"] = core.now()
    state["phase"] = "done"
    core.save_state(run, state)
    core.add_event(run, "finish", "orquestador", "", f"fin: {state['status']}")
    path = odd_render.render(run, state, "report")
    marker = core.read_json(core.ODD_HOME / "active.json", {})
    if marker.get("run") == str(run):
        (core.ODD_HOME / "active.json").unlink(missing_ok=True)
    if not args.no_open:
        open_file(path)
    print(path)


def main(argv=None):
    parser = argparse.ArgumentParser(prog="oddctl.py")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("init")
    p.add_argument("--task", required=True)
    p.add_argument("--auto", action="store_true")
    p.add_argument("--allow-dirty", action="store_true")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("phase")
    p.add_argument("phase")
    p.add_argument("--note")
    p.set_defaults(func=cmd_phase)

    p = sub.add_parser("model")
    p.add_argument("role")
    p.add_argument("model")
    p.add_argument("reason")
    p.set_defaults(func=cmd_model)

    p = sub.add_parser("loop")
    p.add_argument("name")
    p.set_defaults(func=cmd_loop)

    p = sub.add_parser("event")
    p.add_argument("kind")
    p.add_argument("actor")
    p.add_argument("target")
    p.add_argument("label")
    p.set_defaults(func=cmd_event)

    p = sub.add_parser("check")
    p.add_argument("--all", action="store_true")
    p.add_argument("name", nargs="?")
    p.add_argument("command", nargs=argparse.REMAINDER)
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("shot")
    p.add_argument("screen")
    p.add_argument("--size")
    p.add_argument("--name")
    p.set_defaults(func=cmd_shot)

    for name, func in (("lint", cmd_lint), ("sonar", cmd_sonar), ("files", cmd_files), ("cost", cmd_cost),
                       ("watch", cmd_watch)):
        sub.add_parser(name).set_defaults(func=func)

    p = sub.add_parser("render")
    p.add_argument("kind", choices=["plan", "report"])
    p.add_argument("--open", action="store_true")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("finish")
    p.add_argument("--status", choices=["ok", "failed", "cancelled"], required=True)
    p.add_argument("--no-open", action="store_true")
    p.set_defaults(func=cmd_finish)

    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
