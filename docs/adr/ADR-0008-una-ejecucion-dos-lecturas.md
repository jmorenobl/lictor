# ADR-0008: `coverage` puntúa la ejecución de `unit`

## Estado
Borrador (2026-11-13) del paso 4b (F46), revisado el mismo día por un revisor independiente. Va después de ADR-0007. Sin código.

## Contexto
En el perfil `python-uv`, `unit` ejecuta `tests/unit` y `coverage` vuelve a ejecutar `tests/unit`, esta vez bajo cobertura (`python-uv.json:20-21`). Es el antipatrón 1 del patrón de pruebas: la misma suite dos veces. Lo heredan todos los proyectos que tienen `pytest-cov`. En los proyectos medidos gana menos de 1 s por intento: lo que lo justifica es que el perfil por defecto deje de enseñar el antipatrón.

El plan (decisión 9) elige la opción C de la revisión, sin perfil nuevo. La idea: `coverage` no ejecuta nada y puntúa un informe que deja `unit`. Cada control decide una cosa: `unit` si los tests pasan, y `coverage` la puntuación, con su `mode` y su `min_score`. Los dos `id` conservan su significado, así que `{"id": "coverage", "mode": "block", "min_score": 80}` sigue igual.

Hay un precedente sin declarar: `coverage-domain`, en el `lictor.json` de Lictor, lee el `.coverage` que deja `coverage` en el mismo clon. Todos los controles de una verificación corren en el mismo clon, y cada `{out}` cae en la evidencia, no en el clon:
- en el run, en `<paso>/control_<id>.xml`, en un directorio nuevo en cada verificación, así que los reintentos y `-rN` no mezclan informes;
- en `check`, en `.lictor/check/<id>.xml`, que cada `check` borra.

## Propuesta
**Los argumentos los declara `coverage`, el control que los necesita, no `unit`:**

```json
{"id": "coverage", "level": "L2", "reads": "unit",
 "adds": "--cov=src --cov-branch --cov-fail-under=0 --cov-report=json:{out}",
 "report": "coverage", "mode": "measure", "empty_exit_codes": [5],
 "when_exists": "tests/unit", "when_plugin": "pytest_cov.plugin"}
```

- **`reads: "unit"`:** `coverage` no tiene `run`. Lee el resultado de una ejecución de `unit`.
- **`adds`:** los argumentos que `unit` recibe al final de su comando cuando `coverage` va a leerlo, es decir, cuando `coverage` está activo, en el mismo momento, y no se salta por `when_plugin` ni por `when_exists`. `{out}` es el `{out}` de `coverage`, como en cualquier control.
- **`unit` no cambia en el perfil.** El argumento de cobertura va donde se busca: `--cov=mipaquete` se cambia por `id` en `coverage`.
- **Quitar `--cov-report=term`** evita que la tabla de cobertura entre en la salida de `unit`, que va al prompt cuando falla.
- **`--cov-fail-under=0`:** sin él, un `fail_under` en `[tool.coverage.report]` del proyecto haría fallar `unit` sin ningún test en rojo, y lo que hoy solo mide pasaría a bloquear. Hay que comprobarlo con `pytest-cov` antes de fijarlo; es de memoria.

## Preguntas abiertas, con la recomendación del borrador

1. **¿Qué es `coverage` cuando no hay informe que puntuar?** Se decide en este orden:
   - **`when_plugin` primero, como hoy.** Sin `pytest_cov.plugin`, `coverage` se salta con su motivo de siempre y `unit` corre sin `adds`. Si el perfil no puede listar los plugins (`installed_plugins` es `None`), `coverage` se salta también, con un motivo propio. Nunca se añaden argumentos a ciegas: sin el plugin, `--cov` rompe `unit` con «unrecognized arguments».
   - **`when_exists` de `coverage`, como hoy.** Sin `tests/unit`, se salta.
   - **Si `unit` se saltó,** `coverage` se salta también, con el motivo de `unit`.
   - **Si `unit` corrió y no dejó informe,** el código de salida y `empty_exit_codes` son los de `unit`. Con un código que `unit` cuenta como «sin casos», `coverage` pasa sin puntuación, como hoy. Con cualquier otro, «Sin informe de cobertura» y `ok=False`, como hoy: bloquea en `block` y queda como fallo registrado en `measure`.
   - **Si `unit` falló y dejó informe,** `coverage` puntúa el informe y no mira el código de salida, que es de `unit`. La puntuación se marca en «Puntuaciones» como tomada de un `unit` fallido, porque una ejecución rota a medias da una cifra baja que no lo dice.
2. **¿Qué valida el cargador?** Después de resolver los cambios por `id`, sale con 10 si:
   - `reads` no nombra un control activo: también si `{"id": "unit", "enabled": false}` lo quitó. El mensaje da las dos salidas: quitar `coverage` también, o darle un `run` propio;
   - el control leído va detrás de `coverage`, o no va justo antes. Así, entre la ejecución de `unit` y la lectura del informe no corre código del proyecto;
   - el control leído tiene otro `stage` (ADR-0007). El mensaje da la salida a quien escriba `{"id": "coverage", "stage": "check"}`: darle un `run` propio;
   - el control leído lee a su vez de otro (sin cadenas);
   - `lictor.json` escribe a la vez `run` y `reads`.

   **Un `run` escrito en `coverage` gana y quita `reads` y `adds`:** el control vuelve a ser el de hoy y ejecuta sus tests. Lo necesita el `lictor.json` de Lictor, cuyo `coverage` ejecuta `tests` entero.

   **No hay aviso de `doctor` para las copias de la 0.2.0 (F43):** Lictor no tiene usuarios que las tengan.
3. **¿Qué ejecuta `lictor check` cuando la selección parte la pareja?** Con `--control coverage`, o con un `--level` que elige `coverage` y no `unit` (en Lictor, `unit` es L2 y `coverage` L4), se añade `unit`, y la salida lo dice. El `unit` añadido cuenta como cualquier control: con `block`, su fallo hace salir a `check` con 10. Al revés no hace falta: `unit` sin `coverage` corre sin `adds`.
4. **¿Qué registra un control que no ejecuta nada?**
   - El código de salida es el de `unit`, la duración es 0 y `output_path` es el log de `unit`.
   - No necesita `IntegritySnapshot` propio, porque no ejecuta nada: la ejecución de `unit` ya está dentro del suyo.
   - `preflight` y `doctor` no buscan su programa, porque no tiene `run`.
   - La evidencia de `unit` guarda su comando efectivo, con `adds`, porque ya no se lee entero en su entrada de `lictor.json`.
5. **¿Dónde vive la regla?** En el dominio. Una función decide, con el resultado de `unit` y lo que encontró, si `coverage` se salta, falla sin informe o puntúa. Otra decide si `unit` recibe `adds`. `VerifyAttempt` y `CheckProject`, que hoy repiten la lógica de los controles, las usan las dos, y sus tests entran en `[tool.mutmut]`.
6. **Sin compatibilidad con la 0.3.0,** como en ADR-0007: Lictor no tiene usuarios todavía. La huella cambia y un run a medias no se reanuda; el CHANGELOG lo dice en una línea. El perfil sigue siendo `python-uv@0.1`, porque los `id` conservan su significado y el cargador solo admite una versión de cada perfil.
7. **¿Y si el proyecto escribe su propio `run` en `unit`** (otro directorio, `-n auto`)? `adds` se añade igual: `coverage` lee esa ejecución, sea cual sea. La skill dice que no se ponga `--cov` en `unit`.
8. **¿Qué hace el `lictor.json` de Lictor?** Nada en esta versión. Su `coverage` ejecuta `tests` entero con `-n auto` y sigue con su `run`, así que `tests/unit` corre dos veces (18 s por `check`), y se acepta. `coverage-domain` sigue dependiendo de un `.coverage` sin declarar. Declararlo exigiría un `reads` que lee un informe de otro formato, y queda fuera.

## Consecuencias
- El perfil por defecto ejecuta los unitarios una vez por intento.
- **Un test que solo falla bajo cobertura pasa de medir a bloquear:** ahora rompe `unit`. El agente no lo ve venir, porque su `agent_commands` sigue sin `--cov`. Es raro, y se acepta.
- **El comando de `unit` depende de otro control.** Se lee en su evidencia, no en su entrada.
- `schema/lictor.schema.json` y `python-uv.skill.md` cambian.

## Alternativas descartadas
- **A. Un control con dos informes (`{out}` JUnit y `{score_out}`), con un perfil `python-uv@0.2`.** Obliga a migrar los `lictor.json`, a que el cargador admita dos versiones de un perfil, y cambia lo que significa `mode`: un `{"id": "coverage", "mode": "measure"}` escrito hoy dejaría los unitarios sin bloquear.
- **B. Que el lector de cobertura lea también el JUnit.** Tiene el mismo problema que A: dos veredictos en un `ControlResult`, y un `mode` ambiguo.
- **`plugin_args` en `unit`, con `{out:coverage}`** (el primer borrador):
  - exige rehacer `fill_report_path`, que hoy sustituye un `{out}` literal y borra el informe anterior;
  - deja una referencia colgando cuando `coverage` se quita;
  - con `installed_plugins` en `None`, añade `--cov` a ciegas;
  - y pone el argumento de cobertura en un control que no lo usa.
