# ADR-0001: El estado vive en .lictor/, dentro del repositorio e ignorado por git

## Estado
Aceptada

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

