# ADR-0008: `coverage` puntúa la ejecución de `unit`

## Estado
Aceptada (2026-11-16). Sale del borrador del paso 4b del plan de pruebas y controles (F46), del 2026-11-13, que revisó ese mismo día un revisor independiente. El 2026-11-16 la revisaron contra el código cinco revisores independientes, uno detrás de otro. La cuarta revisión mostró que mezclaba dos decisiones, y se separó: cómo se leen los informes y se juzgan los controles está en ADR-0009, que va antes en la implementación, y esta se queda en F46. Va después de ADR-0007.

## Contexto
En el perfil `python-uv`, `unit` ejecuta `tests/unit`, y `coverage` vuelve a ejecutarlo bajo cobertura (`python-uv.json:20-21`). Es el antipatrón 1 del patrón de pruebas: la misma suite dos veces. Lo heredan todos los proyectos que tienen `pytest-cov`. En los proyectos medidos se gana menos de 1 s por intento (dni: `unit` 0,49 s, `coverage` 0,72 s). Lo que lo justifica es que el perfil por defecto deje de enseñar el antipatrón.

El plan (decisión 9) elige la opción C de la revisión: `coverage` no ejecuta nada y puntúa un informe que deja `unit`. Así cada control decide una cosa:
- `unit`, si los tests pasan;
- `coverage`, la puntuación, con su `mode` y su `min_score`.

Los dos `id` conservan su significado, así que `{"id": "coverage", "mode": "block", "min_score": 80}` sigue igual.

**Lo que da ADR-0009, y aquí se usa:**
- **Los informes** se leen con un `ReportReader[R]` que devuelve un hecho tipado: para cobertura, `CoverageReport`, `MissingReport` o `InvalidReport`.
- **Los veredictos** son funciones puras del dominio. Un veredicto es `Passed | Failed(reason)`.
- **El resultado de un control** es `Skipped`, `NotExecuted` o `Executed`, y un `Executed` se juzgó (`Judged`) o no (`Unjudged`, porque manipuló el estado). Lo que hace la verificación con él lo decide `consequence`.
- **El tipo de un control del dominio** es `CommandControl`. `BootstrapResult.controls` guarda la tupla ordenada de todos los controles.
- **Todo log** empieza por su comando efectivo.
- **El journal** es `verify.finished.v2`, con un códec en el dominio.

### Cómo corren hoy los controles
- **Orden y parada.** Todos los de una verificación corren en el mismo clon, uno detrás de otro, en el orden resuelto. La verificación para en el primero `block` que falla, y tras él no registra nada más (`verify_attempt.py:390-423`). `lictor check` también para, salvo con `--keep-going`.
- **Lectura del informe.** Cada informe se lee después de su comando, de `verify_and_restore` y de `save_output`. Si el comando manipuló el estado, no se lee (`verify_attempt.py:581-610`).
- **Informes en la evidencia.** Cada `{out}` cae en la evidencia, no en el clon:
  - en el run, en `<paso>/control_<id>.xml` y `.log`, en un directorio nuevo en cada verificación;
  - en `check`, en `.lictor/check/<id>.xml` y `.log`, que cada `check` sustituye.

  `runs/` está fuera de `IntegritySnapshot`. `fill_report_path` trocea el comando y sustituye cada `{out}` por una sola ruta, después de borrar el informe (`run_files.py:231-234`).
- **Comandos troceados.** Ningún comando se trocea con `shlex` al cargar. Un `run` con comillas sin cerrar revienta con un `ValueError` sin capturar al ejecutarse (`rules.py:966`, `run_files.py:234`).
- **Qué llega al prompt.** La salida de un control `block` que falla solo llega cuando su JUnit no trae casos, es decir, cuando la suite no llegó a ejecutarse (`verify_attempt.py:409-412`). La salida entera va siempre a `error.txt` y al log.
- **`IntegritySnapshot`.** En el run, cada comando de control corre dentro de uno. En `check`, ninguno (`check_project.py:218-247`).
- **Plugins.** `missing_plugin` ejecuta el control cuando el perfil no puede listar los plugins (`installed_plugins` es `None`, `rules.py:597-605`). `python-uv` declara `plugins_command`, así que eso solo ocurre en tests, y lo documentan `README.md:502` y AGENTS.md. Son opcionales:
  - `VerificationEnvironment.installed_plugins` (`verification_env.py:144-148`);
  - `CheckProjectRequest.installed_plugins` (`check_project.py:76`);
  - el `environment` de `VerifyAttempt` (`verify_attempt.py:287`, `:550`).
- **El cargador mezcla por `id` como diccionarios** (`loader.py:284-290`), sobre un perfil volcado con sus valores por defecto (`loader.py:250`).
  - En el control resuelto no se distingue un campo escrito de uno por defecto.
  - Un cambio con `enabled: false` se mezcla antes de quitarlo.
  - `exclude_unset` conserva un `null` escrito (`loader.py:282`).
  - Las validaciones de conjunto que existen, como la unicidad de `id`, son `field_validator` del tipo (`models.py:384-387`, `:423-426`). Valen también para un `LictorConfig` construido sin `load_config`, como hacen `bootstrap_runtime(config=...)` (`bootstrap.py:140-144`) y los tests, y `Profile` tiene la suya.
- **El perfil.** `python-uv@0.1`, y el cargador solo admite la versión que existe: pedir otra sale con un error (`loader.py:98-106`).

### Comprobado con `pytest-cov` 7.1.0 y `coverage` 7.17.0 (2026-11-16), las versiones que fija `uv.lock`
- **`fail_under`.** Si no se pasa `--cov-fail-under`, se toma el `fail_under` de `[tool.coverage.report]`. Con un 99 y una cobertura del 75 %, pytest sale con 1 aunque todos los tests pasen. Con `--cov-fail-under=0`, sale con 0.
- **Tests fallidos.** El informe JSON se escribe también cuando fallan tests, salvo con `--no-cov-on-fail`.
- **Sin tests.** Con `tests/unit` sin tests y código en `src`, pytest sale con 5 y escribe el informe, con un 0 %. Solo no hay informe cuando lo medido no tiene ningún fichero: entonces, con un test que pasa, sale con 0 y sin informe.
- **Paquete vacío.** Con `--cov` sobre un paquete que solo tiene un `__init__.py` vacío, hay informe, con 0 sentencias.
- **Salida.** Con un `--cov-report` escrito, no se añade la tabla `term`. La salida gana cuatro líneas: dos de cabecera, una en blanco y «Coverage JSON written to file …». Sin tests gana además un `CoverageWarning: No data was collected` de dos líneas.

## Decisión
1. **Un control ejecuta un comando o lee la ejecución de otro, y el tipo hace imposible mezclarlos.**
   - **Un control que ejecuta** tiene `run`, como hoy, y todo lo que es de una ejecución: `empty_exit_codes`, `timeout_s` y `when_exists`.
   - **Un control lector** tiene `reads` y `adds`, y nada de lo que es de una ejecución:
     - **`reads`** nombra el `id` del control cuya ejecución lee;
     - **`adds`** son los argumentos que el control leído recibe al final de su comando cuando su lector está configurado (punto 4). Llevan siempre `{out}`, que es el del lector.
   - **Un lector solo puede tener `report: "coverage"`,** y solo puede leer un control que ejecuta con `report: "junit"`.
     - Es la única pareja con una lectura definida (punto 6).
     - Un lector de JUnit o de código de salida repetiría el veredicto del control leído, y uno de mutación no tiene ejecución que leer.
     - Un control leído con `report: "coverage"` llevaría dos `--cov-report=json:` en el mismo comando.
     - Ampliarlo pide otro ADR, con su veredicto.
   - **Un control tiene como mucho un lector.** Dos `adds` en el mismo comando chocarían, y dos lecturas de una ejecución son lo que el antipatrón ya hace mal.

   **En la configuración, `ControlConfig` es la unión de dos modelos,** uno por forma, los dos con `extra="forbid"` y con un discriminador invocable: un control con `reads` es lector, y uno con `run` ejecuta.
   - El lector declara `report: Literal["coverage"]`, y un `field_validator` exige `{out}` en `adds`, como hoy `_validate_junit_out` en `run`. Así un lector con `timeout_s`, con otro formato o sin `{out}` no se puede representar.
   - Uno sin `run` ni `reads` es un error con su propio mensaje: «El control '<id>' no dice qué ejecuta: escribe run, o reads y adds».
   - **Todo comando de la configuración tiene que trocearse con `shlex` al cargar:** el `run` de cada control, `adds` y `acceptance.run`. Si no, es un error de carga que nombra el control. Así un comando con comillas sin cerrar no revienta al ejecutarse.
   - **`ControlChange`, lo que escribe `lictor.json`, gana `reads` y `adds`.** En `run`, `reads` y `adds` es opcional escribirlos, pero no admite `null`: el esquema los declara como cadenas, no como `str | None`, porque un `null` no quitaría la forma y su significado sería ambiguo.

   **En el dominio, `ReaderControl` se suma a `CommandControl`** (ADR-0009) en la unión `Control`, en `domain/controls.py`.
   - `domain_controls(config)` (ADR-0009) da los dos.
   - `GraphContext` y la petición de `check` reciben la tupla ordenada de `BootstrapResult.controls`, y `controls_of_stage` recibe `Control`. El `Protocol` `Staged` desaparece.
   - **Lo que hoy toma `c.run` de todos los controles** (`bootstrap.py:250` y `:261`: los programas que buscan `preflight`, la reanudación y `doctor`) **toma solo el de los que ejecutan.**
   - **`allow_empty_message`** (`loader.py:365`), que lee los `empty_exit_codes` del perfil, dice para un lector que no tiene `empty_exit_codes` y que valen los de su control leído.

   **El perfil pasa a `python-uv@0.2`, y queda así:**

   ```json
   {"id": "unit", "level": "L2", "run": "uv run pytest tests/unit -q --junitxml {out}", "report": "junit", "mode": "block", "empty_exit_codes": [5], "when_exists": "tests/unit"},
   {"id": "coverage", "level": "L2", "reads": "unit",
    "adds": "--cov=src --cov-branch --cov-fail-under=0 --cov-report=json:{out}",
    "report": "coverage", "mode": "measure", "when_plugin": "pytest_cov.plugin"}
   ```

   - **`unit` no cambia.**
   - **`coverage` pierde `run`, `empty_exit_codes` y `when_exists`,** que son de la ejecución, y la ejecución es la de `unit`. Sin `tests/unit`, `unit` se salta, y `coverage` se salta con él (punto 6).
   - **Sin `--cov-report=term`:** la tabla de cobertura no tiene sitio en el log ni en `error.txt` de `unit`, y llegaría al prompt justo cuando la suite no arranca, que es cuando la salida tiene que explicar el fallo.
   - **`--cov-fail-under=0` anula el `fail_under` del proyecto.** Sin él, ese umbral haría fallar `unit` sin ningún test en rojo, y lo que hoy solo mide pasaría a bloquear sin que nadie lo escribiera. El umbral que valga se escribe en `coverage` como `min_score`.
   - **La versión sube porque el perfil cambia lo que verifica:**
     - el `fail_under` del proyecto deja de aplicarse;
     - un test que solo falla bajo cobertura pasa a bloquear;
     - un test en rojo deja de bloquear a través de `coverage`;
     - `when_plugin` exige `plugins_command`.

     Un `lictor.json` que pide `python-uv@0.1` pidió esas reglas, y darle otras con el mismo nombre sería cambiarlas sin que lo sepa. El cargador sigue admitiendo una sola versión de cada perfil, así que `@0.1` sale con 10, con un mensaje que dice qué cambia y que remite al CHANGELOG. Lo que cambia es del perfil, y el cargador no sabe nada de pytest, así que el perfil lo declara en `retired_versions` (`{"0.1": "mide la cobertura en la ejecución de unit"}`) y el cargador compone el mensaje: «El perfil python-uv@0.1 ya no existe: usa python-uv@0.2, que mide la cobertura en la ejecución de unit (ver el CHANGELOG)». `lictor init` escribe `@0.2`.
2. **Cómo se aplica un cambio por `id`, y dónde se valida.**

   **«Escrito» es estar en `model_fields_set`,** valga lo que valga, también un `null` en un campo que lo admite (`when_exists`, `when_plugin`). Así `{"id": "coverage", "when_exists": null}` sobre un lector es un campo de ejecución escrito, y es un error.

   **La forma se resuelve antes de pydantic,** con una función que recibe el control base (del perfil, o ninguno para un `id` nuevo) y el cambio escrito, y aplica esta tabla. Comprueba también el `report`, de modo que todos los errores de forma tienen su propio mensaje. «Campos de ejecución» son `empty_exit_codes`, `timeout_s` y `when_exists`.

   | Base | Lo escrito en el cambio | Resultado |
   |:--|:--|:--|
   | Ejecuta | Sin `run`, `reads` ni `adds` | Ejecuta. Se mezclan los campos escritos, como hoy. |
   | Ejecuta | `run` | Ejecuta, con el `run` nuevo y los demás campos del base, como hoy. Así el `unit` de Lictor conserva `empty_exit_codes` y `when_exists`. |
   | Ejecuta | `reads` y `adds`, sin `run` ni campos de ejecución, con `report: "coverage"` escrito o en el base | Lector. Se descartan del base `run` y los campos de ejecución. |
   | Ejecuta | `reads` y `adds`, con un `report` que no es `coverage` | Error: un lector solo puede ser de cobertura. |
   | Ejecuta | `reads` y `adds` y un campo de ejecución | Error: un lector no los tiene, y no se aplicarían. |
   | Ejecuta | `reads` o `adds` solos | Error: hacen falta los dos para convertirlo en lector. |
   | Lector | Sin `run`, `reads` ni `adds`, ni un `report` distinto de `coverage` | Lector. Se mezclan los campos escritos. |
   | Lector | `reads` o `adds`, o los dos | Lector, con lo escrito. Así se cambia solo `adds`. |
   | Lector | Un `report` distinto de `coverage`, sin `run` | Error: un lector solo puede ser de cobertura. |
   | Lector | `run` | Ejecuta. Se descartan `reads` y `adds`, y los campos de ejecución salen de lo escrito o de sus valores por defecto. Así el `lictor.json` de Lictor le da su `run` a `coverage`. |
   | Lector | Un campo de ejecución sin `run` | Error: en un lector no se aplicaría. |
   | Cualquiera, o ninguno | `run` junto a `reads` o `adds` | Error: son dos formas. |
   | Ninguno (`id` nuevo) | `level`, `report` y `run` | Ejecuta, como hoy. |
   | Ninguno (`id` nuevo) | `level`, `report: "coverage"`, `reads` y `adds`, sin campos de ejecución | Lector. |
   | Ninguno (`id` nuevo) | Otra combinación | Error que nombra las dos formas y lo que falta. |

   - **Con `enabled: false`, el cambio no se mezcla:** quita el control, y cualquier otro campo escrito a su lado es un error, porque no se aplicaría.
   - **Cada error sale con 10 y su propio mensaje,** que nombra el control, lo escrito y lo que lo arregla. Como la forma está resuelta antes de pydantic, nunca llega al usuario el error de las dos ramas de una unión.

   **Las reglas de conjunto son de los tipos `LictorConfig` y `Profile`,** en un `model_validator` que llama a `domain_controls` y a una función del dominio. Así valen para un `LictorConfig` construido a mano, como la unicidad de `id`, y un perfil roto no carga. Fallan si:
   - `reads` no nombra un control activo;
   - el control leído es lector, o no tiene `report: "junit"`;
   - un control tiene dos lectores;
   - un control leído va después de su lector;
   - un lector corre en un momento en que su control leído no corre (punto 6).

   **Los mensajes dan las salidas:**
   - **Para un `reads` que no nombra un control activo** (también si lo quitó `{"id": "unit", "enabled": false}`): quitar `coverage` también, o darle un `run` propio.
   - **Para `{"id": "unit", "stage": "check"}` con `coverage` en `attempt`:** poner `coverage` también en `check`, quitarlo, o darle un `run` propio.

   **`adds` se escribe entero.** Un proyecto que cambia `--cov=src` tiene que conservar el resto, y si quita `--cov-fail-under=0` vuelve a aplicarse su `fail_under`. El cargador no puede saberlo, porque no conoce los argumentos de ninguna herramienta, que son del perfil (`test_no_profile_tool_names_outside_profiles_dir`). Lo dicen la descripción del campo en el esquema y la skill del perfil, que da siempre el `adds` completo.
3. **`when_plugin` tiene un solo significado, en todos los controles:** sin el plugin, el control se salta con el motivo de hoy (`plugin_skip_reason`).
   - **Un control con `when_plugin` solo vale en un perfil con `plugins_command`,** sea lector o no.
     - La regla de hoy (si no se puede saber, se ejecuta) era una rama que solo ejercían los tests.
     - En un lector, esa regla significaría añadir `--cov` a ciegas: sin el plugin, `unit` se rompería con «unrecognized arguments», y el fallo de una dependencia de cobertura caería sobre los tests.
   - **Dónde se exige.** Lo exigen dos sitios, para que no haya un camino que se lo salte:
     - un validador de `Profile`, que tiene los controles del perfil y su `verification.plugins_command`;
     - `resolve_defences`, por donde pasa todo arranque (`bootstrap.py:145`), para los controles que añade el proyecto.
   - **`installed_plugins` deja de ser opcional.** `VerificationEnvironment.installed_plugins` es una tupla: lo que listó `plugins_command`, o ninguno si el perfil no lo declara, y entonces ningún control puede depender de un plugin. Por lo mismo:
     - `CheckProjectRequest.installed_plugins` es una tupla;
     - el `environment` de `VerifyAttempt` es obligatorio, y los tests usan uno falso;
     - `missing_plugin` recibe siempre una secuencia, y su rama de `None` desaparece sin dejar un error interno en su lugar.
   - **Un lector no tiene `when_exists`,** así que el plugin es lo único que decide antes de que corra su control leído.
4. **El comando efectivo del control leído depende solo de la configuración, de dónde se verifica y del plugin,** nunca de lo que elijan `--level` o `--control`. Recibe `adds` cuando se cumplen dos cosas:
   - **su lector corre donde se verifica,** que se sabe por la configuración, no por la selección:
     - en un run, cuando el lector es `attempt` (ADR-0007);
     - en `check`, siempre, porque `check` ejecuta los dos momentos;
   - **y el lector no se salta por el plugin** (punto 3).

   Así `lictor check --control unit` ejecuta el mismo `unit` que `lictor check` entero y que el run con `coverage` en `attempt`, y un fallo se reproduce igual.

   - **Lo decide una función del dominio,** antes de ejecutar el control leído, con la misma decisión del plugin que se registra para el lector, de modo que no puedan contradecirse.
   - **La decisión del plugin se calcula antes, pero se registra y surte efecto en la posición del lector,** como la de cualquier control. Un lector saltado consta como saltado si la verificación llega a su turno. Si antes para un fallo `block`, no consta, como no consta hoy ningún control tras ese fallo.
   - **El comando se compone por tokens, nunca por cadenas.**
     - `run` y `adds` se trocean por separado con `shlex`.
     - El `{out}` de cada parte se sustituye por la ruta de su control: el del control leído en `run` y el del lector en `adds`.
     - Después se concatenan los tokens.
     - `fill_report_path` pasa a recibir la lista de partes con su ruta y borra todos esos informes antes de ejecutar, porque un informe viejo no prueba nada.
   - **Si el proyecto escribe su propio `run` en `unit`** (otro directorio, `-n auto`), `adds` se añade igual: `coverage` lee esa ejecución, sea cual sea. Si ese `run` no es de pytest, los argumentos lo rompen. La salida es quitar `coverage` o darle un `run` propio, y la skill del perfil dice que no se ponga `--cov` en `unit`.
   - **El agente ve el comando con que se verificó.** Sus `agent_commands` siguen sin `--cov`, porque el agente no mide cobertura. Pero cuando el comando efectivo de un control que falla no es su `run`, el error condensado que va al prompt lo nombra: «unit se ejecutó como: <comando efectivo>». Así un test que solo falla bajo cobertura se puede reproducir en la copia, en vez de gastar intentos con un comando con el que pasa. La regla («nombrar el comando cuando no es el `run`») es del dominio, junto a la del comando efectivo.
     - **Solo si se ejecutó.** Un control saltado o que no pudo ejecutarse no ejecutó ningún comando, así que su error no nombra ninguno, y `lictor check` tampoco marca su línea con la nota del punto 7.
     - **Con `{out}` como está configurado, y una frase que lo explica.** Las rutas reales apuntan a la evidencia, en `.lictor/runs/` del proyecto, y nombrarlas invitaría al agente a escribir en el estado del run. Por eso el comando va como está configurado, cada `{out}` en su sitio, seguido de «Cada {out} es la ruta donde Lictor deja un informe, fuera de tu copia: para reproducirlo, pon en cada uno una ruta distinta fuera del proyecto». Sin esa frase, copiado tal cual, el comando escribiría los dos informes en un fichero `{out}` de la copia, que `L0-rutas` rechazaría.
5. **El informe del lector se lee en cuanto termina su control leído, y solo si el lector está en la verificación.**
   - **Cuándo se lee:** cuando `unit` acaba, con el mismo momento y las mismas condiciones que su JUnit: después de `verify_and_restore` y de `save_output`. El `CoverageReader` de ADR-0009 lee el `{out}` del lector, y el hecho leído queda en memoria en la verificación en curso, asociado al lector, hasta su turno. En ese turno solo se da el veredicto. Un reintento es otra verificación, con su directorio y su memoria.
   - **Solo se lee si el lector está entre los controles de esta verificación.** Con `lictor check --control unit`, `unit` recibe `adds` (punto 4), el informe queda escrito en `.lictor/check/` y nadie lo lee, porque `coverage` no está en la selección.
   - **Si `unit` manipuló el estado,** es un `Unjudged` (ADR-0009) y no se lee ninguno de sus dos informes. `consequence` acaba la verificación en `L0-estado`, como hoy, y el lector no consta.
   - **Si `unit` se corta por tiempo,** el informe que dejó se lee igual. Si está a medias, es un `InvalidReport`.
   - **Ningún comando corre entre la lectura del informe y el veredicto del lector.** El orden deja de tener lectura de seguridad: el control leído tiene que ir antes que su lector, y el tipo lo exige (punto 2), pero no necesariamente justo antes. Un control que un proyecto añade detrás de `unit` cae justo detrás (`_resolve_controls`), y exigir adyacencia daría exit 10 por un cambio inocente.
   - **Esto no es una defensa nueva.** `runs/` está fuera de `IntegritySnapshot`, y el informe lo escribe el proceso de `unit`, que corre código del proyecto y puede forjarlo, igual que hoy el de `coverage`. Es el límite conocido de `c6`, que este cambio no cierra ni empeora. Leer en el acto evita que un control intermedio, que también corre código del proyecto, tenga una ocasión más que hoy de tocarlo.
   - **Un control `block` que falle entre los dos** para la verificación antes del turno del lector, como cualquier fallo.
   - **El lector no tiene tiempo propio.** El `timeout_s` de `unit` cubre su ejecución, que bajo cobertura es más lenta. En el perfil es el valor por defecto, 600 s, que cubre de sobra suites de unitarios. El que tenga una más lenta se lo escribe a `unit`.
6. **Qué es `coverage` después de correr `unit`.**
   1. **Momento.** Un lector no puede correr donde no corre su control leído:
      - **un lector `attempt` con un control leído `check` se rechaza,** porque en el run el lector no tendría qué leer;
      - **un lector `check` con un control leído `attempt` vale.** En un run, el lector no está y `unit` corre tal cual. En `check` corren los dos, y `unit` recibe `adds`. Es la forma de medir la cobertura solo en `check` sin ejecutar los unitarios dos veces, con la consecuencia que dice el punto 7.
   2. **El resultado de un lector es una de tres variantes:**
      - `Skipped` y `NotExecuted`, de ADR-0009;
      - **`Read(reads, report, verdict)`,** una cuarta variante que este ADR añade:
        - `reads` es el `id` del control leído;
        - `report` es el hecho de cobertura que se leyó: `CoverageReport`, `MissingReport` o `InvalidReport`;
        - `verdict` es el del lector, `Passed | Failed(reason)`;
        - la puntuación es una propiedad derivada de `report`;
        - no lleva código de salida, ni duración, ni log, porque no ejecutó nada.

      **`consequence` y `failure_of`** (ADR-0009) cubren `Read`:
      - un `Read` que pasa, o de un control `measure`, sigue;
      - uno que falla, de un control `block`, bloquea, con el motivo del veredicto, sin casos y sin salida.

   **El veredicto del lector lo da una función del dominio** que recibe el resultado del control leído (una variante de ADR-0009) y el hecho de cobertura. **No vuelve a interpretar el código de salida de `unit`:** usa el veredicto que `unit` ya tiene. «Sin casos» es que el `Judged` de `unit` pasó con un `JUnitReport` sin casos, que es como `unit` cuenta una pasada sin tests.

   | Resultado de `unit` | Informe de cobertura | `coverage` | Puntuación |
   |:--|:--|:--|:--|
   | `Skipped` | Cualquiera, no se lee | `Skipped`, con el motivo de `unit` | Ninguna |
   | `NotExecuted` | Cualquiera, no se lee | `NotExecuted`: «unit no se ejecutó: <motivo de unit>» | Ninguna |
   | `Unjudged` | No se lee | No consta: la verificación acaba en `L0-estado` | — |
   | `Judged` que pasó, sin casos | `MissingReport`, o válido sin nada medido | `Read` que pasa | Ninguna: con `min_score` falla porque no midió nada |
   | `Judged`, cualquier otro | `MissingReport` | `Read` que falla: «Sin informe de cobertura» | Ninguna |
   | `Judged`, cualquier otro | Válido sin nada medido | `Read` que falla: «El informe de cobertura no midió nada: lo que mide no tiene código» (ADR-0009, cambio 6) | Ninguna |
   | `Judged`, cualquiera | `InvalidReport` | `Read` que falla: «El informe de cobertura no es válido: <motivo>» | Ninguna |
   | `Judged`, cualquiera | Válido, con algo medido | `Read` que pasa, y después `min_score` | La del informe; con `unit` fallido, marcada (punto 8) |

   - **Un `unit` que se cortó por tiempo, o que murió sin dejar JUnit, es un `Judged` que falló** (ADR-0009, cambios 1 y 10). Con él, `coverage` falla si no hay informe. No pasa «sin puntuación», como pasaría si se reinterpretara el código 5.
   - **«Sin informe» y «nada medido» reciben el mismo veredicto, con su propio motivo,** porque son el mismo hecho: no hay nada que puntuar. Que coverage.py escriba o no el informe depende de si lo medido tiene ficheros, un detalle que no debe decidir el veredicto.
   - **Con informe válido, `coverage` puntúa sea cual sea el veredicto de `unit`.** Con «sin casos» y código en `src` la puntuación es un 0 %, una medida cierta.
   - **Con `unit` fallido** (en `measure`, o en `lictor check --keep-going`), la puntuación se marca. Con `unit` en `block`, que es lo del perfil, la verificación para en `unit` y `coverage` no llega a su turno, como hoy.
7. **`lictor check` completa la selección en un solo sentido, y dice lo que hizo.** Una función del dominio sustituye a `_selected`. Recibe:
   - los controles, en el orden de `BootstrapResult.controls`;
   - la aceptación;
   - `--level` y `--control`;
   - los plugins instalados, porque su regla depende de la decisión del plugin.

   Hace lo que hoy hace `_selected`:
   - normaliza los niveles;
   - sale con un `ConfigError` ante un `id` que no existe, nombrando los que hay;
   - decide si entra la aceptación.

   Además, completa la selección: si elige un lector y no su control leído (`--control coverage`, o un `--level` que elige uno y no el otro), añade el leído, salvo que el lector se vaya a saltar por el plugin, porque entonces no hay nada que leer. Devuelve los controles que se ejecutan, en el orden de la configuración, y los añadidos con su motivo.
   - **`CheckProjectResult` lleva los añadidos, y `cli.py` los imprime:** «Se ejecuta también unit, cuya ejecución lee coverage». El `unit` añadido cuenta como cualquier control: con `block`, su fallo hace salir a `check` con 10.
   - **Si elige el control leído y no su lector,** no se añade nada. El leído corre con su comando efectivo del punto 4, el mismo que con el lector, y su informe de cobertura no se lee (punto 5).
   - **Si el control leído recibe `adds` de un lector `check`,** su línea lo dice: «unit (con los argumentos de coverage, que solo corre en check)». Un test que solo falla bajo cobertura pasa en todos los intentos del run y falla en `check` como `unit`. Sin esa nota, el fallo no llevaría la marca «(solo en check)» de ADR-0007, que va por `id`, y se leería como una regresión del run.
   - **`check` deja de verificar exactamente lo que el run en un caso:** con un lector `check`, `lictor check` nunca ejecuta el `unit` sin cobertura que ejecutó el run. AGENTS.md lo dice junto a «`lictor check` verifica lo que haría `run`».
   - En el perfil los dos son L2, así que con `--level` solo ocurre si un proyecto les cambia el nivel.
8. **Qué registra un lector.** Cada dato se guarda una vez, donde nace:
   - **El códec de ADR-0009 escribe un `Read` con `outcome: "read"`,** `mode`, `reads`, `passed`, `reason` si falló y `score` si la hay, en `verify.finished.v2`. No lleva código de salida, duración ni log.
   - **`report.md` y la salida de `lictor check`** muestran un `Read` como «lee unit», con su veredicto y su puntuación. Como log, la salida de `check` apunta al del control leído.
   - **La marca del `unit` fallido sale de los dos resultados de la misma verificación, sin otro campo.** «Puntuaciones» de `report.md` y la salida de `lictor check` añaden «(de un unit fallido)» cuando el resultado del control leído es un `Judged` cuyo veredicto falló. Es el mismo hecho, y guardarlo dos veces podría contradecirse.
   - **`IntegritySnapshot` no cambia.** En el run, la ejecución de `unit` va dentro del suyo, y el lector no ejecuta nada. En `check` no hay ninguno, como hoy para todos los controles.
9. **La regla vive en el dominio,** en funciones puras, sin I/O:
   - en `domain/controls.py`, junto a los tipos de control:
     - la decisión del plugin (punto 3), que se toma una vez para las dos posiciones en `verification_steps`, los pasos que siguen la verificación del run y la de `check`;
     - el comando efectivo del control leído, y si el error lo nombra (punto 4);
     - la selección de `check` (punto 7);
     - las reglas de conjunto (punto 2), que llaman los validadores de `LictorConfig` y `Profile`;
   - en `domain/results.py` y `domain/verdicts.py`, junto a las demás variantes y veredictos de ADR-0009, el veredicto del lector (punto 6). `results.py` ya importa los tipos de control, así que en `controls.py` sería un ciclo.

   Un resultado guarda el tipo de su control: `CommandEntry` lleva un `CommandControl` y nunca un `Read`, y `ReaderEntry`, un `ReaderControl` y nunca un `Executed`. `ControlEntry` es la unión de los dos.

   La tabla de la forma (punto 2) es del adaptador de configuración, porque trabaja con lo que escribe una persona antes de que exista un `Control`. Va en una función propia, probada con cada fila.

   `VerifyAttempt` y `CheckProject` usan estas funciones, y ninguna regla se repite entre ellos. Los tests de `domain/controls.py` entran en `[tool.mutmut]`.
10. **El `lictor.json` de Lictor cambia solo de perfil.** Pasa a `python-uv@0.2`. Su `coverage` escribe su `run`: ejecuta `tests` entero con `-n auto` y sigue igual, así que `tests/unit` corre dos veces (18 s por `check`). Es lo correcto mientras su cobertura mida otra suite que `unit`: los tests de integración entran en la cifra, y `unit` es la capa rápida del cierre local.

    **El fichero apenas cambia, pero su `coverage` resuelto sí.** Hoy hereda del perfil `empty_exit_codes: [5]` y `when_exists: "tests/unit"`, y deja de heredarlos, porque el `coverage` del perfil es un lector (punto 2). No los necesita: `tests` siempre existe en Lictor, y una suite sin casos debe fallar.

    **`coverage-domain` sigue dependiendo de un `.coverage` sin declarar.** Declararlo exigiría un control que ejecuta un comando sobre el informe de otro, que es otra cosa que `reads`, y queda fuera. Por eso el cargador no ve esa dependencia, y los dos controles se quedan en `attempt`, como dice ADR-0007 (punto 8).
11. **Sin compatibilidad con la 0.3.0,** como en ADR-0007: Lictor no tiene usuarios todavía.
    - La huella cambia, y un run a medias no se reanuda (exit 10).
    - El perfil sube a `@0.2`, y un `lictor.json` con `@0.1`, como las copias enteras de la 0.2.0 (F43), sale con el mensaje del punto 1. No hay aviso de `doctor` aparte: el error de carga ya lo dice.
    - El CHANGELOG lo dice en una línea.

## Consecuencias
- **El perfil por defecto ejecuta los unitarios una vez por intento.**
- **Un test que solo falla bajo cobertura pasa de medir a bloquear,** porque ahora rompe `unit`. El agente lo ve, porque su error nombra el comando con que se verificó (punto 4), y puede reproducirlo en su copia.
- **Un test en rojo ya no bloquea a través de `coverage`.** Con `unit` en `measure` y `coverage` en `block`, hoy un test en rojo hace fallar `coverage` por su código de salida. Con este ADR, `coverage` puntúa, lleva la marca «(de un unit fallido)», y la verificación sigue si llega al mínimo. Es lo que dice «cada control decide una cosa»: quien quiera que un test en rojo bloquee pone `unit` en `block`, que es lo del perfil. Va en el CHANGELOG.
- **Con `coverage` en `check`, `unit` no ejecuta lo mismo en el run que en `check`.** Un test que solo falla bajo cobertura aparece en `check` como fallo de `unit`, con la nota del punto 7.
- **`lictor check --control unit` ejecuta `unit` con los argumentos de `coverage`** cuando el perfil lo tiene: es el mismo `unit` que verifica todo lo demás.
- **El `fail_under` del proyecto deja de aplicarse en la verificación.** El umbral se escribe como `min_score`, que es donde Lictor lo lee y lo explica.
- **El comando de `unit` depende de otro control.** Se lee en su evidencia, que empieza por el comando efectivo de cada log (ADR-0009).
- **Un `coverage` con `run` propio tiene que escribir todo lo que su ejecución necesita,** porque el perfil ya no se lo da.
- **`when_plugin` exige `plugins_command` en todos los controles,** y la lista de plugins deja de ser opcional en la aplicación.
- **Todo comando se trocea al cargar,** así que uno mal escrito es un error de configuración y no una excepción al ejecutarse.
- **El perfil pasa a `python-uv@0.2`,** y un `lictor.json` con `@0.1` no carga.
- **Pruebas:**
  - **Dominio, en `[tool.mutmut]`:**
    - las funciones del punto 9, con cada fila de la tabla del punto 6;
    - el comando efectivo en run y en `check`, con el lector en cada momento y sin el plugin, y cuándo lo nombra el error;
    - la selección del punto 7, con un `id` desconocido, la aceptación, la posición del añadido y el plugin ausente;
    - las reglas de conjunto, también el control leído que no es JUnit;
    - `consequence` y `failure_of` con un `Read`.
  - **Configuración:**
    - cada fila de la tabla del punto 2;
    - `enabled: false` con otros campos;
    - un `null` escrito, en `run`, `reads` o `adds` (rechazado, y el esquema no lo admite) y en un campo de ejecución sobre un lector (error);
    - un control sin `run` ni `reads`;
    - un comando que no trocea con `shlex`, en `run`, `adds` y `acceptance.run`;
    - un `LictorConfig` construido a mano con una pareja inválida;
    - un `Profile` con un lector huérfano, y otro con `when_plugin` sin `plugins_command`;
    - un control del proyecto con `when_plugin` en un perfil sin `plugins_command`, en `resolve_defences`;
    - un lector de `id` nuevo escrito antes que su control leído;
    - `allow_empty` escrito en `coverage`;
    - `{"id": "unit", "stage": "check"}` con su mensaje;
    - un `lictor.json` con `python-uv@0.1`, con su mensaje;
    - que la huella cambia al cambiar `adds`.
  - **Cableado de `VerifyAttempt` y `CheckProject`** (fuera de `[tool.mutmut]`, con mutantes a mano contra la suite):
    - `adds` aplicado o no;
    - el comando compuesto por tokens con dos `{out}`;
    - el informe leído al terminar `unit`, aunque un control intermedio lo cambie;
    - el informe no leído si el lector no está en la selección;
    - `unit` con tampering: no se lee, y el lector no consta;
    - `unit` cortado por tiempo con el informe a medias;
    - el borrado de los dos informes;
    - la posición en que consta el lector;
    - el error condensado de un `unit` que falla con `adds`, que nombra su comando.
  - **`check`:**
    - `--control coverage` añade `unit` y lo dice;
    - su fallo sale con 10;
    - un `--level` que parte la pareja;
    - el plugin ausente no añade `unit`;
    - `--control unit` ejecuta el mismo comando que `check` entero;
    - `--keep-going` con la marca;
    - la nota del punto 7 con un lector `check`.
  - **Informe:** `BuildReport` con un `Read`, con «lee unit» y con «(de un unit fallido)».
  - **Repro:**
    - **uno `@smoke`,** con la configuración entera de `lictor init`, que comprueba con el log de `unit` que `tests/unit` corre una sola vez por verificación y que la puntuación sale igual que con dos ejecuciones. Entra en `SMOKE` (`tests/integration/test_repro_layers.py:22-37`), que queda con 9 de un máximo de 10;
    - **tres con `@all_controls`,** que entran en `ALL_CONTROLS` (`:41-48`), porque sin la configuración entera `light_config` quitaría `unit` y `coverage` y afirmarían una ausencia sin que nada corra:
      - que un `fail_under` del proyecto no hace fallar `unit`;
      - un proyecto sin `pytest-cov`: `unit` corre sin `adds`, y `coverage` se salta;
      - un lector en `stage: check` con su leído en `attempt`: sin `--cov` en el log del run, y con él en `check`.
  - **Tests que cambian:**
    - los que ponen `unit` en `check` sobre el perfil real: `tests/integration/test_cli.py:91`, `tests/unit/test_bootstrap.py:91` y `tests/unit/test_config_project_file.py:112` y `:151`. Pasan a poner los dos en `check`, o a esperar el error;
    - `tests/unit/test_config_project_file.py:92`, que escribe `timeout_s` en `coverage`;
    - `tests/unit/test_config.py:1114-1130`, que afirman `coverage.when_exists`, `"--cov" in coverage.run` y `"--cov-report=json:{out}" in coverage.run`;
    - la rama de `None` de `missing_plugin`: `tests/unit/test_rules.py:706-707`, `tests/unit/test_check_project.py:199-201` y `tests/unit/test_verification_env.py:557`;
    - `tests/unit/test_stages.py:19-33`, que usa un `_Control` estructural;
    - las construcciones directas de `VerificationControl`, `CheckControlSpec` y `ControlConfig`: `tests/unit/test_verify_attempt.py`, `test_check_project.py`, `test_check_working_tree.py:27`, `test_config.py:1015` y `tests/integration/test_graph.py:237` y `:313`;
    - `light_config` (`tests/integration/repro/harness.py:119-123`) y su test (`tests/integration/test_repro_layers.py:99-106`). Pasan a tratar la pareja junta: quitan los dos, o conservan los dos si el caso nombra cualquiera, para que un caso ligero no salga con 10;
    - las 39 menciones de `python-uv@0.1`, en los tests, los ejemplos, `scripts/init-repo.sh`, `lictor.json`, README.md y AGENTS.md;
    - el test de la 0.2.0 (`test_a_lictor_json_of_0_2_0_is_refused_and_says_what_to_remove`, desde ADR-0009), que comprueba además que esa copia, sin `acceptance.report`, se rechaza por `@0.1` con su mensaje.
  - **Esquema:** `schema/lictor.schema.json` se regenera con `reads` y `adds` en `ControlChange`, sin `null` en `run`, `reads` ni `adds`, y con la descripción de `adds` (punto 2).
- **Documentación que cambia:**
  - **`README.md`:**
    - `:460`: para añadir un control hacen falta `level` y `report`, y además `run`, o `reads` y `adds`;
    - `:502`: `when_plugin` exige `plugins_command`, y `coverage` lee la ejecución de `unit`;
    - `:504-510`: «Una pasada sin tests (código 5) no tiene puntuación» deja de ser cierto. Con código en `src` hay informe y un 0 %;
    - `python-uv@0.2`;
  - **AGENTS.md:**
    - los controles lectores;
    - el párrafo de `when_plugin` y `missing_plugin`;
    - «a full copy of the profile … resolves … to the same `LictorConfig`», que deja de ser cierto;
    - «another `id` adds a control, needs `level`, `run` and `report`»;
    - el párrafo de `light_config` y `LIGHT_DISABLED`;
    - la salvedad de `lictor check` del punto 7;
    - `python-uv@0.2`;
  - **El CHANGELOG:**
    - el perfil `@0.2` y la huella;
    - el `fail_under` ignorado;
    - un test que solo falla bajo cobertura bloquea;
    - un test en rojo ya no bloquea a través de `coverage`;
    - `when_plugin` exige `plugins_command`;
    - los comandos se trocean al cargar;
  - **`python-uv.skill.md`,** que es donde va lo de pytest:
    - `min_score` en vez de `fail_under`;
    - nada de `--cov` en `unit`;
    - el `adds` completo cuando se cambia lo medido;
    - cómo medir la cobertura solo en `check` (`{"id": "coverage", "stage": "check"}`) y lo que eso supone;
    - qué escribir para darle a `coverage` un `run` propio;
  - **`skill/body.md`,** que no nombra herramientas ni perfiles: solo el concepto de un control que lee la ejecución de otro, y que un umbral se escribe como `min_score`;
  - **`.audits/qa/plan-checks.md`, sección 4b:** remite a este ADR y a ADR-0009, porque `plugin_args`, `from: "unit"`, el aviso de `doctor` (F43), «no hace falta `python-uv@0.2`» y «el `lictor.json` de Lictor puede hacer lo mismo» ya no valen.

## Alternativas descartadas
- **A. Un control con dos informes (`{out}` JUnit y `{score_out}`).** Pone dos veredictos en un resultado, y le da a `mode` un significado ambiguo: un `{"id": "coverage", "mode": "measure"}` dejaría los unitarios sin bloquear.
- **B. Que el lector de cobertura lea también el JUnit.** Tiene el mismo problema que A.
- **`plugin_args` en `unit`, con `{out:coverage}`** (el primer borrador):
  - deja una referencia colgando cuando se quita `coverage`;
  - con `installed_plugins` en `None`, añade `--cov` a ciegas;
  - y pone el argumento de cobertura en un control que no lo usa.

  Los dos diseños tienen que componer un comando con dos `{out}` (punto 4), así que eso no los distingue.
- **Un solo modelo plano de control con un validador.** Deja representable un lector con campos de ejecución. Sobre el control resuelto, «declarado» solo podría leerse como «distinto del valor por defecto».
- **Una unión de pydantic sin resolver la forma antes.** Daría al usuario el error de las dos ramas, no lo que tiene que arreglar.
- **Validar las reglas de conjunto solo en el cargador.** Un `LictorConfig` construido a mano, o un perfil, se las saltaría.
- **Lectores de cualquier formato, o de cualquier control leído.** Ninguna pareja salvo cobertura sobre JUnit tiene una lectura con sentido, y cada una pediría su veredicto.
- **Que el comando del control leído dependa de la selección de `check`.** `--control unit` ejecutaría otro `unit` que el run, y un fallo no se reproduciría.
- **Leer el informe en el turno del lector.** Deja una ventana en la que corre código del proyecto entre la escritura y la lectura, y obliga a exigir adyacencia, que rompe con un cambio inocente.
- **Prohibir un lector `check` con su control leído `attempt`.** Obligaría a darle un `run` propio, que es ejecutar dos veces lo que este ADR quiere ejecutar una.
- **Exigir `plugins_command` solo a los lectores, o solo en el cargador.** Dejaría a `when_plugin` con dos significados, o un camino que se lo salta.
- **Sustituir la rama de `None` de `installed_plugins` por un error interno.** Cambiaría de sitio una rama en vez de quitarla. Con la lista siempre definida, no existe.
- **Que el veredicto del lector reinterprete el código de salida de `unit`.** Daría dos lecturas del mismo hecho: un `unit` muerto sin informe fallaría, y `coverage` pasaría «sin puntuación».
- **Un `Read` con una fuente «no leída» dentro.** Sería un «leído» que no leyó, cuando ADR-0009 ya tiene `NotExecuted` para «tocaba y no pudo».
- **Que `coverage` se salte cuando `unit` falla o no se ejecuta.** Con `unit` en `measure`, la cobertura de una suite con un test en rojo es la que hay, y esconderla sería peor que marcarla. Un `unit` que no pudo ejecutarse deja sin medir lo que tocaba medir.
- **Copiar en el lector el código de salida y el log de `unit`, o un campo propio para la marca del `unit` fallido.** Guardaría dos veces un mismo hecho, que podría contradecirse.
- **Conservar `--cov-report=term`.** Pondría la tabla en la evidencia de `unit` y en el prompt cuando la suite no arranca.
- **Que el cargador compruebe los argumentos de `adds`.** Tendría que conocer los de pytest, que son del perfil.
- **Mantener el perfil en `@0.1`.** Daría reglas nuevas con el nombre de las viejas, sin que el `lictor.json` que las pidió lo sepa.
- **Que el agente reciba `--cov` en sus comandos.** Mediría cobertura en cada llamada sin necesitarla. Basta con que su error nombre el comando con que falló.
- **Declarar ya la dependencia de `coverage-domain`.** Necesita otro tipo de control: uno que ejecuta un comando sobre el informe de otro.

## Nota (2026-11-26)

`agent_commands` ya no existe: las órdenes del agente salen de los controles que bloquean en cada
intento y de la aceptación, y el arreglo de un control es su `agent_fix` (ADR-0018). Donde esta ADR
dice `agent_commands`, se lee «las órdenes que la ADR-0018 deriva de los controles».
