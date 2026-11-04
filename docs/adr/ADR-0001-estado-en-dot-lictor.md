# ADR-0001: El estado vive en .lictor/, dentro del repositorio e ignorado por git

## Estado
Aceptada, revisada (ver «Revisión» al final)

## Contexto
Lictor necesita persistir bitácora (`journal.jsonl`), checkpoints (`checkpoints.sqlite`), sello de especificación y driver (`seal.json`), salidas crudas de ejecuciones (`runs/`) e informes finales (`report.md`).
Se valoraron dos opciones principales:
1. **Opción A (`.lictor/` dentro del repositorio del proyecto, añadido a `.gitignore`):** el estado viaja con el repositorio, se ubica donde el usuario lo espera y se limpia con el ciclo de vida del proyecto.
2. **Opción B (fuera del repositorio, en `~/.lictor/projects/<nombre>/`):** separar físicamente el estado para evitar que el agente acceda a él.

En la versión v0.1 no existe sandbox de aislamiento por contenedores: cualquier comando ejecutado por el proyecto (como `pytest`) corre con los permisos del usuario actual y podría alcanzar `~/.lictor/` de todos modos. Por tanto, la opción B proporciona una falsa sensación de seguridad sin ventajas reales.

## Decisión
Adoptar la **Opción A**: el directorio `.lictor/` se sitúa en la raíz del repositorio e ignorado por git.
La protección de la integridad del estado no depende de su ubicación en el sistema de archivos, sino de tres mecanismos de control:
1. **Reglas de denegación del agente:** el adaptador `ClaudeCLI` configura `--disallowedTools` explícitos sobre `Read(.lictor/**)`, `Edit(.lictor/**)` y `Write(.lictor/**)`.
2. **Instantánea de integridad (L0-estado):** Lictor guarda en memoria `lictor.json`, `seal.json`, `checkpoints.sqlite` con su WAL, la bitácora, HEAD y los ficheros de control de `.git` antes de cada llamada no confiable, y los compara después. Si se detecta alteración, falla con `failed_at: L0-estado` y restaura los archivos.
3. **Sandbox futuro (a partir del Incremento 4):** el agente operará en un contenedor sobre un árbol de trabajo limpio que excluye `.lictor/` y `.git`.

## Consecuencias
- `git status` no detecta `.lictor/` por estar en `.gitignore`, requiriendo la instantánea de integridad.
- Lictor no limpia ni resetea el árbol de trabajo del proyecto: el agente trabaja en una copia desde la Fase 4 y `.lictor/` queda fuera de su directorio, aunque siga al alcance por ruta absoluta hasta el sandbox.
- Múltiples clones del repositorio gestionan su propio estado local de forma independiente y predecible.

## Revisión (2026-11, plan de corrección)
La decisión no cambia: el estado sigue en `.lictor/`, dentro del proyecto e ignorado por git. Cambian los mecanismos que lo protegen, porque la auditoría de la v0.1 mostró que las reglas de denegación no bastaban: un comando del proyecto alcanza `.lictor/` igual que el agente.

1. **El agente ya no trabaja en el proyecto.** Cada llamada recibe una copia de HEAD fuera del proyecto, sin `.git` ni `.lictor/`, que se borra al terminar. Lictor lee su resultado como un árbol (`Vcs.capture`) y no limpia ni revierte nunca el árbol del proyecto. La consecuencia «`reset_to_head` respeta los ignorados» ya no aplica.
2. **Los temporales viven en un directorio privado** (`<tmp>/lictor-<hash del proyecto>`), fuera del proyecto, con modo `0o700` y comprobado por descriptor.
3. **Un cerrojo por proyecto** (`.lictor/run.lock`, `flock` no bloqueante) impide que dos `lictor run` se pisen la copia y el clon. Lo toman también `init`, `check`, `register` y, para construir su entorno, `doctor`. No es estado de la ejecución: ni la instantánea ni el ancla lo leen.
4. **La instantánea de integridad** guarda y restaura además el WAL del checkpoint, `current_run`, `fake_executor.json`, `verify_env.json` y, con el `Vcs`, HEAD y los ficheros de control de `.git`. Se aplica a cada llamada al agente y a cada comando que ejecuta código del proyecto.
5. **Una reanudación compara contra el ancla del checkpoint** (HEAD, el hash de `seal.json` y la huella de los ficheros de control de `.git`) y se niega con exit 10 si no cuadra. Eso encarece una falsificación, pero no la impide: el checkpoint está al alcance del mismo usuario.
6. **Las reglas de denegación** (`Read`, `Edit` y `Write` sobre `.lictor/**`) se mantienen como defensa adicional. Ninguna garantía depende ya de ellas.
7. **`runs/` tiene retención:** al empezar una ejecución nueva se conservan las `retention.keep_runs` más recientes (10 por defecto) y nunca la de `current_run`. Lo retirado queda en la bitácora (`runs.pruned`) y el informe lo distingue de la evidencia alterada.

Hasta el sandbox del incremento 4, el verde no resiste a un agente adversario: el agente corre con los permisos del usuario y llega a `.lictor/` por ruta absoluta.

