"""Adds the project sources to the end of the path, once pytest has started.

Lictor copies this file into the verification environment, so it runs where Lictor isn't installed
and must import nothing of it. The hook runs after the interpreter's startup and after the plugins
are imported, and `append` keeps `src` behind `site-packages`: project code can't replace a
dependency or run during startup.
"""

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


def pytest_load_initial_conftests(early_config: "pytest.Config") -> None:
    """Make the sources importable for the conftests and the tests that come next."""
    sys.path.append(str(early_config.invocation_params.dir / "src"))
