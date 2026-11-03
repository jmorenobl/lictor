"""Run the mutation pass and leave its counts where the `mutation` reader looks for them.

`mutmut run` only prints its score; `mutmut export-cicd-stats` writes it to a fixed path. This
script runs both and copies that file to the path it is given, so the control can read it. The
file of an earlier pass is deleted first: if the export fails, no report is left and the script
exits non-zero, so an old score can't pass for this one.

Usage: uv run python scripts/mutation_score.py <report.json>
"""

import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STATS = Path("mutants") / "mutmut-cicd-stats.json"
MUTMUT = (sys.executable, "-m", "mutmut")


def main(argv: Sequence[str], mutmut: Sequence[str] = MUTMUT, root: Path = REPO_ROOT) -> int:
    if len(argv) != 1:
        print("Uso: mutation_score.py <informe.json>", file=sys.stderr)
        return 2
    report = Path(argv[0])
    report.unlink(missing_ok=True)
    (root / STATS).unlink(missing_ok=True)
    passed = subprocess.run([*mutmut, "run"], cwd=root, check=False).returncode
    exported = subprocess.run([*mutmut, "export-cicd-stats"], cwd=root, check=False).returncode
    if exported != 0 or not (root / STATS).is_file():
        print(f"No se pudo exportar la puntuación (código {exported})", file=sys.stderr)
        return passed or exported or 1
    report.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(root / STATS, report)
    return passed


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
