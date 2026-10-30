# AGENTS.md

Guidance and operational constraints for AI agents working on **Lictor**.

## Project Overview

Lictor is a deterministic harness and agentic runtime that implements Gherkin acceptance scenarios using an autonomous coding agent, systematically verifying each implementation through strict multi-level quality gates (L1–L5).

- **Language / Runtime:** Python >= 3.12 (managed via Astral `uv`)
- **Core Architecture:** Hexagonal Architecture (Ports and Adapters) enforced with `import-linter`
- **Primary Dependencies:** LangGraph, Pydantic v2, Typer, `gherkin-official`, `defusedxml`

---

## Essential Commands

All commands are executed using `uv`. Always run them from the repository root.

### Environment & Dependencies
```bash
uv sync --all-groups       # Install all dependencies including dev tools
```

### Quality Gates & Static Checks (L1 & L4)
```bash
# Code style and formatting (L1)
uv run ruff check .
uv run ruff format --check .
uv run ruff format .       # Automatically format code

# Strict typechecking (L1)
uv run mypy --strict src

# Architectural boundaries check (L4)
uv run lint-imports
```

### Testing & Verification (L2, L4 & L5)
```bash
# Fast unit tests (L2)
uv run pytest tests/unit -q

# Integration & architecture acceptance tests
uv run pytest tests/integration -q

# Full test suite with coverage enforcement (L4 - minimum 90% branch coverage)
uv run pytest tests --cov=lictor --cov-branch --cov-fail-under=90 -q

# Domain purity test coverage (L4 - minimum 95% domain coverage)
uv run coverage report --include="*/lictor/domain/*" --fail-under=95

# Security audits (L4)
uvx pip-audit
gitleaks detect --no-banner

# Mutation testing (L5)
uv run mutmut run
```

---

## Architectural Boundaries (.importlinter)

Lictor strictly enforces hexagonal architecture layers. Dependencies only point inward:

```
[CLI / Bootstrap]
       ↓
[Orchestration (LangGraph)]
       ↓
[Application (Use Cases & Ports)]
       ↓
[Domain (Pure Models, Rules & Routing)]
```

### Layer Contracts & Rules:
1. **Domain Purity (`lictor.domain`):**
   - Zero framework or adapter dependencies.
   - **Forbidden imports:** `lictor.adapters`, `langgraph`, `typer`, `pydantic`, `defusedxml`, `gherkin`.
   - Contains pure Python dataclasses, business rules, routing algorithms, and domain error definitions.
2. **Application Layer (`lictor.application`):**
   - Coordinates domain models and defines ports (`lictor.application.ports.py`) via `typing.Protocol`.
   - **Forbidden imports:** `lictor.adapters`, `langgraph`, `typer`, `pydantic`.
3. **Orchestration Layer (`lictor.orchestration`):**
   - Composes LangGraph state graphs and workflow nodes.
   - Receives adapters already instantiated via dependency injection from `lictor.bootstrap`.
   - **Forbidden imports:** `lictor.adapters`.
4. **Adapter Independence (`lictor.adapters.*`):**
   - Adapters (`agents`, `vcs`, `runner`, `readers`, `spec`, `store`, `config`) implement application ports.
   - Adapters must never import from each other.

---

## Project Structure

```
├── pyproject.toml              # Build config, tools (ruff, mypy, pytest, mutmut)
├── lictor.json                 # Quality gate profiles and control configurations
├── .importlinter               # Architectural contract definitions
├── src/lictor/
│   ├── domain/                 # Pure domain: model.py, rules.py, routing.py, errors.py
│   ├── application/
│   │   ├── ports.py            # Protocols (Executor, EventLog, Clock, Vcs, Runner, etc.)
│   │   └── usecases/           # Application use case interactors
│   ├── adapters/
│   │   ├── agents/             # Agent executors (Claude CLI, Fake, etc.)
│   │   ├── vcs/                # Version control adapters (git CLI)
│   │   ├── runner/             # Subprocess and command runners
│   │   ├── readers/            # Report parsers (exit_code, junit)
│   │   ├── spec/               # Gherkin parser adapters
│   │   ├── store/              # Event store & clock adapters
│   │   └── config/             # Profile loader and Pydantic schemas
│   ├── orchestration/          # LangGraph state machine & workflows
│   ├── prompts/                # Agent system prompt templates
│   ├── bootstrap.py            # Composition root / dependency injection wire-up
│   └── cli.py                  # Typer CLI entrypoint
└── tests/
    ├── unit/                   # Fast isolated unit tests
    └── integration/            # Architecture tests and cross-layer integration tests
```

---

## Code Style & Conventions

- **Python Version:** 3.12 idioms throughout (`X | Y` union types, built-in collections `list[T]`, `dict[K, V]`).
- **Typing:** Strict typing mandatory (`py.typed` is packaged). Every function, method parameter, and return value must have explicit type annotations.
- **Line Length:** 100 characters maximum (configured in Ruff).
- **Suppressions:** Avoid `# noqa`, `# type: ignore`, or `# pragma: no cover`. If an external library has missing stubs, use targeted overrides in `pyproject.toml` rather than inline ignores.
- **Domain Errors:** Raise domain-specific exceptions from `lictor.domain.errors` rather than generic `ValueError` or `RuntimeError`.
- **Event-Driven Auditability:** Significant domain actions must emit typed `Event` objects adhering to CloudEvents specification.

---

## Agent Dos and Don'ts

### Do
- Run `uv run ruff check .`, `uv run mypy --strict src`, and `uv run lint-imports` before completing any change.
- Implement new capabilities starting from the Domain and Ports, then implement Use Cases, and finally attach Adapters.
- Provide comprehensive unit tests in `tests/unit/` for all new domain and application logic.
- Mock or fake external processes (`subprocess`, `git`, external CLI binaries) using corresponding ports in unit tests.

### Don't
- **Never** import `pydantic` or any third-party framework into `src/lictor/domain/`.
- **Never** create cross-dependencies between adapter packages (e.g., `adapters.agents` importing `adapters.vcs`).
- **Never** perform direct git operations (commit, push, checkout) inside agent tasks unless specifically implementing the `Vcs` port.
- **Never** lower coverage thresholds in `pyproject.toml` or `lictor.json` to make tests pass.

