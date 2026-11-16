Para proyectos Python gestionados con `uv` (`lictor init` lo detecta por `pyproject.toml`).

**Preparar:**

1. `uv init --lib <nombre>`. Ya crea el repositorio git, con la rama por defecto de la persona:
   para llamarla `main`, `git branch -m main`; un `git init` después no hace nada.
2. **Todas las dependencias antes del run**, de ejecución y de desarrollo: el agente no puede tocar
   `pyproject.toml` ni `uv.lock`. Como mínimo, `uv add --dev pytest pytest-bdd ruff mypy`. Pregunta
   si hará falta algo más (una librería, `pytest-cov` si quiere medir cobertura) y añádelo ahora.
3. Lo que exija `pyproject.toml` se fija ahora también: el `[project.scripts]` de una CLI, por
   ejemplo. El módulo y la función que fijes ahí van luego en `hints`.
4. `uv init` deja un `hello()` de ejemplo en el paquete y un `requires-python` con la versión de la
   persona. `lictor init` toma de ahí la versión de Python de `hints`; el resto de `hints` sí hay
   que ajustarlo al proyecto.

**Cómo se prueba:** el proyecto no se instala en el entorno de verificación, y `src/` está en el
path de importación detrás de las dependencias. Por eso:

- Una CLI se prueba en el proceso: el driver llama a `main(argv)` y lee la salida con `capsys`;
  nunca lanza el ejecutable. Decide con la persona si `main` devuelve el código de salida o termina
  el proceso, y escríbelo en `hints`. Que `argparse` termina con `SystemExit` también en `--help` y
  en sus propios errores es un hueco que preguntar.
- Un ejemplo de `hints` para una CLI: «Python 3.14, gestor uv, tests con pytest. El código va en
  src/romanos/. La CLI es `romanos.cli:main(argv: list[str]) -> int`, declarada en
  `[project.scripts]`; los tests la llaman en el proceso y leen stdout y stderr con `capsys`; el
  proyecto no está instalado, así que no se lanza el ejecutable.»

**Controles del perfil:** `lint` (ruff), `types` (mypy) y `unit` (pytest de `tests/unit`), y la
aceptación (pytest de `tests/acceptance`). Además, `coverage` mide la cobertura de `src` en modo
`measure`: no bloquea nada, y su porcentaje sale en «Puntuaciones» de `report.md` y en
`lictor check`. No ejecuta nada: lee la ejecución de `unit`, que recibe al final de su comando
`--cov=src --cov-branch --cov-fail-under=0 --cov-report=json:{out}`, así que los unitarios corren
una sola vez. Solo mide si el proyecto tiene `pytest-cov`; sin él, `unit` corre tal cual y cada run
muestra `coverage` como omitido, no como fallido. Si la persona quiere medir cobertura, o un
mínimo, que lo añada antes del run con `uv add --dev pytest-cov`.

**Cobertura con pytest:**

- **El mínimo es `min_score`, nunca `fail_under`.** El perfil pasa `--cov-fail-under=0`, así que un
  `fail_under` de `[tool.coverage.report]` no se aplica: si se aplicara, haría fallar `unit` sin
  ningún test en rojo. `{"id": "coverage", "mode": "block", "min_score": 80}`.
- **Nada de `--cov` en el `run` de `unit`.** Los argumentos de cobertura los pone `coverage`; dos
  veces chocarían. Si la persona cambia el `run` de `unit` (otro directorio, `-n auto`), `coverage`
  lee esa ejecución igual, y tiene que seguir siendo de pytest.
- **Para medir otra cosa, el `adds` completo.** Se escribe entero, y lo que no se escriba no se
  añade: `{"id": "coverage", "adds": "--cov=src/mipaquete --cov-branch --cov-fail-under=0
  --cov-report=json:{out}"}`. Quitar `--cov-fail-under=0` vuelve a aplicar el `fail_under` del
  proyecto.
- **Medir solo en `lictor check`:** `{"id": "coverage", "stage": "check"}`. El run ejecuta `unit`
  tal cual y `check` con los argumentos de cobertura, así que un test que solo falla bajo
  cobertura pasa en el run y falla en `check`, en la línea de `unit`, que lo marca. Al revés no
  vale: `unit` en `check` con `coverage` en cada intento es un error, porque en el run no habría
  qué leer; van los dos en `check`, o se quita `coverage`.
- **Un `coverage` con su propio comando:** se le escribe `run`, con todo lo que esa ejecución
  necesita (`--cov=src --cov-branch --cov-report=json:{out}`, y `empty_exit_codes` o
  `timeout_s` si hacen falta), y ejecuta los tests otra vez. Solo tiene sentido si mide otra
  suite que `unit`.

**Cuándo corre cada control:** los cuatro del perfil (`lint`, `types`, `unit` y `coverage`) van
en cada intento. Si la persona añade una mutación (por ejemplo, con `mutmut`) o una auditoría de
dependencias (`pip-audit`), propón `"stage": "check"`: la mutación es demasiado lenta para cada
intento, y el agente no puede cambiar las dependencias que se auditan. Por ejemplo, con
`pip-audit` añadido antes del run con `uv add --dev pip-audit`:
`{"id": "audit", "level": "L4", "run": "uv run pip-audit", "report": "exit-code", "stage": "check"}`.
Si un control que añadió ya está en `lictor.json`, basta con añadirle `"stage": "check"`.
Un control de secretos (`gitleaks`) va en cada intento: el agente puede escribir un secreto en
`src/`, y es rápido.
