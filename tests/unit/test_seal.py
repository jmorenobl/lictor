"""Unit tests for the hashes the seal, the environment and the evidence rely on."""

import hashlib
import os
from pathlib import Path

import pytest

from lictor.application.seal import collect_monitored_files, hash_file, hash_tree
from lictor.domain.errors import SpecError


def _write(root: Path, rel: str, content: str = "x", mode: int = 0o644) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


def _sample_tree(root: Path) -> Path:
    _write(root, "a.txt", "uno")
    _write(root, "bin/run", "#!/bin/sh\n", mode=0o755)
    (root / "vacio").mkdir()
    (root / "enlace").symlink_to("a.txt")
    return root


def test_hash_tree_of_a_known_tree_is_stable(tmp_path: Path) -> None:
    # Pinned: the environment manifest and the evidence in the journal store this value.
    assert hash_tree(_sample_tree(tmp_path)) == (
        "sha256:36b7bcdb719b06e370b53a955bf41bcc817cf70c12f3db06c8a8de60eec4830c"
    )


def test_hash_tree_of_an_empty_directory(tmp_path: Path) -> None:
    assert hash_tree(tmp_path) == (
        "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


@pytest.mark.parametrize(
    "change",
    [
        lambda r: _write(r, "a.txt", "dos"),
        lambda r: (r / "a.txt").rename(r / "b.txt"),
        lambda r: (r / "bin" / "run").chmod(0o644),
        lambda r: (r / "a.txt").chmod(0o744),
        lambda r: ((r / "enlace").unlink(), (r / "enlace").symlink_to("bin/run")),
        lambda r: ((r / "enlace").unlink(), _write(r, "enlace", "a.txt")),
        lambda r: (r / "otro").mkdir(),
    ],
)
def test_hash_tree_sees_content_names_modes_links_and_directories(
    tmp_path: Path, change: object
) -> None:
    root = _sample_tree(tmp_path)
    before = hash_tree(root)

    assert callable(change)
    change(root)

    assert hash_tree(root) != before


def test_hash_file_is_the_prefixed_sha256(tmp_path: Path) -> None:
    path = _write(tmp_path, "f", "")
    assert hash_file(path) == (
        "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )


def test_bytecode_is_never_part_of_the_seal(tmp_path: Path) -> None:
    _write(tmp_path, "features/a.feature")
    _write(tmp_path, "features/suelto.pyc")
    _write(tmp_path, "features/__pycache__/nota.txt")
    _write(tmp_path, "tests/acceptance/test_a.py")
    _write(tmp_path, "tests/acceptance/__pycache__/test_a.cpython-312.pyc")

    files = collect_monitored_files(tmp_path, ["tests/acceptance/"])

    assert list(files) == ["features/a.feature", "tests/acceptance/test_a.py"]


def test_a_driver_path_may_be_a_single_file(tmp_path: Path) -> None:
    driver = _write(tmp_path, "tests/test_drv.py", "d")

    assert collect_monitored_files(tmp_path, ["tests/test_drv.py"]) == {
        "tests/test_drv.py": hash_file(driver)
    }


def test_a_link_inside_the_project_is_followed(tmp_path: Path) -> None:
    target = _write(tmp_path, "src/real.py", "r")
    (tmp_path / "tests" / "acceptance").mkdir(parents=True)
    (tmp_path / "tests" / "acceptance" / "enlace.py").symlink_to(target)
    (tmp_path / "tests" / "solo.py").symlink_to(target)

    files = collect_monitored_files(tmp_path, ["tests/acceptance/", "tests/solo.py"])

    assert files == {
        "tests/acceptance/enlace.py": hash_file(target),
        "tests/solo.py": hash_file(target),
    }


def test_a_link_out_of_the_project_is_refused(tmp_path: Path) -> None:
    outside = _write(tmp_path / "fuera", "secreto")
    project = tmp_path / "p"
    (project / "features").mkdir(parents=True)
    (project / "features" / "enlace").symlink_to(outside)

    with pytest.raises(SpecError) as exc_info:
        collect_monitored_files(project, [])

    assert str(exc_info.value) == (
        "Enlace simbólico fuera del repositorio no permitido: features/enlace"
    )


def test_a_driver_file_linked_out_of_the_project_is_refused(tmp_path: Path) -> None:
    outside = _write(tmp_path / "fuera", "secreto")
    project = tmp_path / "p"
    project.mkdir()
    (project / "drv.py").symlink_to(outside)

    with pytest.raises(SpecError, match=r"fuera del repositorio no permitido: drv\.py"):
        collect_monitored_files(project, ["drv.py"])


def test_a_link_loop_is_a_spec_error(tmp_path: Path) -> None:
    (tmp_path / "features").mkdir()
    os.symlink("b", tmp_path / "features" / "a")
    os.symlink("a", tmp_path / "features" / "b")

    with pytest.raises(SpecError, match=r"^Enlace simbólico inválido en features/a: "):
        collect_monitored_files(tmp_path, [])


def test_a_link_loop_in_a_driver_directory_is_a_spec_error(tmp_path: Path) -> None:
    (tmp_path / "tests" / "acceptance").mkdir(parents=True)
    os.symlink("b", tmp_path / "tests" / "acceptance" / "a")
    os.symlink("a", tmp_path / "tests" / "acceptance" / "b")

    with pytest.raises(SpecError, match=r"^Enlace simbólico inválido en tests/acceptance/a: "):
        collect_monitored_files(tmp_path, ["tests/acceptance/"])


def test_a_broken_link_in_features_is_a_spec_error(tmp_path: Path) -> None:
    (tmp_path / "features").mkdir()
    os.symlink("no_existe.feature", tmp_path / "features" / "roto.feature")

    with pytest.raises(SpecError, match=r"^Enlace simbólico inválido en features/roto\.feature: "):
        collect_monitored_files(tmp_path, [])


def test_a_broken_link_as_the_driver_file_is_a_spec_error(tmp_path: Path) -> None:
    os.symlink("no_existe.py", tmp_path / "drv.py")

    with pytest.raises(SpecError, match=r"^Enlace simbólico inválido en drv\.py: "):
        collect_monitored_files(tmp_path, ["drv.py"])


def test_a_chain_of_links_is_sealed_with_the_hash_of_its_target(tmp_path: Path) -> None:
    target = _write(tmp_path, "features/real.feature", "Feature: real")
    os.symlink("real.feature", tmp_path / "features" / "y")
    os.symlink("y", tmp_path / "features" / "x")

    files = collect_monitored_files(tmp_path, [])

    assert files == {
        "features/real.feature": hash_file(target),
        "features/x": hash_file(target),
        "features/y": hash_file(target),
    }


@pytest.mark.parametrize("pattern", ["tests/accX/", "tests/acc "])
def test_a_driver_directory_is_taken_by_its_exact_name(tmp_path: Path, pattern: str) -> None:
    exact = _write(tmp_path, pattern.rstrip("/") + "/t.py", "t")
    _write(tmp_path, "tests/acc/otro.py")

    files = collect_monitored_files(tmp_path, [pattern])

    assert list(files.values()) == [hash_file(exact)]


def test_a_name_that_is_not_utf8_is_hashed_as_its_bytes(tmp_path: Path) -> None:
    try:
        (tmp_path / os.fsdecode(b"\xff")).write_bytes(b"x")
        os.symlink(os.fsdecode(b"\xfe"), tmp_path / "enlace")
    except OSError:
        pytest.skip("este sistema de ficheros solo admite nombres UTF-8")

    assert hash_tree(tmp_path).startswith("sha256:")


def _hash_tree_by_rglob(root: Path) -> str:
    """The walk hash_tree replaced, kept to prove the faster one gives the same hash."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        digest.update(path.relative_to(root).as_posix().encode("utf-8", "surrogateescape"))
        if path.is_symlink():
            digest.update(b"\0link\0")
            digest.update(str(path.readlink()).encode("utf-8", "surrogateescape"))
        elif path.is_file():
            digest.update(b"\0exec\0" if path.stat().st_mode & 0o111 else b"\0file\0")
            digest.update(path.read_bytes())
        digest.update(b"\0")
    return f"sha256:{digest.hexdigest()}"


def _tricky_tree(root: Path) -> Path:
    # Names whose order as strings differs from their order part by part: `a-b` < `a/b` as text,
    # but the directory `a` goes first.
    for rel in ("a/b", "a/c/d", "a-b", "a.txt", "a0", "B", "Z/z", ".oculto", "ñ", "a/c-e"):
        _write(root, rel, rel)
    _write(root, "bin/run", "#!/bin/sh\n", mode=0o755)
    (root / "vacio" / "dentro").mkdir(parents=True)
    (root / "al-dir").symlink_to("a")  # a link to a directory is never followed
    (root / "roto").symlink_to("no-existe")
    (root / "normalizado").symlink_to("./a//c/")  # Path writes the target as `a/c`
    (root / "a" / "arriba").symlink_to("../a.txt")
    return root


def test_hash_tree_matches_the_rglob_walk_it_replaced(tmp_path: Path) -> None:
    root = _tricky_tree(tmp_path)
    expected = _hash_tree_by_rglob(root)

    assert hash_tree(root) == expected
    # Pinned too, so the reference can't drift with pathlib.
    assert expected == "sha256:0e1ca5a92735fdc2b2ac0442ba6e59825f618fed1ef5feed2a35d53b5851dd98"


def test_hash_tree_matches_the_rglob_walk_with_a_name_that_is_not_utf8(tmp_path: Path) -> None:
    try:
        _write(tmp_path, os.fsdecode(b"a\xff/b\xfe"), "x")
        _write(tmp_path, "a/b", "y")
        os.symlink(os.fsdecode(b"\xfd"), tmp_path / "enlace")
    except OSError:
        pytest.skip("este sistema de ficheros solo admite nombres UTF-8")

    assert hash_tree(tmp_path) == _hash_tree_by_rglob(tmp_path)
