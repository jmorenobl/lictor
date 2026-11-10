"""What every test shares: independence from a verification that may be running this suite."""

import tempfile

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


@pytest.fixture(autouse=True)
def _lictor_home_outside_the_users(
    tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No test may write the real `~/.lictor`, where `lictor run` creates its worktrees.

    Lictor refuses a home under the temporary directory, and every directory pytest hands out is
    under it, so each test also gets a temporary directory of its own beside the home.
    """
    monkeypatch.setenv("TMPDIR", str(tmp_path_factory.mktemp("tmp")))
    monkeypatch.setattr(tempfile, "tempdir", None)
    monkeypatch.setenv("LICTOR_HOME", str(tmp_path_factory.mktemp("lictor-home")))
