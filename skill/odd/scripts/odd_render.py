"""Builds the plan and the final report of an /odd run as one self-contained
HTML file each (styles and screenshots inlined), always with the same look.

Everything the agents wrote is escaped: their JSON is data, never markup.
Inline `code` and line breaks are the only formatting kept.
"""

from __future__ import annotations

import base64
import html
import re
from pathlib import Path

import odd_core as core

CSS = """
:root{--bg:#161514;--panel:#1f1e1c;--panel2:#262421;--line:#34312d;--text:#ece8df;--muted:#a39d92;
--accent:#e0a43a;--ok:#74b97a;--bad:#e06c5a;--warn:#d9a441;--blue:#7d97ad;--code:#2d2a26}
*{box-sizing:border-box}html{background:var(--bg)}
body{margin:0;background:var(--bg);color:var(--text);font:15px/1.55 "Inter","Segoe UI",system-ui,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:40px 24px 80px}
header .kicker{color:var(--accent);font-weight:700;letter-spacing:.14em;text-transform:uppercase;font-size:12px}
header h1{font-size:30px;line-height:1.2;margin:6px 0 14px}
.chips{display:flex;flex-wrap:wrap;gap:8px}
.chip{background:var(--panel2);border:1px solid var(--line);border-radius:999px;padding:3px 12px;font-size:13px;color:var(--muted)}
.chip b{color:var(--text);font-weight:600}
.pipe{display:flex;gap:6px;margin:24px 0 8px;flex-wrap:wrap}
.pipe span{flex:1;min-width:92px;text-align:center;padding:7px 4px;border-radius:8px;font-size:12px;font-weight:600;
background:var(--panel);border:1px solid var(--line);color:var(--muted)}
.pipe .done{color:var(--ok);border-color:#3c5a3f}.pipe .now{color:var(--bg);background:var(--accent);border-color:var(--accent)}
.pipe .bad{color:var(--bad);border-color:#6b3a32}
nav.toc{margin:22px 0 8px;display:flex;flex-wrap:wrap;gap:6px 18px;font-size:14px}
nav.toc a{color:var(--muted);text-decoration:none}nav.toc a:hover{color:var(--accent)}
section{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:22px 24px;margin-top:18px}
section h2{margin:0 0 14px;font-size:19px;display:flex;gap:10px;align-items:baseline}
section h2 .n{color:var(--accent);font-variant-numeric:tabular-nums}
h3{font-size:15px;margin:18px 0 8px;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
p{margin:6px 0}ul{margin:6px 0;padding-left:22px}li{margin:3px 0}
code{background:var(--code);border-radius:5px;padding:1px 6px;font:13px/1.4 "JetBrains Mono","Cascadia Code",Consolas,monospace}
pre{background:#111;border:1px solid var(--line);border-radius:8px;padding:12px;overflow:auto;font:12.5px/1.45 "JetBrains Mono",Consolas,monospace;color:#cfc9bd;max-height:340px}
table{width:100%;border-collapse:collapse;font-size:14px;margin:6px 0}
th{text-align:left;color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.05em;border-bottom:1px solid var(--line);padding:6px 8px}
td{border-bottom:1px solid #2a2825;padding:7px 8px;vertical-align:top}
td.num{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.tag{display:inline-block;border-radius:6px;padding:1px 8px;font-size:12px;font-weight:700}
.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}.muted{color:var(--muted)}
.tag.ok{background:#22331f}.tag.bad{background:#3a211d}.tag.warn{background:#3a2f1a}.tag.muted{background:var(--panel2)}
.tag.low{background:#22331f;color:var(--ok)}.tag.medium{background:#3a2f1a;color:var(--warn)}.tag.high{background:#3a211d;color:var(--bad)}
.tldr{margin-top:18px;border-left:3px solid var(--accent);background:var(--panel2);border-radius:0 10px 10px 0;padding:10px 14px}
.tldr b{display:block;color:var(--accent);font-size:12px;letter-spacing:.1em;text-transform:uppercase;margin-bottom:2px}
.gallery{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:14px}
figure{margin:0;background:var(--panel2);border:1px solid var(--line);border-radius:10px;overflow:hidden}
figure img{width:100%;display:block;cursor:zoom-in}figure figcaption{padding:8px 12px;font-size:13px}
details summary{cursor:pointer;color:var(--muted);font-size:13px;margin-top:6px}
.empty{color:var(--muted);font-style:italic}
.big{font-size:26px;font-weight:700}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:8px}
.kpi{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:10px 14px}
.kpi span{display:block;color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.06em}
footer{margin-top:28px;color:var(--muted);font-size:12px;text-align:center}
@media (max-width:640px){.wrap{padding:24px 16px 60px}header h1{font-size:23px}section{padding:16px}
table{display:block;overflow-x:auto}}
"""

ZOOM_JS = """document.querySelectorAll('figure img').forEach(i=>i.onclick=()=>window.open().document.write(
'<body style="margin:0;background:#111"><img src="'+i.src+'" style="max-width:100%">'));"""

COMPLEXITY = {"low": "baja", "medium": "media", "high": "alta"}
VERDICT = {"pass": ("ok", "pasa"), "fail": ("bad", "falla"), "partial": ("warn", "parcial"),
           "skip": ("muted", "omitido")}
EVENT_KINDS_SHOWN = {"phase", "dispatch", "return", "check", "loop", "model", "sonar", "shot", "question",
                     "decision", "finish"}


# --- small builders ---------------------------------------------------------


def text(value) -> str:
    """Escapes agent text, keeping `code` and line breaks."""
    escaped = html.escape(str(value or ""))
    escaped = re.sub(r"`([^`\n]+)`", r"<code>\1</code>", escaped)
    return escaped.replace("\n", "<br>")


def tag(kind: str, label: str) -> str:
    return f'<span class="tag {html.escape(kind)}">{html.escape(label)}</span>'


def verdict(value) -> str:
    kind, label = VERDICT.get(str(value), ("muted", str(value or "—")))
    return tag(kind, label)


def items(values) -> str:
    values = [value for value in (values or []) if value]
    if not values:
        return '<p class="empty">Nada.</p>'
    return "<ul>" + "".join(f"<li>{text(value)}</li>" for value in values) + "</ul>"


def table(headers: list[str], rows: list[list[str]], numeric: set[int] = frozenset()) -> str:
    if not rows:
        return '<p class="empty">Nada.</p>'
    head = "".join(f"<th>{html.escape(name)}</th>" for name in headers)
    body = "".join(
        "<tr>" + "".join(f'<td class="num">{cell}</td>' if i in numeric else f"<td>{cell}</td>"
                         for i, cell in enumerate(row)) + "</tr>"
        for row in rows
    )
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def section(number: int, anchor: str, title: str, body: str, summary: str) -> str:
    return (f'<section id="{anchor}"><h2><span class="n">{number:02d}</span>{html.escape(title)}</h2>{body}'
            f'<div class="tldr"><b>En resumen</b>{text(summary)}</div></section>')


def money(usd: float) -> str:
    return f"${usd:,.2f}" if usd >= 0.01 else f"${usd:.4f}"


def tokens(count: int) -> str:
    return f"{count / 1_000_000:.2f} M" if count >= 1_000_000 else f"{count / 1000:.1f} k" if count >= 1000 else str(count)


def duration(state: dict) -> str:
    try:
        end = core.parse_time(state["finished_at"]) if state.get("finished_at") else core.parse_time(core.now())
        seconds = int((end - core.parse_time(state["started_at"])).total_seconds())
    except (KeyError, ValueError):
        return "—"
    return f"{seconds // 3600} h {seconds % 3600 // 60:02d} min" if seconds >= 3600 else f"{seconds // 60} min {seconds % 60:02d} s"


def image(path: Path) -> str:
    try:
        data = base64.b64encode(path.read_bytes()).decode()
    except OSError:
        return '<p class="empty">Captura no encontrada.</p>'
    return f'<img alt="{html.escape(path.stem)}" src="data:image/png;base64,{data}">'


def numbered(run: Path, stem: str) -> list[dict]:
    """sections/<stem>-1.json, -2.json... in order (each fix loop adds one)."""
    found = []
    for path in (run / "sections").glob(f"{stem}-*.json"):
        suffix = path.stem.rsplit("-", 1)[-1]
        if suffix.isdigit():
            data = core.read_json(path)
            if isinstance(data, dict):
                found.append((int(suffix), data))
    return [data for _, data in sorted(found, key=lambda pair: pair[0])]


def page(kind_label: str, state: dict, pipeline: str, toc: list[tuple[str, str]], body: str, extra_chips=()) -> str:
    chips = [
        ("Rama", state.get("branch", "—")), ("Modo", state.get("mode", "—")),
        ("Inicio", state.get("started_at", "")[:16].replace("T", " ")), ("Estado", state.get("status", "—")),
        *extra_chips,
    ]
    chip_html = "".join(f'<span class="chip">{html.escape(k)}: <b>{html.escape(str(v))}</b></span>' for k, v in chips)
    toc_html = "".join(f'<a href="#{a}">{html.escape(t)}</a>' for a, t in toc)
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>ODD · {html.escape(kind_label)}</title>
<style>{CSS}</style></head><body><div class="wrap">
<header><div class="kicker">ODD · {html.escape(kind_label)}</div><h1>{text(state.get('task', ''))}</h1>
<div class="chips">{chip_html}</div></header>{pipeline}<nav class="toc">{toc_html}</nav>{body}
<footer>Archivo temporal de /odd · no forma parte del proyecto · {html.escape(state.get('slug', ''))}</footer>
</div><script>{ZOOM_JS}</script></body></html>"""


def pipeline(state: dict, events: list[dict]) -> str:
    reached = [event.get("to") for event in events if event.get("kind") == "phase"]
    current = state.get("phase")
    finished = current == "done"
    failed = state.get("status") in ("con fallos", "cancelado")
    order = [key for key, _ in core.PHASES]
    # Fix loops go back to earlier phases: only what comes before the current one is done.
    last = reached[-1] if reached else None
    position = order.index(current) if current in order else (order.index(last) + 1 if last in order else 0)
    parts = []
    for index, (key, label) in enumerate(core.PHASES):
        if key == current and not finished:
            css = "now"
        elif index < position:
            css = "done"
        else:
            css = ""
        if finished and failed and key == last:
            css = "bad"
        parts.append(f'<span class="{css}">{html.escape(label)}</span>')
    return '<div class="pipe">' + "".join(parts) + "</div>"


# --- the plan ---------------------------------------------------------------


def plan_sections(plan: dict, state: dict) -> list[tuple[str, str, str, str]]:
    """(anchor, title, body, summary) of every part of the plan."""
    sums = plan.get("section_summaries") or {}
    steps = plan.get("steps") or []
    step_rows = [[
        html.escape(str(step.get("id", i + 1))), f"<b>{text(step.get('title'))}</b><br>{text(step.get('detail'))}",
        "<br>".join(f"<code>{html.escape(str(f))}</code>" for f in step.get("files") or []),
        tag(step.get("complexity", "medium"), COMPLEXITY.get(step.get("complexity"), step.get("complexity", "?"))),
    ] for i, step in enumerate(steps)]
    criteria = plan.get("acceptance") or []
    shots = plan.get("screenshots") or []
    models = plan.get("models") or {}
    model_rows = [[html.escape(role), f"<b>{html.escape(str(info.get('model', '?')))}</b>", text(info.get("reason"))]
                  for role, info in models.items() if isinstance(info, dict)]
    questions = plan.get("questions") or []
    parts = [
        ("objetivo", "Objetivo y enfoque",
         f"<p>{text(plan.get('goal'))}</p><h3>Enfoque</h3>{items(plan.get('approach'))}",
         sums.get("goal") or plan.get("summary", "")),
        ("pasos", "Pasos", table(["#", "Paso", "Archivos", "Complejidad"], step_rows),
         sums.get("steps") or f"{len(steps)} pasos; complejidad máxima "
         f"{COMPLEXITY.get(max((s.get('complexity', 'low') for s in steps), key=['low', 'medium', 'high'].index, default='low'))}."),
        ("criterios", "Criterios de aceptación",
         table(["Id", "Criterio", "Cómo se comprueba"],
               [[html.escape(str(c.get("id", ""))), text(c.get("criterion")), text(c.get("how"))] for c in criteria]),
         sums.get("acceptance") or f"El validador comprobará {len(criteria)} criterios."),
        ("capturas", "Capturas previstas",
         table(["Pantalla", "Tamaño", "Qué mirar"],
               [[f"<code>{html.escape(str(s.get('screen', '')))}</code>", html.escape(str(s.get("size") or "por defecto")),
                 text(s.get("check"))] for s in shots]),
         sums.get("screenshots") or (f"{len(shots)} capturas para revisar cómo se ve." if shots
                                     else "No hace falta mirar la interfaz.")),
        ("riesgos", "Riesgos", items(plan.get("risks")),
         sums.get("risks") or f"{len(plan.get('risks') or [])} riesgos identificados."),
        ("modelos", "Modelos propuestos", table(["Rol", "Modelo", "Por qué"], model_rows),
         sums.get("models") or "Cada rol usa el modelo más barato que basta para su parte."),
    ]
    docs = "Sí: cambia la arquitectura, se actualizará el blueprint." if plan.get("architecture_change") \
        else "No: la arquitectura no cambia; no se toca la documentación."
    parts.append(("docs", "Documentación", f"<p>{html.escape(docs)}</p>", docs))
    if questions:
        parts.append(("preguntas", "Preguntas abiertas", items(questions),
                      "Hay que responderlas antes de implementar." if state.get("mode") != "auto"
                      else "Modo auto: el planner asumió lo indicado en cada una."))
    return parts


def render_plan(run: Path, state: dict) -> str:
    plan = core.read_json(run / "sections" / "plan.json", {}) or {}
    parts = plan_sections(plan, state)
    body = "".join(section(i + 1, a, t, b, s) for i, (a, t, b, s) in enumerate(parts))
    if state.get("mode") != "auto":
        body += section(len(parts) + 1, "seguir", "Cómo seguir",
                        "<p>Responde en Claude Code: <b>aprobar</b>, pide <b>cambios</b> o <b>cancela</b>.</p>",
                        "Nada se implementa hasta que lo apruebes.")
    return page("Plan", state, pipeline(state, core.read_events(run)), [(a, t) for a, t, _, _ in parts], body)


# --- the report -------------------------------------------------------------


def render_report(run: Path, state: dict) -> str:
    events = core.read_events(run)
    plan = core.read_json(run / "sections" / "plan.json", {}) or {}
    final = core.read_json(run / "sections" / "final.json", {}) or {}
    implementations = numbered(run, "implement")
    validations = numbered(run, "validation")
    sonar_fixes = numbered(run, "sonar-fix")
    docs = core.read_json(run / "sections" / "docs.json")
    checks = core.read_json(run / "checks.json", []) or []
    sonar = core.read_json(run / "sonar-raw.json")
    sonar_history = core.read_json(run / "sonar-history.json", []) or []
    try:
        files = core.changed_files(Path(state["project"]), state["base"])
    except (RuntimeError, KeyError, OSError):
        files = []
    cost = core.costs(state, events) if state.get("started_at") else {"rows": [], "total_usd": 0}
    loops = state.get("loops", {})
    parts = []

    # 1. Summary
    kpis = "".join(f'<div class="kpi"><span>{html.escape(k)}</span><div class="big">{html.escape(v)}</div></div>' for k, v in [
        ("Estado", state.get("status", "—")), ("Duración", duration(state)), ("Coste", money(cost["total_usd"])),
        ("Archivos", str(len(files))), ("Vueltas", str(sum(loops.values()))),
    ])
    parts.append(("resumen", "Resumen", f'<div class="kpis">{kpis}</div>' + f"<p>{text(final.get('summary'))}</p>"
                  f"<h3>Puntos clave</h3>{items(final.get('highlights'))}"
                  f"<h3>Qué revisar a mano</h3>{items(final.get('check_by_hand'))}"
                  f"<h3>Riesgos que quedan</h3>{items(final.get('risks'))}",
                  final.get("summary") or "Sin resumen final."))

    # 2. Plan
    steps = plan.get("steps") or []
    parts.append(("plan", "Plan", f"<p>{text(plan.get('goal'))}</p>"
                  + table(["#", "Paso", "Complejidad"], [[html.escape(str(s.get("id", i + 1))), text(s.get("title")),
                          tag(s.get("complexity", "medium"), COMPLEXITY.get(s.get("complexity"), "?"))]
                          for i, s in enumerate(steps)])
                  + (f"<h3>Ajustes pedidos al plan</h3>{items(final.get('plan_feedback'))}" if final.get("plan_feedback") else "")
                  + '<p class="muted">El plan completo está en plan.html, en la misma carpeta.</p>',
                  plan.get("summary") or "Sin plan."))

    # 3. Implementation
    body = ""
    for i, impl in enumerate(implementations, 1):
        body += (f"<h3>Vuelta {i}{' · ' + html.escape(str(impl['model'])) if impl.get('model') else ''}</h3>"
                 f"<p>{text(impl.get('summary'))}</p>"
                 + table(["Archivo", "Qué cambia"], [[f"<code>{html.escape(str(c.get('file', '')))}</code>", text(c.get("what"))]
                                                    for c in impl.get("changes") or []])
                 + (("<p><b>Tests</b></p>" + table(["Archivo", "Qué prueba"],
                     [[f"<code>{html.escape(str(t.get('file', '')))}</code>", text(t.get("what"))] for t in impl.get("tests") or []]))
                    if impl.get("tests") else "")
                 + (f"<p><b>Notas</b></p>{items(impl.get('notes'))}" if impl.get("notes") else ""))
    parts.append(("implementacion", "Implementación", body or '<p class="empty">No se implementó nada.</p>',
                  implementations[-1].get("summary") if implementations else "No se llegó a implementar."))

    # 4. Checks
    rows = [[html.escape(c["name"]), verdict("pass" if c["passed"] else "fail"), f"{c['seconds']} s",
             html.escape(c["ts"][11:19]),
             f"<details><summary>salida</summary><pre>{html.escape(c['output'])}</pre></details>" if c["output"].strip() else ""]
            for c in checks]
    failed = sum(1 for c in checks if not c["passed"])
    last_ok = bool(checks) and checks[-1]["passed"]
    parts.append(("checks", "Checks", table(["Check", "Resultado", "Tiempo", "Hora", ""], rows),
                  "No se ejecutó ningún check." if not checks else
                  f"{len(checks)} ejecuciones, {failed} con fallos; la última {'pasó' if last_ok else 'falló'}."))

    # 5. Validation
    body = ""
    for i, val in enumerate(validations, 1):
        body += f"<h3>Vuelta {i} · {verdict(val.get('verdict'))}</h3><p>{text(val.get('summary'))}</p>"
        body += table(["Id", "Criterio", "Resultado", "Evidencia"], [[
            html.escape(str(c.get("id", ""))), text(c.get("criterion")), verdict(c.get("status")), text(c.get("evidence"))]
            for c in val.get("criteria") or []])
        if val.get("issues"):
            body += "<p><b>Problemas</b></p>" + table(["Gravedad", "Dónde", "Problema"], [[
                html.escape(str(x.get("severity", ""))),
                f"<code>{html.escape(str(x.get('file', '')))}{':' + html.escape(str(x['line'])) if x.get('line') else ''}</code>",
                text(x.get("problem"))] for x in val["issues"]])
    shots = (validations[-1].get("screenshots") if validations else None) or []
    if shots:
        figures = ""
        for shot in shots:
            name = Path(str(shot.get("file", ""))).name
            figures += (f"<figure>{image(run / 'shots' / name)}<figcaption>{verdict(shot.get('verdict'))} "
                        f"<code>{html.escape(name)}</code><br>{text(shot.get('notes'))}</figcaption></figure>")
        body += f'<h3>Capturas</h3><div class="gallery">{figures}</div>'
    parts.append(("validacion", "Validación", body or '<p class="empty">No se validó.</p>',
                  validations[-1].get("summary") if validations else "No se llegó a validar."))

    # 6. SonarQube
    if sonar and sonar.get("status") == "hecho":
        body = (f"<p>Quality gate: {verdict('pass' if sonar.get('quality_gate') == 'OK' else 'fail')} "
                f"<code>{html.escape(str(sonar.get('quality_gate')))}</code></p>"
                + table(["Pasada", "Gate", "Issues en lo cambiado", "Issues en el resto"],
                        [[str(i), html.escape(h["gate"]), str(h["changed"]), str(h["elsewhere"])]
                         for i, h in enumerate(sonar_history, 1)], {2, 3}))
        for title, key in (("Issues en archivos cambiados", "issues_changed"), ("Issues previos en el resto", "issues_elsewhere")):
            listed = sonar.get(key) or []
            if listed:
                body += f"<h3>{title}</h3>" + table(["Dónde", "Regla", "Mensaje"], [[
                    f"<code>{html.escape(str(x['file']))}:{html.escape(str(x.get('line') or ''))}</code>",
                    html.escape(x["rule"]), text(x["message"])] for x in listed[:80]])
        for i, fix in enumerate(sonar_fixes, 1):
            body += f"<h3>Corrección {i}</h3><p>{text(fix.get('summary'))}</p>" + table(["Dónde", "Regla", "Qué se hizo"], [[
                f"<code>{html.escape(str(f.get('file', '')))}:{html.escape(str(f.get('line') or ''))}</code>",
                html.escape(str(f.get("rule", ""))), text(f.get("what"))] for f in fix.get("fixed") or []])
        summary = (f"Gate {sonar.get('quality_gate')} tras {len(sonar_history)} pasada(s); "
                   f"{len(sonar.get('issues_changed') or [])} issues en lo cambiado y "
                   f"{len(sonar.get('issues_elsewhere') or [])} previos en el resto del proyecto.")
    else:
        reason = (sonar or {}).get("reason", "no se ejecutó")
        body = f'<p class="empty">SonarQube omitido: {html.escape(reason)}.</p>'
        summary = f"Sin análisis de SonarQube ({reason})."
    parts.append(("sonar", "SonarQube", body, summary))

    # 7. Docs
    if docs:
        body = f"<p>{text(docs.get('summary'))}</p>" + items(f"`{f}`" for f in docs.get("updated") or [])
        summary = docs.get("summary", "")
    else:
        summary = ("La arquitectura no cambió: no hizo falta actualizar el blueprint." if not plan.get("architecture_change")
                   else "Tocaba actualizar el blueprint y no se hizo.")
        body = f"<p>{html.escape(summary)}</p>"
    parts.append(("docs", "Documentación", body, summary))

    # 8. Files
    added = sum(f["added"] for f in files)
    removed = sum(f["removed"] for f in files)
    status_tag = {"nuevo": "ok", "borrado": "bad", "modificado": "muted"}
    body = table(["Archivo", "Estado", "+", "−"], [[
        f"<code>{html.escape(f['file'])}</code>", tag(status_tag.get(f["status"], "muted"), f["status"]),
        f'<span class="ok">+{f["added"]}</span>', f'<span class="bad">−{f["removed"]}</span>'] for f in files], {2, 3})
    if state.get("preexisting"):
        body += f"<p class='warn'>Ya había cambios antes de empezar en: {html.escape(', '.join(state['preexisting']))}</p>"
    parts.append(("archivos", "Archivos modificados", body,
                  f"{len(files)} archivos ({sum(f['status'] == 'nuevo' for f in files)} nuevos), +{added} / −{removed} líneas, "
                  f"sin commitear en la rama {state.get('branch', '?')}."))

    # 9. Models and cost
    model_rows = [[html.escape(m["role"]), f"<b>{html.escape(m['model'])}</b>", text(m["reason"])]
                  for m in state.get("model_log", [])]
    cost_rows = [[html.escape(r["agent"]), f"<code>{html.escape(r['model'])}</code>", str(r["calls"]),
                  tokens(r["input"] + r["cache_read"] + r["cache_write"]), tokens(r["output"]),
                  money(r["usd"]) if r["priced"] else "sin precio"] for r in cost["rows"]]
    by_agent: dict[str, float] = {}
    for r in cost["rows"]:
        by_agent[r["agent"]] = by_agent.get(r["agent"], 0) + r["usd"]
    top = max(by_agent.items(), key=lambda pair: pair[1]) if by_agent else None
    parts.append(("costes", "Modelos y costes",
                  "<h3>Elección de modelos</h3>" + table(["Rol", "Modelo", "Por qué"], model_rows)
                  + "<h3>Coste por agente</h3>" + table(["Agente", "Modelo", "Llamadas", "Entrada", "Salida", "USD"], cost_rows, {2, 3, 4, 5})
                  + f"<p><b>Total: {money(cost['total_usd'])}</b> <span class='muted'>· precio de API; con suscripción es el "
                    "equivalente, no lo que se cobra.</span></p>",
                  f"Total {money(cost['total_usd'])}" + (f"; lo que más costó fue {top[0]} ({money(top[1])})." if top else ".")))

    # 10. Timeline
    shown = [e for e in events if e.get("kind") in EVENT_KINDS_SHOWN]
    tool_calls = sum(1 for e in events if e.get("kind") == "tool")
    body = table(["Hora", "Qué", "De → a", "Detalle"], [[
        html.escape(e["ts"][11:19]), html.escape(e["kind"]),
        html.escape(f"{e.get('from', '')} → {e.get('to', '')}" if e.get("to") else e.get("from", "")),
        text(e.get("label")) + (f" <span class='muted'>({text(e['reason'])})</span>" if e.get("reason") else "")]
        for e in shown])
    parts.append(("cronologia", "Cronología", body,
                  f"{len(shown)} pasos destacados y {tool_calls} llamadas a herramientas en {duration(state)}; "
                  + (", ".join(f"{k}: {v} vuelta(s)" for k, v in loops.items()) or "sin vueltas de corrección") + "."))

    html_body = "".join(section(i + 1, a, t, b, s) for i, (a, t, b, s) in enumerate(parts))
    return page("Informe", state, pipeline(state, events), [(a, t) for a, t, _, _ in parts], html_body,
                [("Duración", duration(state)), ("Coste", money(cost["total_usd"]))])


def render(run: Path, state: dict, kind: str) -> Path:
    content = render_plan(run, state) if kind == "plan" else render_report(run, state)
    path = run / f"{kind}.html"
    path.write_text(content, encoding="utf-8")
    return path
