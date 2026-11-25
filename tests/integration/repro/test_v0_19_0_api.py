"""0.19.0 (ADR-0017, F80): the public API is compared with the commit the run started from.

With the real profile and `uv`: the `api` control measures each verification against the run's
base, measured once in a clean clone of it; in `measure` a green that breaks it passes with a
warning and a section of `report.md`, in `block` the attempt fails naming what broke, what the
person accepts in `api.accepted` doesn't, and a refactor that breaks what its green didn't is
discarded at `L0-regresión`.

Only what the base had counts: the template's `iva.hello`, which the driver's stubs replace, and a
module `iva.extra` committed before the run. What the run itself creates (`iva.general`) is new.
"""

from pathlib import Path

from lictor.application.ports import AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    MODULE_PATH,
    Agent,
    Outcome,
    events_of,
    false_greens,
    git,
    good_impl,
    green_ids,
    run_lictor,
    with_refactor,
    write,
)

ALL = ["IVA-001", "IVA-002", "IVA-003"]
EXTRA_PATH = "src/iva/extra.py"
EXTRA = 'def tasa(importe: float, pais: str = "ES") -> float:\n    return importe\n'
# The same function without its second parameter: a call that named `pais` no longer works.
BROKEN = "def tasa(importe: float) -> float:\n    return importe\n"
# The driver's stubs replace the template's `hello`: that break is the driver's, and accepted here.
DRIVER_BREAK = "iva.hello"


def _project(make_project: ProjectFactory, **config: object) -> Path:
    root = make_project(config_changes=config or None)
    write(root, EXTRA_PATH, EXTRA)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "feat: tasa por país")
    return root


def _breaking(task: AgentTask, scenario_id: str, attempt: int) -> None:
    good_impl(task, scenario_id)
    if attempt == 1:
        write(task.cwd, EXTRA_PATH, BROKEN)


def _api_records(root: Path) -> list[dict[str, object]]:
    return [
        e["data"]["controls"]["api"]
        for e in events_of(root, "verify.finished")
        if "api" in (e["data"].get("controls") or {})
    ]


def _broken(record: dict[str, object], key: str = "broken") -> set[tuple[str, str]]:
    api = record["api"]
    assert isinstance(api, dict)
    return {(b["name"], b["change"]) for b in api[key]}


def test_a_green_that_breaks_the_api_passes_in_measure_and_the_report_names_it(
    make_project: ProjectFactory,
) -> None:
    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        good_impl(task, scenario_id)
        write(task.cwd, EXTRA_PATH, BROKEN)

    root = _project(make_project)
    base = git(root, "rev-parse", "HEAD").strip()

    assert run_lictor(root, Agent(impl=impl)) == Outcome(0)

    assert green_ids(root) == ALL
    records = _api_records(root)
    assert records
    assert all(r["mode"] == "measure" for r in records)
    last = records[-1]["api"]
    assert isinstance(last, dict)
    assert last["base"] == base
    assert _broken(records[-1]) == {
        ("iva.extra.tasa", "param_removed"),
        (DRIVER_BREAK, "removed"),
    }
    # The base is measured once, in the first verification that ran the control.
    assert len(list((root / ".lictor" / "runs").rglob("api-base.json"))) == 1
    report = (root / ".lictor" / "report.md").read_text(encoding="utf-8")
    section = report.split("## API pública")[1].split("\n## ")[0]
    assert "- `iva.extra.tasa`: pierde el parámetro pais" in section
    assert false_greens(root) == []


def test_a_blocking_api_control_fails_the_attempt_and_the_retry_hears_what_broke(
    make_project: ProjectFactory,
) -> None:
    root = _project(
        make_project,
        controls=[{"id": "api", "mode": "block"}],
        api={"accepted": [DRIVER_BREAK]},
    )
    agent = Agent(impl=_breaking)

    assert run_lictor(root, agent) == Outcome(0)

    assert green_ids(root) == ALL
    failed = [e for e in events_of(root, "verify.finished") if not e["data"]["ok"]]
    assert [(e["scenarioid"], e["data"]["failed_at"]) for e in failed] == [
        (sid, "api") for sid in ALL
    ]
    retry = agent.prompts["IVA-001"][1]
    assert "`iva.extra.tasa`: pierde el parámetro pais" in retry
    assert "LICTOR: SPEC_CONFLICT" in retry
    assert "No quites ni cambies de forma incompatible la API pública" in retry
    assert _broken(_api_records(root)[-1], "accepted") == {(DRIVER_BREAK, "removed")}
    assert false_greens(root) == []


def test_what_the_person_accepts_to_break_doesnt_block(make_project: ProjectFactory) -> None:
    root = _project(
        make_project,
        controls=[{"id": "api", "mode": "block"}],
        api={"accepted": [DRIVER_BREAK, "iva.extra"]},
    )

    assert run_lictor(root, Agent(impl=_breaking)) == Outcome(0)

    assert green_ids(root) == ALL
    assert all(e["data"]["ok"] for e in events_of(root, "verify.finished"))
    first = _api_records(root)[0]
    assert _broken(first) == set()
    assert _broken(first, "accepted") == {
        ("iva.extra.tasa", "param_removed"),
        (DRIVER_BREAK, "removed"),
    }


@with_refactor
def test_a_refactor_that_breaks_what_its_green_didnt_is_discarded(
    make_project: ProjectFactory,
) -> None:
    def refactor(task: AgentTask, _scenario_id: str, _attempt: int) -> None:
        write(task.cwd, EXTRA_PATH, BROKEN)

    root = _project(make_project)

    assert run_lictor(root, Agent(impl=good_impl, refactor=refactor)) == Outcome(0)

    assert green_ids(root) == ALL
    outcomes = {
        e["scenarioid"]: (e["data"]["outcome"], e["data"]["failed_at"])
        for e in events_of(root, "scenario.refactored")
    }
    assert outcomes == dict.fromkeys(ALL, ("discarded", "L0-regresión"))
    reasons = [
        line
        for path in (root / ".lictor" / "runs").rglob("*-refactor-verify*/error.txt")
        for line in path.read_text(encoding="utf-8").splitlines()
    ]
    assert any("api: rompe `iva.extra.tasa`: pierde el parámetro pais" in r for r in reasons)
    assert (root / EXTRA_PATH).read_text(encoding="utf-8") == EXTRA
    assert (root / MODULE_PATH).is_file()
    assert false_greens(root) == []
