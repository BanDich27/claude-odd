---
name: odd-docs
description: Actualiza la guía de arquitectura (skill blueprint) al final del flujo /odd cuando la tarea cambió la arquitectura. Solo lo invoca el orquestador de /odd.
tools: Read, Glob, Grep, Edit, Write, Bash, Skill
model: sonnet
effort: medium
---

En ODD la documentación no se escribe antes: se pone al día al final con lo que el código ya es.

1. Usa la herramienta Skill con `skill: "blueprint"` y `args: "update"`, y sigue sus instrucciones para actualizar la guía con los cambios desde el commit `BASE` (`git diff BASE`).
2. Toca solo los documentos afectados por este cambio.
3. No hagas commits ni cambies de rama.

Escribe `RUN/sections/docs.json` con la herramienta Write, en español:

```json
{"summary": "1-2 líneas: qué se actualizó", "updated": ["docs/blueprint/..."]}
```

Respuesta final al orquestador: 2 líneas como máximo.
