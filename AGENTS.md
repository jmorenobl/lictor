# AGENTS.md

Guidance and operational constraints for AI agents working on **Lictor**.

## Project Overview

Lictor is a deterministic harness and agentic runtime that implements Gherkin acceptance scenarios using an autonomous coding agent, systematically verifying each implementation through strict multi-level quality gates (L1–L5).

- **Language / Runtime:** Python >= 3.12 (managed via Astral `uv`)
- **Core Architecture:** Hexagonal Architecture (Ports and Adapters) enforced with `import-linter`
- **Primary Dependencies:** LangGraph, Pydantic v2, Typer, `gherkin-official`, `defusedxml`

> **Known limitations:** a green commit doesn't yet withstand an adversarial agent: see «Known limitations» in [docs/internals/isolation.md](docs/internals/isolation.md).

---

## Essential Commands

All commands are executed using `uv`. Always run them from the repository root.

```bash
uv sync --all-groups                      # Install all dependencies including dev tools
uv run ruff check . && uv run ruff format --check .
uv run mypy --strict src scripts
uv run python scripts/check_architecture.py
uv run pytest tests/unit -q
```

### What to run and when

The CI gives the green. Locally, run what fails fast, once; never the whole of `repro` or `lictor check` without `--level` before a pull request (that took over 45 min, and the CI repeats it).

- **While editing:** the test file you are working on (`uv run pytest tests/unit/test_x.py -q`), `uv run ruff check <files>`, and `uv run mypy --strict src scripts` if a type changed.
- **Closing a change, once** (about 2.5 min):
  ```bash
  uv run lictor check --level L1,L2,L4     # lint, format, types, unit, architecture, coverage without repro, audit, secrets
  uv run pytest tests -n auto -q -m smoke  # the 8 cases with the whole configuration of `lictor init`
  ```
- **Repro:** only the exact case (`uv run pytest <node id>`), to reproduce a failure of the CI. The files go by audit phase, not by area, so there is no «repro of the area».

Every other command (integration, `repro`, toolchains, coverage, mutation, audits, a whole `lictor check`), the CI's layers, its budget, running in parallel and testing without an LLM (`FakeExecutor`) are in [docs/internals/testing.md](docs/internals/testing.md). The audit reproduction cases have their own rules in [docs/internals/repro.md](docs/internals/repro.md): read it before touching `tests/integration/repro/`.

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

`lictor run` drives a 14-node LangGraph graph (`orchestration/graph.py`), and each scenario moves through `pick_next → implement → verify → commit | quarantine`, and after `commit` through `refactor → verify_refactor → commit_refactor` (ADR-0010). The pieces that interact:

- **Routing is split.** Most conditional edges are pure functions in `domain/routing.py`, tested without a graph. A few trivial ones are closures in `create_graph`. Graph state is a flat serializable `TypedDict`; nodes convert to and from domain types at the boundary (ADR-0003).

The detail of each mechanism lives in `docs/internals/`, one file per area. Read the file of the area you are changing before editing it: each point there names the functions, ADRs and findings involved, and the invariants that tests hold. Search them with `grep -rn '<name>' docs/internals` when you only have an identifier.

- [The oracle: driver, red check and proofs](docs/internals/oracle.md): Git is the source of truth for green scenarios, and for what they were green for · The red is judged by class · The profile declares what makes the oracle valid · A new scenario the code already meets is proved, not assumed · The stubs' impostors ask for an assertion of a Then · Without a plugin the driver declares its stubs' constants · The driver's commit passes the controls that block every attempt · Collateral greens skip the agent · Scenarios map to tests by what they executed, or by name · A person can register what Lictor can't prove · A green that leaves `features/` is retired, and its driver says so.
- [The refactor after each green](docs/internals/refactor.md): A refactor follows each implemented green · The refactor sees complexity, and may not raise it · The refactor sees duplication, and may not add to it · The refactor stays in what its scenario changed.
- [Controls, reports and verdicts](docs/internals/controls.md): L0 gates are not controls · `lictor check` verifies what `run` would, and what it leaves for later · A suite that crashed proves nothing · Readers read facts; the domain gives every verdict · A result is a variant, and the journal keeps a projection of it · The agent's commands come from the controls · The public API is compared with the commit the run started from, or where it left `api.base` · A run says what its greens can be trusted for · The check of each control that blocks every attempt is granted to the agent as is · A minimum of coverage with nothing measured waits for the first green.
- [Preconditions and the start contract](docs/internals/start-contract.md): Prerequisites are checked before anything is spent · `doctor` and a new run check the start contract · The controls that block pass on HEAD before the first attempt · A run tells what it changed that no control executed · The start contract reads the CI, and `--cov` as pytest-cov does.
- [Isolation, integrity and resume](docs/internals/isolation.md): A run works in a worktree and a branch of its own · The agent works in a copy, and the project belongs to the user · Temporary directories are private · One run per worktree at a time · What's verified is what's committed · Every implement attempt starts from HEAD · The seal changes who may write what · Untrusted commands are wrapped in `IntegritySnapshot` · Git runs hardened · The control files include the common directory · A resume compares against the anchor · Checkpoints are written synchronously · `.git/config` is guarded by its keys · A run's evidence reaches the checkout at every exit.
- [The verification environment and the profile](docs/internals/verification-env.md): Verification runs in its own environment · Project code never enters the interpreter's startup · Verification starts from an empty environment · Each command has an empty home of its own · `prepare` doesn't share its environment with the commands · The profile owns what verification guarantees, in three classes · `lictor.json` holds what the person decides · `forbidden_paths` and `banned_patterns` are heuristics of the profile · The acceptance is marked, and pytest-timeout doesn't cut it · `lictor_with_src` runs a tool that imports the project outside pytest.
- [The agent, pauses, exits and evidence](docs/internals/agent-and-lifecycle.md): A quarantine may blame the driver, without claiming it · A declared conflict is judged by its attempt, not by its text · A path only a person may change stops the run, not the scenario · Pausing is an interrupt · A backend that answers badly doesn't cost a scenario · Exit 40 means "couldn't run", not "the work failed" · The agent runs without user or project settings · The project keeps a stub of the skill; the content is the installed version's · Every wait for a process is bounded · The prompt gets the scenario as its author wrote it · The project's code rules go in `lictor.json`, never read from its files · Status comes from structured fields, never from the text · The terminal tells each step, from the journal · Every exit explains itself · A run's evidence lives in `.lictor/runs/<run_id>/<owner>/` · What goes to the prompt is bounded; the rest goes to `runs/`.

---

## Constraints Enforced by Tests

These are easy to trip:

- **No profile tool names in `src/lictor` outside `adapters/config/profiles/`.** `test_no_profile_tool_names_outside_profiles_dir` fails on any standalone `ruff`, `mypy`, `pytest`, `pytest-bdd` or `uv` in source, including docstrings, comments and prompt templates. Toolchain specifics belong in profiles.
- **The profile's modules are written for Python 3.9.** They run in the project's interpreter. `test_profile_modules_python39.py` parses each `python_uv/lictor_*.py` with 3.9's grammar, requires `from __future__ import annotations` where annotations would be evaluated, and refuses `isinstance(x, A | B)`; where `uv python find 3.9` finds one without downloading, it also imports each module there. Before 0.17.0 three of them failed to import on 3.9.
- **The domain can't import I/O modules** (`subprocess`, `sqlite3`, `shutil`, `os`, `socket`). `test_domain_has_no_io_imports` checks this separately from `.importlinter`.
- **`schema/lictor.schema.json` must equal `LictorFile.model_json_schema()`**, the file a person writes, not the resolved `LictorConfig`. After changing `adapters/config/models.py`, regenerate it with `export_config_schema` (`adapters/config/loader.py`).
- **Mutation testing covers the domain and the small application modules** listed in `[tool.mutmut]` (`anchor`, `events`, `seal`, `spec_history`, `agent_events`, `commit_green`, `commit_refactor`, `quarantine`), with the test files listed there. Tests for new domain modules must be added to that list to count. The four large use cases stay out: their decisions live in the domain (`classify_agent`, `keeps_attempt`, `path_within`, the routing), and manual mutants cover their wiring against the whole suite.


---

## Project Structure

The tree of `src/lictor/` and `tests/`, with what each package holds, is in [docs/internals/structure.md](docs/internals/structure.md).

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
- Run `uv run ruff check .`, `uv run mypy --strict src scripts`, and `uv run python scripts/check_architecture.py` before completing any change.
- Implement new capabilities starting from the Domain and Ports, then implement Use Cases, and finally attach Adapters.
- Provide comprehensive unit tests in `tests/unit/` for all new domain and application logic.
- Mock or fake external processes (`subprocess`, `git`, external CLI binaries) using corresponding ports in unit tests.
- Describe a new or changed mechanism in its file of `docs/internals/`, not here: this file keeps the commands, the boundaries, the constraints and one line per mechanism in the index of «How a Run Works», and Claude Code warns past 150k characters.

### Don't
- **Never** import `pydantic` or any third-party framework into `src/lictor/domain/`.
- **Never** create cross-dependencies between adapter packages (e.g., `adapters.agents` importing `adapters.vcs`).
- **Never** perform direct git operations (commit, push, checkout) inside agent tasks unless specifically implementing the `Vcs` port.
- **Never** lower coverage thresholds in `pyproject.toml` or `lictor.json` to make tests pass.
