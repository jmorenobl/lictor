import ast
import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_LICTOR = REPO_ROOT / "src" / "lictor"
DOMAIN_DIR = SRC_LICTOR / "domain"
PROFILES_DIR = SRC_LICTOR / "adapters" / "config" / "profiles"

FORBIDDEN_DOMAIN_IO_MODULES = frozenset(
    {
        "subprocess",
        "sqlite3",
        "shutil",
        "os",
        "socket",
    }
)


def _extract_imported_root_modules(py_file: Path) -> set[str]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module.split(".")[0])
    return modules


def test_domain_has_no_io_imports() -> None:
    py_files = sorted(DOMAIN_DIR.rglob("*.py"))
    assert py_files, "No se encontraron módulos en domain/"

    violations: dict[str, set[str]] = {}
    for py_file in py_files:
        imported = _extract_imported_root_modules(py_file)
        forbidden = imported & FORBIDDEN_DOMAIN_IO_MODULES
        if forbidden:
            violations[str(py_file.relative_to(REPO_ROOT))] = forbidden

    assert not violations, f"Módulos de E/S prohibidos en domain/: {violations}"


def _collect_profile_tool_names() -> set[str]:
    tool_names: set[str] = {"ruff", "mypy", "pytest", "pytest-bdd", "uv"}
    for profile_path in PROFILES_DIR.glob("*.json"):
        data = json.loads(profile_path.read_text(encoding="utf-8"))
        name = data.get("name")
        if isinstance(name, str) and name:
            tool_names.add(name)
        for detect_file in data.get("detect", []):
            if isinstance(detect_file, str) and detect_file:
                tool_names.add(detect_file)
    return tool_names


def test_no_profile_tool_names_outside_profiles_dir() -> None:
    tool_names = _collect_profile_tool_names()
    patterns = {
        tool: re.compile(rf"(?<![A-Za-z0-9_-]){re.escape(tool)}(?![A-Za-z0-9_-])")
        for tool in sorted(tool_names)
    }

    violations: list[str] = []
    for file_path in sorted(SRC_LICTOR.rglob("*")):
        if file_path.is_dir():
            continue
        if PROFILES_DIR in file_path.parents:
            continue
        if "__pycache__" in file_path.parts or file_path.suffix == ".pyc":
            continue

        content = file_path.read_text(encoding="utf-8")
        for tool, pattern in patterns.items():
            if pattern.search(content):
                rel = file_path.relative_to(REPO_ROOT)
                violations.append(f"{rel}: contiene '{tool}'")

    assert not violations, (
        "Nombres de herramientas de perfiles encontrados fuera de adapters/config/profiles/: "
        + ", ".join(violations)
    )

