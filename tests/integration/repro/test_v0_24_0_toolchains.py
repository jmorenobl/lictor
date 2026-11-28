"""The stubs' impostor of a profile without a plugin, in Go and Node (ADR-0021).

The driver declares each stub's constants on its line; Lictor runs the acceptance with each of
them and rejects a test that passes with all of them. These were the basic level's declared false
greens until 0.24.0: a test that asserts nothing, and one whose assertion holds whatever the code
returns. Each case's oracle runs the toolchain itself on every green, without Lictor.
"""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from lictor.cli import app
from lictor.domain.model import AgentTask
from tests.integration.repro.harness import (
    Agent,
    Outcome,
    events_of,
    git,
    green_ids,
    require,
    run_lictor,
    write,
)
from tests.integration.repro.toolchains import (
    GO_DRIVER_PATH,
    GO_IMPL,
    GO_LENIENT,
    GO_STUB,
    NODE_DRIVER,
    NODE_DRIVER_PATH,
    NODE_IMPL,
    NODE_STUB,
    go_driver,
    go_false_greens,
    go_project,
    node_false_greens,
    node_project,
)

pytestmark = pytest.mark.toolchain


def _driver(*sources: str, stub: str = GO_STUB) -> object:
    """A driver that writes `sources[attempt - 1]`, the last one from then on."""

    def driver(task: AgentTask, attempt: int = 1) -> None:
        write(task.cwd, "nif/nif.go", stub)
        write(task.cwd, GO_DRIVER_PATH, sources[min(attempt, len(sources)) - 1])

    return driver


def _impl(source: str = GO_IMPL) -> object:
    def impl(task: AgentTask, _scenario_id: str, _attempt: int = 1) -> None:
        write(task.cwd, "nif/nif.go", source)

    return impl


def _rejections(root: Path) -> list[str]:
    return [e["data"]["reason"] for e in events_of(root, "driver.rejected")]


@pytest.mark.parametrize("check", ["vacuo", "debil"])
def test_go_a_test_that_passes_with_every_constant_is_rejected_and_its_retry_is_green(
    tmp_path: Path, check: str
) -> None:
    """The basic level's false green until 0.24.0: NIF-002 asserts nothing, or nothing that can
    fail, and code that ignores the letter passed it."""
    root = go_project(tmp_path / "nif")
    agent = Agent(
        driver=_driver(go_driver(second=check), go_driver()),
        impl=_impl(GO_LENIENT),  # type: ignore[arg-type]
    )

    outcome = run_lictor(root, agent)

    assert _rejections(root) == ["impostor"]
    told = agent.prompts["driver"][1]
    assert "El test Test_NIF_002 del escenario NIF-002 pasa también" in told
    assert "return false" not in told.split("El intento anterior falló")[1]
    # The retry compares: the lenient code meets NIF-001 alone, so NIF-002 is never a green.
    assert outcome.exit_code != 0
    assert "NIF-002" not in green_ids(root)
    assert go_false_greens(root) == []
    report = (root / ".lictor" / "report.md").read_text(encoding="utf-8")
    assert "Los stubs de un escenario en cuarentena siguen en la rama con la marca" in report


def test_go_a_right_driver_is_sealed_at_once_and_reaches_green(tmp_path: Path) -> None:
    root = go_project(tmp_path / "nif")
    agent = Agent(driver=_driver(go_driver()), impl=_impl())  # type: ignore[arg-type]

    assert run_lictor(root, agent) == Outcome(0)

    assert _rejections(root) == []
    (sealed,) = events_of(root, "driver.sealed")
    assert sealed["data"]["stub_impostors"] == 1
    assert sealed["data"]["stub_unchecked"] == []
    assert green_ids(root) == ["NIF-001", "NIF-002"]
    assert go_false_greens(root) == []
    (measured,) = events_of(root, "driver.measured")
    declared = measured["data"]["declared"]
    assert declared["NIF-001"] == {
        "by_variant": ["falla", "pasa"],
        "outcome": "probado",
        "verdict": "aceptado",
    }
    phases = sealed["data"]["phases"]
    assert phases["stubs"]["runs"] == 2
    report = (root / ".lictor" / "report.md").read_text(encoding="utf-8")
    assert "| Escenario | Rojo | Con cada constante | Regla básica |" in report
    assert " | falla, pasa | aceptado |" in report


FEATURE_LETRA = """# language: es
Característica: Letra

  @LET-001
  Escenario: La letra de un número
    Dado el número "12345678"
    Cuando pido su letra
    Entonces la letra es "Z"
"""
LETRA_STUB = (
    "\n// Letra da la letra que toca a un número de ocho cifras.\n"
    "func Letra(numero string) string {\n"
    '\tpanic("lictor: no implementado") // lictor-impostor: return "a" | return "b"\n'
    "}\n"
)
WEAK_LETRA = (
    "\nfunc Test_LET_001(t *testing.T) {\n\tdefer enEscenario(t)\n"
    '\tif got := nif.Letra("12345678"); got == "" {\n\t\tt.Fatal("sin letra")\n\t}\n}\n'
)


def test_go_a_weak_test_of_a_string_is_rejected_with_constants_that_arent_empty(
    tmp_path: Path,
) -> None:
    root = go_project(tmp_path / "nif")
    write(root, "features/letra.feature", FEATURE_LETRA)
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "test: la letra")
    agent = Agent(driver=_driver(go_driver(extra=WEAK_LETRA), stub=GO_STUB + LETRA_STUB))

    assert run_lictor(root, agent) == Outcome(10)

    assert "El test Test_LET_001 del escenario LET-001 pasa también" in agent.prompts["driver"][1]
    final = events_of(root, "driver.rejected")[-1]["data"]["error"]
    assert "solo pide que haya un resultado" in final
    assert green_ids(root) == []


def test_go_a_constant_that_doesnt_compile_reaches_the_retry_with_the_compiler(
    tmp_path: Path,
) -> None:
    root = go_project(tmp_path / "nif")
    broken = GO_STUB.replace("return true", 'return "sí"')

    def driver(task: AgentTask, attempt: int = 1) -> None:
        write(task.cwd, "nif/nif.go", broken if attempt == 1 else GO_STUB)
        write(task.cwd, GO_DRIVER_PATH, go_driver())

    agent = Agent(driver=driver, impl=_impl())  # type: ignore[arg-type]

    assert run_lictor(root, agent) == Outcome(0)

    told = agent.prompts["driver"][1]
    assert "Con la segunda constante de cada stub no se pudo juzgar la aceptación" in told
    assert "cannot use" in told
    assert green_ids(root) == ["NIF-001", "NIF-002"]
    assert go_false_greens(root) == []


def test_go_a_stub_line_without_its_constants_is_named_before_anything_runs(
    tmp_path: Path,
) -> None:
    root = go_project(tmp_path / "nif")
    bare = GO_STUB.replace(" // lictor-impostor: return false | return true", "")
    agent = Agent(driver=_driver(go_driver(), stub=bare))

    assert run_lictor(root, agent) == Outcome(10)

    assert (
        "La línea nif/nif.go:5 lleva «lictor: no implementado» sin «lictor-impostor:»"
        in (agent.prompts["driver"][1])
    )
    assert not list((root / ".lictor" / "runs").glob("*/driver/001-red/impostor-1"))


def test_go_a_driver_without_the_mark_is_sealed_unchecked_and_says_so(tmp_path: Path) -> None:
    """A recipe before 0.24.0: the impostor can't run, so nothing was checked, and every report
    says so instead of the level alone."""
    root = go_project(tmp_path / "nif")
    old = GO_STUB.replace(
        'panic("lictor: no implementado") // lictor-impostor: return false | return true',
        'panic("no implementado")',
    )
    agent = Agent(driver=_driver(go_driver(), stub=old), impl=_impl())  # type: ignore[arg-type]

    assert run_lictor(root, agent) == Outcome(0)

    (sealed,) = events_of(root, "driver.sealed")
    assert sealed["data"]["stub_impostors"] == 0
    assert sealed["data"]["stub_unchecked"] == ["NIF-001", "NIF-002"]
    assert sealed["data"]["unchecked_why"] == "sin_marca_con_codigo"
    report = (root / ".lictor" / "report.md").read_text(encoding="utf-8")
    assert "el driver no declaró ningún stub" in report
    status = CliRunner().invoke(app, ["status", "--cwd", str(root)]).output
    assert "2 tests del driver aceptados sin comprobar" in status


def test_node_a_test_that_asserts_nothing_is_rejected(tmp_path: Path) -> None:
    root = node_project(tmp_path / "iva")
    vacuous = (
        'import { test } from "node:test";\nimport { general } from "../iva/index.js";\n\n'
        'test("IVA-001 IVA general", () => {\n  general(100);\n});\n'
    )

    def driver(task: AgentTask, attempt: int = 1) -> None:
        write(task.cwd, "iva/index.js", NODE_STUB)
        write(task.cwd, NODE_DRIVER_PATH, vacuous if attempt == 1 else NODE_DRIVER)

    def impl(task: AgentTask, _scenario_id: str, _attempt: int = 1) -> None:
        write(task.cwd, "iva/index.js", NODE_IMPL)

    agent = Agent(driver=driver, impl=impl)

    assert run_lictor(root, agent) == Outcome(0)

    assert _rejections(root) == ["impostor"]
    assert (
        "El test IVA-001 IVA general del escenario IVA-001 pasa también"
        in (agent.prompts["driver"][1])
    )
    assert green_ids(root) == ["IVA-001"]
    assert node_false_greens(root) == []


DOC_GO = "// Package nif valida NIF.\npackage nif\n\n// Longitud de un NIF.\nconst Longitud = 9\n"


def test_go_a_paused_run_whose_code_is_all_excluded_is_refused_on_resume(tmp_path: Path) -> None:
    """[M6]: without code at the start nothing is judged; the sealed driver brings the first, and
    `code_exclude` excludes it, so resuming the paused run must stop before the agent again."""
    root = go_project(tmp_path / "nif", code_exclude=["*.go"], controls=[])
    for path in (root / "nif").iterdir():
        path.unlink()
    write(root, "otro/otro.go", "// Package otro da un paquete al módulo.\npackage otro\n")
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", "chore: sin código en nif")

    def driver(task: AgentTask, _attempt: int = 1) -> None:
        write(task.cwd, "nif/doc.go", DOC_GO)
        write(task.cwd, "nif/nif.go", GO_STUB)
        write(task.cwd, GO_DRIVER_PATH, go_driver())

    def limited(task: AgentTask, _scenario_id: str, _attempt: int = 1) -> str | None:
        return "rate_limited"

    first = Agent(driver=driver, impl=limited)  # type: ignore[arg-type]
    require(run_lictor(root, first) == Outcome(50), "el primer run debía pausar tras sellar")
    second = Agent(driver=driver, impl=_impl())  # type: ignore[arg-type]

    assert run_lictor(root, second) == Outcome(10)

    assert second.calls == []
    stops = [e["data"]["reason"] for e in events_of(root, "run.stopped")]
    assert any("code_exclude (*.go) excluye todos los ficheros" in reason for reason in stops)


def test_go_doctor_keeps_its_environment_until_told_otherwise(tmp_path: Path) -> None:
    """G5: the module and build caches stay from one `doctor` to the next; `--fresh` drops them."""
    root = go_project(tmp_path / "nif")

    first = CliRunner().invoke(app, ["doctor", "--cwd", str(root)]).output
    again = CliRunner().invoke(app, ["doctor", "--cwd", str(root)]).output
    fresh = CliRunner().invoke(app, ["doctor", "--fresh", "--cwd", str(root)]).output

    assert "reutilizado de un doctor anterior" not in first
    assert "✓ Entorno de verificación: sin prepare: no se construye ni se vigila; reutilizado" in (
        again
    )
    assert "reutilizado" not in fresh
    assert (root / ".lictor" / "doctor-env" / "env" / "gocache").is_dir()


SKIP_002 = (
    "func Test_NIF_002(t *testing.T) {\n\tdefer enEscenario(t)\n"
    '\tif got := nif.Valido("12345678A"); got {\n\t\tt.Skip("de momento no")\n\t}\n}\n'
)


def test_go_a_test_that_skips_with_a_constant_is_rejected_not_sealed(tmp_path: Path) -> None:
    """Fable's B1 of the PR: a case skipped with one constant read as «that variant doesn't
    count», which left NIF-002 unjudged with no reason, and the driver sealed a false green."""
    root = go_project(tmp_path / "nif", banned_patterns=["//\\\\s*nolint"])
    header, _, rest = go_driver().partition("func Test_NIF_002")
    skipping = header + SKIP_002 + rest[rest.index("\nfunc enEscenario") :]
    agent = Agent(driver=_driver(skipping), impl=_impl(GO_LENIENT))  # type: ignore[arg-type]

    run_lictor(root, agent)

    assert events_of(root, "driver.sealed") == []
    assert "El test Test_NIF_002 del escenario NIF-002 pasa también" in agent.prompts["driver"][1]
    assert green_ids(root) == []


VET_BAD = "\nfunc ayuda() {\n\tx := 1\n\tx = x\n\t_ = x\n}\n"


def test_go_one_rejection_says_both_the_vacuous_test_and_the_vet_warning(tmp_path: Path) -> None:
    """G3 and I6: `go vet` reads the driver; its warning and the impostor's go in one rejection."""
    root = go_project(tmp_path / "nif")
    bad = go_driver(second="vacuo", extra=VET_BAD)
    agent = Agent(driver=_driver(bad, go_driver()), impl=_impl())  # type: ignore[arg-type]

    assert run_lictor(root, agent) == Outcome(0)

    assert _rejections(root) == ["impostor"]
    told = agent.prompts["driver"][1]
    assert "El test Test_NIF_002 del escenario NIF-002 pasa también" in told
    assert "El commit del driver no pasa el control 'vet'" in told
    assert green_ids(root) == ["NIF-001", "NIF-002"]
    assert go_false_greens(root) == []


def test_go_constants_that_dont_compile_at_all_are_told_without_blaming_each_test(
    tmp_path: Path,
) -> None:
    """V1 of Fable's second look: with no variant that ran, no test «passes with the constants
    that ran»; what the agent hears is why each variant couldn't be judged."""
    root = go_project(tmp_path / "nif")
    broken = GO_STUB.replace("return false | return true", 'return "no" | return "sí"')
    agent = Agent(driver=_driver(go_driver(), stub=broken))

    assert run_lictor(root, agent) == Outcome(10)

    told = agent.prompts["driver"][1]
    assert "Con la primera constante de cada stub no se pudo juzgar la aceptación" in told
    assert "Con la segunda constante de cada stub no se pudo juzgar la aceptación" in told
    assert "pasa con las constantes que se pudieron ejecutar" not in told
