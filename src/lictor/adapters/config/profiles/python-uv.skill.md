Para proyectos Python gestionados con `uv` (`lictor init` lo detecta por `pyproject.toml`).

- **Todas las dependencias antes del run**, de ejecución y de desarrollo: el agente no puede tocar
  `pyproject.toml` ni `uv.lock`. Como mínimo, `uv add --dev pytest pytest-bdd ruff mypy`. Pregunta
  si hará falta algo más (una librería, `pytest-cov` si quiere medir cobertura) y añádelo ahora.
- **El proyecto no se instala en el entorno de verificación**, y `src/` está en el path de
  importación detrás de las dependencias. Una CLI se prueba en el proceso: el driver llama a
  `main(argv)` y lee la salida con `capsys`, nunca lanza el ejecutable. Eso va en `hints`.
- **Un mínimo de cobertura se escribe como `min_score`, nunca como `fail_under`**: `{"id":
  "coverage", "mode": "block", "min_score": 80}`. Y nada de `--cov` en el `run` de `unit`: los
  argumentos de cobertura los pone `coverage`, y dos veces chocarían.
- **Adoptar un proyecto con código:** `lictor doctor` dice qué dependencia falta (✗) y «lo que los
  controles no ven» (⚠): tests sin control, ficheros que cargan los tests y el agente no puede
  cambiar, código que nadie lee. Repásalo con la persona aviso por aviso (detalle, abajo).
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
- **Tests que ningún control ejecuta.** Si deben contar, se amplía el `run` de `unit` por `id` y se
  añade su directorio a `paths.writable`. Por ejemplo, para todos los tests sin la marca
  `integration`: `{"id": "unit", "run": "uv run pytest tests -m \"not integration\"
  --ignore=tests/acceptance -q --junitxml {out}", "when_exists": "tests"}`, y el `agent_run` de
  `coverage` con los mismos argumentos. Lo que `-m` deja fuera no se ve.
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

**Controles del perfil:** `lint` (ruff), `format` (`ruff format --check src`), `types` (mypy) y
`unit` (pytest de `tests/unit`), y la aceptación (pytest de `tests/acceptance`). Además, `coverage` mide la cobertura de `src` en modo
`measure`: no bloquea nada, y su porcentaje sale en «Puntuaciones» de `report.md` y en
`lictor check`. No ejecuta nada: lee la ejecución de `unit`, que recibe al final de su comando
`--cov=src --cov-branch --cov-fail-under=0 --cov-report=json:{out}`, así que los unitarios corren
una sola vez. Solo mide si el proyecto tiene `pytest-cov`; sin él, `unit` corre tal cual y cada run
muestra `coverage` como omitido, no como fallido. Si la persona quiere medir cobertura, o un
mínimo, que lo añada antes del run con `uv add --dev pytest-cov`.

**Cobertura con pytest:**

- **Por qué `min_score` y no `fail_under`.** El perfil pasa `--cov-fail-under=0`, así que un
  `fail_under` de `[tool.coverage.report]` no se aplica: si se aplicara, haría fallar `unit` sin
  ningún test en rojo.
- **Otro `run` de `unit`.** Si la persona lo cambia (otro directorio, `-n auto`), `coverage` lee
  esa ejecución igual, y tiene que seguir siendo de pytest.
- **Para medir otra cosa, el `adds` completo.** Se escribe entero, y lo que no se escriba no se
  añade: `{"id": "coverage", "adds": "--cov=src/mipaquete --cov-branch --cov-fail-under=0
  --cov-report=json:{out}"}`. Quitar `--cov-fail-under=0` vuelve a aplicar el `fail_under` del
  proyecto.
  El agente mide la cobertura con el `agent_run` de `coverage`, que usa el módulo del perfil
  `lictor_coverage.py`: cuenta lo que queda sin cubrir como Lictor y, en un refactor, dice dónde
  sube frente al verde. Si se cambia `adds`, se cambia también `agent_run` (por ejemplo,
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
