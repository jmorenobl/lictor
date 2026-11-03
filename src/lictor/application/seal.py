"""Monitored file collection and seal utilities."""

import hashlib
from collections.abc import Sequence
from pathlib import Path

from lictor.domain.errors import SpecError

EXECUTABLE_BITS = 0o111


def hash_file(file_path: Path) -> str:
    """Calculate sha256 checksum with sha256: prefix."""
    digest = hashlib.sha256(file_path.read_bytes()).hexdigest()
    return f"sha256:{digest}"


def hash_tree(root: Path) -> str:
    """Hash every file under a directory: paths, modes, link targets and content."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8", "surrogateescape"))
        if path.is_symlink():
            digest.update(b"\0link\0")
            digest.update(str(path.readlink()).encode("utf-8", "surrogateescape"))
        elif path.is_file():
            digest.update(b"\0exec\0" if path.stat().st_mode & EXECUTABLE_BITS else b"\0file\0")
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def _verify_symlink_and_hash(
    path: Path,
    project_root: Path,
    resolved_root: Path,
    files_map: dict[str, str],
) -> None:
    if any(part == "__pycache__" for part in path.parts) or path.suffix == ".pyc":
        return
    if path.is_symlink():
        try:
            # Without strict, 3.13 returns a loop as the link itself, and every version a broken
            # link as its target, and neither is a file the seal would see.
            resolved_p = path.resolve(strict=True)
        except (OSError, RuntimeError) as exc:  # a loop is RuntimeError in 3.12, OSError after
            rel = path.relative_to(project_root).as_posix()
            raise SpecError(f"Enlace simbólico inválido en {rel}: {exc}") from exc
        if not resolved_p.is_relative_to(resolved_root):
            rel = path.relative_to(project_root).as_posix()
            raise SpecError(f"Enlace simbólico fuera del repositorio no permitido: {rel}")
    if path.is_file():
        rel_path = path.relative_to(project_root).as_posix()
        files_map[rel_path] = hash_file(path)


def _collect_driver_pattern(
    drv_pattern: str,
    project_root: Path,
    resolved_root: Path,
    files_map: dict[str, str],
) -> None:
    drv_target = project_root / drv_pattern.rstrip("/")
    if drv_target.is_file() or drv_target.is_symlink():
        _verify_symlink_and_hash(drv_target, project_root, resolved_root, files_map)
    elif drv_target.is_dir():
        for df in sorted(drv_target.rglob("*")):
            _verify_symlink_and_hash(df, project_root, resolved_root, files_map)


def collect_monitored_files(project_root: Path, driver_paths: Sequence[str]) -> dict[str, str]:
    """Collect and hash all files under features/** and paths.driver.

    Raises SpecError for a symlink that loops, is broken or points outside project_root.
    """
    files_map: dict[str, str] = {}
    resolved_root = project_root.resolve()

    features_dir = project_root / "features"
    if features_dir.is_dir():
        for feat in sorted(features_dir.rglob("*")):
            _verify_symlink_and_hash(feat, project_root, resolved_root, files_map)

    for drv_pattern in driver_paths:
        _collect_driver_pattern(drv_pattern, project_root, resolved_root, files_map)

    return {k: files_map[k] for k in sorted(files_map)}
