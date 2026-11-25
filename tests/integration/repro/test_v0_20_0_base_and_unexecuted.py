"""0.20.0: `api.base` fixes the base of the public API, and a run tells what no control executed.

With the real profile and `uv`. H4: a work in several runs is compared with where it left the
branch it will be integrated into, not with the start of each run; what the start already broke
is told once, at the start. H2: once a run moves its branch, `report.md` lists what it changed
that no control executed, by the start contract's rule over the final tree.
"""

from lictor.application.ports import AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    Agent,
    Outcome,
    events_of,
    false_greens,
    git,
    good_impl,
    green_ids,
    run_lictor,
    write,
)

ALL = ["IVA-001", "IVA-002", "IVA-003"]
EXTRA_PATH = "src/iva/extra.py"
EXTRA = 'def tasa(importe: float, pais: str = "ES") -> float:\n    return importe\n'
BROKEN = "def tasa(importe: float) -> float:\n    return importe\n"


def test_a_second_run_of_a_work_is_compared_where_the_work_left_its_branch(
    make_project: ProjectFactory,
) -> None:
    # The template's branch is `master`: the work leaves it.
    root = make_project(config_changes={"api": {"base": "master"}})
    write(root, EXTRA_PATH, EXTRA)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "feat: tasa por país")
    fork = git(root, "rev-parse", "HEAD").strip()
    # A first run of the work, already integrated in its branch, broke `tasa`.
    git(root, "checkout", "-q", "-b", "trabajo")
    write(root, EXTRA_PATH, BROKEN)
    git(root, "commit", "-qam", "feat: tasa sin país")

    assert run_lictor(root, Agent()) == Outcome(0)

    assert green_ids(root) == ALL
    (started,) = events_of(root, "run.started")
    assert started["data"]["api_start"] == {"base": fork, "broken": ["iva.extra.tasa"]}
    bases = {
        e["data"]["controls"]["api"]["api"]["base"]
        for e in events_of(root, "verify.finished")
        if "api" in (e["data"].get("controls") or {})
    }
    assert bases == {fork}
    assert false_greens(root) == []


def test_the_report_lists_what_the_run_changed_that_no_control_executed(
    make_project: ProjectFactory,
) -> None:
    def impl(task: AgentTask, scenario_id: str, _attempt: int) -> None:
        good_impl(task, scenario_id)
        write(task.cwd, "src/iva/tarifas.json", '{"general": 21}\n')
        write(task.cwd, "tests/unit/test_tarifas.py", "def test_tarifas():\n    assert True\n")

    root = make_project()
    start = git(root, "rev-parse", "HEAD").strip()

    assert run_lictor(root, Agent(impl=impl)) == Outcome(0)

    (found,) = events_of(root, "run.unexecuted")
    assert found["data"]["started_at"] == start
    kinds = {f["path"]: f["kind"] for f in found["data"]["files"]}
    assert kinds["src/iva/tarifas.json"] == "no_es_codigo"
    # The light project drops `unit`: no control runs that test.
    assert kinds["tests/unit/test_tarifas.py"] == "sin_control"
    report = (root / ".lictor" / "report.md").read_text(encoding="utf-8")
    section = report.split("## Cambios que ningún control ejecutó")[1].split("\n## ")[0]
    assert "`src/iva/tarifas.json`" in section
    assert "`tests/unit/test_tarifas.py`" in section
