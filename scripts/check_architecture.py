"""Check the layer contracts by reading the sources, without running any of them.

Import Linter puts its working directory first on the path and then imports lazily, so this runs
from the project root, where the agent can't write, and `src` goes last: a module in `src` named
like a dependency must neither shadow it nor run in here. This file is out of the agent's reach too.
"""

import sys
from pathlib import Path

from importlinter.cli import lint_imports

if __name__ == "__main__":
    sys.path.append(str(Path.cwd() / "src"))
    sys.exit(lint_imports())
