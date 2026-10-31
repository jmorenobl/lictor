"""Audit what `uv.lock` installs: run alone, `pip-audit` only sees the environment it runs in."""

import subprocess
import sys
import tempfile
from pathlib import Path


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        requirements = Path(tmp) / "requirements.txt"
        subprocess.run(
            [
                "uv",
                "export",
                "--frozen",
                "--no-emit-project",
                "--format",
                "requirements-txt",
                "--quiet",
                "--output-file",
                str(requirements),
            ],
            check=True,
        )
        audit = ["uvx", "pip-audit", "-r", str(requirements), "--disable-pip", "--require-hashes"]
        return subprocess.run(audit, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
