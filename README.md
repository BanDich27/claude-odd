# claude-odd

`/odd` para Claude Code: un flujo ODD (*organic driven development*) con varios agentes.

```
/odd [--auto] [tarea]
  plan (opus) → plan.html → ⏸ apruebas → implementar (haiku/sonnet/opus) → make check
  → validar con capturas (opus, independiente) → luacheck + SonarQube → blueprint si cambió la arquitectura
  → quality → report.html
```

- **Interactivo** (por defecto): se para a que apruebes el plan, a responder sus preguntas y cuando un bucle de corrección no converge.
- **`--auto`**: no se para; asume lo razonable y lo deja escrito en el informe.
- **Nunca hace commit ni push.** Trabaja en una rama `odd/<tarea>` y deja los cambios sin commitear.
- Los HTML y las capturas van a `/tmp/odd/<fecha>-<tarea>/` (o a `$ODD_HOME`), nunca al proyecto.
- Visor en directo: se abre en un panel de Windows Terminal (`odd.json` → `"watch": "split" | "tab" | "window" | "off"`). Para abrirlo a mano: `python3 skill/odd/scripts/watch.py`.

## Instalar en un proyecto

```sh
./install.sh /ruta/al/proyecto [config.json]
```

Enlaza el skill y los agentes (un `git pull` aquí los actualiza), crea `.claude/odd.json` si falta, añade los hooks a `.claude/settings.local.json` y lo excluye todo del git del proyecto (`.git/info/exclude`). Reinicia Claude Code después de instalar.

### SonarQube
El paso de Sonar necesita un token: `export SONAR_TOKEN=...` o guárdalo en `~/.config/odd/sonar_token` con `chmod 600`. Sin token, ese paso se omite y el informe lo dice.

## Piezas

| Archivo | Qué hace |
|---|---|
| `skill/odd/SKILL.md` | El orquestador: fases, bucles (3 vueltas como máximo, con escalado de modelo), reglas para elegir modelo |
| `agents/odd-*.md` | planner, implementer, validator, sonar-fixer, docs |
| `skill/odd/scripts/oddctl.py` | Estado de la ejecución, checks, capturas, Sonar, costes, HTML |
| `skill/odd/scripts/odd_render.py` | Plantilla única de `plan.html` y `report.html` |
| `skill/odd/scripts/hook.py` | Hooks → eventos (para el visor y para atribuir costes) |
| `skill/odd/scripts/watch.py` | Animación en el terminal |

Costes: se leen del uso real de cada mensaje en los transcripts de Claude Code y se calculan con los precios de API de `odd_core.PRICES`. Con suscripción es el equivalente, no lo que se cobra.

Depurar hooks: `export ODD_DEBUG=1` antes de abrir Claude Code escribe lo que llega en `<run>/hook-debug.jsonl`.

## Tests

```sh
python3 -m unittest discover -s tests
```
