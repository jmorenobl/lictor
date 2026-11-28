# Testing Lictor: the whole suite, the CI and its budget

`AGENTS.md` keeps what to run while editing and when closing a change. This file has the rest: every command, how the suite runs in parallel, the CI's layers and its budget, and testing without an LLM.

## Every command

### Testing & Verification (L2, L4 & L5)
```bash
# Fast unit tests (L2)
uv run pytest tests/unit -q

# Integration & architecture tests
uv run pytest tests/integration -n auto -q

# Same, without the slow audit reproduction cases
uv run pytest tests/integration -q -m "not repro"

# What every platform runs in a pull request (about 2 min), and what a PR runs of repro
uv run pytest tests -n auto -q -m "not repro"
uv run pytest tests -n auto -q -m "repro and not toolchain and not exhaustive"

# The cases on Go and Node with the generic profile (go, gotestsum and node on the PATH; without
# them they skip, and with LICTOR_REQUIRE_TOOLCHAINS=1, as their CI job sets it, they fail)
uv run pytest tests -n auto -q -m "toolchain and not exhaustive"

# Coverage enforcement (L4 - minimum 90% branch coverage) of everything but repro, as the
# `coverage` control of lictor.json runs it; the nightly measures the whole number, repro included
uv run pytest tests -n auto -m "not repro and not exhaustive" --cov=lictor --cov-branch --cov-fail-under=90 -q

# Domain purity test coverage (L4 - minimum 95% domain coverage)
uv run coverage report --include="*/lictor/domain/*" --fail-under=95

# Security audits (L4). `uvx pip-audit` alone would audit its own environment, not uv.lock
uv run python scripts/audit_dependencies.py
gitleaks detect --no-banner

# Mutation testing (L5); the script also leaves the score where the `mutation` reader reads it
uv run mutmut run
uv run python scripts/mutation_score.py mutants/score.json

# Every control of lictor.json and the acceptance (the repro cases), in a clean clone, in the real
# verification environment; without --keep-going it stops at the first blocking failure. Each test
# runs once: `coverage` runs everything but repro, and the acceptance only repro. Without --level it
# takes as long as the whole suite: it is for measuring, not for closing a change
uv run lictor check --keep-going [--control <id> ...]
```

### Running a Subset
```bash
uv run pytest tests/unit/test_routing.py::test_route_load_spec_seal_ok -q   # Single test
uv run pytest tests/unit/test_routing.py -k verify -q                       # By keyword
uv run lictor check --level L1,L2                                           # Gates from lictor.json
uv run lictor check --control types --control unit                          # Some controls by id
```

`lictor check` runs the controls in `lictor.json` plus the acceptance suite (`--control acceptance`), as a run would, and also the controls with `"stage": "check"`, which a run leaves to it: see «`lictor check` verifies what `run` would» below. Lictor's own `mutation` and `audit` are `check`, so a run doesn't pay them in each attempt; `--level L1,L2,L4` still runs `audit`. The `coverage-domain` control reads the `.coverage` file left by the `coverage` control in the same clone, so it depends on that one running first in the same `check`.

## Several agents, the whole suite and the budget

- **Several agents on one machine:** `-n 4` instead of `-n auto`, or they slow each other down (F45).
- **The whole `tests` at once** (`uv run pytest tests -n auto`, as the nightly does) needs `--dist worksteal`: the `repro` cases are collected first, and with the default distribution each worker starts with a run of consecutive tests, so one worker got every `repro` case and ran them one after another while the rest sat idle (macOS went from 36 to over 90 min). A run of `-m "not repro"` or of only `repro` doesn't need it.
- **A new test slower than 10 s** needs the `repro` mark and a reason: something only seen with everything set up. The CI lists the others as warnings.

Budget, measured in the CI. Going over is an issue, like a broken test. `scripts/ci_budget.py` holds the CI's figures and warns, never fails; `timeout-minutes` is only the cut against a hung job, about 2.5 times what a job takes (a `lictor check` step's comes from its controls' `timeout_s`: see below the table).

| When | Budget |
|:-----|:-------|
| While editing | < 30 s |
| Closing a change, locally | < 3 min |
| Pull request, critical path, without the queue | < 12 min |
| Each job of the nightly | < 60 min |
| Self-verification (L1, L2, L4), one job | < 6 min |
| Mutation (L5), one job | < 10 min |

The mutation is linear in mutants, about 38 ms each on the CI's runner with 4 children, and grows with the domain: past about 13 000 mutants (10 348 on 2026-11-26) it is split in shards. Each `lictor check` step of the CI has its own cut, and each control's `timeout_s` (600 s by default, 900 s for `mutation`) stays under it with what the rest of the step takes on a slow runner, so a hung control fails with its log and its verdict, and `--keep-going` goes on: `test_lictor_cuts_a_hung_control_before_the_ci_cuts_its_step` holds it.

## Testing Without an LLM

Set `backend.kind` to `"fake"` in `lictor.json` to use `FakeExecutor` (`adapters/agents/fake.py`). Its modes are `good`, `fail`, `cheat`, `conflict`, `rate_limit` and `crash`, and they can be set per scenario. A refactor call has its own `refactor_mode` and writes only what `files_to_write` holds under `refactor` or `refactor:<ID>`: by default nothing, so it ends `unchanged`. `bootstrap_runtime` reads its options from `.lictor/fake_executor.json`, or accepts `executor=` directly. `tests/integration/test_e2e_demo_iva.py` runs full cycles against a copy of `examples/demo-iva/`, inside a linked worktree with `fake_executor.json` in its `.lictor/`: from the person's checkout the run would get a worktree of its own, without it.

For behaviour the modes can't express, pass `FakeExecutor(effect=fn)`: `fn(task, scenario_id, attempt)` runs arbitrary code in the agent's turn (forged bytecode, git commands, killing the run) and may return a status. `effect` can't go in `fake_executor.json`, so it needs `executor=`.

## Running in Parallel

The suite runs under `pytest-xdist` (`-n auto`), which is not in `addopts` because `mutmut` runs pytest without it. Each worker builds its own `template` under its own `tmp_path_factory` base, every case works in its `tmp_path`, and no test opens a port; `test_each_worker_builds_its_own_template` holds the first. The only thing workers share is the `uv` cache, which locks itself.

Every test has a `timeout` of 600 s (`pytest-timeout`, method `signal`, in `[tool.pytest.ini_options]`): the slowest case takes 84 s locally with coverage and CI runners are up to about 2.6 times slower, so 300 s left too little margin. A test that hangs fails with its traceback instead of stalling the suite. Keep the slowest case well below it. `mutmut` isn't affected: it kills its mutants by CPU time, far sooner. Memory is bounded by `scripts/mutation_score.py`: at most 4 mutants at once (`--max-children`) and 1 GiB of data per process (`RLIMIT_DATA`, which Linux enforces), so a mutant that loops piling up memory gets a `MemoryError` and counts as killed. One that turned `os.scandir(entry.path)` into `os.scandir(None)` in `hash_tree` reached 13,8 GB without it, and hung the self-verification in CI.

No test judges a wait by the wall clock: a loaded machine made `< 4 s` fail twice (F22, F39). A test of a call that must not wait for a process leaves that process alive for `DETACHED_LIFE_S` (300 s, `Detached` and the `detached` fixture in `tests/conftest.py`) and checks it is still alive when the call returns; one of a timeout gives the command that long and checks the mark it would leave at its end isn't there. Either fails only if the call really waited, and the fixture kills what is left.

The CI runs in layers (`.github/workflows/`). The `toolchain` cases (Go and Node with `generic`, `test_v0_23_0_toolchains.py` and `test_v0_23_2_toolchains.py`, the latter with a module proxy on disk (`go_proxy`) so Go's module cache fills without a network, their own harness `repro/toolchains.py` with an oracle that runs `go run` or `node` on each green's tree) run only in their job, «Cadenas: Go y Node», on Linux with Go 1.26 and `gotestsum` v1.13.0, once with Node 24 (the maintenance LTS) and once with 26 (the active one), since a recipe of the skill broke on 24 alone. The job sets `LICTOR_REQUIRE_TOOLCHAINS=1`, so a missing tool fails instead of skipping; every other job leaves them out by their mark, and locally they skip without the tools. In a pull request, `-m "not repro"` runs on Linux with Python 3.12, 3.13 and 3.14 and on macOS with 3.14 (3.12 on macOS is what a maintainer runs locally). `repro` runs on Linux with 3.13 and on macOS with 3.14, each in three parts split by `pytest-split` from `.test_durations`: three keep a Linux part under a macOS one, which sets the critical path. The self-verification measures coverage without `repro`, and the mutation runs in a job of its own beside it (`Mutación`). A last job (`Presupuesto`, `scripts/ci_budget.py`) compares each job and the critical path with the budget, the queue apart, and warns without failing. `suite-completa.yml` runs everything, `exhaustive` included, on the six platforms: every night (off the hour, which GitHub delays least), by hand, and before a release, which `release.yml` only publishes if it passes. It runs with `--dist worksteal` (see «What to run and when»), and its own `Presupuesto` (`ci_budget.py full-suite-jobs`) compares each job with its 60 min, without the critical path of a pull request, and never stops the release that waits on it. Its Linux 3.12 cell measures the whole coverage, `repro` included, in its summary; its Linux 3.13 cell leaves the artifact `test_durations`, the duration of every `repro` case without coverage. Copy it over `.test_durations` when the repro cases change much; a case missing there counts as the average, so a new one is never lost, only worse placed. `exhaustive` marks the cells of a matrix beyond one per class, which a PR doesn't run. A nightly can fail without a failed test when GitHub finds no macOS runner («not acquired by runner»): rerun it with `gh run rerun --failed <id>`, which before a release also reruns the publishing job that waited on it.

`tests/conftest.py` gives every test its own `TMPDIR` and a `LICTOR_HOME` beside it, so no test writes the real `~/.lictor`: Lictor refuses a home under the temporary directory, and every directory pytest hands out is under the system's. It also clears the variables of a verification that may be running the suite, test by test: left in place, a nested `uv sync` would rewrite the verification environment and a nested pytest would load its plugins. It can't happen at startup, because xdist workers inherit them. A test that launches an interpreter with an exact environment needs `-B`, or its bytecode lands in that environment and fails the hash.

