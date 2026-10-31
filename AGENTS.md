# AGENTS.md

Guidance and operational constraints for AI agents working on **Lictor**.

## Project Overview

Lictor is a deterministic harness and agentic runtime that implements Gherkin acceptance scenarios using an autonomous coding agent, systematically verifying each implementation through strict multi-level quality gates (L1–L5).

- **Language / Runtime:** Python >= 3.12 (managed via Astral `uv`)
- **Core Architecture:** Hexagonal Architecture (Ports and Adapters) enforced with `import-linter`
- **Primary Dependencies:** LangGraph, Pydantic v2, Typer, `gherkin-official`, `defusedxml`

> **Known limitation:** a green commit doesn't yet withstand an adversarial agent. The verified tree, the commit and the state share the agent's working tree, process and environment. This stays true until phase 4 of the correction plan closes (`.audits/analisis-base/informe.md`).

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

# Same, without the slow audit reproduction cases
uv run pytest tests/integration -q -m "not repro"

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

### Running a Subset
```bash
uv run pytest tests/unit/test_routing.py::test_route_load_spec_seal_ok -q   # Single test
uv run pytest tests/unit/test_routing.py -k verify -q                       # By keyword
uv run lictor check --level L1,L2                                           # Gates from lictor.json
```

`lictor check` runs the controls in `lictor.json` plus the acceptance suite. The `coverage-domain` control reads the `.coverage` file left by the `coverage` control, so it depends on that one running first.

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

## How a Run Works

`lictor run` drives an 11-node LangGraph graph (`orchestration/graph.py`), and each scenario moves through `pick_next → implement → verify → commit | quarantine`. The pieces that interact:

- **Routing is split.** Most conditional edges are pure functions in `domain/routing.py`, tested without a graph. A few trivial ones are closures in `create_graph`. Graph state is a flat serializable `TypedDict`; nodes convert to and from domain types at the boundary (ADR-0003).
- **Git is the source of truth for green scenarios.** `CheckRed` derives them from `Scenario:` commit trailers, not from the checkpoint. `CommitGreen` and the driver seal commit check HEAD trailers (`Scenario`, `Lictor-Run`) so that re-running a node after a crash doesn't commit twice. Changing commit messages or trailers can break resume.
- **Every implement attempt starts from HEAD.** `ImplementScenario` verifies the seal, then calls `vcs.reset_to_head()`, so a retry never inherits the previous attempt's edits; only the condensed error carries over. The order matters: a tampered seal must abort without wiping the tree.
- **`reset_to_head` must not remove ignored files** (`git clean -fd`, never `-x`), because `.lictor/` is git-ignored and holds the run's state (ADR-0001).
- **The seal changes who may write what.** `features/` and `paths.driver` are hashed into `.lictor/seal.json` after the red check passes on a fresh driver. Before sealing, the driver role may write them. After, any change fails `L0-sello`. On resume, a changed `lictor.json` or seal exits with code 10.
- **Untrusted commands are wrapped in `IntegritySnapshot`** (`verify_attempt.py`). Agent calls and every control or acceptance command run between `take` and `verify_and_restore`, which restores `.lictor/` state files and `lictor.json` on tampering. New code that runs agent or project commands needs the same wrapper.
- **L0 gates are not controls.** `L0-rutas`, `L0-sello`, `L0-supresiones` and `L0-estado` are hardcoded in `VerifyAttempt`. Levels L1–L5 are data in `lictor.json`, merged over the profile defaults in `adapters/config/profiles/`. `mode: measure` records a failure without blocking.
- **Scenarios map to tests by name.** `map_test_cases_to_scenarios` normalizes `IVA-001` to `iva_001` and requires exactly one match per scenario in the JUnit test name or classname. Otherwise it raises `DriverError`.
- **Pausing is an interrupt.** On a rate limit, `pause` calls LangGraph `interrupt()` and `resume_to` records the node to return to. That attempt isn't counted. The CLI exits 50, and the next `lictor run` resumes from `.lictor/current_run` via `Command(resume=True)`. Exit codes are stable public API (`ExitCode` in `domain/errors.py`).

---

## Constraints Enforced by Tests

These are easy to trip:

- **No profile tool names in `src/lictor` outside `adapters/config/profiles/`.** `test_no_profile_tool_names_outside_profiles_dir` fails on any standalone `ruff`, `mypy`, `pytest`, `pytest-bdd` or `uv` in source, including docstrings, comments and prompt templates. Toolchain specifics belong in profiles.
- **The domain can't import I/O modules** (`subprocess`, `sqlite3`, `shutil`, `os`, `socket`). `test_domain_has_no_io_imports` checks this separately from `.importlinter`.
- **`schema/lictor.schema.json` must equal `LictorConfig.model_json_schema()`.** After changing `adapters/config/models.py`, regenerate it with `export_config_schema` (`adapters/config/loader.py`).
- **Mutation testing only covers the domain, using three test files** (`test_rules.py`, `test_routing.py`, `test_model.py`, listed in `[tool.mutmut]`). Tests for new domain modules must be added there to count.

---

## Testing Without an LLM

Set `backend.kind` to `"fake"` in `lictor.json` to use `FakeExecutor` (`adapters/agents/fake.py`). Its modes are `good`, `fail`, `cheat`, `conflict`, `rate_limit` and `crash`, and they can be set per scenario. `bootstrap_runtime` reads its options from `.lictor/fake_executor.json`, or accepts `executor=` directly. `tests/integration/test_e2e_demo_iva.py` runs full cycles against a copy of `examples/demo-iva/`.

For behaviour the modes can't express, pass `FakeExecutor(effect=fn)`: `fn(task, scenario_id, attempt)` runs arbitrary code in the agent's turn (forged bytecode, git commands, killing the run) and may return a status. `effect` can't go in `fake_executor.json`, so it needs `executor=`.

## Audit Reproduction Cases

`tests/integration/repro/` holds the cases from `.audits/analisis-base/informe.md`, one file per correction phase. Unlike the other e2e tests, they use the real `python-uv` profile and `uv`, with the configuration from `lictor init`, so they are the model for new end-to-end tests. They take about 3 minutes; add `-m "not repro"` to skip them while iterating.

- A case asserts the desired behaviour and stays under `@pending(phase, finding)`, a strict `xfail` that only tolerates `AssertionError`. Closing a case means removing its marker.
- Preconditions go through `require`, which raises `ReproSetupError`, so a broken set-up fails the test instead of counting as the expected failure.
- `false_greens` re-checks every `Scenario:` commit in a clean clone with an oracle that uses neither pytest nor the project venv. Keep it that way: cases `c3`, `c4` and `c6` fool anything that runs inside the suite.
- Agent effects must tolerate a missing `.git` or `.lictor/`, which phases 3 and 4 take away from the agent.

`.audits/analisis-base/repro/` keeps the original lab scripts and `.audits/analisis-base/seguimiento.md` tracks each phase.

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
- **Language:** User-facing strings, error messages, event data, commit subjects and ADRs are in Spanish; identifiers and docstrings are mostly in English. Commits use Conventional Commits with Spanish descriptions. Design documents are in `references/` and `docs/adr/`.

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

