# ADR-0007: Cada control declara su momento: en cada intento o solo en `lictor check`

## Estado
Aceptada (2026-11-13). Sale de la sesión de diseño del paso 4a del plan de pruebas y controles (F23) y la revisaron el mismo día dos revisores independientes.

## Contexto
Hoy un run ejecuta todos los controles de `lictor.json` en cada intento de cada escenario (`VerifyAttempt._run_commands`), y `lictor check` ejecuta los mismos. El nivel (`level`) es una etiqueta: dice qué es un control, no cuándo corre.

Eso obliga a elegir entre dos males con un control que no debería correr en cada intento:

- **Dejarlo en el run.** Con el `lictor.json` de Lictor, cada intento de cada escenario pagaría 1,5 min de mutación, además de la auditoría de dependencias (medido en la CI el 2026-11-12). Es el antipatrón 3 del patrón de pruebas: ejecutarlo todo en cada iteración.
- **Quitarlo** (`"enabled": false`). Entonces tampoco corre en `lictor check`, y la CI del proyecto tiene que lanzarlo por su cuenta, fuera de Lictor.

La matriz de momentos del patrón (sección 3) saca del intento dos clases de control, por razones distintas:

- **Por coste:** la mutación, que no cabe en el presupuesto del intento.
- **Porque el cambio no lo puede tocar:** la auditoría de dependencias. El agente no puede cambiar `pyproject.toml` ni `uv.lock`, así que auditarlos en cada intento no aporta nada.

Los secretos no están en ninguna de las dos. El agente puede escribir un secreto en `src/`, `gitleaks` tarda segundos y revisa el historial: un secreto que llega a un commit verde obliga a reescribir commits que llevan `Scenario:`. Van en el intento.

El plan aprobado (decisión 8) fija la primera versión: un campo `stage: attempt | check`, sin `run_end`. Este ADR decide lo que el plan deja abierto.

## Decisión
1. **Cada control tiene un campo `stage`**, con dos valores:
   - `attempt` (por defecto): corre en cada verificación de un run y en `lictor check`, como hoy;
   - `check`: no corre en el run; corre solo en `lictor check`.

   El nombre no es `when`: `when_exists` y `when_plugin` son condiciones para saltarse un control, y `"when": "check"` se leería como «cuando sea check». Tampoco se reutiliza `level`, que diría a la vez qué es un control y cuándo corre.

   Lo llevan `ControlConfig` (el control resuelto, y el del perfil) y `ControlChange` (lo que escribe `lictor.json`). Se escribe por `id`, como `mode`, y también vale en un control que el proyecto añade. La aceptación no tiene `stage`: es el oráculo y corre siempre.

   **Cuándo va un control en `check`:** cuando el agente no necesita ver su fallo para corregir su trabajo, porque no cabe en el intento o porque el intento no puede cambiar lo que comprueba. Lo que el agente tiene que ver va en el intento: solo recibe el fallo de lo que bloquea en el intento.

   **Un perfil puede declarar un control `check`, pero hoy ninguno lo hace.** Si un perfil lo hace algún día, cambia lo que protege el run de todos sus proyectos sin que lo escriban, y va en el CHANGELOG como un cambio.
2. **`stage` y `mode` son ejes independientes,** y valen las cuatro combinaciones:

   | | `attempt` | `check` |
   |:--|:--|:--|
   | `block` | Para el intento, como hoy | Solo hace fallar `lictor check` (exit 10) |
   | `measure` | Mide en cada intento, como hoy | Mide en `lictor check` |

   - **`min_score` vale igual en los dos momentos:** `enforce_min_score` ya se aplica en `CheckProject`.
   - **Lo que mide un control `measure` + `check` solo queda en la salida de `lictor check`:** `check` no deja informe, y cada `check` sustituye `.lictor/check/`. Quien quiera conservarlo lo guarda su CI.
   - **`level`, `--level` y `--control` no cambian:** `lictor check --level L4` ejecuta los controles L4 de cualquier momento. Por eso el cierre local de AGENTS.md (`--level L1,L2,L4`) seguirá ejecutando `audit` aunque esté en `check`.
   - **`lictor check` marca los controles `check` en su salida** («✗ mutation (solo en check)»), para que un fallo tras un run se lea como lo que es.
3. **El run no ejecuta los controles `check`,** ni los registra como omitidos en cada intento.

   **El filtro es una regla del dominio,** que aplica `bootstrap`. `VerifyAttempt` solo recibe los `attempt`, así que no necesita saber del momento. `GraphContext` recibe aparte la lista de los `check`, para nombrarlos (punto 4).

   **Lo que se compruebe antes de empezar depende del momento:**
   - el `preflight` de un run y la reanudación (`_check_resume_guard`) buscan solo el programa de los controles `attempt`: uno que solo usa `check`, como un `gitleaks` que está en la CI y no en la máquina, no impide empezar ni reanudar;
   - `doctor` busca el de todos, como hoy, porque responde también por `lictor check`.

   **La huella de la configuración incluye los controles `check` enteros.** Cambiar el momento de un control, o el `run` o el `min_score` de uno `check`, impide reanudar (exit 10), como cualquier otro cambio de lo que se verifica. Se acepta aunque el run no ejecute esos controles: `run.started` ya los listó, y el motivo final los nombra.
4. **En el run, los controles `check` se nombran dos veces, nunca por intento:**
   - **En `report.md`.** `run.started` los lista en `check_controls`: una lista de `{id, level, mode, min_score}`. `report.md` los saca de ahí, en una sección propia, «Controles solo en `lictor check`», antes de «Controles en medición». Un journal sin el campo cuenta como una lista vacía. No salen en «Controles omitidos»: omitido es lo que tocaba en el intento y no se pudo ejecutar.
   - **En el motivo final, en una sola frase.** La añade `_finish`, que es por donde pasan todas las salidas, cuando hay controles `check` y la ejecución commiteó al menos un verde: implementado, colateral o registrado de nuevo. Una salida sin commits, como «Spec inválida» o un error interno, no la lleva. Una parada que se reanuda (pausa, reanudación rechazada) tampoco, porque no pasa por `_finish`.
     - Por defecto, también en un worktree enlazado cuando el run para antes de su final (por ejemplo, con exit 10 después de algunos verdes): «Quedan sin comprobar mutation y audit, que solo corren en `lictor check`: ejecútalo antes de integrar.»
     - Cuando un run en un worktree enlazado llega a su final, que es cuando sale `integration_hint`, se funde con él: «El resultado está en la rama lictor/x, en `<ruta del worktree>`. Quedan sin comprobar mutation y audit, que solo corren en `lictor check`: ejecútalo en ese worktree, o deja que lo haga la CI de la pull request, antes de integrarlo con `git merge lictor/x` o con una pull request.»
     - Sin controles `check`, `integration_hint` dice lo de hoy.

   `lictor status` y las líneas de progreso de `RunProgress` no los muestran.
5. **Si `lictor check` falla tras el run, los escenarios siguen verdes y el arreglo es de la persona.** Es lo mismo que una CI que falla después de fusionar:
   - el verde de un escenario lo da su commit con el trailer `Scenario:`, verificado por los controles `attempt` y la aceptación, y `check` no lo cambia ni lo retira;
   - Lictor no culpa a ningún escenario, no marca la rama y no hay código de salida nuevo: `check` sale con 10, como con cualquier control bloqueante;
   - el siguiente run no lo ve: sus intentos no ejecutan ese control, y sigue commiteando verdes encima.

   Si eso se queda corto (que el agente reciba el fallo, o que la rama no se pueda integrar), la respuesta es `run_end`, que necesita decidir antes a qué escenario culpa y en qué estado deja la rama. No entra en esta versión.
6. **`doctor` avisa (⚠) siempre que haya controles `check`,** sin cambiar su código de salida: «Controles que solo corren en `lictor check`: mutation y audit. Un run no los ejecuta: solo protegen lo que integras si tu CI ejecuta `lictor check`.».
   - **Sale en cada `doctor`, también cuando la CI sí ejecuta `check`.** Es un ⚠ permanente, y se acepta: no hay forma fiable de saber qué ejecuta la CI. Buscar `lictor check` en `.github/workflows/` daría una tranquilidad falsa, con otro sistema de CI, un script intermedio o un paso desactivado.
   - **Si `lictor.json` no carga, no sale:** sin configuración no hay controles que nombrar, y ya sale `✗ Configuración`.
7. **La skill explica el momento al proponer un control,** en lenguaje llano y con el criterio del punto 1:
   - **En `skill/body.md`,** genérico y sin nombres de herramientas. Al proponer `"stage": "check"`, la skill dice dos cosas: hay que añadir `lictor check` a la CI del proyecto, y un fallo ahí después del run lo arregla la persona.
   - **En `python-uv.skill.md`.** Los cuatro controles del perfil (`lint`, `types`, `unit` y `coverage`) van en cada intento. Si la persona añade mutación o una auditoría de dependencias, la skill propone `check`, con un ejemplo de cambio por `id`. Un control de secretos va en cada intento.
8. **El perfil `python-uv` no cambia de valores:** sus cuatro controles son `attempt` según la matriz.

   **El `lictor.json` de Lictor pone `mutation` y `audit` en `check`.** `secrets` se queda en `attempt`, por lo dicho en el Contexto. `coverage` y `coverage-domain` también se quedan: `coverage-domain` lee el `.coverage` que deja `coverage` en el mismo clon, y el cargador no puede ver esa dependencia, porque no está declarada. ADR-0008 la declara, y entonces el cargador exige que los dos controles tengan el mismo momento. Lo que corre en la PR no cambia, porque la CI ya ejecuta `lictor check --keep-going --level L1,L2,L4,L5`.
9. **No se conserva la compatibilidad con la 0.3.0:** Lictor no tiene usuarios todavía. `stage` entra en la huella como cualquier otro campo, así que un run a medias de la 0.3.0 no se reanuda con la 0.4.0 (exit 10). El CHANGELOG lo dice en una línea. El test de la 0.2.0 (`test_a_lictor_json_of_0_2_0_keeps_its_fingerprint`) se actualiza con la huella nueva.
10. **`run_end` queda fuera** hasta que `check` se quede corto, y antes necesita su respuesta al fallo (punto 5).

### Las tres condiciones de «Dirección para F23»
1. **Lo verificado es lo que se commitea.** Se cumple. Los verdes de escenario solo salen de `VerifyAttempt` y `CheckRed`, sobre el candidato en un clon limpio, y ninguno de los dos depende de los controles `check`. `CheckRed` no ejecuta controles. `lictor check` no mueve ninguna rama ni da verdes: su commit candidato queda fuera de la rama.

   **El agente no puede pasar un control a `check` para librarse de él,** por lo que es fijo:
   - el run lee los controles una vez, al arrancar (`bootstrap`);
   - `IntegritySnapshot` restaura `lictor.json`;
   - un cambio de la huella impide reanudar.

   `paths.protected` también lo nombra, pero es una decisión del proyecto, que un `paths` escrito puede reemplazar. Lo único nuevo es que una persona puede sacar un control del run con una línea, y eso es la decisión misma.
2. **Un control «al final del run» necesita su respuesta al fallo.** No aplica: un control `check` no corre en el run. El punto 5 dice qué pasa cuando falla fuera del run.
3. **Las puertas que bloquean son deterministas.** No cambia nada: `stage` mueve controles que ya existían, no añade puertas, y ninguna depende de un modelo.

## Consecuencias
- **Un control `check` no protege los commits del run,** solo lo que se integra si alguien ejecuta `lictor check`. Lo mitigan el motivo final, `integration_hint`, el aviso de `doctor` y la skill. Un proyecto cuya CI no ejecuta `lictor check` y que desoye el ⚠ no tiene ese control. Es el riesgo que se acepta.
- **Un run sobre una rama con un control `check` en rojo sigue commiteando verdes encima.** Nada lo para hasta que la persona lo arregla. También es un riesgo aceptado.
- **El agente no ve los fallos de un control `check`.** Un 92 % de mutación frente a un mínimo de 95 no le llega en ningún intento. Lo arregla la persona, o un run nuevo con escenarios que lo pidan.
- **`lictor check` deja de ser solo «lo que `run` haría»:** ejecuta los controles de los dos momentos, así que es lo que haría el run más lo que el run deja para después. AGENTS.md, el README y la ayuda de `check` lo dicen así. No hay `--stage`: `--level` y `--control` bastan para elegir.
- **Un run con controles caros cabe en el presupuesto del intento** sin quitarlos del proyecto.
- **Lo que cambia en los tests:**
  - el test de la 0.2.0 (punto 9);
  - `schema/lictor.schema.json`, que se regenera porque `ControlChange` tiene un campo más;
  - unitarios de la regla del dominio que filtra por momento y de la frase del motivo, que entran en `[tool.mutmut]`.

## Alternativas descartadas
- **`when: attempt | check`.** Choca con `when_exists` y `when_plugin`, que son condiciones.
- **Usar `level` para el momento,** con un nivel por momento. Mezcla qué es un control y cuándo corre, que es lo que el patrón critica de la propuesta original de niveles.
- **Listar los controles `check` como omitidos en cada intento.** Llena «Controles omitidos» con lo mismo en cada intento, y lo confunde con lo que no se pudo ejecutar.
- **`run_end` en esta versión,** una vez sobre el último commit. Exige decidir a qué escenario se culpa y en qué estado queda la rama, y el caso que lo justifique todavía no existe.
- **Que `doctor` detecte si la CI ejecuta `lictor check`.** Es una heurística que solo vería algunos sistemas de CI, y su silencio se leería como una garantía.
- **Comprobar en el `preflight` del run, o en la reanudación, los programas de los controles `check`.** Pararía un run por un programa que no va a usar.
- **La frase del motivo en cualquier salida.** Con una salida sin commits no queda nada que comprobar, y la frase sería ruido.
