# AGENTS.md

Guidance and operational constraints for AI agents working on **Lictor**.

## Project Overview

Lictor is a deterministic harness and agentic runtime that implements Gherkin acceptance scenarios using an autonomous coding agent, systematically verifying each implementation through strict multi-level quality gates (L1–L5).

- **Language / Runtime:** Python >= 3.12 (managed via Astral `uv`)
- **Core Architecture:** Hexagonal Architecture (Ports and Adapters) enforced with `import-linter`
- **Primary Dependencies:** LangGraph, Pydantic v2, Typer, `gherkin-official`, `defusedxml`

> **Known limitations:** a green commit doesn't yet withstand an adversarial agent. Since phase 3 it is the exact tree that passed in a clean clone, but `.git`, `.lictor/` and the verification environment share the agent's user, so it can still tamper with them. That stays true until phase 4 closes. Separately, any project code imported during the tests runs in the runner's process, so it can alter the runner as well as forge the report (`test_c6`). No phase of this plan closes that: it stays as a strict `xfail` until the sandbox of increment 4, and until then a green doesn't withstand an adversary. The `c4` and `c4b` cases only close specific routes: an installed plugin, `src` in the interpreter's startup path (`sitecustomize`), a package in `src` replacing a dependency, and inherited variables. They don't close `c4` in general.

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
- **Git is the source of truth for green scenarios.** `CheckRed` derives them from `Scenario:` commit trailers, not from the checkpoint. Changing commit messages or trailers can break resume.
- **What's verified is what's committed.** After the L0 gates, `VerifyAttempt` builds a candidate commit outside the branch (`create_candidate`, with a throwaway index) and runs controls and acceptance in a clean clone of it (`verification_tree`). `CheckRed` does the same and seals from the clone. The branch only moves to that exact SHA with a conditional `update-ref` (`advance_head`), which adds nothing, so what the working tree holds afterwards can't get in. `Vcs` has no `commit` on purpose. Re-running `commit` after a crash compares HEAD with the candidate; `CheckRed` compares the `Lictor-Run` trailer.
- **Verification runs in its own environment.** `VerificationEnvironment` builds `.lictor/verify-env` once per run from `verification.prepare` and hashes every file, bytecode included. `ensure` compares the hash before and after the commands: a mismatch fails `L0-entorno`, rebuilds and emits `env.tampered`. A failed `prepare` is exit 40. Commands run through `verification.env`, which in `python-uv` must stop the tool from syncing or creating an environment in the clone and from writing bytecode: dropping any of those variables changes the hash or the tree under test. The project isn't installed in that environment: an editable install would import the agent's tree instead of the clone's. Plugins are explicit (`PYTEST_PLUGINS`), since autoload is off.
- **Project code never enters the interpreter's startup.** `PYTHONPATH` must not name the tree under test: it would run `sitecustomize` in every Python tool, `mypy` included, and pytest's `pythonpath` isn't a substitute, because pytest applies it before importing `PYTEST_PLUGINS`, so `src` could replace a dependency. `python-uv` fixes `PYTEST_ADDOPTS="-o pythonpath="`, which also voids what the project's `pyproject.toml` declares, and `verification.files` puts a plugin in the hashed environment that appends `src` in `pytest_load_initial_conftests`: after startup, after the plugins, and behind `site-packages`. Tools that need the project on the path get it the same way, after starting: `lint-imports` runs from `src`, whose working directory it adds itself.
- **Verification inherits nothing that loads code.** `verification.unset` holds patterns of inherited variables to remove, and `env` then fixes values, so `env` wins. `command_env` expresses a removal as `None`, which is what `CommandRunner.run` takes. It only covers verification: `lictor check` and the agent's commands keep the caller's environment.
- **Every implement attempt starts from HEAD.** `ImplementScenario` verifies the seal, then calls `vcs.reset_to_head()`, so a retry never inherits the previous attempt's edits; only the condensed error carries over. The order matters: a tampered seal must abort without wiping the tree.
- **`reset_to_head` must not remove ignored files** (`git clean -fd`, never `-x`), because `.lictor/` is git-ignored and holds the run's state (ADR-0001).
- **The seal changes who may write what.** `features/` and `paths.driver` are hashed into `.lictor/seal.json` after the red check passes on a fresh driver. Before sealing, the driver role may write them. After, any change fails `L0-sello`. On resume, a changed `lictor.json` or seal exits with code 10.
- **Untrusted commands are wrapped in `IntegritySnapshot`** (`verify_attempt.py`). Agent calls and every control or acceptance command run between `take` and `verify_and_restore`, which restores `.lictor/` state files and `lictor.json` on tampering. New code that runs agent or project commands needs the same wrapper.
- **L0 gates are not controls.** `L0-rutas`, `L0-sello`, `L0-supresiones`, `L0-entorno` and `L0-estado` are hardcoded in `VerifyAttempt`. Levels L1–L5 are data in `lictor.json`, merged over the profile defaults in `adapters/config/profiles/`. `mode: measure` never blocks: result, exit code and output are recorded whether it passes or fails, in the `controls` of `verify.finished` and in `report.md`. There's no score yet: `mutmut` exits 0 with survivors, so the mutation control reads `ok` until its reader arrives in phase 6.
- **Scenarios map to tests by name.** `map_test_cases_to_scenarios` normalizes `IVA-001` to `iva_001` and requires exactly one match per scenario in the JUnit test name or classname. Otherwise it raises `DriverError`.
- **Pausing is an interrupt.** On a rate limit, `pause` calls LangGraph `interrupt()` and `resume_to` records the node to return to. That attempt isn't counted. The CLI exits 50, and the next `lictor run` resumes from `.lictor/current_run` via `Command(resume=True)`. Exit codes are stable public API (`ExitCode` in `domain/errors.py`).
- **A backend that answers badly doesn't cost a scenario.** Status `auth`, or `AGENT_FAILURE_LIMIT` consecutive `error` calls, gives the signal `unavailable`: `pause` interrupts with the reason `agent_unavailable`, no attempt is spent and the CLI exits 40. It resumes the same way as a rate limit. An `error` below the limit doesn't spend an attempt either; `max_turns` and `timeout` do, so a hung backend still spends attempts until a later phase separates that case.
- **Exit 40 means "couldn't run", not "the work failed".** `ENVIRONMENT_UNAVAILABLE` holds at start (`preflight`, `doctor`), on resume and mid-run: someone has to fix the environment and relaunch, and no scenario is to blame. Keep future environment failures, such as the sandbox, under this code and put the specifics in the message.
- **`forbidden_paths` and `banned_patterns` come from the profile.** The first are matched against each path component and rejected in `L0-rutas`, along with any symlink; the driver's own paths are exempt, so it may keep a `conftest.py`. The second are case-insensitive regexes over added lines, in `L0-supresiones`. They are heuristics: they raise the cost of cheating, they don't close it. `sitecustomize.py` and `usercustomize.py` are listed as an extra defence; what keeps them from running is the startup path.
- **The agent runs without user or project settings.** `ClaudeCLI` passes `--setting-sources ""` and `--strict-mcp-config`, so hooks, permissions and `CLAUDE.md` from the machine or the repository don't apply. OAuth survives this. It reads `--output-format stream-json`, the only format with `rate_limit_event`.
- **Status comes from structured fields, never from the text.** `rate_limited` needs a failed call (`is_error` or a non-zero exit code) with `api_error_status` 429 or `rate_limit_info.status == "rejected"`, or that `rejected` with no `result` event at all. A successful call stays `ok` whatever those fields say. `auth` needs a 401, or `claude auth status --json` reporting `loggedIn: false` after an `api_error`. Unrecognised output is `error`, with the raw message kept in `AgentResult.text`.
- **Every exit explains itself.** `_finish` (`orchestration/graph.py`) sets `exit_reason` in the state and writes it to `run.finished` and `report.md`, and the CLI prints it. A stop that can still resume (pause, refused resume) goes through `_record_stop` as `run.stopped`. New exits must go through one of the two: an exit code alone is not enough.
- **A run's evidence lives in `.lictor/runs/<run_id>/<owner>/`**, where the owner is a scenario or `driver` (`application/run_files.py`). Each attempt leaves `NNN-implement.json` or `NNN-driver.json`, `.stderr.log` beside it, and one directory per verification: `NNN-verify/` or `NNN-red/` with `acceptance.xml`, `acceptance.log`, `control_<id>.xml`, `control_<id>.log` and `error.txt`. Names are never reused: retries and resumes get `-rN`. `NNN` is the attempt the agent call belonged to, which is why `attempt_slot` exists apart from `attempt`.
- **What goes to the prompt is bounded; the rest goes to `runs/`.** `condense_error` keeps, in order, the reason, each failed case's name and message, and then details and assertion lines. Callers pass only the cases that matter, not expected pending reds. `render_full_error` has no limit and goes to `error.txt`.
- **A suite that crashed proves nothing.** The report is deleted before each run. `suite_crash_reason` rejects a run without test cases, with an exit code outside `acceptance.failure_exit_codes`, or with a failure code and no failed test. Exit codes other than 0 stay normal while scenarios are pending, so the profile must declare which ones mean "tests failed".
- **The agent's commands aren't the controls.** `agent_commands` in the profile are runnable as they stand and include acceptance, and each must match `agent_tools`. Controls keep `{out}` and belong to verification.

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

`tests/integration/repro/` holds the cases from the v0.1 audit, one file per correction phase. Unlike the other e2e tests, they use the real `python-uv` profile and `uv`, with the configuration from `lictor init`, so they are the model for new end-to-end tests. They take about 3 minutes; add `-m "not repro"` to skip them while iterating.

- A case asserts the desired behaviour and stays under `@pending(phase, finding)`, a strict `xfail` that only tolerates `AssertionError`. Closing a case means removing its marker.
- Preconditions go through `require`, which raises `ReproSetupError`, so a broken set-up fails the test instead of counting as the expected failure.
- `false_greens` re-checks every `Scenario:` commit in a clean clone with an oracle that uses neither pytest nor the project venv. Keep it that way: cases `c3`, `c4` and `c6` fool anything that runs inside the suite.
- Agent effects must tolerate a missing `.git` or `.lictor/`, which phases 3 and 4 take away from the agent.

The audit report, the original lab scripts and the phase tracking live in `.audits/analisis-base/`, which is git-ignored and exists only on the maintainer's machine.

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
│   │   ├── run_files.py        # Evidence paths under runs/ and the clean verification tree
│   │   ├── verification_env.py # Verification environment: build, hash, rebuild
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

