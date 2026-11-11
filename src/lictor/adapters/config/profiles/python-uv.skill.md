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
aceptación (pytest de `tests/acceptance`). Además, `coverage` mide la cobertura de `src` con los tests
unitarios, en modo `measure`: no bloquea nada, y su porcentaje sale en «Puntuaciones» de
`report.md` y en `lictor check`. Solo corre si el proyecto tiene `pytest-cov`. Sin él, cada run lo
muestra como omitido, no como fallido. Si la persona quiere medir cobertura, o un mínimo, que lo
añada antes del run con `uv add --dev pytest-cov`.
