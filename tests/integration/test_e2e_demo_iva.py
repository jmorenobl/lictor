import json
import shutil
import subprocess
from pathlib import Path

from typer.testing import CliRunner

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


def _setup_demo_iva_repo(tmp_path: Path, fake_config: dict[str, object] | None = None) -> Path:
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
        "profile": "python-uv@0.1",
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
            "report": "junit",
            "driver_hints": "hints",
        },
        "controls": [],
        "suppressions": ["# noqa", "# type: ignore"],
        "banned_patterns": [],
        "forbidden_paths": [],
        "verification": {"inherit": ["PATH", "HOME", "VIRTUAL_ENV"]},
        "agent_tools": [],
        "hints": "demo-iva hints",
        "gitignore": [".venv/", "__pycache__/"],
    }
    (project_dir / "lictor.json").write_text(json.dumps(lictor_json, indent=2), encoding="utf-8")
    (project_dir / ".gitignore").write_text(".lictor/\n__pycache__/\n.venv/\n", encoding="utf-8")

    # Write .lictor/fake_executor.json
    lictor_dir = project_dir / ".lictor"
    lictor_dir.mkdir(parents=True, exist_ok=True)
    if fake_config is not None:
        (lictor_dir / "fake_executor.json").write_text(json.dumps(fake_config), encoding="utf-8")

    subprocess.run(["git", "add", "."], cwd=project_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "chore: initial commit"],
        cwd=project_dir,
        check=True,
        capture_output=True,
    )

    return project_dir


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
