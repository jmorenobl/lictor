import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from lictor.adapters.config import loader
from lictor.adapters.config.loader import load_profile
from lictor.cli import app

runner = CliRunner()

DEMO_IVA_DIR = Path(__file__).resolve().parents[2] / "examples" / "demo-iva"

DRIVER_FILES = {
    "tests/acceptance/test_iva.py": (
        "import iva\n\n"
        "def test_iva_001():\n    iva.calcular_iva_general(100)\n\n"
        "def test_iva_002():\n    iva.calcular_iva_reducido(100)\n\n"
        "def test_iva_003():\n    iva.calcular_iva_negativo(-5)\n"
    ),
    "src/iva/__init__.py": (
        "def calcular_iva_general(importe: float) -> float:\n"
        "    raise NotImplementedError\n\n"
        "def calcular_iva_reducido(importe: float) -> float:\n"
        "    raise NotImplementedError\n\n"
        "def calcular_iva_negativo(importe: float) -> None:\n"
        "    raise NotImplementedError\n"
    ),
}

IVA_001_CODE = (
    "def calcular_iva_general(importe: float) -> float:\n"
    "    return round(importe * 1.21, 2)\n\n"
    "def calcular_iva_reducido(importe: float) -> float:\n"
    "    raise NotImplementedError\n\n"
    "def calcular_iva_negativo(importe: float) -> None:\n"
    "    raise NotImplementedError\n"
)

IVA_002_CODE = (
    "def calcular_iva_general(importe: float) -> float:\n"
    "    return round(importe * 1.21, 2)\n\n"
    "def calcular_iva_reducido(importe: float) -> float:\n"
    "    return round(importe * 1.10, 2)\n\n"
    "def calcular_iva_negativo(importe: float) -> None:\n"
    "    raise NotImplementedError\n"
)

IVA_003_CODE = (
    "def calcular_iva_general(importe: float) -> float:\n"
    "    return round(importe * 1.21, 2)\n\n"
    "def calcular_iva_reducido(importe: float) -> float:\n"
    "    return round(importe * 1.10, 2)\n\n"
    "def calcular_iva_negativo(importe: float) -> None:\n"
    "    if importe < 0:\n"
    "        return None\n"
    "    raise AssertionError\n"
)

GOOD_FILES = {
    "driver": DRIVER_FILES,
    "IVA-001": {"src/iva/__init__.py": IVA_001_CODE},
    "IVA-002": {"src/iva/__init__.py": IVA_002_CODE},
    "IVA-003": {"src/iva/__init__.py": IVA_003_CODE},
}


@pytest.fixture(autouse=True)
def _profile_without_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """These cycles use the interpreter at hand: the real environment is what `repro` covers."""
    profiles = tmp_path / "perfiles"
    profiles.mkdir()
    profile = load_profile("python-uv").model_dump(mode="json")
    profile |= {
        "name": "sin-entorno",
        "banned_patterns": [],
        "forbidden_paths": [],
        "suppressions": ["# noqa", "# type: ignore"],
        # Without the profile's plugin nothing reports how a test failed or what it executed.
        "oracle": {},
        "verification": {
            "inherit": ["PATH", "HOME", "VIRTUAL_ENV"],
            "env": {"PYTHONDONTWRITEBYTECODE": "1"},
        },
    }
    # Without a command that lists the plugins, no control can depend on one (ADR-0008), and
    # the acceptance can't need one; without an environment there is nothing to survey in.
    for control in profile["controls"]:
        control.pop("when_plugin", None)
    profile["acceptance"]["needs_plugins"] = {}
    profile["survey"] = None
    (profiles / "sin-entorno.json").write_text(json.dumps(profile), encoding="utf-8")
    monkeypatch.setattr(loader, "DEFAULT_PROFILES_DIR", profiles)


def _setup_demo_iva_repo(
    tmp_path: Path,
    fake_config: dict[str, object] | None = None,
    config_changes: dict[str, object] | None = None,
) -> Path:
    project_dir = tmp_path / "demo-iva"
    # Copy essential files from examples/demo-iva
    project_dir.mkdir(parents=True)
    shutil.copytree(DEMO_IVA_DIR / "features", project_dir / "features")
    shutil.copytree(DEMO_IVA_DIR / "src", project_dir / "src")
    shutil.copy(DEMO_IVA_DIR / "pyproject.toml", project_dir / "pyproject.toml")

    # Initialize git repository
    subprocess.run(
        ["git", "init", "-b", "master"], cwd=project_dir, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=project_dir,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=project_dir,
        check=True,
        capture_output=True,
    )

    # Initialize lictor.json with fake backend
    lictor_json = {
        "profile": "sin-entorno@0.2",
        "backend": {
            "kind": "fake",
            "model": "claude-3-5-sonnet-20241022",
            "auth": "subscription",
            "max_turns": 10,
            "timeout_s": 60,
        },
        "limits": {
            "max_attempts": 3,
            "max_driver_attempts": 2,
            "error_max_chars": 4000,
        },
        "paths": {
            "writable": ["src/"],
            "driver": ["tests/acceptance/"],
            "protected": ["features/", "tests/acceptance/", "lictor.json", "pyproject.toml"],
        },
        "acceptance": {
            "run": "python3 -m pytest tests/acceptance -q --junitxml {out}",
        },
        "controls": [
            {"id": c, "enabled": False} for c in ("lint", "format", "types", "unit", "coverage")
        ],
        "agent_tools": [],
        "hints": "demo-iva hints",
        "gitignore": [".venv/", "__pycache__/"],
        **(config_changes or {}),
    }
    (project_dir / "lictor.json").write_text(json.dumps(lictor_json, indent=2), encoding="utf-8")
    (project_dir / ".gitignore").write_text(".lictor/\n__pycache__/\n.venv/\n", encoding="utf-8")

    subprocess.run(["git", "add", "."], cwd=project_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "chore: initial commit"],
        cwd=project_dir,
        check=True,
        capture_output=True,
    )

    # A run works in a linked worktree of its own: these cycles start inside one, as `lictor run`
    # does after creating it, so that the fake executor's options are in the run's `.lictor/`.
    worktree = tmp_path / "demo-iva-run"
    subprocess.run(
        ["git", "worktree", "add", "-q", "-b", "lictor/demo", str(worktree)],
        cwd=project_dir,
        check=True,
        capture_output=True,
    )
    lictor_dir = worktree / ".lictor"
    lictor_dir.mkdir(parents=True, exist_ok=True)
    if fake_config is not None:
        (lictor_dir / "fake_executor.json").write_text(json.dumps(fake_config), encoding="utf-8")
    return worktree


def test_e2e_demo_iva_good_mode(tmp_path: Path) -> None:
    fake_config = {
        "mode": "good",
        "files_to_write": GOOD_FILES,
    }
    repo = _setup_demo_iva_repo(tmp_path, fake_config)

    res = runner.invoke(app, ["run", "--cwd", str(repo)])
    assert res.exit_code == 0

    # Verify 3 commits with Scenario trailers
    log_out = subprocess.run(
        ["git", "log", "--format=%(trailers:key=Scenario,valueonly)"],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()

    scenarios_committed = [s.strip() for s in log_out if s.strip()]
    assert "IVA-001" in scenarios_committed
    assert "IVA-002" in scenarios_committed
    assert "IVA-003" in scenarios_committed

    report_file = repo / ".lictor" / "report.md"
    assert report_file.is_file()
    report_text = report_file.read_text(encoding="utf-8")
    assert "Escenarios en verde**: 3" in report_text


def test_run_tells_each_step_on_stderr_and_leaves_stdout_alone(tmp_path: Path) -> None:
    repo = _setup_demo_iva_repo(tmp_path, {"mode": "good", "files_to_write": GOOD_FILES})

    res = runner.invoke(app, ["run", "--cwd", str(repo)])

    assert res.exit_code == 0, res.output
    lines = res.stderr.splitlines()
    assert lines[:3] == [
        "Entorno de verificación listo.",
        "Escenarios: 3 (0 en verde, 3 pendientes).",
        "driver: escribiendo el driver, intento 1 de 2…",
    ]
    # The seal says how long its red took, and where (F52).
    assert lines[3].startswith("driver: sellado (1 llamada, 0,01 $, rojo en ")
    assert ": aceptación " in lines[3]
    assert "IVA-001: implementando, intento 1 de 3…" in lines
    assert any(line.startswith("IVA-001: verde, intento 1, ") for line in lines)
    # Each implemented green goes through one refactor, which the fake leaves unchanged.
    assert "IVA-001: refactorizando sin cambiar el comportamiento…" in lines
    assert any(line.startswith("IVA-001: refactor sin cambios, nada que mejorar") for line in lines)
    assert lines[-1].startswith("Total: 7 llamadas, ")
    assert lines[-1].endswith("; 3 en verde, 0 en cuarentena, 0 pendientes.")
    assert "Escenarios:" not in res.stdout


def test_status_after_a_run_reads_as_spanish_with_its_counts(tmp_path: Path) -> None:
    repo = _setup_demo_iva_repo(tmp_path, {"mode": "good", "files_to_write": GOOD_FILES})
    assert runner.invoke(app, ["run", "--quiet", "--cwd", str(repo)]).exit_code == 0

    res = runner.invoke(app, ["status", "--cwd", str(repo)])

    assert res.exit_code == 0, res.output
    lines = res.stdout.splitlines()
    assert lines[1] == "Estado: terminada con código 0."
    assert "Verdes: 3 de 3 (3 implementados). En cuarentena: 0. Pendientes: 0." in lines
    assert any(
        line.startswith("Agente: 7 llamadas, ")
        and "(driver: 1 llamada" in line
        and "refactor: 3 llamadas" in line
        for line in lines
    )
    assert "  - IVA-001: verde (implementado)" in lines
    assert "None" not in res.stdout
    payload = json.loads(runner.invoke(app, ["status", "--json", "--cwd", str(repo)]).stdout)
    assert (payload["calls"], payload["driver"]["calls"]) == (7, 1)
    assert payload["refactor"]["calls"] == 3
    assert set(payload["refactor"]["outcomes"].values()) == {"unchanged"}
    assert payload["greens"]["implemented"] == ["IVA-001", "IVA-002", "IVA-003"]


def test_run_quiet_tells_nothing_but_the_outcome(tmp_path: Path) -> None:
    repo = _setup_demo_iva_repo(tmp_path, {"mode": "good", "files_to_write": GOOD_FILES})

    res = runner.invoke(app, ["run", "--quiet", "--cwd", str(repo)])

    assert res.exit_code == 0, res.output
    assert "driver:" not in res.output
    assert "Total:" not in res.output


def test_e2e_demo_iva_fail_mode_quarantines(tmp_path: Path) -> None:
    # Driver is valid, but implementation always fails
    fake_config = {
        "mode": "fail",
        "files_to_write": {
            "driver": DRIVER_FILES,
        },
    }
    repo = _setup_demo_iva_repo(tmp_path, fake_config)

    res = runner.invoke(app, ["run", "--cwd", str(repo)])
    assert res.exit_code == 30

    report_text = (repo / ".lictor" / "report.md").read_text(encoding="utf-8")
    assert "intentos agotados" in report_text


def test_e2e_demo_iva_cheat_mode_reverts_protected_file(tmp_path: Path) -> None:
    # Cheat tries to modify tests/acceptance/test_iva.py during implement
    fake_config = {
        "mode": "cheat",
        "files_to_write": {
            "driver": DRIVER_FILES,
            "cheat:IVA-001": {
                "tests/acceptance/test_iva.py": "def test_iva_001(): pass # cheated\n",
            },
        },
    }
    repo = _setup_demo_iva_repo(tmp_path, fake_config)

    res = runner.invoke(app, ["run", "--cwd", str(repo)])
    assert res.exit_code == 30


def test_e2e_demo_iva_conflict_mode_quarantines_with_exit_11(tmp_path: Path) -> None:
    fake_config = {
        "mode": "conflict",
        "files_to_write": {
            "driver": DRIVER_FILES,
        },
    }
    repo = _setup_demo_iva_repo(tmp_path, fake_config)

    res = runner.invoke(app, ["run", "--cwd", str(repo)])
    assert res.exit_code == 11

    report_text = (repo / ".lictor" / "report.md").read_text(encoding="utf-8")
    assert "spec_conflict" in report_text


def test_e2e_demo_iva_rate_limit_pauses_50_and_resumes(tmp_path: Path) -> None:
    # 1. First run hits rate_limit on IVA-001 -> pause with exit code 50
    fake_config = {
        "mode": "rate_limit",
        "files_to_write": {
            "driver": DRIVER_FILES,
        },
    }
    repo = _setup_demo_iva_repo(tmp_path, fake_config)

    res1 = runner.invoke(app, ["run", "--cwd", str(repo)])
    assert res1.exit_code == 50

    # 2. Update fake config to good mode and re-run -> resumes and exits 0!
    good_config = {
        "mode": "good",
        "files_to_write": GOOD_FILES,
    }
    (repo / ".lictor" / "fake_executor.json").write_text(json.dumps(good_config), encoding="utf-8")

    res2 = runner.invoke(app, ["run", "--cwd", str(repo)])
    assert res2.exit_code == 0


def test_e2e_demo_iva_crash_and_resumes(tmp_path: Path) -> None:
    # 1. Crash on IVA-002
    fake_config = {
        "mode": "crash",
        "crash_on_scenario": "IVA-002",
        "files_to_write": GOOD_FILES,
    }
    repo = _setup_demo_iva_repo(tmp_path, fake_config)

    # First run crashes with exit 1
    res1 = runner.invoke(app, ["run", "--cwd", str(repo)])
    assert res1.exit_code == 1

    # 2. Re-run without crash -> resumes and completes remaining scenarios!
    good_config = {
        "mode": "good",
        "files_to_write": GOOD_FILES,
    }
    (repo / ".lictor" / "fake_executor.json").write_text(json.dumps(good_config), encoding="utf-8")

    res2 = runner.invoke(app, ["run", "--cwd", str(repo)])
    assert res2.exit_code == 0


def test_e2e_demo_iva_manual_feature_edit_mid_run_terminates_with_10(tmp_path: Path) -> None:
    # Pause on IVA-002 via rate_limit
    fake_config = {
        "mode": "good",
        "scenario_modes": {"IVA-002": "rate_limit"},
        "files_to_write": GOOD_FILES,
    }
    repo = _setup_demo_iva_repo(tmp_path, fake_config)

    res1 = runner.invoke(app, ["run", "--cwd", str(repo)])
    assert res1.exit_code == 50

    # User manually edits a .feature file while execution was paused
    feature_file = repo / "features" / "iva.feature"
    feature_file.write_text(
        feature_file.read_text(encoding="utf-8") + "\n# edit\n", encoding="utf-8"
    )

    # Re-running must detect seal mismatch and terminate with 10 without reverting user edit
    res2 = runner.invoke(app, ["run", "--cwd", str(repo)])
    assert res2.exit_code == 10
    assert "# edit" in feature_file.read_text(encoding="utf-8")


# A refactor of IVA-003's code that keeps its behaviour: the rate lives in one helper.
REFACTORED_CODE = (
    "def _con_tipo(importe: float, tipo: float) -> float:\n"
    "    return round(importe * (1 + tipo), 2)\n\n"
    "def calcular_iva_general(importe: float) -> float:\n"
    "    return _con_tipo(importe, 0.21)\n\n"
    "def calcular_iva_reducido(importe: float) -> float:\n"
    "    return _con_tipo(importe, 0.10)\n\n"
    "def calcular_iva_negativo(importe: float) -> None:\n"
    "    if importe < 0:\n"
    "        return None\n"
    "    raise AssertionError\n"
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout


def _refactors(repo: Path) -> dict[str, str]:
    """How each refactor ended, from the journal."""
    lines = (repo / ".lictor" / "journal.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    return {
        e["scenarioid"]: e["data"]["outcome"]
        for e in events
        if e["type"] == "lictor.scenario.refactored.v1"
    }


def test_a_refactor_that_keeps_behaviour_is_its_own_commit(tmp_path: Path) -> None:
    files = {**GOOD_FILES, "refactor:IVA-003": {"src/iva/__init__.py": REFACTORED_CODE}}
    repo = _setup_demo_iva_repo(tmp_path, {"mode": "good", "files_to_write": files})

    res = runner.invoke(app, ["run", "--cwd", str(repo)])

    assert res.exit_code == 0, res.output
    assert _refactors(repo) == {
        "IVA-001": "unchanged",
        "IVA-002": "unchanged",
        "IVA-003": "applied",
    }
    head = _git(repo, "log", "-1", "--format=%s%n%(trailers:key=Scenario,valueonly)").split("\n")
    assert head[0] == "refactor(IVA-003): mejorar el código sin cambiar el comportamiento"
    assert head[1].strip() == ""  # no `Scenario:`: the green stays the scenario's own commit
    green = _git(repo, "log", "-1", "--format=%H", "--grep", "Scenario: IVA-003").strip()
    refactor_of = _git(repo, "log", "-1", "--format=%(trailers:key=Lictor-Refactor-Of,valueonly)")
    assert refactor_of.strip() == green
    assert (repo / "src" / "iva" / "__init__.py").read_text(encoding="utf-8") == REFACTORED_CODE
    report = (repo / ".lictor" / "report.md").read_text(encoding="utf-8")
    assert "- **IVA-003**: aplicado en `" in report.split("## Refactors")[1]


def test_a_refactor_that_breaks_a_green_is_discarded_and_the_green_stays(tmp_path: Path) -> None:
    broken = IVA_002_CODE.replace("return round(importe * 1.21, 2)", "return importe")
    files = {**GOOD_FILES, "refactor:IVA-002": {"src/iva/__init__.py": broken}}
    repo = _setup_demo_iva_repo(tmp_path, {"mode": "good", "files_to_write": files})
    # The driver must check the value for the refactor to break it.
    driver = DRIVER_FILES["tests/acceptance/test_iva.py"].replace(
        "    iva.calcular_iva_general(100)\n", "    assert iva.calcular_iva_general(100) == 121\n"
    )
    files["driver"] = {**DRIVER_FILES, "tests/acceptance/test_iva.py": driver}
    (repo / ".lictor" / "fake_executor.json").write_text(
        json.dumps({"mode": "good", "files_to_write": files}), encoding="utf-8"
    )

    res = runner.invoke(app, ["run", "--cwd", str(repo)])

    assert res.exit_code == 0, res.output
    assert _refactors(repo)["IVA-002"] == "discarded"
    assert "refactor(IVA-002)" not in _git(repo, "log", "--format=%s")
    assert "IVA-002: refactor descartado (falló en acceptance); el verde se queda" in res.stderr
    report = (repo / ".lictor" / "report.md").read_text(encoding="utf-8")
    assert "Escenarios en verde**: 3" in report
    assert "*Ningún escenario en cuarentena.*" in report


def test_a_refactor_that_touches_the_features_is_discarded(tmp_path: Path) -> None:
    feature = (DEMO_IVA_DIR / "features" / "iva.feature").read_text(encoding="utf-8")
    files = {**GOOD_FILES, "refactor:IVA-001": {"features/iva.feature": feature + "\n# otro\n"}}
    repo = _setup_demo_iva_repo(tmp_path, {"mode": "good", "files_to_write": files})

    res = runner.invoke(app, ["run", "--cwd", str(repo)])

    assert res.exit_code == 0, res.output
    assert _refactors(repo)["IVA-001"] == "discarded"
    assert "Escenarios en verde**: 3" in (repo / ".lictor" / "report.md").read_text("utf-8")


def test_a_pending_scenario_a_refactor_turns_green_is_a_collateral(tmp_path: Path) -> None:
    files = {**GOOD_FILES, "refactor:IVA-001": {"src/iva/__init__.py": IVA_002_CODE}}
    repo = _setup_demo_iva_repo(tmp_path, {"mode": "good", "files_to_write": files})

    res = runner.invoke(app, ["run", "--cwd", str(repo)])

    assert res.exit_code == 0, res.output
    assert _refactors(repo)["IVA-001"] == "applied"
    report = (repo / ".lictor" / "report.md").read_text(encoding="utf-8")
    assert "- **IVA-002**: pasó tras el refactor de IVA-001 en `" in report
    # IVA-002 was never implemented: one call fewer than three scenarios and three refactors.
    assert "IVA-002: implementando" not in res.stderr
    assert "IVA-002" not in _refactors(repo)


def test_a_rate_limit_in_a_refactor_pauses_and_the_resume_refactors(tmp_path: Path) -> None:
    config = {"mode": "good", "files_to_write": GOOD_FILES, "refactor_mode": "rate_limit"}
    repo = _setup_demo_iva_repo(tmp_path, config)

    assert runner.invoke(app, ["run", "--cwd", str(repo)]).exit_code == 50
    assert "IVA-001" not in _refactors(repo)

    config["refactor_mode"] = "good"
    (repo / ".lictor" / "fake_executor.json").write_text(json.dumps(config), encoding="utf-8")
    res = runner.invoke(app, ["run", "--cwd", str(repo)])

    assert res.exit_code == 0, res.output
    assert _refactors(repo) == dict.fromkeys(("IVA-001", "IVA-002", "IVA-003"), "unchanged")


def test_a_project_can_turn_the_refactor_off(tmp_path: Path) -> None:
    repo = _setup_demo_iva_repo(
        tmp_path,
        {"mode": "good", "files_to_write": GOOD_FILES},
        config_changes={"refactor": {"enabled": False}},
    )

    res = runner.invoke(app, ["run", "--cwd", str(repo)])

    assert res.exit_code == 0, res.output
    assert _refactors(repo) == {}
    assert res.stderr.splitlines()[-1].startswith("Total: 4 llamadas, ")
