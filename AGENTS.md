# AGENTS.md

Guidance and operational constraints for AI agents working on **Lictor**.

## Project Overview

Lictor is a deterministic harness and agentic runtime that implements Gherkin acceptance scenarios using an autonomous coding agent, systematically verifying each implementation through strict multi-level quality gates (L1–L5).

- **Language / Runtime:** Python >= 3.12 (managed via Astral `uv`)
- **Core Architecture:** Hexagonal Architecture (Ports and Adapters) enforced with `import-linter`
- **Primary Dependencies:** LangGraph, Pydantic v2, Typer, `gherkin-official`, `defusedxml`

> **Known limitations:** a green commit doesn't yet withstand an adversarial agent. It is the exact tree that passed in a clean clone, with no user configuration involved, and the agent no longer works in the project: it gets a copy with no `.git` and no `.lictor/`. That closes the routes that go through the agent's working directory, not the rest: it runs as your user, so with an absolute path it can reach `.git`, `.lictor/`, the verification environment, `~/.netrc` and the interpreter. Those writes are detected and undone while Lictor is alive (`IntegritySnapshot`, the environment hash) and refuse a resume after a kill (the anchor in the checkpoint), unless the checkpoint is rewritten as well. A process the agent leaves behind can plant in the environment between `prepare` and its hash, and the hash then vouches for it (`test_nothing_planted_during_the_build_is_trusted`). Separately, any project code imported during the tests runs in the runner's process, so it can alter the runner as well as forge the report, the properties of the outcome plugin included (`test_c6`). No phase of this plan closes those two: they stay as strict `xfail` until the sandbox of increment 4, and until then a green doesn't withstand an adversary. The `c4` and `c4b` cases only close specific routes: an installed plugin, `src` in the interpreter's startup path (`sitecustomize`), a package in `src` replacing a dependency, and inherited variables. They don't close `c4` in general.

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
uv run mypy --strict src scripts

# Architectural boundaries check (L4)
uv run python scripts/check_architecture.py
```

### Testing & Verification (L2, L4 & L5)
```bash
# Fast unit tests (L2)
uv run pytest tests/unit -q

# Integration & architecture acceptance tests
uv run pytest tests/integration -n auto -q

# Same, without the slow audit reproduction cases
uv run pytest tests/integration -q -m "not repro"

# Full test suite with coverage enforcement (L4 - minimum 90% branch coverage)
uv run pytest tests -n auto --cov=lictor --cov-branch --cov-fail-under=90 -q

# Domain purity test coverage (L4 - minimum 95% domain coverage)
uv run coverage report --include="*/lictor/domain/*" --fail-under=95

# Security audits (L4). `uvx pip-audit` alone would audit its own environment, not uv.lock
uv run python scripts/audit_dependencies.py
gitleaks detect --no-banner

# Mutation testing (L5); the script also leaves the score where the `mutation` reader reads it
uv run mutmut run
uv run python scripts/mutation_score.py mutants/score.json

# Every control of lictor.json in a clean clone, in the real verification environment (the CI's
# self-verification, minus the acceptance, whose suite the `coverage` control already runs); without
# --keep-going it stops at the first blocking failure
uv run lictor check --keep-going [--control <id> ...]
```

### Running a Subset
```bash
uv run pytest tests/unit/test_routing.py::test_route_load_spec_seal_ok -q   # Single test
uv run pytest tests/unit/test_routing.py -k verify -q                       # By keyword
uv run lictor check --level L1,L2                                           # Gates from lictor.json
uv run lictor check --control types --control unit                          # Some controls by id
```

`lictor check` runs the controls in `lictor.json` plus the acceptance suite (`--control acceptance`), as a run would: see «`lictor check` verifies what `run` would» below. The `coverage-domain` control reads the `.coverage` file left by the `coverage` control in the same clone, so it depends on that one running first in the same `check`.

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
- **Git is the source of truth for green scenarios, and for what they were green for.** `SpecHistory` (`application/spec_history.py`) takes, for each scenario, the most recent commit whose `Scenario:` trailer names it, and hashes that scenario in the features of that commit's own tree (`GherkinSource.spec_hashes`, cached by commit and file). `classify_scenarios` compares it with the current hash: `green`, `reopened` if it differs or isn't found, `new` without a commit. The hash (`spec_hash`) covers steps with their kind, doc strings, tables, backgrounds and examples, not names, tags, comments, spacing or the keywords' language; the form is Lictor's own, so a new `gherkin-official` reopens nothing. There's no hash trailer and nothing to migrate. `load_spec` classifies before `build_driver`, so the driver's prompt lists the greens and asks to update the reopened ones (`scenario.reopened`); `CheckRed` classifies again from git, not from the checkpoint. Changing commit messages or trailers can break resume.
- **The red is judged by class** (`judge_red`). A green must pass. A pending one, reopened or new, must fail with a valid red, and what happens when it passes depends on the driver. With the sealed driver its test was proved when it was sealed: a reopened one is registered again without the agent (as when the scenario wasn't found in its green commit's tree: a change to `features/` breaks the seal) and a new one is a collateral green. With a fresh driver nothing tells a pass from a vacuous test. A new one is `unproven`: in a new project `src/` only holds stubs, so it's most likely a test that checks nothing, and the driver is rejected as retryable (`sin_prueba`); only when attempts run out does the run stop with exit 10, saying first to fix the test and then, if the code already met the scenario, to register it with `lictor register <ids>`. A reopened one is `respecified`: the step may still hold the old value, so the run stops with exit 10 without retrying (`especificacion_cambiada`), and a person who knows the change was only wording registers it with `lictor register`. So nothing is registered inside `red_check` before the seal; the branch only moves to the driver. What passes without the agent goes into one empty commit on top of HEAD (`Scenario:` for each, `Lictor-Collateral` for collaterals), with `scenario.reregistered` or a collateral `scenario.green`.
- **The profile declares what makes the oracle valid** (`oracle`, only in the profile, read by `resolve_defences`). `link: "scenario"` maps each test to the scenario it executed, as the plugin `lictor_outcome` reports it in JUnit properties (`lictor.scenario`, `lictor.feature`); a name that contradicts it, or carries an ID without executing it, rejects the driver, and so does executing it from any file but the scenario's own (`Scenario.file`, passed to `map_test_cases_to_scenarios` as `files` by `CheckRed` and `VerifyAttempt`): pytest-bdd reads a copy with the same tag even if it isn't a `.feature`, which `load_spec` never loads. `red_kinds` lists the failures that count as red: `lictor.failure` is `assertion`, `not_implemented` or `error`, decided by the exception's class in the plugin, never by its text, and `not_implemented` also needs `lictor.origin` in `paths.writable` outside `paths.driver` (`red_failure_problem`). `impostors` reruns the acceptance of a fresh driver in clones where each matching line becomes each replacement (`return None`, `return True`): every new pending scenario must fail with at least one, and a run that crashes, doesn't map or still raises `NotImplementedError` proves nothing (`impostor_problem`). Any failure counts as detected, so impostors only catch a test that ignores the returned value: one without an assert that reads `calc(...).total` fails with both (`AttributeError`) and passes. Evidence goes to `NNN-red/impostor-<n>/`. A fresh driver with tests of no scenario is rejected (`sin_escenario`). Without `oracle` the old rules apply: any failure is red and tests map by name. The plugin runs in the process under test, which can forge it as it can forge the report (`c6`).
- **Collateral greens skip the agent.** After a passing verification, `collateral_greens` finds the pending scenarios the same tree also makes pass, and `VerifyAttempt` chains an empty commit on the candidate (`commit_tree(parent=..., empty=True)`). `last_verify.candidate` is the head of that chain, so `commit` moves the branch to both at once and marks them all green.
- **A quarantine may blame the driver, without claiming it.** Each attempt records the kind and origin of the scenario's failure (`VerifyResult.failure`, state `failures`). If all of them are an `error` raised inside `paths.driver` (`suspicious_driver`), the reason is `driver_sospechoso`, still exit 30. It doesn't flag a driver with an assert no implementation can meet, nor a `pytest.fail` in a step: both are `assertion`.
- **The agent works in a copy, and the project belongs to the user.** Each agent call gets `agent_workspace` (`application/run_files.py`): a fresh export of HEAD outside the project, with no `.git`, no `.lictor/` and no ignored files, gone after the call. `Vcs.capture` reads it into a throwaway index over HEAD, so the result is an exact tree (binaries, modes, deletions), not a textual patch, and `Vcs.changes` and `added_lines` give L0 its diff, with renames split so a move can't hide what it removes. A `.gitignore` in the copy can only hide files from the result. Nothing in Lictor resets or reverts the project's working tree any more: the only write is `advance_head`, and `Vcs` has neither `reset_to_head` nor `revert_paths` on purpose. A run's attempts never reach the project unless verified.
- **Temporary directories are private.** The copy, the clone and each command's home live under `scratch_dir` (`application/run_files.py`), in `<tmp>/lictor-<hash of the project path>`, outside the project. The name is predictable and `/tmp` is shared on Linux, so the root is created with mode `0o700`, and one that already exists is opened with `O_DIRECTORY | O_NOFOLLOW` and checked on the descriptor: a real directory, not a symlink, owned by the current user, with no write for group or others. Otherwise it exits 40 naming the directory: delete it if it is yours, or point `TMPDIR` to a private one. One that passes but isn't `0o700`, like the `0o755` of older versions, is made private with `fchmod` on the same descriptor: nobody else could have created anything in it, and an unprivileged user can't give a file away. `sweep_scratch` checks the same before deleting anything.
- **One run per project at a time.** `lictor run` holds `exclusive_run` (`run_files.py`), a non-blocking `flock` on `.lictor/run.lock`, from before the sweep until it returns: without it a second run swept the first one's copy and clone. A second run exits 10 without writing anything: no `run.stopped`, because the journal and `current_run` belong to the run in progress, which would read any change as tampering. The kernel drops the lock with the process, so a kill leaves nothing behind, and the descriptor isn't inherited, so an orphaned command doesn't keep it. `lictor init` takes it too, because it rewrites `lictor.json`; `register`, because it moves the branch; `check`, because it builds in the temporary directory, which a run starting would sweep; and `doctor`, to build its throwaway environment: if a run holds it, `doctor` checks everything else and leaves the environment «no comprobado». `status` and `report` don't take it: they touch neither the temporary directory nor what `IntegritySnapshot` guards. The lock file is in neither the snapshot nor the anchor.
- **What's verified is what's committed.** After the L0 gates, `VerifyAttempt` builds a candidate commit outside the branch (`commit_tree`) from the captured tree and runs controls and acceptance in a clean clone of it (`verification_tree`). `CheckRed` does the same and seals from the clone. The branch moves with `advance_head`, one `merge --ff-only`: a killed run leaves one state or the other, an edit the commit doesn't touch survives, and one it would overwrite stops with exit 10, resumable through `pause` (`branch_blocked`) with what was verified kept. `Vcs` has no `commit` on purpose, and `commit_tree` refuses a commit that changes nothing unless `empty=True`, which only registrations pass. Re-running `commit` after a crash compares HEAD with the candidate; `CheckRed` compares the `Lictor-Run` trailer and the tree.
- **Prerequisites are checked before anything is spent.** `CheckPreconditions` (`application/usecases/preconditions.py`) is what `preflight`, `doctor` and a resume check: a clean tree (not on resume, which checks it its own way), the committer identity as `commit_tree` will see it (`Vcs.committer_identity`, `git var GIT_COMMITTER_IDENT` through the same `_run_git`, with git's message), the executor, the private temporary directory, the first word of every control and of acceptance (on the `PATH` the command gets, `VerificationEnvironment.search_path`, or committed and executable in HEAD if it is a relative path) and the verification environment. The domain decides the exit code (`precondition_exit_code`): 10 if anything is the project's (configuration, an empty command, a dirty tree), 40 if only the machine has to change. `preflight` builds the run's environment after the cheap checks and before `load_spec`, holding the checkpoint lock like any node that runs code it didn't write, so a broken `prepare` or no identity stops before any agent call (`e29`). `doctor` reports every check without stopping and builds a throwaway environment (`VerificationEnvironment.throwaway`, under the temporary directory, with its log and journal in `.lictor/doctor/`), never a run's.
- **Verification runs in its own environment.** `VerificationEnvironment` builds `.lictor/verify-env` once per run from `verification.prepare` and hashes every file, bytecode included. `ensure` compares the hash before and after the commands: a mismatch fails `L0-entorno`, rebuilds and emits `env.tampered`. A failed `prepare` is exit 40. Commands run through `verification.env`, which in `python-uv` must stop the tool from syncing or creating an environment in the clone and from writing bytecode: dropping any of those variables changes the hash or the tree under test. The project isn't installed in that environment: an editable install would import the agent's tree instead of the clone's. Autoload is off, and plugins come from what is installed: after `prepare` and before the hash, `plugins_command` asks the environment's own interpreter (`-I -B`) for the `pytest11` entry points without importing them. The list joins the profile's `plugins` in `plugin_variable` and goes to the manifest. `ensure` asks again once the hash matches and compares, and the commands read it from memory, never from the manifest, because project code runs between them. Nothing is discovered at run time. Each entry must be a module: `module:attr` can't be loaded by name and exits 40. Plugins register under their module name, so `-p no:<name>` only blocks one whose module is `<name>` or `pytest_<name>`.
- **Project code never enters the interpreter's startup.** `PYTHONPATH` must not name the tree under test: it would run `sitecustomize` in every Python tool, `mypy` included, and pytest's `pythonpath` isn't a substitute, because pytest applies it before importing `PYTEST_PLUGINS`, so `src` could replace a dependency. `python-uv` fixes `PYTEST_ADDOPTS="-o pythonpath="`, which also voids what the project's `pyproject.toml` declares, and `verification.files` copies a plugin (`profiles/python_uv/lictor_project_path.py`, a real module that imports nothing of Lictor) into the hashed environment, in the directory the environment's own interpreter reports (`files_dir_command`, with `-I` for isolation and `-B` because `-I` ignores `PYTHONDONTWRITEBYTECODE`; never `-S`, because `site` is what activates the venv). It appends `src` in `pytest_load_initial_conftests`: after startup, after the plugins, and behind `site-packages`. Tools that need the project on the path get it the same way, after starting and at the end: `scripts/check_architecture.py` appends `src` and then calls Import Linter. It must not run from `src`: Import Linter puts its working directory first, so a `src/typing_extensions.py` would run inside the control and could exit 0 over a broken contract.
- **Verification starts from an empty environment.** `verification.inherit` lists the exact names that pass, and `env` then fixes values, so `env` wins and whatever nobody listed stays out. `command_env` returns the whole environment and `CommandRunner.run` uses it as given: `env=None` inherits, anything else replaces. A missing variable breaks visibly (a tool that isn't found fails `prepare` with exit 40). This covers verification, `lictor check` included; only the agent's commands keep the caller's environment.
- **Each command has an empty home of its own.** `{home_dir}` in `env` is a new directory per command, outside the project, so no user configuration decides a control (a permissive `~/.config/ruff/ruff.toml` gave 3 false greens) and nothing one command writes there configures the next. The real `~/.config/...` isn't read even if project code writes to it, because `HOME` points elsewhere.
- **`prepare` doesn't share its environment with the commands.** Network settings and credentials pass through `prepare_inherit`, which the commands never see. It has an empty home too, so what it needs from the real one is explicit: `prepare_ask` asks the tool with the caller's environment (`UV_PYTHON_INSTALL_DIR`: without it `uv` silently picks another interpreter) and `prepare_home_files` points to a file there (`NETRC`, `~/.netrc`), unless the variable is inherited. `prepare` runs `--no-cache`: a poisoned entry of the shared cache reached the environment and the hash vouched for it, and `--refresh` doesn't help, so every build downloads against the hashes of `uv.lock`. No network means exit 40. Repro cases use the shared cache through a copy of the profile; `make_project(downloads=True)` keeps the real one.
- **The profile owns what verification guarantees.** `prepare`, `prepare_inherit`, `prepare_ask`, `prepare_home_files`, `env`, `inherit`, `reserved`, `plugins`, `plugins_command`, `files`, `suppressions`, `banned_patterns` and `forbidden_paths` live only in the profile; `resolve_defences` (`adapters/config/loader.py`) reads them on every load and `LictorConfig` has no such fields, so declaring one in `lictor.json` fails and a copy can't go stale. A project only adds, through `verification.env`, `inherit` and `prepare_inherit`, and naming a variable the profile fixes, inherits or reserves exits 10. A case that must prove it doesn't depend on a heuristic relaxes it in a copy of the profile (`allowed_paths` in `make_project`), never in `lictor.json`. `paths.protected` stays a project decision, so `uv.lock` is also in `forbidden_paths`: what the lock installs loads as a plugin, so its defence can't depend on `paths`. `UV_*` is reserved because those variables decide where the environment lives, whether `uv` may sync it and where code is downloaded from; a private index authenticates through `~/.netrc` or `NETRC`, not `UV_INDEX_*`.
- **Every implement attempt starts from HEAD.** `ImplementScenario` verifies the seal and then gives the agent a new copy, so a retry never inherits the previous attempt's edits; only the condensed error carries over. A tampered seal stops before the agent runs.
- **The seal changes who may write what.** `features/` and `paths.driver` are hashed into `.lictor/seal.json` after the red check passes on a fresh driver. Before sealing, the driver role may write them. After, any change fails `L0-sello`. On resume, a changed seal or resolved configuration exits with code 10. `config_fingerprint` (`adapters/config/loader.py`) hashes `lictor.json` merged with its profile plus the content of `verification.files`, so a profile edit counts and a format-only edit of `lictor.json` doesn't.
- **Untrusted commands are wrapped in `IntegritySnapshot`** (`verify_attempt.py`). Agent calls and every control or acceptance command run between `take` and `verify_and_restore`, which restores the state files (the checkpoint with its `-wal`, the journal, the seal, `verify_env.json`, `current_run`, `fake_executor.json`, `lictor.json`) and, given a `Vcs`, HEAD and the repository's control files, and names what changed in `changes`. `-shm` doesn't count, because merely reading the database moves it, as `lictor status` does. Control files go back before any git command runs: no `-c` can name a filter in advance. An agent call that tampered gives the signal `rejected`: the attempt fails at `L0-estado` without verifying, spends the attempt and emits `state.tampered`. New code that runs agent or project commands needs the same wrapper, with the `Vcs`.
- **Git runs hardened.** Every call in `GitCli` carries `core.fsmonitor=false` and `core.hooksPath=/dev/null`, ignores replaced objects and drops inherited locations (`GIT_DIR` and the like). `export_tree` and `capture` don't read user configuration either, so a global `core.autocrlf` can't change the tree under test.
- **A resume compares against the anchor.** The checkpoint records HEAD, the hash of `seal.json` and the fingerprint of the repository's control files (`observe_anchor`, set at `preflight`, after sealing and after each commit). `_integrity_refusal` (`cli.py`) compares the fingerprint first, reading bytes without running git, then the rest, and refuses with exit 10, also on uncommitted changes (D4). What a crash can legitimately leave is accepted: the candidate of the last verification, the driver's tree before the seal, or, with `red_check` pending, one commit on top of the anchored head with its same tree (`baseline_head_accepted`): the repeated red check tests every green again. It raises the cost of a forgery, it doesn't rule one out: the checkpoint is within reach of the same user.
- **Checkpoints are written synchronously** (`DURABILITY`, `orchestration/graph.py`). With the default, a background thread writes the WAL while the agent runs: a kill loses it, and `IntegritySnapshot` reads it as tampering. Anything that invokes the graph must pass it. It doesn't cover `put_writes`, which LangGraph still leaves to a thread: `preflight` (it builds the verification environment), `build_driver`, `red_check`, `implement` and `verify` hold the saver's lock for the whole node (`_holding_checkpoint_writes`), so a write in progress ends before the snapshot and one not started waits. Python 3.13 shows the race and 3.12 hides it; `test_a_late_checkpoint_write_is_not_tampering` forces it on any version. A new node that runs agent or project code needs the same wrapper, and it can't touch the saver inside: the lock isn't reentrant, so the node would wait for itself forever. Only Lictor's own nodes run there, so that is a programming error for the suite to catch (the global `timeout` turns the hang into a failure with its traceback), not something production patches over. The wrapper relies on `SqliteSaver.lock`, so `langgraph-checkpoint-sqlite` is pinned below 4.
- **L0 gates are not controls.** `L0-sin-cambios`, `L0-rutas`, `L0-sello`, `L0-supresiones`, `L0-entorno` and `L0-estado` are hardcoded in `VerifyAttempt`; `L0-sin-cambios` fails an attempt whose tree equals HEAD before anything runs. Levels L1–L5 are data in `lictor.json`, merged over the profile defaults in `adapters/config/profiles/`. `mode: measure` never blocks: result, exit code and output are recorded whether it passes or fails, in the `controls` of `verify.finished` and in `report.md`. A control with `report: "mutation"` also records its score (`ControlResult.score`, `MutationScore`): `scripts/mutation_score.py` deletes the previous export, runs the pass and its export and copies the counts to `{out}`, and leaves no report and exits non-zero if the export fails, so an old score can't pass for a new one. `mutation_score` counts as killed only what the tests killed, out of every mutant tried; timeouts, untested and suspicious ones count as not killed and are shown apart. `control_summaries`, `report.md` (under «Puntuaciones», whatever the mode) and `lictor check` show it beside the result. A mutation control may declare `min_score` (in `lictor.json`, a project decision): `enforce_min_score` (domain) fails it below that percentage, in `check` and in a run. Lictor's own is `block` at 95: under the worse platform (95,9 % on macOS; 96,3 % on Linux, where the six `hash_tree` mutants that need a non-UTF-8 name die) with room for known noise. The script also leaves `mutation-survivors.txt` beside `{out}`, which the CI keeps as an artifact. A report with no test cases passes only if its exit code is in the control's `empty_exit_codes` (the profile's `unit` declares `[5]`); `allow_empty` no longer exists and `lictor.json` with it is refused at load, naming what the profile declares. A control with `when_exists` is skipped only if that path is in neither HEAD nor the candidate (`control_path_status`), and fails if the attempt removed it; the skip and its reason go to `controls` and `report.md`. `lictor check` does the same against HEAD and what it verifies.
- **`lictor check` verifies what `run` would.** `CheckWorkingTree` verifies HEAD or, with uncommitted changes, the commit that committing them would make: `Vcs.capture` of the working tree (skipping `.lictor`) and `commit_tree` outside the branch, which only then needs an identity. Controls and acceptance run in a clean clone of it (`verification_tree`), in a throwaway environment built for that same commit, whose hash is compared at the end. There is no working-tree mode. It stops at the first blocking failure unless `--keep-going`; `--control` chooses by id, together with `--level`. Evidence goes to `.lictor/check/`, which each `check` replaces. It exits 0, 10 if a blocking control fails, 40 if the environment fails or changes. The CI's self-verification is `uv run lictor check --keep-going --level L1,L2,L4,L5`: every control but the acceptance, because the `coverage` control already runs `tests/integration` and fails on any failed test.
- **A person can register what Lictor can't prove.** `lictor register <ID>...` (`RegisterScenarios`) takes the lock, refuses if a run can be resumed, needs a clean tree and an identity, is all or nothing (`registration_problems`), states what is asserted for a new or a reopened scenario and asks (or `--yes`; without a terminal it refuses). It makes one empty commit with a `Scenario:` per scenario and `Lictor-Asserted-By`, never `Lictor-Run`, and emits `scenario.asserted`. `SpecHistory` reads both trailers (`GreenRecord.by_lictor`, `asserted_by`); `spec.loaded` carries each green's commit, and `report.md` and `lictor status` show a green whose commit lacks `Lictor-Run` as registered by hand. Every green Lictor has committed carries `Lictor-Run` since its first version (`b4a603e`). The mark is informative: anyone can write a trailer. It is how a project that already has code adopts Lictor: register what the code meets before the first run, so the fresh driver is never told to make a working test fail.
- **Scenarios map to tests by what they executed, or by name.** With `link: "scenario"`, `map_test_cases_to_scenarios` takes the scenario from each case's properties; with `name`, it normalizes `IVA-001` to `iva_001` and looks for it in the test name or classname. Either way it requires exactly one test per scenario, or raises `DriverError`. `Scenario Outline` is refused at `load_spec`, so a scenario never has several cases.
- **Pausing is an interrupt.** On a rate limit, `pause` calls LangGraph `interrupt()` and `resume_to` records the node to return to. That attempt isn't counted. The CLI exits 50, and the next `lictor run` resumes from `.lictor/current_run` via `Command(resume=True)`. Exit codes are stable public API (`ExitCode` in `domain/errors.py`).
- **A backend that answers badly doesn't cost a scenario.** Status `auth`, or `AGENT_FAILURE_LIMIT` consecutive `error` calls, gives the signal `unavailable`: `pause` interrupts with the reason `agent_unavailable`, no attempt is spent and the CLI exits 40. It resumes the same way as a rate limit. An `error` below the limit doesn't spend an attempt either, for the driver too: `classify_agent` and `keeps_attempt` (domain) decide both, and `route_build_driver` sends a driver call that kept its attempt straight back to `build_driver`, with no red check judging its tree. `max_turns` spends one. A call that runs out of time spends one only if the model answered (`model_responded`: an `assistant` event whose model isn't `<synthetic>`); if it never did, it is an `error` («El modelo no respondió en N s»), so a hung backend pauses at the second call instead of quarantining scenarios, or `rate_limited` if the partial stream carries a `rejected` `rate_limit_event`. Either way the raw file keeps the partial stream.
- **Exit 40 means "couldn't run", not "the work failed".** `ENVIRONMENT_UNAVAILABLE` holds at start (`preflight`, `doctor`: no identity, a missing program, the temporary directory, `prepare`), on resume and mid-run: someone has to fix the environment and relaunch, and no scenario is to blame. Keep future environment failures, such as the sandbox, under this code and put the specifics in the message.
- **`forbidden_paths` and `banned_patterns` are heuristics of the profile.** The first are matched against each path component and rejected in `L0-rutas`, along with any symlink; the driver's own paths are exempt, so it may keep a `conftest.py`. The second are case-insensitive regexes over added lines, in `L0-supresiones`. They are heuristics: they raise the cost of cheating, they don't close it. `sitecustomize.py` and `usercustomize.py` are listed as an extra defence; what keeps them from running is the startup path.
- **The agent runs without user or project settings.** `ClaudeCLI` passes `--setting-sources ""` and `--strict-mcp-config`, so hooks, permissions and `CLAUDE.md` from the machine or the repository don't apply. OAuth survives this. It reads `--output-format stream-json`, the only format with `rate_limit_event`.
- **Every wait for a process is bounded.** `SubprocessRunner` reads bytes and decodes them with `replace` (`e20`), gives the command `stdin=DEVNULL`, waits for it within its timeout, kills its whole group however it ends, and then gives the pipes `DRAIN_GRACE_S` (1 s): whatever still holds them escaped the group with `setsid`, and it stops being read, with a note in stderr (`d21`). That process stays alive; only the sandbox reaches it. Each stream keeps its first MiB and its last 15, cut on line breaks with the marker on a line of its own (a piece with no line break stays raw, so redrawn progress keeps its end), so `runs/` logs and raw streams are bounded per file, and their number by the retention of runs. `CommandOutput.timed_out` and `started` say what the return code can't. `ClaudeCLI` gets the same runner from `bootstrap` for the call, `health()` and `auth status`, and reads `cli_version` from the stream's `system/init`, not from a `health()` per attempt.
- **The prompt gets the scenario as its author wrote it.** `SpecSource.scenario_text` finds the scenario by its ID tag in the AST and takes its original lines, comments above it included, up to the next background, scenario or rule, after the backgrounds of the feature and of its rule. A doc string can't cut it (`d27`), and one that isn't found gives only its header.
- **Status comes from structured fields, never from the text.** `rate_limited` needs a failed call (`is_error` or a non-zero exit code) with `api_error_status` 429 or `rate_limit_info.status == "rejected"`, or that `rejected` with no `result` event at all. A successful call stays `ok` whatever those fields say. `auth` needs a 401, or `claude auth status --json` reporting `loggedIn: false` after an `api_error`. Unrecognised output is `error`, with the raw message kept in `AgentResult.text`.
- **Every exit explains itself.** `_finish` (`orchestration/graph.py`) sets `exit_reason` in the state and writes it to `run.finished` and `report.md`, and the CLI prints it. A stop that can still resume (pause, refused resume) goes through `_record_stop` as `run.stopped`. New exits must go through one of the two: an exit code alone is not enough.
- **A run's evidence lives in `.lictor/runs/<run_id>/<owner>/`**, where the owner is a scenario or `driver` (`application/run_files.py`). Each attempt leaves `NNN-implement.json` or `NNN-driver.json`, `.stderr.log` beside it, and one directory per verification: `NNN-verify/` or `NNN-red/` with `acceptance.xml`, `acceptance.log`, `control_<id>.xml` (the report the control was given as `{out}`, whatever its format: a mutation control leaves JSON there), `control_<id>.log` and `error.txt`. Names are never reused: retries and resumes get `-rN`. `NNN` is the attempt the agent call belonged to, which is why `attempt_slot` exists apart from `attempt`. `runs/` stays out of `IntegritySnapshot`: no decision reads it once a step is over. A new run (never a resume) keeps the `retention.keep_runs` most recent, counting itself (10 by default, outside `config_fingerprint` because only a new run reads it), and never the one `current_run` names; names without a run id's shape are left alone (`runs_to_prune`, domain). `runs.pruned` is written before deleting, and the report tells a step missing from a retired run apart from altered evidence. Each step leaves its hash in the journal (`step_evidence`), which is protected, and `report.md` names what no longer matches. The report keeps one event per type, attempt and evidence directory: a node that repeats after a crash counts once, a verification that ran twice shows twice. A run that stops in `preflight` leaves `report.md` as it was. The same report counts the journal lines it skipped, corrupt or foreign; a forged line with the exact shape of an event can't be told apart.
- **What goes to the prompt is bounded; the rest goes to `runs/`.** `condense_error` keeps, in order, the reason, each failed case's name and message, and then details and assertion lines. Callers pass only the cases that matter, not expected pending reds. `render_full_error` has no limit and goes to `error.txt`.
- **A suite that crashed proves nothing.** The report is deleted before each run. `suite_crash_reason` rejects a run without test cases, with an exit code outside `acceptance.failure_exit_codes`, or with a failure code and no failed test. Exit codes other than 0 stay normal while scenarios are pending, so the profile must declare which ones mean "tests failed".
- **The agent's commands aren't the controls.** `agent_commands` in the profile are runnable as they stand and include acceptance, and each must match `agent_tools`. Controls keep `{out}` and belong to verification.

---

## Constraints Enforced by Tests

These are easy to trip:

- **No profile tool names in `src/lictor` outside `adapters/config/profiles/`.** `test_no_profile_tool_names_outside_profiles_dir` fails on any standalone `ruff`, `mypy`, `pytest`, `pytest-bdd` or `uv` in source, including docstrings, comments and prompt templates. Toolchain specifics belong in profiles.
- **The domain can't import I/O modules** (`subprocess`, `sqlite3`, `shutil`, `os`, `socket`). `test_domain_has_no_io_imports` checks this separately from `.importlinter`.
- **`schema/lictor.schema.json` must equal `LictorConfig.model_json_schema()`.** After changing `adapters/config/models.py`, regenerate it with `export_config_schema` (`adapters/config/loader.py`).
- **Mutation testing covers the domain and the small application modules** listed in `[tool.mutmut]` (`anchor`, `events`, `seal`, `spec_history`, `agent_events`, `commit_green`, `quarantine`), with the test files listed there. Tests for new domain modules must be added to that list to count. The four large use cases stay out: their decisions live in the domain (`classify_agent`, `keeps_attempt`, `path_within`, the routing), and manual mutants cover their wiring against the whole suite.

---

## Testing Without an LLM

Set `backend.kind` to `"fake"` in `lictor.json` to use `FakeExecutor` (`adapters/agents/fake.py`). Its modes are `good`, `fail`, `cheat`, `conflict`, `rate_limit` and `crash`, and they can be set per scenario. `bootstrap_runtime` reads its options from `.lictor/fake_executor.json`, or accepts `executor=` directly. `tests/integration/test_e2e_demo_iva.py` runs full cycles against a copy of `examples/demo-iva/`.

For behaviour the modes can't express, pass `FakeExecutor(effect=fn)`: `fn(task, scenario_id, attempt)` runs arbitrary code in the agent's turn (forged bytecode, git commands, killing the run) and may return a status. `effect` can't go in `fake_executor.json`, so it needs `executor=`.

## Running in Parallel

The suite runs under `pytest-xdist` (`-n auto`), which is not in `addopts` because `mutmut` runs pytest without it. Each worker builds its own `template` under its own `tmp_path_factory` base, every case works in its `tmp_path`, and no test opens a port; `test_each_worker_builds_its_own_template` holds the first. The only thing workers share is the `uv` cache, which locks itself.

Every test has a `timeout` of 600 s (`pytest-timeout`, method `signal`, in `[tool.pytest.ini_options]`): the slowest case takes 84 s locally with coverage and CI runners are up to about 2.6 times slower, so 300 s left too little margin. A test that hangs fails with its traceback instead of stalling the suite. Keep the slowest case well below it. `mutmut` isn't affected: it kills its mutants by CPU time, far sooner.

`tests/conftest.py` clears the variables of a verification that may be running the suite, test by test: left in place, a nested `uv sync` would rewrite the verification environment and a nested pytest would load its plugins. It can't happen at startup, because xdist workers inherit them. A test that launches an interpreter with an exact environment needs `-B`, or its bytecode lands in that environment and fails the hash.

## Audit Reproduction Cases

`tests/integration/repro/` holds the cases from the v0.1 audit, one file per correction phase. Unlike the other e2e tests, they use the real `python-uv` profile and `uv`, with the configuration from `lictor init`, so they are the model for new end-to-end tests. Add `-m "not repro"` to skip them while iterating.

- A case asserts the desired behaviour and stays under `@pending(phase, finding)`, a strict `xfail` that only tolerates `AssertionError`. Closing a case means removing its marker. Every phase of the plan is closed, so no case is `@pending`; the two limits only the sandbox of increment 4 closes are `@known_limitation`, also a strict `xfail`.
- A case that fixes behaviour that already holds, as `test_phase7_spec_edit.py` (an uncommitted edit of a `.feature` stops the run with 10 and survives), needs no marker: it guards against a regression.
- Preconditions go through `require`, which raises `ReproSetupError`, so a broken set-up fails the test instead of counting as the expected failure.
- `false_greens` re-checks every `Scenario:` commit in a clean clone with an oracle that uses neither pytest nor the project venv. Keep it that way: cases `c3`, `c4` and `c6` fool anything that runs inside the suite.
- An attack that needs `.git` or `.lictor/` comes in two variants: one from `task.cwd`, which must prove the agent really was in a copy (`_require_copy`), and one by absolute path against the project, which must prove the anchor catches it. Without the first the case passes because the attack didn't run; without the second it proves nothing about the anchor.
- The harness `DRIVER` is a `pytest-bdd` driver whose steps read their values from the Gherkin; edit its checks through `TOTAL_CHECK` and `NEGATIVE_CHECK`. An attack that only touches ignored files or paths outside the copy needs `no_op_change`, or the attempt stops at `L0-sin-cambios` before reaching the gate the case is about.
- `test_phase4_recovery.py` kills a real process with SIGKILL at every node, before, during and after its work, plus `red_check:registro`, after a registration moved the branch. `kill_runner.py` is that process. Set `LICTOR_KILL_MATRIX` to a path to collect the result of each cell.

`tests/integration/test_example_prestamos.py` keeps `examples/prestamos-biblioteca` honest: its features load, and the three scenarios its code already meets pass, with an oracle that reads the Gherkin and calls the code without pytest-bdd.

The audit report, the original lab scripts and the phase tracking live in `.audits/analisis-base/`, which is git-ignored and exists only on the maintainer's machine.

---

## Project Structure

```
├── pyproject.toml              # Build config, tools (ruff, mypy, pytest, mutmut)
├── lictor.json                 # Quality gate profiles and control configurations
├── .importlinter               # Architectural contract definitions
├── examples/                   # demo-iva, and prestamos-biblioteca (adopting a project with code)
├── src/lictor/
│   ├── domain/                 # Pure domain: model.py, rules.py, routing.py, errors.py
│   ├── application/
│   │   ├── ports.py            # Protocols (Executor, EventLog, Clock, Vcs, Runner, etc.)
│   │   ├── anchor.py           # What the checkpoint records for a resume to compare
│   │   ├── run_files.py        # Evidence paths under runs/, the agent's copy and the clean tree
│   │   ├── spec_history.py     # Green commits and the spec hash of each in its own tree
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
- Run `uv run ruff check .`, `uv run mypy --strict src scripts`, and `uv run python scripts/check_architecture.py` before completing any change.
- Implement new capabilities starting from the Domain and Ports, then implement Use Cases, and finally attach Adapters.
- Provide comprehensive unit tests in `tests/unit/` for all new domain and application logic.
- Mock or fake external processes (`subprocess`, `git`, external CLI binaries) using corresponding ports in unit tests.

### Don't
- **Never** import `pydantic` or any third-party framework into `src/lictor/domain/`.
- **Never** create cross-dependencies between adapter packages (e.g., `adapters.agents` importing `adapters.vcs`).
- **Never** perform direct git operations (commit, push, checkout) inside agent tasks unless specifically implementing the `Vcs` port.
- **Never** lower coverage thresholds in `pyproject.toml` or `lictor.json` to make tests pass.

