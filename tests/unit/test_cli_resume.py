"""Why `lictor run` refuses to resume, and how a paused run explains its exit.

The rules themselves live in the domain (`anchor_problems`, `baseline_head_accepted`); these tests
fix the order `cli.py` asks them in and what it accepts besides the anchored head, against a
runtime of doubles: the repro cases prove the same end to end, at a hundred times the cost.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from lictor import cli
from lictor.adapters.store.clock import FixedClock
from lictor.application.seal import hash_file
from lictor.application.usecases.preconditions import (
    PreconditionCheck,
    PreconditionsReport,
    PreconditionsRequest,
)
from lictor.bootstrap import BootstrapResult
from lictor.domain.model import Event, PreconditionProblem
from lictor.domain.rules import ANCHOR_FORMAT

HEAD = "a" * 40
MOVED = "b" * 40
VERIFIED = "c" * 40
TREE = "t" * 40
OTHER_TREE = "u" * 40
REPO = "fingerprint-0"


@dataclass
class _Vcs:
    """Only what a resume reads from the repository."""

    head: str = HEAD
    repo: str = REPO
    parents: dict[str, str] = field(default_factory=dict)
    trees: dict[str, str] = field(default_factory=dict)
    changed: list[str] = field(default_factory=list)

    def get_head_sha(self) -> str:
        return self.head

    def fingerprint(self) -> str:
        return self.repo

    def parent_of(self, rev: str) -> str:
        return self.parents.get(self.head if rev == "HEAD" else rev, "")

    def get_tree(self, rev: str) -> str:
        return self.trees.get(self.head if rev == "HEAD" else rev, TREE)

    def status(self) -> list[str]:
        return list(self.changed)


@dataclass
class _SpecSource:
    seal_holds: bool = True

    def verify_seal(self, project_root: Path, driver_paths: Sequence[str]) -> bool:
        return self.seal_holds


@dataclass
class _Preconditions:
    problems: tuple[PreconditionProblem, ...] = ()
    requests: list[PreconditionsRequest] = field(default_factory=list)

    def execute(self, request: PreconditionsRequest) -> PreconditionsReport:
        self.requests.append(request)
        return PreconditionsReport(checks=(PreconditionCheck("x", problems=self.problems),))


@dataclass
class _Journal:
    events: list[Event] = field(default_factory=list)

    def append(self, event: Event) -> None:
        self.events.append(event)


@dataclass
class _Runtime:
    vcs: _Vcs = field(default_factory=_Vcs)
    config_hash: str = "config-0"
    spec_source: _SpecSource = field(default_factory=_SpecSource)
    preconditions: _Preconditions = field(default_factory=_Preconditions)
    event_log: _Journal = field(default_factory=_Journal)
    clock: FixedClock = field(default_factory=FixedClock)
    graph_context: SimpleNamespace = field(
        default_factory=lambda: SimpleNamespace(paths_driver=["tests/acceptance/"])
    )

    def as_bootstrap(self) -> BootstrapResult:
        return cast(BootstrapResult, self)

    def stops(self) -> list[dict[str, object]]:
        return [e.data for e in self.event_log.events if e.type == "lictor.run.stopped.v1"]


def _anchor(**changes: str) -> dict[str, str]:
    return {"head": HEAD, "seal": "", "repo": REPO, "format": ANCHOR_FORMAT, **changes}


def _values(**changes: Any) -> dict[str, Any]:
    return {"anchor": _anchor(), "config_hash": "config-0", **changes}


def _refusal(
    runtime: _Runtime, root: Path, values: dict[str, Any], pending: str | None = None
) -> str | None:
    return cli._integrity_refusal(runtime.as_bootstrap(), root, values, pending)


def test_a_run_that_nothing_touched_resumes(tmp_path: Path) -> None:
    assert _refusal(_Runtime(), tmp_path, _values()) is None


def test_a_run_not_yet_anchored_compares_nothing(tmp_path: Path) -> None:
    """Before `preflight` there is no anchor: whoever started the run, nothing to compare."""
    runtime = _Runtime(vcs=_Vcs(head=MOVED, repo="otro"))

    assert _refusal(runtime, tmp_path, {"anchor": {}}) is None


def test_an_anchor_from_an_older_version_is_not_read_as_tampering(tmp_path: Path) -> None:
    runtime = _Runtime(vcs=_Vcs(repo="otro"))
    anchor = _anchor()
    del anchor["format"]

    reason = _refusal(runtime, tmp_path, _values(anchor=anchor))

    assert reason is not None
    assert "versión anterior de Lictor" in reason
    assert "`lictor run --new`" in reason
    assert "fuiste tú" not in reason


def test_changed_control_files_come_first_whatever_else_changed(tmp_path: Path) -> None:
    """They are read without git: nothing that runs git may go before them."""
    runtime = _Runtime(vcs=_Vcs(repo="otro", head=MOVED, changed=["x.py"]))
    runtime.config_hash = "config-1"

    reason = _refusal(runtime, tmp_path, _values())

    assert reason is not None
    assert reason.startswith("cambiaron los ficheros de control de .git")
    assert cli._UNDO_OR_START_OVER in reason


def test_a_changed_configuration_refuses_before_the_repository(tmp_path: Path) -> None:
    runtime = _Runtime(vcs=_Vcs(head=MOVED))
    runtime.config_hash = "config-1"

    reason = _refusal(runtime, tmp_path, _values())

    assert reason is not None
    assert reason.startswith("la configuración cambió a mitad de ejecución")


def test_a_moved_head_is_refused_naming_both_commits(tmp_path: Path) -> None:
    reason = _refusal(_Runtime(vcs=_Vcs(head=MOVED)), tmp_path, _values())

    assert reason == (
        f"HEAD está en {MOVED[:12]} y la ejecución lo dejó en {HEAD[:12]}. "
        f"{cli._UNDO_OR_START_OVER}"
    )


def test_the_last_verified_candidate_is_accepted_as_head(tmp_path: Path) -> None:
    """A kill between advancing the branch and checkpointing leaves HEAD on the candidate."""
    runtime = _Runtime(vcs=_Vcs(head=VERIFIED))
    verified = _values(last_verify={"candidate": {"sha": VERIFIED}})

    assert _refusal(runtime, tmp_path, verified) is None
    assert _refusal(runtime, tmp_path, _values(last_verify={"candidate": None})) is not None


@pytest.mark.parametrize(
    ("pending", "parent", "tree", "accepted"),
    [
        ("red_check", HEAD, TREE, True),
        ("implement", HEAD, TREE, False),
        ("red_check", VERIFIED, TREE, False),
        ("red_check", HEAD, OTHER_TREE, False),
    ],
)
def test_a_registration_on_the_anchored_head_is_accepted_only_with_red_check_pending(
    tmp_path: Path, pending: str, parent: str, tree: str, accepted: bool
) -> None:
    vcs = _Vcs(head=MOVED, parents={MOVED: parent}, trees={MOVED: tree})

    reason = _refusal(_Runtime(vcs=vcs), tmp_path, _values(), pending)

    assert (reason is None) is accepted


@pytest.mark.parametrize(("sealed", "accepted"), [(False, True), (True, False)])
def test_the_drivers_commit_is_accepted_only_before_sealing(
    tmp_path: Path, sealed: bool, accepted: bool
) -> None:
    """The driver commits inside its node: the checkpoint only knows the tree it was built from."""
    vcs = _Vcs(head=MOVED, trees={MOVED: OTHER_TREE})
    values = _values(attempt_tree=OTHER_TREE, seal_ok=sealed)

    reason = _refusal(_Runtime(vcs=vcs), tmp_path, values)

    assert (reason is None) is accepted


def test_a_driver_tree_other_than_the_attempts_is_refused(tmp_path: Path) -> None:
    vcs = _Vcs(head=MOVED, trees={MOVED: OTHER_TREE})

    assert _refusal(_Runtime(vcs=vcs), tmp_path, _values(attempt_tree=TREE)) is not None


def test_a_seal_file_other_than_the_anchored_one_is_refused(tmp_path: Path) -> None:
    seal = tmp_path / ".lictor" / "seal.json"
    seal.parent.mkdir()
    seal.write_text('{"files": {}}', encoding="utf-8")
    values = _values(anchor=_anchor(seal=hash_file(seal)))
    assert _refusal(_Runtime(), tmp_path, values) is None

    seal.write_text('{"files": {"x": "y"}}', encoding="utf-8")
    reason = _refusal(_Runtime(), tmp_path, values)

    assert reason is not None
    assert ".lictor/seal.json no es el que escribió la ejecución" in reason


def test_a_sealed_spec_or_driver_that_changed_is_refused(tmp_path: Path) -> None:
    runtime = _Runtime(spec_source=_SpecSource(seal_holds=False))

    assert _refusal(runtime, tmp_path, _values(seal_ok=False)) is None
    reason = _refusal(runtime, tmp_path, _values(seal_ok=True))

    assert reason is not None
    assert reason.startswith("la especificación o el driver cambiaron a mitad de ejecución")


def test_uncommitted_changes_are_refused_naming_them(tmp_path: Path) -> None:
    runtime = _Runtime(vcs=_Vcs(changed=["src/a.py", "notas.txt"]))

    reason = _refusal(runtime, tmp_path, _values())

    assert reason is not None
    assert reason.startswith("hay cambios sin confirmar: src/a.py, notas.txt.")


def _snapshot(values: dict[str, Any], *nodes: str, interrupts: Sequence[Any] = ()) -> Any:
    tasks = [SimpleNamespace(interrupts=list(interrupts))] if interrupts else []
    return SimpleNamespace(values=values, next=tuple(nodes), tasks=tasks)


def test_the_resume_guard_refuses_with_10_and_says_why_in_the_journal(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime = _Runtime(vcs=_Vcs(head=MOVED))

    code = cli._check_resume_guard(runtime.as_bootstrap(), tmp_path, "r1", _snapshot(_values()))

    assert code == 10
    [stop] = runtime.stops()
    assert stop["exit_code"] == 10
    assert str(stop["reason"]).startswith("HEAD está en")
    assert capsys.readouterr().err.startswith("Error: HEAD está en")
    assert runtime.preconditions.requests == [], "a refused run checks nothing else"


def test_the_resume_guard_passes_the_pending_node_to_the_anchor(tmp_path: Path) -> None:
    vcs = _Vcs(head=MOVED, parents={MOVED: HEAD})
    runtime = _Runtime(vcs=vcs)

    snapshot = _snapshot(_values(), "red_check")

    assert cli._check_resume_guard(runtime.as_bootstrap(), tmp_path, "r1", snapshot) is None


@pytest.mark.parametrize(("kind", "code"), [("environment", 40), ("config", 10)])
def test_the_resume_guard_refuses_on_a_precondition_by_who_has_to_fix_it(
    tmp_path: Path, kind: Any, code: int
) -> None:
    runtime = _Runtime(preconditions=_Preconditions((PreconditionProblem(kind, "falta git"),)))

    got = cli._check_resume_guard(runtime.as_bootstrap(), tmp_path, "r1", _snapshot(_values()))

    assert got == code
    assert runtime.stops() == [{"exit_code": code, "reason": "no se puede reanudar: falta git"}]


def test_the_resume_guard_checks_preconditions_without_the_tree_or_a_new_environment(
    tmp_path: Path,
) -> None:
    """The tree was checked with what a crash may leave; the environment is the run's own."""
    runtime = _Runtime()

    assert cli._check_resume_guard(runtime.as_bootstrap(), tmp_path, "r1", _snapshot({})) is None

    [request] = runtime.preconditions.requests
    assert (request.run_id, request.clean_tree, request.environment) == ("r1", False, "none")
    # Only the `attempt` controls' programs: one `check` uses can't stop a resume (ADR-0007).
    assert request.check_stage is False
    assert runtime.stops() == []


def _paused(reason: str | None, detail: str = "") -> Any:
    value = {"reason": reason, "detail": detail} if reason else {"detail": detail}
    return _snapshot({}, "pause", interrupts=[SimpleNamespace(value=value)])


def test_a_run_without_interrupts_has_no_pause_code() -> None:
    runtime = _Runtime()

    assert cli._paused_exit_code(runtime.as_bootstrap(), "r1", _snapshot({}, "verify")) is None
    assert runtime.stops() == []


@pytest.mark.parametrize(
    ("reason", "code", "says"),
    [
        ("branch_blocked", 10, "Ejecución detenida antes de avanzar la rama"),
        ("agent_unavailable", 40, "el agente no está disponible"),
        ("rate_limited", 50, "pausada por límite de cuota"),
        (None, 50, "pausada por límite de cuota"),
    ],
)
def test_a_paused_run_exits_by_why_it_stopped_and_records_it(
    reason: str | None, code: int, says: str, capsys: pytest.CaptureFixture[str]
) -> None:
    runtime = _Runtime()

    got = cli._paused_exit_code(runtime.as_bootstrap(), "r1", _paused(reason, "src/a.py cambió"))

    assert got == code
    [stop] = runtime.stops()
    assert stop["exit_code"] == code
    assert says in str(stop["reason"])
    err = capsys.readouterr().err
    assert says in err
    assert "src/a.py cambió" in err


def test_a_blocked_branch_keeps_its_detail_in_the_recorded_reason() -> None:
    runtime = _Runtime()

    cli._paused_exit_code(runtime.as_bootstrap(), "r1", _paused("branch_blocked", "README.md"))

    [stop] = runtime.stops()
    assert "README.md" in str(stop["reason"])
    assert "lo verificado se conserva" in str(stop["reason"])
