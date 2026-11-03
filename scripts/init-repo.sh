#!/usr/bin/env bash
# Inicializa el repositorio de Lictor v0.1 usando las herramientas oficiales
# (uv init, uv add, git init) en lugar de plantillas manuales para los archivos
# que generan ellas, según "references/Lictor v0.1 diseño de la primera iteración.docx".
#
# Uso: ./scripts/init-repo.sh [--no-git]
set -euo pipefail

GIT=1
for arg in "$@"; do
  case "$arg" in
    --no-git) GIT=0 ;;
    *) echo "Opción desconocida: $arg" >&2; exit 2 ;;
  esac
done

if ! command -v uv >/dev/null 2>&1; then
  echo "Error: 'uv' no está instalado (brew install uv o https://docs.astral.sh/uv/)." >&2
  exit 1
fi

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# ---------------------------------------------------------------------------
# 1. Inicializar proyecto e instalar dependencias con uv
# ---------------------------------------------------------------------------
echo "==> 1. Inicializando paquete Python con uv"
if [[ ! -f pyproject.toml ]]; then
  uv init --package --app --name lictor --python 3.12 --vcs git \
    --description "Implementa escenarios Gherkin con un agente y los verifica de forma determinista."
fi

echo "==> 2. Añadiendo dependencias de producción con uv add"
uv add \
  langgraph \
  langgraph-checkpoint-sqlite \
  typer \
  "pydantic>=2" \
  gherkin-official \
  defusedxml

echo "==> 3. Añadiendo dependencias de desarrollo con uv add --dev"
uv add --dev \
  pytest \
  pytest-cov \
  ruff \
  mypy \
  import-linter \
  mutmut \
  types-defusedxml

# Añade a pyproject.toml solo las secciones [tool.*] de las herramientas de calidad
if ! grep -q "^\[tool\.ruff\]" pyproject.toml; then
  cat >>pyproject.toml <<'EOF'

[tool.ruff]
line-length = 100
target-version = "py312"
extend-exclude = ["examples"]

[tool.ruff.lint]
select = ["E", "W", "F", "I", "B", "UP", "SIM", "RUF", "S", "N", "PT", "PL", "C90"]

[tool.ruff.lint.mccabe]
max-complexity = 10

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101", "PLR2004"]
"src/lictor/adapters/**" = ["S603", "PLR0913"]

[tool.mypy]
strict = true
python_version = "3.12"
mypy_path = "src"
packages = ["lictor"]

[[tool.mypy.overrides]]
module = ["gherkin.*"]
ignore_missing_imports = true

[tool.pytest.ini_options]
testpaths = ["tests"]
pythonpath = ["src"]
addopts = "-ra"

[tool.coverage.run]
branch = true
source = ["lictor"]

[tool.mutmut]
paths_to_mutate = ["src/lictor/domain/"]
tests_dir = ["tests/unit/"]
EOF
fi

# Ajusta el entrypoint generado por `uv init --package` (lictor = "lictor:main") a "lictor.cli:app"
sed -i.bak 's|lictor = "lictor:main"|lictor = "lictor.cli:app"|g' pyproject.toml && rm -f pyproject.toml.bak

# Completa el .gitignore generado por uv con el directorio de estado .lictor/ y cachés de herramientas
for entry in ".lictor/" ".pytest_cache/" ".mypy_cache/" ".ruff_cache/" ".coverage" "htmlcov/" "mutants/" "examples/demo-iva/.venv/"; do
  grep -qxF "$entry" .gitignore 2>/dev/null || echo "$entry" >>.gitignore
done

# Tipado PEP 561 y limpieza del __init__.py por defecto de uv init
: > src/lictor/py.typed
: > src/lictor/__init__.py

# ---------------------------------------------------------------------------
# 2. Estructura de paquetes y módulos por capas (arquitectura hexagonal)
# ---------------------------------------------------------------------------
echo "==> 4. Creando estructura de paquetes y módulos vacíos"

mkdir -p \
  src/lictor/domain \
  src/lictor/application/usecases \
  src/lictor/adapters/{agents,vcs,runner,readers,spec,store,config/profiles} \
  src/lictor/orchestration \
  src/lictor/prompts \
  tests/{unit,integration} \
  docs/adr \
  schema

# __init__.py de todos los paquetes
for pkg in \
  src/lictor \
  src/lictor/domain \
  src/lictor/application \
  src/lictor/application/usecases \
  src/lictor/adapters \
  src/lictor/adapters/{agents,vcs,runner,readers,spec,store,config} \
  src/lictor/orchestration \
  tests tests/unit tests/integration; do
  [[ -f "$pkg/__init__.py" ]] || : > "$pkg/__init__.py"
done

# Módulos del diseño
for mod in \
  src/lictor/domain/{errors,model,rules,routing}.py \
  src/lictor/application/ports.py \
  src/lictor/application/usecases/{prepare_driver,check_red,implement_scenario,verify_attempt,commit_green,quarantine,build_report,init_project,check_project}.py \
  src/lictor/adapters/agents/{claude_cli,fake}.py \
  src/lictor/adapters/vcs/git_cli.py \
  src/lictor/adapters/runner/{subprocess_runner,fake}.py \
  src/lictor/adapters/readers/{exit_code,junit}.py \
  src/lictor/adapters/spec/gherkin_source.py \
  src/lictor/adapters/store/{jsonl_eventlog,clock}.py \
  src/lictor/adapters/config/{models,loader}.py \
  src/lictor/orchestration/graph.py \
  src/lictor/bootstrap.py; do
  [[ -f "$mod" ]] || : > "$mod"
done

cat >src/lictor/cli.py <<'EOF'
import typer

app = typer.Typer(no_args_is_help=True)
EOF

# ---------------------------------------------------------------------------
# 3. Archivos propios del diseño de Lictor (prompts, perfil, contratos, lictor.json)
# ---------------------------------------------------------------------------
echo "==> 5. Escribiendo archivos declarativos del diseño (.importlinter, perfil, prompts, lictor.json)"

cat >.importlinter <<'EOF'
[importlinter]
root_package = lictor
include_external_packages = True

[importlinter:contract:capas]
name = Las dependencias apuntan hacia dentro
type = layers
layers =
    lictor.cli | lictor.bootstrap
    lictor.orchestration
    lictor.application
    lictor.domain

[importlinter:contract:dominio-puro]
name = El dominio no conoce frameworks
type = forbidden
source_modules = lictor.domain
forbidden_modules =
    lictor.adapters
    langgraph
    typer
    pydantic
    defusedxml
    gherkin

[importlinter:contract:aplicacion]
name = La aplicación solo ve dominio y puertos
type = forbidden
source_modules = lictor.application
forbidden_modules =
    lictor.adapters
    langgraph
    typer
    pydantic

[importlinter:contract:grafo]
name = El grafo recibe los adaptadores ya construidos
type = forbidden
source_modules = lictor.orchestration
forbidden_modules = lictor.adapters

[importlinter:contract:adaptadores]
name = Los adaptadores no se conocen entre sí
type = independence
modules =
    lictor.adapters.agents
    lictor.adapters.vcs
    lictor.adapters.runner
    lictor.adapters.readers
    lictor.adapters.spec
    lictor.adapters.store
    lictor.adapters.config
EOF

cat >src/lictor/adapters/config/profiles/python-uv.json <<'EOF'
{
  "name": "python-uv",
  "version": "0.1",
  "status": "stable",
  "detect": ["pyproject.toml"],
  "paths": {
    "writable": ["src/", "tests/unit/"],
    "driver": ["tests/acceptance/"],
    "protected": ["features/", "tests/acceptance/", "lictor.json", "pyproject.toml", "uv.lock"]
  },
  "acceptance": {
    "run": "uv run pytest tests/acceptance -q --junitxml {out}",
    "report": "junit",
    "driver_hints": "Usa pytest-bdd. Un test por escenario, enlazado con @scenario y llamado test_<id> (por ejemplo test_iva_001). Crea en src/ la interfaz pública mínima que necesiten los pasos, con funciones que lancen NotImplementedError."
  },
  "controls": [
    {"id": "lint",  "level": "L1", "run": "uv run ruff check src", "report": "exit-code", "mode": "block"},
    {"id": "types", "level": "L1", "run": "uv run mypy src", "report": "exit-code", "mode": "block"},
    {"id": "unit",  "level": "L2", "run": "uv run pytest tests/unit -q --junitxml {out}", "report": "junit", "mode": "block", "empty_exit_codes": [5]}
  ],
  "suppressions": ["# noqa", "# type: ignore", "# pragma: no cover"],
  "agent_tools": ["Bash(uv run pytest:*)", "Bash(uv run ruff:*)", "Bash(uv run mypy:*)"],
  "hints": "Python 3.12, gestor uv, tests con pytest. El código va en src/<paquete>/.",
  "gitignore": ["__pycache__/", "*.pyc", ".venv/", ".pytest_cache/", ".mypy_cache/", ".ruff_cache/"]
}
EOF

cat >lictor.json <<'EOF'
{
  "profile": "python-uv@0.1",
  "controls": [
    {"id": "lint",            "level": "L1", "run": "uv run ruff check .",                "report": "exit-code", "mode": "block"},
    {"id": "format",          "level": "L1", "run": "uv run ruff format --check .",       "report": "exit-code", "mode": "block"},
    {"id": "types",           "level": "L1", "run": "uv run mypy --strict src",           "report": "exit-code", "mode": "block"},
    {"id": "unit",            "level": "L2", "run": "uv run pytest tests/unit -q --junitxml {out}", "report": "junit", "mode": "block"},
    {"id": "architecture",    "level": "L4", "run": "uv run lint-imports",                "report": "exit-code", "mode": "block"},
    {"id": "coverage",        "level": "L4", "run": "uv run pytest tests --cov=lictor --cov-branch --cov-fail-under=90 -q", "report": "exit-code", "mode": "block"},
    {"id": "coverage-domain", "level": "L4", "run": "uv run coverage report --include=*/lictor/domain/* --fail-under=95", "report": "exit-code", "mode": "block"},
    {"id": "audit",           "level": "L4", "run": "uvx pip-audit",                      "report": "exit-code", "mode": "block"},
    {"id": "secrets",         "level": "L4", "run": "gitleaks detect --no-banner",        "report": "exit-code", "mode": "block"},
    {"id": "mutation",        "level": "L5", "run": "uv run mutmut run",                  "report": "exit-code", "mode": "measure"}
  ],
  "acceptance": {"run": "uv run pytest tests/integration -q --junitxml {out}", "report": "junit"}
}
EOF

cat >src/lictor/prompts/implement.md <<'EOF'
Eres el agente de implementación de Lictor. Trabajas en {repo}.

Objetivo: que pase el escenario {id}.

{gherkin_del_escenario}

Reglas:
- Solo puedes crear o modificar archivos en: {writable}.
- No toques {protected}. Cualquier cambio ahí se revierte y cuenta como intento fallido.
- No uses git ni hagas commits. Lictor guarda tu trabajo si pasa la verificación.
- No añadas supresiones: {suppressions}.
- Estos escenarios ya funcionan y deben seguir funcionando: {green_ids}.
- Puedes comprobar tu trabajo con: {check_commands}.

Contexto del proyecto: {hints}

{bloque_de_error}

Si el escenario es imposible o contradice a otro, no lo fuerces. Termina tu respuesta con una línea:
LICTOR: SPEC_CONFLICT <motivo y evidencia>
EOF

cat >src/lictor/prompts/driver.md <<'EOF'
Eres el agente que escribe el driver de aceptación de Lictor. Trabajas en {repo}.

Objetivo: que cada escenario de la spec tenga un test ejecutable que hoy falle,
porque la funcionalidad todavía no existe.

{features}

Reglas:
- Escribe los pasos solo en: {driver_paths}.
- Puedes crear en {writable} la interfaz pública mínima que necesiten los pasos,
  sin lógica: cada función lanza un error de "no implementado".
- No implementes la funcionalidad. No toques {protected_sin_driver}.
- No uses git ni hagas commits.
- Cada test debe llevar el ID de su escenario en el nombre.
- Los escenarios {green_ids} ya funcionan y sus tests deben pasar.

Instrucciones del perfil: {driver_hints}

Contexto del proyecto: {hints}

{bloque_de_error}
EOF

# ---------------------------------------------------------------------------
# 4. Proyecto de prueba demo-iva inicializado también con uv
# ---------------------------------------------------------------------------
echo "==> 6. Inicializando fixture examples/demo-iva con uv"
DEMO="examples/demo-iva"
if [[ ! -f "$DEMO/pyproject.toml" ]]; then
  mkdir -p "$DEMO"
  (
    cd "$DEMO"
    uv init --package --lib --name iva --python 3.12 --vcs none --no-workspace
    uv add --dev pytest pytest-bdd ruff mypy
    mkdir -p features tests/acceptance tests/unit
  )
  cat >"$DEMO/features/iva.feature" <<'EOF'
# language: es
Característica: IVA de una factura

  @IVA-001
  Escenario: IVA general
    Dado una factura con importe 100
    Cuando calculo el IVA general
    Entonces el total es 121

  @IVA-002
  Escenario: IVA reducido
    Dado una factura con importe 100
    Cuando calculo el IVA reducido
    Entonces el total es 110

  @IVA-003
  Escenario: Importe negativo
    Dado una factura con importe -5
    Cuando calculo el IVA general
    Entonces se rechaza por importe no válido
EOF
fi

# ---------------------------------------------------------------------------
# 5. Formato y verificación inicial con las propias herramientas
# ---------------------------------------------------------------------------
echo "==> 7. Formateando y verificando el esqueleto (ruff, mypy, lint-imports)"
uv run ruff format .
uv run ruff check .
uv run mypy --strict src
uv run lint-imports

if [[ $GIT -eq 1 && -z "$(git log --oneline -1 2>/dev/null || true)" ]]; then
  git add -A
  git commit -m "chore: inicializar esqueleto de Lictor v0.1"
fi

echo "==> Listo."
