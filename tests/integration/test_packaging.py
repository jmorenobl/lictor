"""The wheel carries what the profile needs: a profile only usable from the sources is broken."""

import json
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PROFILES = "lictor/adapters/config/profiles"


def test_the_wheel_carries_the_profile_and_its_files(tmp_path: Path) -> None:
    shutil.copytree(
        REPO_ROOT,
        tmp_path / "copia",
        ignore=shutil.ignore_patterns(".*", "mutants", "__pycache__"),
    )
    env = {name: value for name, value in os.environ.items() if not name.startswith("UV_")}
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path / "dist")],
        cwd=tmp_path / "copia",
        env=env,
        check=True,
        capture_output=True,
    )
    wheel = zipfile.ZipFile(next((tmp_path / "dist").glob("*.whl")))
    names = wheel.namelist()

    profile = json.loads(wheel.read(f"{PROFILES}/python-uv.json"))
    assert profile["verification"]["files"]
    for file in profile["verification"]["files"]:
        assert f"{PROFILES}/{file}" in names
