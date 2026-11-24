# ADR-0015: El contrato de arranque: `doctor` ve lo que falta y lo que los controles no ven

## Estado
Aceptada (2026-11-23). Sale de F66, F67 y F70 (`.audits/romanos/fricciones.md`), vistas al adoptar
`pyverifactu` en la fase 4a. Es la pieza 4.1 de `.audits/qa/propuesta-proyecto-real.md`. Se midió
antes de diseñarla (`.audits/qa/contrato-arranque/medicion.md`). La revisó por su cuenta un revisor
independiente, que ejecutó las medidas y probó casos propios
(`.audits/qa/contrato-arranque/revision-fable.md`). Esta versión recoge sus hallazgos, comprobados
ejecutando.

## Contexto
Al adoptar un proyecto que ya tiene código, Lictor no avisa de lo que va a romper el run, o el
proyecto. En `pyverifactu` las tres cosas las vio la revisión de Fable, no Lictor; `doctor` dio
todo ✓ salvo «no hay escenarios»:

- **F66.** No había `pytest-bdd`. El driver no podía escribir un solo test, y añadirlo toca
  `pyproject.toml` y `uv.lock`, que solo cambia una persona. El run habría gastado la llamada del
  driver para parar en `L0-rutas` o en el rojo.
- **F67.** `tests/conftest.py`, que el agente no puede tocar, importaba `BlockchainChain`, justo lo
  que la tarea rehacía. Lo cargan `unit` y la aceptación: renombrar la clase tumba los dos, y el
  run acaba en «solo una persona» (exit 10).
- **F70.** `unit` solo corre `tests/unit`. Otros 146 tests quedaban fuera de todo control y de
  `paths.writable`, 37 de ellos offline (33 en `tests/integration/` y `tests/examples/`, 4 en la
  raíz de `tests/`; la fricción decía 53). La tanda 2 rompió 7 sin que nada lo viera. `types` solo
  mira `src`, y un ejemplo ya fallaba en mypy.

La medición y la revisión añadieron:
- **El hueco de F66 también existe para los programas.** `doctor` busca la primera palabra de cada
  comando (`uv`). Un `ruff` que falte en el lock pasa `doctor` y hace fallar `lint` en cada intento
  (`uv run ruff`: `Failed to spawn`, salida 2). Y un `pytest` que falte es peor: `uv run pytest`
  encuentra `/usr/bin/pytest` en el `PATH` y sale 0, con otro intérprete y sin las dependencias.
- **Con el arreglo que se hizo a mano aún quedaban cosas.** `tests/test_async_*.py`, que `unit` ya
  ejecutaba, no estaban en `paths.writable`; `examples/` pasó a ser escribible, pero ningún control
  lo leía. Y `tests/fixtures/sample_invoices.py` importa el código, lo importan tres tests de
  `tests/unit` y el agente no puede tocarlo: es F67 por otro camino.
- **Leer la línea de órdenes no basta.** `pytest.ini` gana a `pyproject.toml`, `addopts` añade
  `--ignore` y `-m`, `[tool.pytest]` es el formato nativo de pytest 9, y un control puede ser
  `make test` o `tox`. Con dudas que unas veces dan un aviso falso y otras callan, el análisis no
  sirve: tiene que poder decir «no se sabe».
- **Hay que saltar los subproyectos.** En Lictor, `examples/prestamos-biblioteca` tiene su
  `pyproject.toml`, y sus tests no son de Lictor.

## Decisión

### 1. Qué comprueba `doctor`, con qué gravedad

Dos comprobaciones nuevas, después de «Entorno de verificación» y con el mismo entorno:

| Comprobación | Qué mira | Gravedad | Salida |
|:--|:--|:--|:--|
| **Dependencias de la verificación** | los plugins que el perfil exige a la aceptación; y el programa de cada comando que lanza el entorno, que tiene que estar en el entorno, no en la máquina | ✗ del proyecto (⚠ si el control es solo de `check`) | 10 |
| **Lo que los controles no ven** | ver la tabla del punto 3 | ⚠ | no cambia |

- **No hay ningún código de salida nuevo.** Una dependencia que falta es del proyecto
  (`pyproject.toml` y el lock): es `config`, y `precondition_exit_code` ya da 10. Lo que cambia es
  que `doctor` y `run` dan 10 en un caso en que antes `doctor` daba 0 y el run fallaba más tarde.
  Va en el CHANGELOG.
- **Un programa fuera del entorno es ✗.** Un comando que se lanza con el entorno (`uv run X`) es
  para ejecutar lo que fija el lock. Si `X` solo está en la máquina, el lock no lo fija: con
  `pytest`, los tests corren con otro intérprete. Un proyecto que quiera una herramienta de la
  máquina escribe el `run` sin el lanzador, y entonces se busca como hoy. Un control `check` da ⚠,
  como ya hace `CHECK_STAGE_ONLY`.
- **Lo que no ven los controles nunca es ✗.** Puede ser una decisión: los tests que hablan con la
  AEAT no deben correr en cada intento. Lictor lo ve y lo dice; arreglarlo es del proyecto, en
  `lictor.json` (`paths.writable`, el `run` de cada control por `id`), o moviendo una fixture como en
  `ab6b169`.
- **Sin entorno, ninguna de las dos se comprueba.** Si `doctor` no lo construye (hay un run en
  curso, o `prepare` falla), salen «no comprobado», como hoy el entorno.

### 2. Qué declara el perfil

Dos campos nuevos, solo del perfil (`resolve_defences`, como `driver_hints`): entran en
`config_fingerprint`, y un `lictor.json` que los escriba falla al cargar con un mensaje propio
(«lo declara el perfil»), no con el genérico de `extra="forbid"`.

```json
"acceptance": {
  "needs_plugins": {"pytest_bdd.plugin": "uv add --dev pytest-bdd"}
},
"survey": {"run": "uv run python -I -B -m lictor_survey src --in {in} --out {out}"}
```

- **`needs_plugins`**: cada plugin, con cómo se añade. Lictor lo compara con `installed_plugins`,
  lo que `plugins_command` ya lista tras `prepare` sin importar nada. El mensaje dice qué falta, cómo
  se añade, y que hay que confirmar `pyproject.toml` y el lock porque el agente no puede tocarlos.
- **`survey`**: un comando del perfil que lee los ficheros de HEAD y escribe un informe de hechos.
  Lictor escribe en `{in}` un JSON con los comandos (`id`, `run`, etapa y si se salta por
  `when_exists` de cada control con comando, y la aceptación) y el `detect` del perfil. `{in}` es un
  marcador nuevo, solo de `survey`; el fichero queda de evidencia junto al informe.
- **No hay campo para los programas.** Leer `uv run -q -- X` es saber de `uv`, y eso es del perfil:
  lo resuelve el mismo módulo (punto 3). Lictor solo juzga lo que el informe dice de cada uno.

### 3. Cómo se define «lo que los controles no ven» sin ejecutar nada

**Los hechos los da `python_uv/lictor_survey.py`**, un módulo del perfil en `verification.files`,
como `lictor_complexity.py`: solo stdlib, sin importar ni ejecutar nada del proyecto, válido hasta
Python 3.9 (para leer TOML usa `tomllib` o el `tomli` del entorno; sin ninguno, la configuración en
TOML es «no se sabe»). Corre con `uv run` en un clon limpio de HEAD, así que solo ve lo confirmado, y
con el `PATH` que tendrían los comandos. Sabe lo que es de pytest y de `uv`:

- **Cada comando**, uno de tres: **entendido** (con las rutas que ejecuta o lee), **no se sabe**
  (con el motivo), o **no aplica**. Se entiende un `uv run [opciones] [--] X …`, y un `X` que sea
  pytest (o `python -m pytest`) o cualquier otro programa con argumentos. No se sabe: un `make`, un
  `tox`, un `sh -c`, un `--pyargs`, un `--ignore-glob`, una opción desconocida seguida de un valor
  que no empieza por guion, o una configuración que no se pudo leer.
- **El programa** que lanza cada `uv run`: dónde lo resuelve `shutil.which` con ese `PATH`, y si
  esa ruta está dentro del entorno.
- **La configuración de pytest, como la lee pytest:** el primer fichero de los suyos que la tenga
  (`pytest.ini`, `.pytest.ini`, `pyproject.toml` con `[tool.pytest]` o `[tool.pytest.ini_options]`,
  `tox.ini`, `setup.cfg`), y de él `testpaths`, `python_files` y `addopts`, que se lee con las mismas
  reglas que la línea de órdenes.
- **Lo que ejecuta un comando de pytest:** sus argumentos posicionales que sean rutas, existan o no
  (`tests/acceptance` no existe antes del driver), menos sus `--ignore`; sin ninguno, `testpaths` y,
  sin él, todo. Un `-m`, `-k` o `--deselect`, en la línea o en `addopts`, marca el comando como
  «selecciona».
- **Un fichero de tests** es uno que casa con `python_files` y define algún test (funciones
  `test*`, métodos `test*` de clases `Test*` o de una subclase de `TestCase`), fuera del directorio
  del código. Un `scripts/test_connection.py` sin tests no es de tests.
- **Lo que se carga:** un `conftest.py` lo carga un comando si está en un directorio que contiene lo
  que ejecuta, o dentro de él, y no está bajo un `--ignore`. Y los módulos del repositorio fuera del
  código que importan los tests que ejecuta, siguiendo sus imports (absolutos desde la raíz o desde
  el primer directorio sin `__init__.py`, y relativos), con los nombres importados.
- **El código del proyecto** son los paquetes y módulos de primer nivel del directorio que recibe
  (`src`). Sin ninguno, el informe lo dice, y la comprobación sale «no comprobado», nunca sin
  avisos.
- **Un subdirectorio con un fichero de `detect`** es otro proyecto: no se mira, y el informe lo
  nombra. Un fichero que no se puede analizar también se nombra.

**El informe** lo lee `SurveyReader` (ADR-0009), aparte de los `report` de los controles: un
`survey` no es un control y ningún `lictor.json` puede declarar uno.

**El juicio es del dominio** (`domain/blind_spots.py`), con las reglas de rutas de `L0-rutas`: «el
agente puede cambiarlo» es estar en `paths.writable` sin coincidir con `forbidden_paths`. Un comando
«no se sabe» apaga los avisos que dependen de él (los dos primeros si ejecuta tests, el último si
lee código) y deja una línea: «no se puede saber qué ejecuta el control X: …».

| Aviso | Regla | En `pyverifactu` |
|:--|:--|:--|
| Tests que ningún control ejecuta | un fichero de tests que no ejecuta ningún comando | `tests/integration/` (15), `tests/examples/` (1), `tests/` (2): 146 tests |
| Tests que solo ejecuta `lictor check` | lo ejecuta solo un control `check`: el run no lo ve | — |
| Tests que el run ejecuta y el agente no puede cambiar | lo ejecuta un control `attempt` o la aceptación, y está fuera de `writable` y del driver | tras el arreglo: `tests/test_async_*.py` |
| Ficheros que cargan los tests, importan el código y el agente no puede cambiar | un `conftest.py` o un módulo importado por los tests del run, fuera del driver, que importa el código, con las líneas y los nombres | `tests/conftest.py` (`BlockchainChain`, `AEATSoapClient`, …); `tests/fixtures/sample_invoices.py` |
| Código que usa el proyecto y ningún control lee | importa el código, no lo carga ningún test, y ningún comando lo nombra | `examples/*.py` (4) |

Un comando sin rutas (`gitleaks detect`) no cuenta como «lee el código»: solo uno que nombra una ruta
lee lo que hay debajo. Cada aviso nombra los ficheros, agrupados por directorio, y dice de quién es
el arreglo y dónde va. Si un comando selecciona, sus avisos lo dicen: lo que deselecciona no se ve.

### 4. Lo que la persona acepta, en `lictor.json`

Aceptar un punto ciego es una decisión de la persona, y van en `lictor.json`:

```json
"blind_spots": {"accepted": ["tests/integration/", "examples/"]}
```

Un aviso cuyos ficheros están todos bajo rutas aceptadas no se repite: `doctor` y el run dicen en una
línea cuántos hay aceptados, y `report.md` los lista enteros. Un aviso nuevo en esas rutas, de otro
tipo, sí sale. No entra en `config_fingerprint`: solo lo leen los informes.

### 5. Orden y envoltorio

- **En `CheckPreconditions`**, dentro del mismo `with` del entorno desechable de `doctor` (que se
  borra al salir) o tras el `ensure` de `preflight`: primero el entorno, luego el análisis en un
  clon de HEAD, y con los dos «Dependencias de la verificación» y «Lo que los controles no ven».
  «Comandos de verificación» sigue antes, con la primera palabra.
- **Sin `IntegritySnapshot`.** No ejecuta código del proyecto: `-I -B`, solo stdlib y `tomli`, como
  `plugins_command`. Corre bajo el cerrojo de `doctor` o, en `preflight`, bajo el del checkpoint,
  como `prepare`. La evidencia queda en `.lictor/doctor/` o en `runs/<id>/start/`.
- **Si el análisis falla** (no se escribe el informe, no se lee), las dos comprobaciones dan ⚠ con
  el motivo, y los programas no se juzgan: es un análisis de Lictor, y no puede parar un proyecto
  que está bien. Los plugins no dependen de él.
- **`doctor` juzga la configuración del árbol de trabajo con los ficheros de HEAD.** El run usa las
  dos de HEAD. Con `lictor.json` sin confirmar, `doctor` ya lo avisa (el árbol no está limpio); el
  aviso dirá además que el run usará el confirmado.

### 6. `preflight`

- **Las dependencias, sí.** El entorno ya se construye antes de `load_spec`; comprobarlas ahí para
  con 10 antes de la primera llamada del driver. En una reanudación no: `_check_resume_guard`
  rechaza antes cualquier cambio de HEAD, y el lock no cambia sin cambiar HEAD.
- **Los puntos ciegos, solo en un run nuevo y sin parar.** Salen en stderr (como
  `uncommitted_left_out`), van en `run.started` (`blind_spots`, con los aceptados contados) y
  `report.md` los muestra en «Lo que los controles no ven». Así el verde dice lo que deja fuera
  también a quien no pasó por `doctor`. Cuesta un `uv run` y unos 0,05 s de análisis.

### 7. Lo que dice la skill del perfil al adoptar

`python-uv.skill.md` gana, en lo esencial, **Adoptar un proyecto con código**:

1. Las dependencias, como hoy, y `doctor`, que ahora dice cuál falta.
2. Leer «Lo que los controles no ven» con la persona, aviso por aviso, y decidir:
   - **tests sin control:** ampliar el `run` de `unit` por `id` (`uv run pytest tests -m "not
     integration" --ignore=tests/acceptance -q --junitxml {out}`, con `when_exists: "tests"` y el
     `agent_run` de `coverage` igual) y `paths.writable`; o aceptarlos;
   - **tests que el run ejecuta y el agente no cambia:** añadir su directorio a `paths.writable`, o
     sacarlos del `run`;
   - **un fichero cargado que importa el código:** mover la fixture o el auxiliar junto a sus tests,
     o aceptar que cambiar esa API pare el run a la espera de una persona;
   - **código que nadie lee:** añadir su directorio al `run` de `types` y `lint`, o aceptarlo.
   Lo aceptado va en `blind_spots.accepted`.
3. Registrar lo que el código ya cumple (`lictor register`) antes del primer run.

## Alternativas descartadas

- **Preguntar a pytest (`--collect-only`).** Resolvería casi todas las dudas del punto 3, pero
  ejecuta el código del proyecto (cada `conftest.py`, cada módulo de tests) en `doctor` y en
  `preflight`, antes de que nada esté sellado, y falla justo cuando el código está roto. `doctor` no
  ejecuta hoy código del proyecto, y no debe empezar a hacerlo para mirar.
- **Leer `uv.lock` para F66.** Es formato de `uv` en Lictor, y `plugins_command` ya dice lo
  instalado, que es lo que importa.
- **Un campo `launcher` (prefijo y directorio) para los programas.** Pondría la línea de órdenes de
  `uv` en el dominio (`uv run -q -- ruff` funciona), y la regla «en el entorno o en el `PATH`» daba
  ✓ al `/usr/bin/pytest`. Lo resuelve el módulo, en el entorno.
- **Declarar en el perfil las rutas de cada control.** Un `lictor.json` que cambia el `run` de
  `unit` dejaría la declaración vieja.
- **Solo la skill.** Montar la base puede no ser determinista; comprobar que sirve, sí (4.1).

## Lo que queda fuera
- **Tests que ya hay en `paths.driver` antes del primer driver.** El rojo de un driver nuevo rechaza
  los que no son de ningún escenario (`sin_escenario`), pero saber si los que hay los escribió
  Lictor necesita la historia, no los ficheros. Queda para cuando se vea en una adopción.
- **«Una aceptación que arranca y falla por el motivo correcto»** (4.1): es el rojo del driver
  (ADR-0004, ADR-0011, ADR-0012). Antes del driver no hay aceptación que ejecutar.
- **Saber qué API cambiarán los escenarios.** El aviso de un fichero cargado no distingue la API
  estable de la que se va a rehacer: da los nombres importados para que la persona lo compare.
- **Las marcas de `-m`, los hooks de recogida y `collect_ignore`:** se dice que el comando
  selecciona, o «no se sabe», sin juzgar.

## Consecuencias
- Un proyecto sin `pytest-bdd`, o sin una herramienta de un control en el lock, para en `doctor` y
  en `preflight` con 10, diciendo qué añadir, en vez de gastar el driver o los intentos.
- El verde dice lo que deja fuera: en `doctor`, al empezar un run y en `report.md`.
- Lictor sigue sin nombrar herramientas fuera de `adapters/config/profiles/`: sabe de plugins, de un
  informe de hechos y de sus rutas; lo que es de pytest y de `uv` está en el módulo del perfil.
- Un perfil que no declare `survey` no tiene análisis ni comprobación de programas («no comprobado:
  el perfil no lo declara»), y sigue mirando la primera palabra.
- Lictor sobre sí mismo daría avisos ciertos según su `lictor.json` (su aceptación son los `repro`,
  no un driver). No se ejecuta sobre sí mismo, y su CI no corre `doctor`.

## Cómo se comprueba
- `test_lictor_survey.py`: una tabla de árboles y comandos con el informe que debe salir, con los
  casos de la revisión (`make test`, `tox`, `sh -c`, `pytest.ini` frente a `pyproject.toml`,
  `[tool.pytest]`, `addopts` con `--ignore` y `-m`, `conftest.py` bajo un `--ignore`, un `test_*.py`
  sin tests, layout sin `src`, el auxiliar de `pyverifactu`, un subproyecto, un fichero ilegible), y
  con Python 3.9 en el intérprete del proyecto, como los otros módulos del perfil.
- Unitarios del dominio (`blind_spots`, las dependencias, lo aceptado) y del lector, en
  `[tool.mutmut]`.
- `test_preconditions.py`: las dos comprobaciones, su orden, su gravedad y su «no comprobado».
- Un caso `repro` por cada lado: un proyecto sin `pytest-bdd` (`doctor` ✗ y 10; `run` 10 sin
  ningún `agent.started`) y uno adoptado con `tests/integration/`, un `conftest.py` que importa el
  código y `examples/` (`doctor` ⚠ con los avisos y 0). Y `demo-iva` y `prestamos-biblioteca` sin
  ningún aviso.

## Lo que salió al implementarla
- **Sin `launcher`.** Los programas los resuelve el módulo con el `PATH` de `uv run`, dentro del
  entorno, y el dominio solo juzga si lo que encontró es del entorno. En la copia de `demo-iva` sin
  `ruff` en el lock, `doctor` lo dice, y nombra el de la máquina que se habría usado.
- **Los auxiliares que importan los tests** salen como «Ficheros que cargan los tests»: en
  `pyverifactu`, `tests/fixtures/sample_invoices.py` también después del arreglo a mano.
- **Medido con `doctor` real** (no con el prototipo): `pyverifactu` antes de `ab6b169`, ✗ por
  `pytest_bdd.plugin` y los tres avisos; en su HEAD, ✓ y tres avisos ciertos (dos tests en la raíz
  de `tests/`, `tests/conftest.py` y `tests/fixtures/sample_invoices.py`, `examples/`) y que `unit`
  selecciona con `-m`; las copias de `demo-iva` y `prestamos-biblioteca`, sin avisos.
- **Las funciones nuevas no pasan de complejidad 10** en la medida del propio perfil, y no añaden
  clones.
