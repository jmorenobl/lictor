"""Run the mutation pass and leave its counts where the `mutation` reader looks for them.

`mutmut run` only prints its score; `mutmut export-cicd-stats` writes it to a fixed path. This
script runs both and copies that file to the path it is given, so the control can read it. The
file of an earlier pass is deleted first: if the export fails, no report is left and the script
exits non-zero, so an old score can't pass for this one. Beside the report it leaves
`mutation-survivors.txt`, what `mutmut results` lists as not killed: the score alone doesn't say
which mutants differ between two passes or two platforms.

Usage: uv run python scripts/mutation_score.py <report.json>
"""

import shutil
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
STATS = Path("mutants") / "mutmut-cicd-stats.json"
SURVIVORS = "mutation-survivors.txt"
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
    _list_survivors(mutmut, root, report.with_name(SURVIVORS))
    return passed


def _list_survivors(mutmut: Sequence[str], root: Path, target: Path) -> None:
    """Keep the names of what wasn't killed; a failure to list them doesn't change the score."""
    target.unlink(missing_ok=True)
    listed = subprocess.run(
        [*mutmut, "results"], cwd=root, check=False, capture_output=True, text=True
    )
    if listed.returncode != 0:
        print(f"No se pudo listar los supervivientes (código {listed.returncode})", file=sys.stderr)
        return
    target.write_text(listed.stdout, encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
