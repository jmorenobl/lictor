Para proyectos Python gestionados con `uv` (`lictor init` lo detecta por `pyproject.toml`).

- **Todas las dependencias antes del run**, de ejecución y de desarrollo: el agente no puede tocar
  `pyproject.toml` ni `uv.lock`. Como mínimo, `uv add --dev pytest pytest-bdd ruff mypy`. Pregunta
  si hará falta algo más (una librería, `pytest-cov` si quiere medir cobertura) y añádelo ahora.
- **El proyecto no se instala en el entorno de verificación**, y `src/` está en el path de
  importación detrás de las dependencias. Su metadata sí está, dentro de pytest, si es un paquete
  (con `[build-system]`): `version()` y sus entry points funcionan, salvo con una versión que sale
  de git. Una CLI se prueba en el proceso: el
  driver llama a `main(argv)` y lee la salida con `capsys`, nunca lanza el ejecutable. Eso va en
  `hints`.
- **Un mínimo de cobertura se escribe como `min_score`, nunca como `fail_under`**: `{"id":
  "coverage", "mode": "block", "min_score": 80}`. Y nada de `--cov` en el `run` de `unit`: los
  argumentos de cobertura los pone `coverage`, y dos veces chocarían.
- **Adoptar un proyecto con código:** `lictor doctor` dice qué dependencia falta (✗) y «lo que los
  controles no ven» (⚠): tests sin control, ficheros que cargan los tests y el agente no puede
  cambiar, código que nadie lee. Repásalo con la persona aviso por aviso (detalle, abajo).
- **Código que ya cumple escenarios:** Lictor lo prueba alterando lo que devuelve la interfaz desde
  el Cuando y el Entonces: si una aserción del Entonces lo nota, el escenario sale en verde sin
  llamar al agente. Lo que no puede alterar (un servicio, un `Flag` combinado) lo dice al
  rechazarlo: para eso, `lictor register`. El nivel de prueba es «resultado probado».
- **Cuándo corre un control añadido:** una mutación o una auditoría de dependencias, con
  `"stage": "check"`; uno de secretos (`gitleaks`), en cada intento.

<!-- lictor: detalle -->

**Preparar:**

1. `uv init --lib <nombre>`. Ya crea el repositorio git, con la rama por defecto de la persona:
   para llamarla `main`, `git branch -m main`; un `git init` después no hace nada.
2. Las dependencias, todas ahora (lo esencial, arriba).
3. Lo que exija `pyproject.toml` se fija ahora también: el `[project.scripts]` de una CLI, por
   ejemplo. El módulo y la función que fijes ahí van luego en `hints`.
4. `uv init` deja un `hello()` de ejemplo en el paquete y un `requires-python` con la versión de la
   persona. `lictor init` toma de ahí la versión de Python de `hints`; el resto de `hints` sí hay
   que ajustarlo al proyecto.

**Cómo se prueba una CLI:**

- Decide con la persona si `main` devuelve el código de salida o termina el proceso, y escríbelo en
  `hints`. Que `argparse` termina con `SystemExit` también en `--help` y en sus propios errores es
  un hueco que preguntar.
- Un ejemplo de `hints` para una CLI: «Python 3.14, gestor uv, tests con pytest. El código va en
  src/romanos/. La CLI es `romanos.cli:main(argv: list[str]) -> int`, declarada en
  `[project.scripts]`; los tests la llaman en el proceso y leen stdout y stderr con `capsys`; el
  proyecto no está instalado, así que no se lanza el ejecutable.»

**Adoptar un proyecto con código.** Antes del primer run, `lictor doctor`, y cada aviso de «Lo que
los controles no ven» se decide con la persona. Arreglarlo es del proyecto, en `lictor.json`; Lictor
solo lo ve:

- **Dependencias (✗).** Si falta `pytest-bdd` o una herramienta de un control (`ruff`, `mypy`), se
  añade con `uv add --dev` y se confirman `pyproject.toml` y `uv.lock`. Un programa que solo está en
  la máquina tampoco vale: se usaría otro intérprete, sin las dependencias.
- **La metadata del proyecto (⚠ en «Dependencias»).** Lictor lee de `pyproject.toml`, sin
  ejecutar nada, el nombre, la versión y los entry points, y los registra dentro de pytest como si
  el proyecto estuviera instalado. Un proyecto sin `[build-system]` no es un paquete: `uv` no lo
  instala y no tiene metadata, ni en la verificación ni fuera, así que `doctor` lo dice con ✓ y no
  hay nada que arreglar. Si `doctor` avisa (⚠), el código que lee su versión (`version()`) o recorre
  un grupo propio fallará o no encontrará nada en la verificación. Se arregla en `pyproject.toml`,
  con una versión estática o leída de un fichero (`tool.hatch.version.path`,
  `tool.setuptools.dynamic.version`). Una versión de git (`hatch-vcs`, `setuptools-scm`) no se
  registra. Si el código no lee su metadata, el aviso se puede dejar. Un `python -c` que lance un
  test tampoco la ve, igual que no ve `src/`.
- **Tests que ningún control ejecuta.** Si deben contar, se amplía el `run` de `unit` por `id` y se
  añade su directorio a `paths.writable`. Por ejemplo, para todos los tests sin la marca
  `integration`: `{"id": "unit", "run": "uv run pytest tests -m \"not integration\"
  --ignore=tests/acceptance -q --junitxml {out}", "when_exists": "tests"}`, y el `agent_run` de
  `coverage` con los mismos argumentos: sin él, `lictor.json` no carga. Lo que `-m` deja fuera no
  se ve.
- **Otro `run` de `lint` o de `format`.** El agente comprueba con el `run` de cada control que
  bloquea, así que basta con cambiarlo. `format` además le da la orden de formatear
  (`agent_fix`): quien cambia su `run` escribe también su `agent_fix` con los mismos ficheros, o
  `lictor.json` no carga.
- **Tests que el run ejecuta y el agente no puede cambiar.** Su directorio va a `paths.writable`, o
  se sacan del `run`: si no, cambiar la API que usan para el run a la espera de una persona.
- **Ficheros que cargan los tests e importan el código** (un `conftest.py`, o un módulo que importan
  los tests). El agente nunca puede tocar un `conftest.py`. Si los escenarios van a cambiar lo que
  importa (el aviso dice qué nombres), se mueve la fixture o el auxiliar junto a los tests que lo
  usan, en una ruta escribible.
- **Código que usa el proyecto y ningún control lee** (`examples/`, `scripts/`). Se añade su
  directorio al `run` de `lint` y `types`, o se acepta.
- **Lo que la persona acepta** va en `lictor.json`: `"blind_spots": {"accepted":
  ["tests/integration/"]}`. Deja de repetirse y solo se cuenta. Luego, `lictor register` de lo que el
  código ya cumple, antes del primer run.

**Controles del perfil:** `lint` (ruff), `format` (`ruff format --check`, todo el proyecto según la configuración de ruff), `types` (mypy) y
`unit` (pytest de `tests/unit`), y la aceptación (pytest de `tests/acceptance`). Además, `coverage` mide la cobertura de `src` en modo
`measure`: no bloquea nada, y su porcentaje sale en «Puntuaciones» de `report.md` y en
`lictor check`. No ejecuta nada: lee la ejecución de `unit`, que recibe al final de su comando
`--cov=src --cov-branch --cov-fail-under=0 --cov-report=json:{out}`, así que los unitarios corren
una sola vez. Solo mide si el proyecto tiene `pytest-cov`; sin él, `unit` corre tal cual y cada run
muestra `coverage` como omitido, no como fallido. Si la persona quiere medir cobertura, o un
mínimo, que lo añada antes del run con `uv add --dev pytest-cov`.

**Plugins de pytest:** cada plugin instalado en el grupo por defecto carga en todas las órdenes de
pytest de la verificación, `unit` y la aceptación incluidas (no hay `PYTEST_ADDOPTS` que lo
evite). Uno que cambia el orden o la semilla (`pytest-randomly`) va en un grupo que no se instala
por defecto (`[dependency-groups]` aparte, `uv sync --group <grupo>` en la CI); `doctor` avisa de
los que conoce. Un `timeout` de pytest-timeout no corta la aceptación: la corta Lictor.

**Una herramienta que importa el proyecto fuera de pytest** (import-linter): el proyecto no está
instalado, así que se lanza con el lanzador del perfil, que pone `src` al final del camino:
`"run": "uv run python -I -B -m lictor_with_src lint-imports --config pyproject.toml"` y
`"agent_run": "uv run python -I -B {lictor_files}/lictor_with_src.py lint-imports --config
pyproject.toml"`. No uses `uv run --directory src`: mete `src` delante de las dependencias.

**Tests que vigilan al agente** (reglas de arquitectura como tests): en una ruta de la persona,
`tests/architecture/` en `paths.protected`, con su control en cada intento (`"stage":
"attempt"`, `"mode": "block"`), y el aviso de «Lo que los controles no ven» aceptado en
`blind_spots.accepted`: el agente no puede relajarlos.

**Cobertura con pytest:**

- **Por qué `min_score` y no `fail_under`.** El perfil pasa `--cov-fail-under=0`, así que un
  `fail_under` de `[tool.coverage.report]` no se aplica: si se aplicara, haría fallar `unit` sin
  ningún test en rojo.
- **Otro `run` de `unit`.** Si la persona lo cambia (otro directorio, `-n auto`), `coverage` lee
  esa ejecución igual, y tiene que seguir siendo de pytest. Se escribe también el `agent_run` de
  `coverage` con los mismos argumentos, o `null`: si no, `lictor.json` no carga.
- **Para medir otra cosa, el `adds` completo.** Se escribe entero, y lo que no se escriba no se
  añade: `{"id": "coverage", "adds": "--cov=src/mipaquete --cov-branch --cov-fail-under=0
  --cov-report=json:{out}"}`. Quitar `--cov-fail-under=0` vuelve a aplicar el `fail_under` del
  proyecto.
  El agente mide la cobertura con el `agent_run` de `coverage`, que usa el módulo del perfil
  `lictor_coverage.py`: cuenta lo que queda sin cubrir como Lictor y, en un refactor, dice dónde
  sube frente al verde. Si se cambia `adds`, se cambia también `agent_run`, o no carga (por ejemplo,
  `"uv run python -I -B {lictor_files}/lictor_coverage.py tests/unit -q --cov=src/mipaquete"`), para
  que mida lo mismo.
- **Medir solo en `lictor check`:** `{"id": "coverage", "stage": "check"}`. El run ejecuta `unit`
  tal cual y `check` con los argumentos de cobertura, así que un test que solo falla bajo
  cobertura pasa en el run y falla en `check`, en la línea de `unit`, que lo marca. Al revés no
  vale: `unit` en `check` con `coverage` en cada intento es un error, porque en el run no habría
  qué leer; van los dos en `check`, o se quita `coverage`.
- **Un `coverage` con su propio comando:** se le escribe `run`, con todo lo que esa ejecución
  necesita (`--cov=src --cov-branch --cov-report=json:{out}`, y `empty_exit_codes`, `timeout_s`
  o `when_exists` si hacen falta, porque no los hereda de nadie), y ejecuta los tests otra vez.
  Conserva del perfil `mode: "measure"` y `when_plugin: "pytest_cov.plugin"`: si mide con otra
  herramienta, escribe también `"when_plugin": null`, o se saltará por un plugin que no usa. Solo
  tiene sentido si mide otra suite que `unit`.

**Cuándo corre cada control:** los del perfil (`lint`, `format`, `types`, `unit` y `coverage`) van
en cada intento. Los que bloquean tienen que pasar en HEAD antes del primer run: si `doctor` dice
«Los controles pasan en HEAD» con ✗, es del proyecto (un proyecto sin formatear se formatea en un
commit de la persona, o se desactiva `format`). Si la persona añade una mutación (por ejemplo, con `mutmut`) o una auditoría de
dependencias (`pip-audit`), propón `"stage": "check"`: la mutación es demasiado lenta para cada
intento, y el agente no puede cambiar las dependencias que se auditan. Por ejemplo, con
`pip-audit` añadido antes del run con `uv add --dev pip-audit`:
`{"id": "audit", "level": "L4", "run": "uv run pip-audit", "report": "exit-code", "stage": "check"}`.
Si un control que añadió ya está en `lictor.json`, basta con añadirle `"stage": "check"`.
Un control de secretos (`gitleaks`) va en cada intento: el agente puede escribir un secreto en
`src/`, y es rápido.
