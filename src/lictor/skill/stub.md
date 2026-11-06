---
name: lictor
description: >-
  Prepara un proyecto para Lictor, redacta con la persona los escenarios Gherkin que Lictor
  implementará, revisa lictor.json y explica los resultados de un run. Úsala cuando se hable de
  Lictor o de cualquier orden `lictor` (init, doctor, check, run, status, report, register), de
  lictor.json, de features/*.feature con escenarios @XXX-NNN, de .lictor/report.md, de una
  cuarentena, de un driver rechazado o de un código de salida de Lictor (10, 11, 30, 40, 50).
  Cómo funciona Lictor lo dice la skill al cargarse: si no se carga, dilo y no improvises.
allowed-tools:
  - Bash(lictor skill --show)
  - Bash(lictor skill --show *)
---

# Lictor

Este fichero es solo un esbozo: las instrucciones las da la versión de Lictor instalada, y van a
continuación.

!`lictor skill --show 2>&1 || true`

Sigue esas instrucciones antes de hacer nada más.

Si encima de este párrafo no hay instrucciones, sino un error (`command not found`, `No such
command 'skill'`) o la orden sin ejecutar, ejecuta tú `lictor skill --show` y sigue lo que
imprima. Si tampoco funciona, dile a la persona que esta skill necesita Lictor 0.2.0 o posterior
en el `PATH` (lo comprueba `lictor --version`) y no sigas: no improvises a partir de esta
descripción.
