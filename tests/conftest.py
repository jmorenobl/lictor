"""What every test shares: independence from a verification that may be running this suite."""

import pytest

# Lictor's own verification runs this suite with these set. Left in place, a nested `uv sync` would
# rewrite the verification environment and a nested pytest would load its plugins. They are cleared
# per test, not at startup, because xdist workers still have to inherit them.
VERIFICATION_VARIABLES = (
    "UV_PROJECT_ENVIRONMENT",
    "UV_NO_SYNC",
    "UV_FROZEN",
    "UV_LINK_MODE",
    "UV_NO_ENV_FILE",
    "PYTEST_ADDOPTS",
    "PYTEST_PLUGINS",
    "PYTEST_DISABLE_PLUGIN_AUTOLOAD",
)


@pytest.fixture(autouse=True)
def _outside_any_verification(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in VERIFICATION_VARIABLES:
        monkeypatch.delenv(name, raising=False)
