# ADR-0009: Los lectores leen hechos y el dominio da los veredictos

## Estado
Aceptada (2026-11-16). Sale de la cuarta revisión de ADR-0008, que mezclaba dos decisiones: que `coverage` lea la ejecución de `unit` (F46), y cómo se leen los informes y se juzgan los controles. Esta es la segunda. La revisó contra el código un revisor independiente, que amplió la caracterización y ejecutó pytest sobre proyectos mínimos, y esta versión recoge sus hallazgos. Va antes que ADR-0008 en la implementación, que se apoya en ella.

**Su contrato es `tests/unit/characterization_reports.jsonl`** (commit `ccf538b`): 719 filas medidas contra `master` (`50af6a5`) ejecutando los cuatro lectores y la aceptación como lo hace cada sitio del código (`tests/unit/characterization_reports.py`). `test_characterization_reports.py` las comprueba. Lo que aquí se dice «como hoy» es lo que dice ese fichero. Cada cambio del punto 4 cambia sus filas a mano, en el mismo commit que lo implementa, así que el diff del fichero es la lista de cambios. Una fila que cambie sin estar en el punto 4 es un error.

## Contexto
**Los cuatro lectores cumplen un mismo puerto, `ResultReader`,** que recibe la salida, el código de salida, la duración y `empty_exit_codes` y devuelve un `ControlResult` con veredicto (`ports.py:224-238`). Cada lector lee un fichero y además decide si el control pasa (`adapters/readers/`).

**Cada sitio le da al lector algo distinto:**
- **Los controles del run y de `check`:** `reader_input` les da la ruta de `{out}` como cadena. Un control `exit-code` recibe en su lugar `format_output`: stdout, un salto de línea y stderr, recortado (`run_files.py:243-251`). La rama de stdout de `reader_input` no se alcanza, porque `{out}` es obligatorio salvo en `exit-code` (`models.py:254`).
- **La aceptación del run** (`VerifyAttempt._run_acceptance`) recibe la ruta, sin `empty_exit_codes`. Su veredicto lo dan `suite_crash_reason` (`rules.py:408-426`), `describe_crash` (`run_files.py:263-266`), `map_test_cases_to_scenarios` y, en el rojo, `judge_red`. El `ok` del lector se guarda y no decide nada: es falso mientras quede un escenario pendiente en rojo, aunque la verificación pase.
- **La aceptación de `check`** (`CheckProject`) se juzga solo con el `ok` del lector, con `empty_exit_codes=()` (`check_project.py:126-131`, `:207`), sin `suite_crash_reason`. `check` no usa `IntegritySnapshot`.
- **El rojo y los impostores** (`CheckRed`) leen el fichero ellos mismos con `errors="replace"`, y si no existe le pasan al lector el stdout (`check_red.py:260-265`, `:380-384`).
- **`JUnitReader` trata como ruta todo contenido que no empiece por `<`** (`readers/junit.py:15-27`). Así, en `CheckRed`, un `{out}` cuyo contenido es la ruta de otro fichero hace leer ese otro fichero, y un BOM inicial hace que un informe válido se lea como una ruta.

**`ControlResult` es un registro plano de opcionales** (`model.py:308-319`). Que un control se saltó, no se ejecutó, se ejecutó y no se juzgó (manipuló el estado) o se ejecutó y se juzgó solo se deduce de qué campos están llenos:
- un resultado con tampering es `ControlResult(ok=False)`, con código y log y sin error (`verify_attempt.py:594-596`);
- la aceptación lleva un `mode` (`"block"` por defecto) que no tiene;
- un `ok=False` con tampering se distingue de un fallo solo porque `_run_commands` mira `tampered` antes que el modo (`verify_attempt.py:395-404`).

**Ningún sitio de la verificación mira `CommandOutput.timed_out`.** Un comando cortado por tiempo da el código -9, y el motivo dice «código -9», que no explica nada (`model.py:264`).

**El journal.** `verify.finished` se emite con el tipo `lictor.verify.finished.v1` (`graph.py:575`, `:640`).
- Vuelca los resultados en `controls` (`control_summaries`, `verify_attempt.py:169-183`), sin el motivo del fallo ni los casos.
- Aparte, en `control_durations` (`graph.py:582`, `:636-637`), repite las duraciones, que también se suman para `verify_duration_ms` del estado.
- Lo leen `BuildReport` (`build_report.py:534-574`) y `RunProgress`, que solo lee `control_durations` (`progress.py:198-201`).
- `score_from_record` tiene una rama para una mutación de la 0.2.0 sin `kind` (`rules.py:864-880`).
- El checkpoint no lleva resultados de control (`last_verify`, `graph.py:672`).

**Los tipos de un control.** Hay tres en la aplicación: `VerificationControlProtocol` y `VerificationControl` (`verify_attempt.py:65-110`) y `CheckControlSpec` (`check_project.py:31`). El formato del informe es un `str`.
- `VerifyAttempt._run_single_control` y `CheckProject._run_command` repiten el mismo despacho: ejecutar, buscar el lector, leer, `enforce_min_score`.
- La petición de `check` se construye en `cli.py:400-413` desde `runtime.config.controls`, con su propia conversión (`_check_spec`).

### Lo que dice el contrato y no es correcto
Las filas del contrato confirman lo anterior y fijan estos defectos. Cada uno es un cambio del punto 4.
1. **Un JUnit ausente, vacío o de espacios pasa con un código de `empty_exit_codes`** (`junit_control`: «ausente», «vacío (0 bytes)», «solo espacios», «directorio», «enlace roto», con 5 y `[5]`). Pero pytest con 0 tests sale con 5 y escribe un JUnit válido sin casos. Ausente es que la suite murió antes de escribirlo: con un `conftest.py` roto, pytest sale con 4 sin informe.
2. **La codificación de un JUnit decide según el sitio:**
   - un JUnit que declara `iso-8859-1` o que va en UTF-16 hace reventar el lector con un `UnicodeDecodeError` en los controles y en la aceptación (`junit_control`, `acceptance_run`, `acceptance_check`);
   - en el rojo, uno con BOM o en UTF-16 da 0 casos y una suite caída;
   - en el rojo, uno con bytes que no son UTF-8 pasa con `errors="replace"` (`red`).
3. **El rojo y los impostores tienen otras fuentes de informe, además de `{out}`:** el stdout, cuando `{out}` no existe («un XML JUnit»), y el contenido de `{out}` leído como ruta de otro fichero («el contenido es la ruta de otro JUnit»), que pasa (`red`).
4. **La aceptación de `check` no juzga la confianza en su informe** (`acceptance_check`: «1 caso failure» con código 2 da «Fallaron 1 casos de test», no que la suite se interrumpió). El `ok` coincide con el del run en todas las filas; el motivo no.
5. **Los motivos de una suite caída repiten el error del lector** (`acceptance_run`: «… sin casos de test en su informe: No hay casos de test (código de salida N)»), y uno ilegible se describe como si no tuviera casos.
6. **Un informe de cobertura que no mide nada puntúa un 100 %:**
   - `{"totals": {}}`, porque las claves ausentes valen 0 (`readers/coverage.py:26`);
   - 0 sentencias y 0 ramas.

   Los dos cumplen cualquier `min_score` (`coverage`). Lo segundo es lo que pasa con `--cov` sobre un paquete con solo un `__init__.py` vacío.
7. **Recuentos imposibles se aceptan como informes válidos** y cumplen cualquier `min_score`:
   - más líneas cubiertas que sentencias da un 450 % (`coverage`);
   - más mutantes muertos que el total da un 300 % (`mutation`);
   - más omitidos que el total da «0 de 1».
8. **Una mutación sin mutantes probados pasa.** «Todos omitidos», con código 0, da `ok` con 0,0 %. Solo falla si `total` es 0 (`mutation`).
9. **Un informe de cobertura o de mutación que no es UTF-8 se lee con `errors="replace"` y puntúa** («bytes no UTF-8», `coverage`, `mutation`). Un JSON tiene que ser UTF-8 (RFC 8259), y coverage.py escribe ASCII.
10. **Un comando cortado por tiempo se explica con «código -9».**

## Decisión
1. **Los lectores solo leen; los veredictos son funciones puras del dominio.** Un veredicto es una regla: combina lo que dice un informe con el código de salida, `empty_exit_codes`, `failure_exit_codes`, el tiempo, `min_score` o los escenarios. Una regla no vive en el adaptador que lee un fichero.
2. **Un puerto genérico, `ReportReader[R]`, con un lector tipado por formato.**
   - **Firma:** `read(path: Path) -> R | MissingReport | InvalidReport`. Recibe la ruta de `{out}`, nunca una cadena que a veces es una ruta y a veces la salida.
   - **Hechos, tipos del dominio:**
     - `JUnitReport(cases)`, `CoverageReport(totals)` y `MutationReport(counts)`;
     - `MissingReport()`;
     - `InvalidReport(reason)`, con un motivo que nunca está vacío.
   - **Tres lectores,** `JUnitReader: ReportReader[JUnitReport]`, `CoverageReader: ReportReader[CoverageReport]` y `MutationReader: ReportReader[MutationReport]`, que `bootstrap` entrega en un valor con tres campos tipados (`junit`, `coverage`, `mutation`).
   - **El formato del informe es un tipo cerrado del dominio** (`ReportFormat`: `exit-code`, `junit`, `coverage`, `mutation`), en el tipo de control del punto 9. El despacho es un `match` exhaustivo con `assert_never`. Así no se puede pedir un lector que no existe ni recibir un hecho de otro formato, y la rama «no hay lector para este formato» desaparece por tipo, sin error interno.
   - **`exit-code` no tiene informe ni lector.** `ExitCodeReader` desaparece. Su veredicto (punto 3) recibe la salida como hoy: `format_output`, es decir, stdout, un salto de línea y stderr, recortado.
   - **`reader_input` desaparece,** y con ella su rama de stdout.
   - **Qué es cada hecho:**
     - **`MissingReport`** si no hay nada en la ruta: `os.path.lexists` es falso.
     - **`InvalidReport`** con su motivo, para todo lo demás que no se puede leer:
       - un directorio («es un directorio»);
       - un enlace roto («es un enlace que no lleva a ningún fichero»);
       - un fichero que no se puede abrir («no se puede abrir: <motivo del sistema>»);
       - y lo que dice el punto siguiente para cada formato.
   - **JUnit.**
     - **Se analiza desde los bytes con `defusedxml`, que sigue las reglas de XML:** el BOM, la declaración de codificación y, sin ninguna de las dos, UTF-8. Un JUnit cuyos bytes no se pueden decodificar con la codificación que declara es un `InvalidReport`. Uno con BOM o con una codificación declarada es válido.
     - **Una entidad o un DTD siguen siendo un `InvalidReport`,** como hoy.
     - **Un fichero vacío o de solo espacios** es «el informe JUnit está vacío».
     - **Un contenido que no es XML,** también el texto antes del XML, es un `InvalidReport` con el error del analizador, como hoy en los controles.
     - **Un XML sin `<testcase>` es un `JUnitReport` sin casos,** que es lo que escribe pytest con 0 tests.
   - **Cobertura y mutación.**
     - **Se decodifican como UTF-8 estricto y después se analizan.** Un JSON con bytes que no son UTF-8, también uno en UTF-16, es «no es UTF-8» (cambio 9). Un BOM es «no es JSON», como hoy: `json.loads` sobre texto lo rechaza.
     - **Un fichero vacío no se trata aparte:** da «no es JSON», como hoy.
   - **Un informe de cobertura es válido solo si sus recuentos son posibles.** Si no, es «El informe de cobertura no es válido: <motivo>»:
     - `covered_lines` y `num_statements` están presentes;
     - los cuatro valores son enteros no negativos, como hoy;
     - `covered_lines ≤ num_statements` y `covered_branches ≤ num_branches`.
   - **Un informe de mutación es válido solo si sus recuentos son posibles.** Si no, es «El informe de mutación no es válido: <motivo>»:
     - los valores son enteros no negativos, como hoy;
     - ninguno es mayor que `total`;
     - la suma de los resultados, `skipped` incluido, no pasa de `total`.
   - **Un lector nunca lanza por el contenido.**
3. **Los veredictos.** Son funciones puras del dominio. Un veredicto es `Passed | Failed(reason)`, con un motivo que nunca está vacío, así que no hay un fallo sin motivo ni un `ok` con error. Los casos y la puntuación no son parte del veredicto: salen del hecho leído (punto 5).

   **Cada veredicto recibe la ejecución entera** (punto 5): el código, la duración, el tiempo límite y si se cortó por tiempo. **Un comando cortado por tiempo falla siempre con «Se cortó a los N s, su tiempo límite»** (cambio 10), antes que cualquier otro motivo, porque su código no dice nada. Los casos y la puntuación que dejó siguen saliendo de su informe.

   **Qué hace cada uno es lo que dice su tabla del contrato,** salvo las filas que cambia el punto 4:

   | Veredicto | Tabla del contrato | Recibe |
   |:--|:--|:--|
   | Código de salida | `exit_code` | La salida, como `format_output`, el código y `empty_exit_codes` |
   | JUnit de un control | `junit_control` | El hecho JUnit, el código y `empty_exit_codes` |
   | Cobertura de un control que ejecuta | `coverage` | El hecho de cobertura, el código y `empty_exit_codes` |
   | Mutación | `mutation` | El hecho de mutación y el código |
   | Confianza en el informe de la aceptación | `acceptance_run` y `red` | El hecho JUnit, el código y `failure_exit_codes` |
   | Aceptación de `check` | `acceptance_check` | Lo mismo, y después los casos |
   | Puntuación mínima | `min_score` | El veredicto, la puntuación y el mínimo |

   - **Código de salida.** Pasa con 0 o con un código de `empty_exit_codes`. Si no, falla con las últimas 40 líneas de la salida, y con «El comando falló con el código N» cuando esas líneas, recortadas, quedan vacías. Como la salida ya llega recortada, eso solo ocurre sin salida, que es lo de hoy: no es un cambio.
   - **La confianza en el informe de la aceptación** es una sola función, `trust(report, execution, failure_exit_codes) -> Crashed(reason) | Reported(junit)`. Es la única que construye un `Reported`, así que no se puede fiar de un informe sin pasar por ella. Sustituye a `suite_crash_reason`.

     | Informe | Código | Resultado |
     |:--|:--|:--|
     | Cortado por tiempo | Cualquiera | `Crashed`: «La suite se cortó a los N s, su tiempo límite: su informe no es fiable» (cambio 10) |
     | `MissingReport` | Cualquiera | `Crashed`: «La suite terminó con código N sin dejar informe» (cambio 1) |
     | `InvalidReport` | Cualquiera | `Crashed`: «La suite terminó con código N con un informe JUnit no válido: <motivo>» (cambio 5) |
     | Sin casos | Cualquiera | `Crashed`: «La suite terminó con código N sin casos de test en su informe» (cambio 5: sin el sufijo) |
     | Con casos | 0 | `Reported` |
     | Con casos | Fuera de `failure_exit_codes` | `Crashed`: «La suite se interrumpió con código N: su informe no es fiable» |
     | Con casos, ninguno fallido | De `failure_exit_codes` | `Crashed`: «La suite terminó con código N pero ningún test falló en su informe» |
     | Con casos, alguno fallido | De `failure_exit_codes` | `Reported` |

     `describe_crash` pasa a recibir el motivo y la salida, no un `ControlResult`: pone el motivo en la primera línea y la salida detrás, como hoy.
   - **La aceptación del run, el rojo y los impostores** usan el mismo `JUnitReader` sobre `{out}` y el mismo `trust`.
     - Con `Reported`, siguen como hoy: `map_test_cases_to_scenarios`, `judge_red`, `red_failure_problem` e `impostor_problem` reciben los casos del `JUnitReport`.
     - Con `Crashed`, el rojo rechaza el driver por suite caída, como hoy, y un impostor no prueba nada, como hoy uno sin casos.
   - **La aceptación de `check`** usa el mismo `trust`, con su `failure_exit_codes`. Con `Crashed` falla con su motivo. Con `Reported`, falla con «Fallaron N casos de test» si alguno falló, como hoy, y si no pasa. En `check`, el motivo no lleva la salida, como hoy. Pasa y falla en las mismas filas que hoy, y solo cambia el motivo de una suite caída (cambio 4).
   - **`enforce_min_score` se aplica después,** sobre los veredictos que pasaron, como hoy (tabla `min_score`):
     - falla uno sin puntuación con «El control no midió nada y declara un mínimo de N %»;
     - falla uno por debajo del mínimo;
     - no toca uno que ya falló.
4. **Cambios de veredicto deliberados.** Son los defectos del Contexto. Cada uno cambia filas del contrato y va en el CHANGELOG. No hay más: una fila que cambie sin estar aquí es un error.
   1. **Un informe que no está no prueba nada.**
      - **Un JUnit ausente falla siempre:**
        - en un control, con «La suite no dejó informe JUnit (código de salida N)»;
        - en la aceptación y el rojo, como suite caída.
      - **Un directorio o un enlace roto** en `{out}` son un `InvalidReport`, en los cuatro formatos.
      - **Un JUnit vacío o de espacios** es un `InvalidReport` y también falla.
      - **En cobertura,** un directorio o un enlace roto pasan de «Sin informe de cobertura» a informe no válido. El veredicto no cambia, salvo con un código de `empty_exit_codes`: ahí antes pasaban y ahora fallan.
   2. **La codificación de un JUnit sigue las reglas de XML en todos los sitios.**
      - **Pasa de reventar a leerse:** un JUnit que declara su codificación (`iso-8859-1`), en UTF-16 o con BOM.
      - **Pasa de leerse con `errors="replace"`, en el rojo, a no ser válido:** uno con bytes que no se pueden decodificar.
      - **En el rojo,** uno con BOM o en UTF-16 pasa de suite caída a leerse.
   3. **El informe solo sale de `{out}`.** El rojo y los impostores dejan de leer el stdout, y dejan de leer el contenido de `{out}` como la ruta de otro fichero. Esto quita fuentes, no las añade: el proceso bajo prueba ya puede escribir `{out}` (`c6`), y con una sola fuente no puede elegir otra.
   4. **La aceptación de `check` juzga la confianza en su informe como la del run.** El `ok` no cambia en ninguna fila; el motivo, sí.
   5. **Los motivos de una suite caída dicen lo que pasó, sin repetir el error del lector.** Uno ilegible se describe como no válido, no como uno sin casos. Llegan al prompt del agente por `condense_error`.
   6. **Un informe de cobertura que no mide nada no tiene puntuación.**
      - **Qué es no medir nada:** un informe válido con 0 sentencias y 0 ramas. Uno al que le faltan `covered_lines` o `num_statements`, como `{"totals": {}}`, no es válido (punto 2).
      - **Su veredicto es el de un informe ausente,** con su propio motivo, porque es el mismo hecho: no hay nada que puntuar.
        - **Con un código de `empty_exit_codes`, pasa sin puntuación,** y con `min_score` falla porque no midió nada.
        - **Con cualquier otro código, también el 0,** falla con «El informe de cobertura no midió nada: lo que mide no tiene código».

        Antes daba un 100 % que cumplía cualquier mínimo.
   7. **Un informe con recuentos imposibles no es válido** (punto 2). Antes daba porcentajes por encima del 100 %.
   8. **Una mutación sin mutantes probados falla sin puntuación,** con «El informe de mutación no tiene mutantes probados». «Probados» son el total menos los omitidos. Sustituye a «no tiene mutantes», que solo miraba `total` y conservaba una puntuación de 0.
   9. **Un informe de cobertura o de mutación que no es UTF-8 no es válido.**
   10. **Un comando cortado por tiempo lo dice.** En un control, falla con «Se cortó a los N s, su tiempo límite», y en la aceptación es una suite caída con ese motivo. El veredicto no cambia, porque el código -9 ya fallaba; cambia el motivo.
5. **El resultado de un control es una de tres variantes del dominio,** y cada dato se guarda una vez, donde nace. ADR-0008 añade una cuarta para los controles lectores (`Read`).
   - **`Skipped(reason)`:** no tocaba ejecutarlo, por su `when_exists` o su `when_plugin`.
   - **`NotExecuted(reason)`:** tocaba y no pudo ejecutarse, porque el intento borró su `when_exists`.
   - **`Executed(execution, judgement)`:** el control ejecutó su comando.
     - **`execution` es lo que dejó el comando:**
       - el comando efectivo, en tokens;
       - el código de salida;
       - la duración, el tiempo límite y si se cortó por tiempo;
       - la ruta de su log.
     - **`judgement` es una de dos:**
       - **`Judged(report, verdict)`:** se leyó lo que dejó y se juzgó.
         - `report` es el hecho leído (punto 2), o ninguno para `exit-code`, que no tiene informe.
         - `verdict` es el del punto 3, con `min_score` aplicado.
         - **Los casos de test y la puntuación son propiedades derivadas de `report`,** no campos: no pueden contradecir al informe ni estar donde el formato no los tiene.
       - **`Unjudged(changes)`:** el comando manipuló el estado. No se leyó su informe y no hay veredicto, porque decir «falló» sería inventar un veredicto que nadie dio. En `check` no se puede dar, porque `check` no usa `IntegritySnapshot` (Contexto), y nadie escribe esa rama.

   **La unión no tiene `ok`.** Lo que hace una verificación con un resultado lo decide una función del dominio, `consequence(control, result)`, que devuelve una de tres:
   - **`Proceed`:** sigue. Es lo que da un `Skipped`, un `Judged` que pasa, o cualquier resultado de un control `measure` que no sea `Unjudged`.
   - **`Block(failure)`:** la verificación falla en este control. Es lo que da un `NotExecuted` o un `Judged` que falla de un control `block`.
   - **`StateTampered(changes)`:** la verificación acaba en `L0-estado`. Es lo que da un `Unjudged`, sea cual sea el `mode`.

   **Por qué `consequence` y no un `ok`.** Con un `ok` en la unión, un `if not result.ok and mode == "block"` dejaría pasar un control `measure` que manipuló el estado. Hoy no pasa solo porque `_run_commands` mira `tampered` antes que el modo.

   **`failure` sale de `failure_of(result)`,** definida para todas las variantes. Para un `Skipped` o un `Judged` que pasa no hay fallo. Para un `Unjudged` tampoco: su motivo es el de `L0-estado`. Para un `NotExecuted`, devuelve su motivo, sin casos. Para un `Judged` que falla, devuelve el motivo del veredicto y los casos del informe, y añade la salida del comando cuando el informe es `MissingReport`, `InvalidReport` o un `JUnitReport` sin casos: son los casos en que solo la salida explica el fallo. Esto último es la regla de hoy (`verify_attempt.py:409-412`) dicha sobre los hechos.

   **El `mode` es del control, no del resultado.** Las verificaciones guardan pares de control y resultado, con el tipo de control del punto 9.
6. **La aceptación tiene su propio tipo,** porque no es un control: no tiene `mode`, ni `empty_exit_codes`, ni un `ok` que signifique lo mismo en el run y en `check`.
   - **`AcceptanceRun(execution, judgement)`.** `execution` es el mismo de un control, y `judgement` es una de tres:
     - **`Unjudged(changes)`:** manipuló el estado;
     - **`Crashed(reason)`;**
     - **`Reported(junit)`.**

     Las dos últimas salen de `trust` (punto 3).
   - **En el run, el veredicto es el de los escenarios:** el mapeo, `scenario_results` y `judge_red`. `VerifyResult` lleva `acceptance: AcceptanceRun | None`, que es `None` cuando la verificación paró antes de la aceptación.
   - **En `check`,** `CheckProjectResult` lleva el `AcceptanceRun` y su veredicto (punto 3).
   - **El rojo y los impostores** (`CheckRed`) construyen también un `AcceptanceRun`. Su `Unjudged` es el tampering que hoy rechazan con `L0-estado` (`check_red.py:252-258`, `:327-328`).
7. **El journal.** `verify.finished` cambia de forma, así que cambia de versión, y lo escribe y lo lee un solo códec.
   - **El tipo pasa a `lictor.verify.finished.v2`.** Un `v1` es de una versión anterior y no se interpreta. `BuildReport`, `RunProgress` y `lictor status` siguen una misma regla: lo saltan entero y lo cuentan, por evento, en un campo nuevo de `JournalDamage`, `older`, que `report.md` muestra junto a las líneas corruptas y ajenas («N eventos de una versión anterior, sin interpretar»). Así nunca se muestra un evento viejo como si fuera de la forma nueva, y un `v1` vacío, como el de `_reject_attempt` (`graph.py:573-591`), sigue la misma regla.
   - **Un solo códec, en el dominio.** `control_record(control, result)` escribe y `control_result_from_record(raw)` lee; igual para la aceptación. Los usan `control_summaries`, `BuildReport` y `RunProgress`. Un registro que no sigue el esquema es no válido: se salta y se cuenta como ajeno. Hoy escribe la aplicación y leen la aplicación y el dominio, cada uno con sus claves.
   - **El esquema de `verify.finished.v2`:**

     | Clave | Contenido |
     |:--|:--|
     | `controls.<id>.outcome` | `executed`, `skipped` o `not_executed`, y `read` con ADR-0008 |
     | `controls.<id>.mode` | El `mode` del control, tomado del par |
     | `skipped` y `not_executed` | `reason` |
     | `executed` | `exit_code`, `duration_ms`, `timed_out`, `output_path` y `judgement` (`judged` o `unjudged`) |
     | `executed` con `judged` | `passed` (booleano), `reason` si falló, `score` si el informe la tiene, y `cases`, que es solo el recuento por estado (`passed`, `failure`, `error`, `skipped`) |
     | `executed` con `unjudged` | `changes`, lo que cambió |
     | `acceptance` | `null` si la verificación paró antes de la aceptación. Si no, `exit_code`, `duration_ms`, `timed_out`, `output_path`, `judgement` (`unjudged`, `crashed` o `reported`) y, según el caso, `changes`, `reason` o `cases` (el recuento) |

     - **Los casos no van enteros.** Ya están en `control_<id>.xml` y en `acceptance.xml`, con su hash en `step_evidence`. Copiarlos en el journal en cada intento lo llenaría de lo mismo dos veces.
     - **El motivo sí entra, `reason`,** que hoy no se escribe. Es lo que explica un fallo sin abrir la evidencia, y no está en ningún otro sitio del journal.
     - **La puntuación conserva su propio `kind`** (`coverage` o `mutation`) dentro de `score`. Es otro campo, con otro nombre, que `outcome`.
   - **`control_durations` desaparece,** porque repetía `controls.<id>.duration_ms`. Una función del dominio, `verification_duration(results, acceptance)`, suma las duraciones de lo ejecutado. La usan `BuildReport`, `RunProgress` y el estado del grafo (`verify_duration_ms`, `graph.py:636`).
   - **La rama de `score_from_record` para la mutación de la 0.2.0 desaparece:** una puntuación sin `kind` es un registro no válido.
   - **El checkpoint no lleva resultados de control** (`last_verify`), así que no hay nada que migrar en él.
8. **Dónde se muestra cada variante:**
   - **`report.md`:**
     - un `Skipped`, con su motivo, en «Controles omitidos», como hoy;
     - un `NotExecuted`, como fallo con su motivo;
     - un `Unjudged`, como «manipuló el estado; sin veredicto», no como un fallo del control;
     - la aceptación, en su propia línea.
   - **La salida de `lictor check`** imprime la duración y el log solo de lo que se ejecutó (`cli.py:420-429`).
   - **`RunProgress`** pliega el journal con el mismo códec.
9. **El tipo de un control nace aquí, en el dominio.** `CommandControl`, en `domain/controls.py`, lleva todo lo que hoy llevan los tres tipos de la aplicación, con `report: ReportFormat` (punto 2). ADR-0008 le añade `ReaderControl` y la unión `Control`.
   - **Una sola conversión,** `domain_controls(config)`, en el adaptador de configuración, pasa del modelo resuelto a una tupla ordenada de controles del dominio.
   - **`bootstrap` la guarda entera en `BootstrapResult.controls`,** en el orden de la configuración. De ella salen las dos mitades por momento de `GraphContext` (`controls_of_stage`) y la petición de `check`, que `cli.py` deja de construir desde `runtime.config.controls`.
   - **Desaparecen** `VerificationControlProtocol`, `VerificationControl`, `CheckControlSpec` y `_check_spec`.
   - **Ejecutar un control, leerlo y juzgarlo es una sola función de la aplicación,** que usan `VerifyAttempt` y `CheckProject` y que hoy está repetida en `_run_single_control` y `_run_command`. Hace lo que necesita I/O, que es ejecutar, leer el informe y guardar el log. Las decisiones las toma el dominio: el veredicto, `enforce_min_score` y `consequence`. La envoltura de `IntegritySnapshot` sigue siendo de quien llama, porque solo el run la usa.
10. **Todo log empieza por su comando efectivo.** `save_output` recibe el comando y lo escribe antes del código de salida, en sus seis llamadas:
    - los controles del run y de `check`, que también cubre la aceptación de `check`;
    - la aceptación del run;
    - el rojo y los impostores de `CheckRed`;
    - el `prepare` del entorno.

    Es parte de `execution` (punto 5): la evidencia dice siempre qué se ejecutó, que hasta ahora se deducía de la configuración. ADR-0008 lo necesita, porque el comando de `unit` dependerá de otro control. Va aquí porque es un cambio de la evidencia de todos los comandos, no solo de la cobertura. El hash del log entra en `step_evidence` como hoy.
11. **`acceptance.report` desaparece.** La aceptación es el oráculo, y `map_test_cases_to_scenarios` necesita casos: su informe es siempre JUnit, así que un campo de un solo valor no decide nada.
    - **Un `lictor.json` o un perfil que lo escriba sale con 10,** con un mensaje propio (`acceptance_report_message`), como hoy `allow_empty`: «La aceptación siempre lee un informe JUnit: quita acceptance.report».
    - **Desaparecen con él:**
      - `VerifyAttemptRequest.acceptance_report`, `CheckRedRequest.acceptance_report` y `CheckAcceptanceSpec.report`;
      - el `readers.get(..., readers["junit"])` de `bootstrap.py:198`;
      - las ramas de `verify_attempt.py:637-643` y `check_red.py:83` y `:261`.
    - **El validador de `{out}` de la aceptación** (`models.py:135`) lo exige siempre.
    - **Lo escriben hoy** el `lictor.json` de Lictor y el perfil `python-uv`, que lo pierden.
12. **La regla vive en el dominio,** en funciones puras, sin I/O:
    - los tipos de los hechos y de los controles;
    - los veredictos, `trust`, `enforce_min_score`, `consequence`, `failure_of` y `verification_duration`;
    - el códec del journal.

    Sus tests entran en `[tool.mutmut]`. Los lectores quedan en `adapters/readers/`, sin una sola regla.
13. **Sin compatibilidad con la 0.3.0:** Lictor no tiene usuarios todavía.
    - El journal sube a `v2`.
    - La huella cambia, con ADR-0008 y con `acceptance.report`, así que un run a medias no se reanuda.
    - El CHANGELOG lo dice en una línea, junto a los diez cambios del punto 4.

## Consecuencias
- **Los adaptadores leen ficheros y el dominio decide,** para los cuatro formatos y para la aceptación. Un veredicto se prueba sin ficheros, y se muta.
- **El oráculo cambia en los cambios 1, 2, 3, 5 y 10, y en ninguno más.** Lo demuestra el contrato: cada fila de `acceptance_run` y `red` que cambia está en esos puntos.
  - **Dejan de valer como informe:** un JUnit ausente, vacío, ilegible, el que imprime el comando y el que señala otra ruta.
  - **Pasan a leerse,** donde antes reventaban o daban una suite caída: uno con BOM o con una codificación declarada.
- **Cinco falsos verdes de puntuación dejan de pasar un `min_score`:**
  - una cobertura que no midió nada;
  - recuentos de cobertura imposibles;
  - recuentos de mutación imposibles;
  - una mutación sin mutantes probados;
  - un JSON que no es UTF-8.
- **Un comando cortado por tiempo dice que se cortó.**
- **Un control que manipuló el estado no se puede tomar por un fallo,** ni dejarse pasar por ser `measure`.
- **La aceptación deja de llevar un `ok` y un `mode` sin significado,** en el journal y en los tipos.
- **El journal sube a `v2`** y pierde `control_durations`. Un journal con eventos `v1` los cuenta sin interpretarlos.
- **Todo log dice qué comando se ejecutó.**
- **`acceptance.report` deja de existir.**
- **Pruebas:**
  - **El contrato.** `test_characterization_reports.py` sigue comprobando cada fila, sin tocarlo, mientras se refactoriza. Cuando las funciones de los sitios dejen de existir, sus helpers (`characterization_reports.py`) pasan a llamar a los lectores y veredictos nuevos, y las filas solo cambian por el punto 4. Cada commit que implementa un cambio cambia las filas de ese cambio, y nada más.
  - **Dominio, en `[tool.mutmut]`:**
    - cada veredicto y `trust`, con todas las filas del contrato y con el tiempo cortado;
    - `consequence` con cada variante y cada `mode`, también un `measure` con `Unjudged`;
    - `failure_of`, con cada variante y la regla de la salida;
    - el códec, ida y vuelta, y el rechazo de un registro que no sigue el esquema;
    - `verification_duration`;
    - las propiedades derivadas de `Executed`.
  - **Lectores:** cada uno con cada informe del catálogo del contrato. Ya cubre ausente, directorio, enlace roto, vacío, de espacios, no analizable, no UTF-8, BOM, codificación declarada, UTF-16, sin `<testcase>`, recuentos imposibles y 0 sentencias.
  - **Cableado:**
    - el rojo y los impostores con el informe ausente y un XML en el stdout, que se rechazan como suite caída;
    - un control con tampering, `Unjudged` en el journal y en `report.md`;
    - un control `measure` con tampering, que acaba en `L0-estado`;
    - un control cortado por tiempo, con su motivo;
    - la aceptación de `check` sin informe y con casos y un código fuera de `failure_exit_codes`;
    - un journal con eventos `v1`, contados en `older`;
    - `RunProgress`, `BuildReport` y `verify_duration_ms` sumando sin `control_durations`;
    - cada log empieza por su comando, en los seis sitios;
    - un `lictor.json` con `acceptance.report`, que se rechaza con su mensaje.
  - **Tests que cambian:**
    - los de los cuatro lectores, cuyos casos de veredicto pasan al dominio;
    - los que construyen `ControlResult` o lectores falsos:
      - `tests/unit/test_scores.py`, `test_rules.py`, `test_check_red.py`, `test_check_working_tree.py:77`, `test_verify_attempt.py` y `test_check_project.py`;
      - `tests/unit/test_lictor_outcome.py:102`, que llama a `JUnitReader().read(str, exit_code=…)`;
      - `tests/integration/test_graph.py:181` y `:233`;
      - `tests/integration/repro/test_phase2_diagnostics.py:134`, que usa `ExitCodeReader`;
    - los dobles de `test_verify_attempt.py` y `test_check_project.py:38` que entregan el XML por stdout con una aceptación sin `{out}`, que pasan a escribir el fichero;
    - los registros `controls` y `control_durations` de `tests/unit/test_build_report.py` y `test_progress.py`;
    - las dos configuraciones con `"report": "exit-code"` en la aceptación (`tests/integration/test_cli.py:65`, `test_cli_runs.py:37`), que pasan a JUnit y pierden el campo.
- **Documentación:**
  - `schema/lictor.schema.json`, que se regenera con `export_config_schema` sin `acceptance.report`;
  - AGENTS.md, en lo que dice de:
    - los lectores y los veredictos en el dominio;
    - las variantes de resultado y `consequence`;
    - el journal `v2` sin `control_durations`;
    - el comando en cada log;
    - la desaparición de `acceptance.report`;
    - cómo se trata un informe ausente, no válido o cortado por tiempo en la aceptación;
  - el CHANGELOG, con los diez cambios del punto 4.

## Alternativas descartadas
- **Separar solo los lectores de puntuación (cobertura y mutación), con dos puertos.** Deja el veredicto de JUnit y de código de salida en los adaptadores, con el mismo problema: una regla fuera del dominio, sin mutar.
- **Un puerto con un mapa de formato a lector, y el formato como `str`.** Deja representable pedir un formato sin lector, o recibir un hecho de otro formato, y obliga a una rama de error interno.
- **`Executed` con casos y puntuación como campos opcionales.** Repetiría dentro del tipo el registro de opcionales que este ADR quita.
- **`Verdict(ok, error)`.** Admite un `ok` con error y un fallo sin motivo.
- **Un `ok` en la unión de resultados.** Se puede tomar un resultado sin veredicto por un fallo, y dejar pasar un `measure` que manipuló el estado.
- **Un `Executed` con `ok=False` para un resultado con tampering.** Inventa un veredicto que nadie dio.
- **La aceptación como un control más.** Le da un `mode` y un `ok` que no tiene, y el journal del run registra como fallida una aceptación que pasó.
- **Un `Reported` que construye cada caso de uso.** Se podría fiar de un informe sin pasar por `trust`.
- **Cambiar la forma del journal sin cambiar la versión del evento.** Obliga a adivinar la versión por las claves que faltan, con una regla distinta en cada lector.
- **Copiar los casos de test en el journal.** Ya están en la evidencia, con su hash.
- **Conservar lo que hoy hace el código en las filas del punto 4, por «no cambiar nada».** Son defectos: falsos verdes de puntuación, un informe ausente que pasa, excepciones sin capturar y un oráculo con tres fuentes. Prometer «lo mismo que hoy» en esas filas sería prometer los defectos.
- **Leer los JSON con `errors="replace"`, o aceptar un BOM en ellos.** Puntúa un informe corrupto como si fuera válido, o acepta lo que un JSON no puede tener (RFC 8259).
- **Conservar `acceptance.report` como `Literal["junit"]`.** Un campo de un solo valor no decide nada, y sin usuarios no hay nada que conservar.
