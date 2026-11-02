"""Reproduction harness for the cases from the v0.1 audit.

Every case runs the real CLI graph over a demo-iva copy using the real python-uv profile, with
the agent replaced by a scripted FakeExecutor. Cases assert the desired behaviour and stay under
`pending` until their phase closes them. `pending` only tolerates AssertionError: anything that
breaks the set-up raises ReproSetupError and fails the test, so it can't pass for a wrong reason.

Agent effects must tolerate a missing `.git` or `.lictor/`: phases 3 and 4 take both away from
the agent's working copy, and an attack that no longer applies is not a set-up error.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from lictor import cli
from lictor.adapters.agents.fake import FakeExecutor
from lictor.bootstrap import BootstrapResult, bootstrap_runtime
from lictor.domain.model import AgentStatus, AgentTask

SUBPROCESS_TIMEOUT_S = 60

STUB = (
    '"""Interfaz."""\n\n\n'
    "def general(importe: float) -> float:\n"
    '    """General."""\n'
    "    raise NotImplementedError\n\n\n"
    "def reducido(importe: float) -> float:\n"
    '    """Reducido."""\n'
    "    raise NotImplementedError\n\n\n"
    "def negativo(importe: float) -> bool:\n"
    '    """Negativo."""\n'
    "    raise NotImplementedError\n"
)
DRIVER = (
    "import iva\n\n\n"
    "def test_iva_001():\n    assert iva.general(100) == 121\n\n\n"
    "def test_iva_002():\n    assert iva.reducido(100) == 110\n\n\n"
    "def test_iva_003():\n    assert iva.negativo(-5) is True\n"
)
GOOD_1 = STUB.replace("raise NotImplementedError", "return round(importe * 1.21, 2)", 1)
GOOD_2 = GOOD_1.replace("raise NotImplementedError", "return round(importe * 1.10, 2)", 1)
GOOD_3 = GOOD_2.replace("raise NotImplementedError", "return importe < 0", 1)
GOOD_BY_SCENARIO = {"IVA-001": GOOD_1, "IVA-002": GOOD_2, "IVA-003": GOOD_3}
UNIT_TEST = "import iva\n\n\ndef test_ok():\n    assert iva is not None\n"
DRIVER_PATH = "tests/acceptance/test_iva.py"
VERIFY_ENV = ".lictor/verify-env"
MODULE_PATH = "src/iva/__init__.py"

ORACLE = {
    "IVA-001": "iva.general(100) == 121",
    "IVA-002": "iva.reducido(100) == 110",
    "IVA-003": "iva.negativo(-5) is True",
}

Driver = Callable[[AgentTask, int], AgentStatus | None]
Impl = Callable[[AgentTask, str, int], AgentStatus | None]


class ReproSetupError(Exception):
    """The case could not be set up, so its outcome proves nothing."""


def pending(phase: int, finding: str) -> pytest.MarkDecorator:
    """Mark a case as known-broken until `phase` closes it."""
    return pytest.mark.xfail(strict=True, raises=AssertionError, reason=f"Fase {phase}: {finding}")


def known_limitation(finding: str) -> pytest.MarkDecorator:
    """Mark a case no phase of this plan closes: it stays visible until the sandbox arrives."""
    return pytest.mark.xfail(
        strict=True, raises=AssertionError, reason=f"Limitación de la v0.1, incremento 4: {finding}"
    )


def require(condition: object, message: str) -> None:
    """Check a precondition of the case, as opposed to the behaviour under test."""
    if not condition:
        raise ReproSetupError(message)


def write(root: Path, rel_path: str, content: str | bytes) -> None:
    path = root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")


def shell(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a command that may legitimately fail, as an agent would."""
    return subprocess.run(
        args, cwd=cwd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_S, check=False
    )


def git(root: Path, *args: str) -> str:
    """Run git for the harness itself, immune to what the agent left in .git/config."""
    hardened = ["-c", "core.fsmonitor=false", "-c", "core.hooksPath=/dev/null"]
    result = shell(["git", *hardened, *args], root)
    require(result.returncode == 0, f"git {args} falló: {result.stderr}")
    return result.stdout


def good_driver(task: AgentTask, _attempt: int = 1) -> None:
    write(task.cwd, MODULE_PATH, STUB)
    write(task.cwd, DRIVER_PATH, DRIVER)
    write(task.cwd, "tests/unit/test_smoke.py", UNIT_TEST)


def no_op_change(task: AgentTask) -> None:
    """Change the tree without changing behaviour: an attempt with no change stops at L0.

    Attacks that only reach ignored files or paths outside the copy need it to get to the gate
    they are about.
    """
    write(task.cwd, MODULE_PATH, f"{STUB}\n# intento sin efecto\n")


def good_impl(task: AgentTask, scenario_id: str, _attempt: int = 1) -> None:
    write(task.cwd, MODULE_PATH, GOOD_BY_SCENARIO[scenario_id])


@dataclass
class Agent:
    """Scripted agent; `prompts` holds what Lictor sent, keyed by scenario or "driver"."""

    driver: Driver = good_driver
    impl: Impl = good_impl
    prompts: dict[str, list[str]] = field(default_factory=lambda: defaultdict(list))
    errors: list[Exception] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.executor = FakeExecutor(effect=self._effect)

    @property
    def calls(self) -> list[str]:
        return self.executor.calls

    def _effect(self, task: AgentTask, scenario_id: str | None, attempt: int) -> AgentStatus | None:
        self.prompts[scenario_id or "driver"].append(task.prompt)
        try:
            if scenario_id is None:
                return self.driver(task, attempt)
            return self.impl(task, scenario_id, attempt)
        except Exception as exc:
            self.errors.append(exc)
            raise


@dataclass(frozen=True)
class Outcome:
    """How `lictor run` ended; a KeyboardInterrupt stands for a killed process."""

    exit_code: int | None
    exception: BaseException | None = None

    @property
    def killed(self) -> bool:
        return isinstance(self.exception, KeyboardInterrupt)


def run_lictor(root: Path, agent: Agent, *, new: bool = False) -> Outcome:
    def bootstrap(project_root: Path, **_kwargs: Any) -> BootstrapResult:
        return bootstrap_runtime(project_root, executor=agent.executor)

    outcome: Outcome
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(cli, "bootstrap_runtime", bootstrap)
        try:
            outcome = Outcome(cli._execute_run(root, force_new=new))
        except KeyboardInterrupt as exc:
            outcome = Outcome(None, exc)
        except Exception as exc:
            outcome = Outcome(None, exc)
    require(not agent.errors, f"el guion del agente falló: {agent.errors}")
    return outcome


def green_commits(root: Path) -> list[tuple[str, str]]:
    """Return (sha, scenario) for every commit that claims a green scenario, oldest first."""
    log = git(
        root,
        "log",
        "--reverse",
        "--format=%H %(trailers:key=Scenario,valueonly,separator=%x2C)",
    )
    pairs = [line.partition(" ") for line in log.splitlines()]
    # A registration without the agent may name several scenarios in one commit.
    return [(sha, sid) for sha, _, ids in pairs for sid in ids.split(",") if sid]


def green_ids(root: Path) -> list[str]:
    return [sid for _, sid in green_commits(root)]


def false_greens(root: Path) -> list[str]:
    """Check each green commit in a clean clone, with neither pytest nor the project venv.

    The oracle is deliberately independent of the acceptance suite: it calls the public API on
    the committed tree, so nothing ignored by git and no plugin can influence it. Without green
    commits it returns nothing, so callers must first establish that the agent actually ran.
    """
    found: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        clone = Path(tmp) / "clone"
        git(root, "clone", "-q", "--no-hardlinks", ".", str(clone))
        for sha, scenario_id in green_commits(root):
            git(clone, "checkout", "-q", sha)
            check = ORACLE[scenario_id]
            script = (
                "import sys\nsys.path.insert(0, 'src')\nimport iva\n"
                f"raise SystemExit(0 if {check} else 'el commit no cumple: {check}')\n"
            )
            result = shell([sys.executable, "-I", "-S", "-c", script], clone)
            if result.returncode != 0:
                reason = result.stderr.strip().splitlines()[-1]
                found.append(f"{scenario_id}@{sha[:7]}: {reason}")
    return found


def journal(root: Path) -> list[dict[str, Any]]:
    """Read the journal, skipping lines that aren't events (the agent may forge them)."""
    path = root / ".lictor" / "journal.jsonl"
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines() if path.is_file() else []:
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and "type" in event:
            events.append(event)
    return events


def events_of(root: Path, name: str) -> list[dict[str, Any]]:
    return [event for event in journal(root) if event["type"] == f"lictor.{name}.v1"]


def quarantined(root: Path) -> list[str]:
    return [event["scenarioid"] for event in events_of(root, "scenario.quarantined")]


def error_block(prompt: str) -> str:
    """Return the part of a retry prompt that carries the previous error."""
    return prompt.split("Contexto del proyecto:", 1)[1]


def isolated_env() -> dict[str, str]:
    """Environment variables that keep a case independent of the host."""
    return {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}


def uv_sync(root: Path) -> None:
    """Build the project venv, preferring the local cache over the network."""
    if shell(["uv", "sync", "-q", "--offline"], root).returncode != 0:
        result = shell(["uv", "sync", "-q"], root)
        require(result.returncode == 0, f"uv sync falló: {result.stderr}")


def copy_project(template: Path, dest: Path) -> None:
    shutil.copytree(template, dest, symlinks=True, ignore=shutil.ignore_patterns(".venv"))
