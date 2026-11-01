"""Unit tests for the verification environment: hash, rebuild and failure to prepare."""

import json
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from lictor.adapters.runner.fake import FakeRunner
from lictor.adapters.runner.subprocess_runner import SubprocessRunner
from lictor.application.ports import AuditSink, Clock, EventLog, Vcs
from lictor.application.seal import hash_tree
from lictor.application.verification_env import VerificationEnvironment, VerificationEnvSpec
from lictor.domain.errors import EnvironmentUnavailableError
from lictor.domain.model import CommandOutput

SPEC = VerificationEnvSpec(
    prepare="build --into {env_dir}",
    env={"ENV_AT": "{env_dir}/bin", "PLAIN": "1"},
)


WITH_PLUGINS = replace(
    SPEC,
    plugin_variable="CARGA",
    plugins=("propio",),
    plugins_command="plugins {env_dir}",
)


class _Builder(FakeRunner):
    """Stands for the profile's prepare command: fills the directory it is given."""

    def __init__(self, returncode: int = 0) -> None:
        super().__init__()
        self.returncode = returncode
        self.builds = 0
        self.seed: dict[str, str] = {}
        self.answer = ""
        self.answer_returncode = 0
        self.plugins = "plugin_a\nplugin_b\n"
        self.plugins_returncode = 0

    def run(
        self,
        cmd: Sequence[str],
        cwd: Path,
        timeout_s: int | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandOutput:
        super().run(cmd, cwd, timeout_s, env)
        if cmd[0] == "donde":
            stdout = self.answer.replace("{env_dir}", cmd[1])
            return CommandOutput(self.answer_returncode, stdout=stdout, stderr="sin respuesta")
        if cmd[0] == "plugins":
            return CommandOutput(self.plugins_returncode, stdout=self.plugins, stderr="sin lista")
        if cmd[0] != "build":
            return CommandOutput(returncode=0, stdout="", stderr="")
        self.builds += 1
        if self.returncode:
            return CommandOutput(returncode=self.returncode, stdout="", stderr="sin red")
        env_dir = Path(cmd[-1])
        env_dir.mkdir(parents=True)
        (env_dir / "paquete.py").write_text("x = 1\n", encoding="utf-8")
        for rel_path, content in self.seed.items():
            (env_dir / rel_path).parent.mkdir(exist_ok=True)
            (env_dir / rel_path).write_text(content, encoding="utf-8")
        return CommandOutput(returncode=0, stdout="", stderr="")


def _environment(
    tmp_path: Path, runner: FakeRunner, spec: VerificationEnvSpec = SPEC
) -> tuple[VerificationEnvironment, MagicMock]:
    (tmp_path / ".lictor").mkdir(exist_ok=True)
    event_log = MagicMock(spec=EventLog)
    clock = MagicMock(spec=Clock)
    clock.now_iso.return_value = "2026-10-31T10:00:00Z"
    vcs = MagicMock(spec=Vcs)
    audit = AuditSink(event_log=event_log, clock=clock)
    return VerificationEnvironment(vcs, runner, audit, tmp_path, spec), event_log


def _events(event_log: MagicMock) -> list[str]:
    return [call.args[0].type for call in event_log.append.call_args_list]


def test_first_verification_builds_once_and_trusts_it(tmp_path: Path) -> None:
    runner = _Builder()
    environment, event_log = _environment(tmp_path, runner)

    assert environment.ensure("R") is None
    assert environment.ensure("R") is None

    assert runner.builds == 1
    assert _events(event_log) == ["lictor.env.built.v1"]
    built = event_log.append.call_args.args[0]
    assert built.data["reason"] == "primera verificación"
    assert built.data["hash"] == hash_tree(environment.env_dir)
    assert "duration_ms" in built.data


def test_prepare_runs_outside_the_project_with_the_profile_env(tmp_path: Path) -> None:
    runner = _Builder()
    environment, _ = _environment(tmp_path, runner)

    environment.ensure("R")

    call = runner.calls[0]
    assert call["cmd"] == ["build", "--into", str(environment.env_dir)]
    assert call["env"] == {"ENV_AT": f"{environment.env_dir}/bin", "PLAIN": "1"}
    assert call["cwd"] != tmp_path


def test_a_new_run_rebuilds_without_blaming_anyone(tmp_path: Path) -> None:
    runner = _Builder()
    environment, event_log = _environment(tmp_path, runner)
    environment.ensure("R1")

    assert environment.ensure("R2") is None

    assert runner.builds == 2
    assert "lictor.env.tampered.v1" not in _events(event_log)


@pytest.mark.parametrize(
    "rel_path",
    ["nuevo.py", "paquete.py", "__pycache__/paquete.cpython-312.pyc"],
)
def test_any_change_is_reported_and_rebuilt(tmp_path: Path, rel_path: str) -> None:
    runner = _Builder()
    environment, event_log = _environment(tmp_path, runner)
    environment.ensure("R")
    target = environment.env_dir / rel_path
    target.parent.mkdir(exist_ok=True)
    target.write_text("forjado", encoding="utf-8")

    problem = environment.ensure("R")

    assert problem is not None
    assert "cambió" in problem
    assert runner.builds == 2
    assert _events(event_log) == [
        "lictor.env.built.v1",
        "lictor.env.tampered.v1",
        "lictor.env.built.v1",
    ]
    tampered = event_log.append.call_args_list[1].args[0]
    assert tampered.data["expected"] != tampered.data["actual"]
    assert event_log.append.call_args.args[0].data["reason"] == "manipulado"
    rebuilt = target.read_text(encoding="utf-8") if target.exists() else ""
    assert rebuilt != "forjado"
    assert environment.ensure("R") is None


@pytest.mark.parametrize("name", ["a.cpython-312.pyc", "b.pyc"])
def test_bytecode_counts_even_in_a_directory_that_already_existed(
    tmp_path: Path, name: str
) -> None:
    """A new directory changes the hash by itself: this case only passes if the file counts."""
    runner = _Builder()
    environment, _ = _environment(tmp_path, runner)
    runner.seed = {"__pycache__/a.cpython-312.pyc": "original"}
    environment.ensure("R")

    (environment.env_dir / "__pycache__" / name).write_text("forjado", encoding="utf-8")

    assert environment.ensure("R") is not None


def test_a_removed_file_counts_as_a_change(tmp_path: Path) -> None:
    environment, _ = _environment(tmp_path, _Builder())
    environment.ensure("R")
    (environment.env_dir / "paquete.py").unlink()

    assert environment.ensure("R") is not None


def test_only_declared_variables_are_inherited_and_fixed_ones_win(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DECLARADA", "x")
    monkeypatch.setenv("FIJADA", "heredada")
    monkeypatch.setenv("SIN_DECLARAR", "x")
    monkeypatch.delenv("AUSENTE", raising=False)
    spec = VerificationEnvSpec(inherit=("DECLARADA", "FIJADA", "AUSENTE"), env={"FIJADA": "1"})
    environment, _ = _environment(tmp_path, FakeRunner(), spec)

    assert environment.command_env() == {"DECLARADA": "x", "FIJADA": "1"}


def test_inherit_names_are_exact_not_patterns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DECLARADA_B", "x")
    monkeypatch.setenv("declarada", "x")
    spec = VerificationEnvSpec(inherit=("DECLARADA", "DECLARADA_*"))
    environment, _ = _environment(tmp_path, FakeRunner(), spec)

    assert environment.command_env() == {}


def test_commands_run_with_the_exact_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DECLARADA", "x")
    runner = _Builder()
    spec = VerificationEnvSpec(prepare=SPEC.prepare, inherit=("DECLARADA",))
    environment, _ = _environment(tmp_path, runner, spec)

    environment.ensure("R")
    environment.run(["control"], tmp_path, 1)

    assert [call["env"] for call in runner.calls] == [{"DECLARADA": "x"}] * 2


def test_forgetting_a_variable_a_tool_needs_stops_visibly(tmp_path: Path) -> None:
    """With no `PATH` declared the tool isn't found: the build fails and says what it tried."""
    spec = VerificationEnvSpec(prepare="herramienta-de-la-ruta", inherit=())
    environment, event_log = _environment(tmp_path, SubprocessRunner(), spec)
    cast(MagicMock, environment.vcs).export_tree.side_effect = lambda _rev, dest: dest.mkdir()

    with pytest.raises(EnvironmentUnavailableError, match="herramienta-de-la-ruta"):
        environment.ensure("R")

    assert event_log.append.call_args.args[0].type == "lictor.env.failed.v1"


def _with_file(tmp_path: Path) -> VerificationEnvSpec:
    source = tmp_path / "fuente" / "plugin.py"
    source.parent.mkdir()
    source.write_text("x = 2\n", encoding="utf-8")
    return VerificationEnvSpec(
        prepare=SPEC.prepare, files_dir_command="donde {env_dir}", files=(source,)
    )


def test_files_are_installed_where_the_environment_says_before_hashing(tmp_path: Path) -> None:
    runner = _Builder()
    runner.seed = {"lib/x.txt": ""}
    runner.answer = "{env_dir}/lib\n"
    environment, _ = _environment(tmp_path, runner, _with_file(tmp_path))

    environment.ensure("R")

    plugin = environment.env_dir / "lib" / "plugin.py"
    assert plugin.read_text(encoding="utf-8") == "x = 2\n"
    assert environment.ensure("R") is None
    plugin.write_text("x = 3\n", encoding="utf-8")
    assert environment.ensure("R") is not None
    assert plugin.read_text(encoding="utf-8") == "x = 2\n"


def test_the_destination_is_asked_with_the_environment_of_the_commands(tmp_path: Path) -> None:
    runner = _Builder()
    runner.seed = {"lib/x.txt": ""}
    runner.answer = "{env_dir}/lib"
    spec = replace(_with_file(tmp_path), env={"FIJA": "1"})
    environment, _ = _environment(tmp_path, runner, spec)

    environment.ensure("R")

    asked = runner.calls[1]
    assert asked["cmd"] == ["donde", str(environment.env_dir)]
    assert asked["env"] == {"FIJA": "1"}
    assert asked["cwd"] == environment.env_dir


def test_a_failed_question_is_an_environment_failure(tmp_path: Path) -> None:
    runner = _Builder()
    runner.answer_returncode = 3
    environment, event_log = _environment(tmp_path, runner, _with_file(tmp_path))

    with pytest.raises(EnvironmentUnavailableError, match="sin respuesta"):
        environment.ensure("R")

    assert _events(event_log) == ["lictor.env.failed.v1"]
    assert event_log.append.call_args.args[0].data["exit_code"] == 3
    assert not environment.manifest.exists()


@pytest.mark.parametrize("answer", ["/etc", "{env_dir}/no-existe", "", "{env_dir}/../fuera"])
def test_a_destination_outside_the_environment_is_refused(tmp_path: Path, answer: str) -> None:
    (tmp_path / ".lictor" / "fuera").mkdir(parents=True)
    runner = _Builder()
    runner.answer = answer
    environment, event_log = _environment(tmp_path, runner, _with_file(tmp_path))

    with pytest.raises(EnvironmentUnavailableError, match="no es un directorio del entorno"):
        environment.ensure("R")

    assert _events(event_log) == ["lictor.env.failed.v1"]
    assert not environment.manifest.exists()


def test_without_files_nothing_is_asked(tmp_path: Path) -> None:
    runner = _Builder()
    environment, _ = _environment(
        tmp_path, runner, replace(SPEC, files_dir_command="donde {env_dir}")
    )

    environment.ensure("R")

    assert len(runner.calls) == 1


def test_failed_prepare_is_an_environment_failure(tmp_path: Path) -> None:
    environment, event_log = _environment(tmp_path, _Builder(returncode=2))

    with pytest.raises(EnvironmentUnavailableError, match="sin red"):
        environment.ensure("R")

    assert _events(event_log) == ["lictor.env.failed.v1"]
    assert event_log.append.call_args.args[0].data["exit_code"] == 2
    assert not environment.manifest.exists()
    log = tmp_path / ".lictor" / "runs" / "R" / "env" / "build.log"
    assert "sin red" in log.read_text(encoding="utf-8")


def test_failed_rebuild_leaves_no_hash_to_trust(tmp_path: Path) -> None:
    runner = _Builder()
    environment, _ = _environment(tmp_path, runner)
    environment.ensure("R")
    (environment.env_dir / "nuevo.py").write_text("x", encoding="utf-8")
    runner.returncode = 1

    with pytest.raises(EnvironmentUnavailableError):
        environment.ensure("R")

    assert not environment.manifest.exists()


def test_each_build_keeps_its_own_log(tmp_path: Path) -> None:
    environment, _ = _environment(tmp_path, _Builder())
    environment.ensure("R")
    (environment.env_dir / "nuevo.py").write_text("x", encoding="utf-8")
    environment.ensure("R")

    logs = sorted((tmp_path / ".lictor" / "runs" / "R" / "env").iterdir())
    assert [log.name for log in logs] == ["build-r1.log", "build.log"]


def test_project_without_prepare_has_nothing_to_check(tmp_path: Path) -> None:
    runner = _Builder()
    environment, event_log = _environment(tmp_path, runner, VerificationEnvSpec(env={"A": "1"}))

    assert environment.ensure("R") is None

    assert runner.calls == []
    event_log.append.assert_not_called()
    assert environment.command_env() == {"A": "1"}


def test_invalid_manifest_means_building_again(tmp_path: Path) -> None:
    runner = _Builder()
    environment, _ = _environment(tmp_path, runner)
    environment.ensure("R")
    environment.manifest.write_text("[1]", encoding="utf-8")
    assert environment.ensure("R") is None
    environment.manifest.write_text("{roto", encoding="utf-8")
    assert environment.ensure("R") is None

    assert runner.builds == 3


def test_manifest_records_run_and_hash(tmp_path: Path) -> None:
    environment, _ = _environment(tmp_path, _Builder())
    environment.ensure("R")

    assert json.loads(environment.manifest.read_text(encoding="utf-8")) == {
        "run_id": "R",
        "hash": hash_tree(environment.env_dir),
        "plugins": [],
    }


def test_installed_plugins_are_fixed_when_building(tmp_path: Path) -> None:
    runner = _Builder()
    runner.plugins = "plugin_b\n\nplugin_a\nplugin_b\n"
    environment, event_log = _environment(tmp_path, runner, WITH_PLUGINS)

    environment.ensure("R")

    assert environment.command_env()["CARGA"] == "propio,plugin_a,plugin_b"
    manifest = json.loads(environment.manifest.read_text(encoding="utf-8"))
    assert manifest["plugins"] == ["plugin_a", "plugin_b"]
    assert event_log.append.call_args.args[0].data["plugins"] == ["plugin_a", "plugin_b"]


def test_plugins_are_asked_after_prepare_and_inside_the_environment(tmp_path: Path) -> None:
    runner = _Builder()
    environment, _ = _environment(tmp_path, runner, WITH_PLUGINS)

    environment.ensure("R")

    assert [call["cmd"][0] for call in runner.calls] == ["build", "plugins"]
    asked = runner.calls[1]
    assert asked["cmd"] == ["plugins", str(environment.env_dir)]
    assert asked["cwd"] == environment.env_dir


def test_the_files_exist_before_the_plugins_are_asked_and_both_are_hashed(tmp_path: Path) -> None:
    runner = _Builder()
    runner.seed = {"lib/x.txt": ""}
    runner.answer = "{env_dir}/lib"
    spec = replace(_with_file(tmp_path), plugin_variable="CARGA", plugins_command="plugins x")
    environment, _ = _environment(tmp_path, runner, spec)

    environment.ensure("R")

    assert [call["cmd"][0] for call in runner.calls] == ["build", "donde", "plugins"]


def test_whatever_the_question_leaves_behind_is_hashed(tmp_path: Path) -> None:
    class _Littering(_Builder):
        def run(
            self,
            cmd: Sequence[str],
            cwd: Path,
            timeout_s: int | None = None,
            env: dict[str, str] | None = None,
        ) -> CommandOutput:
            if cmd[0] == "plugins" and self.builds == 1:
                (cwd / "resto.pyc").write_text("x", encoding="utf-8")
            return super().run(cmd, cwd, timeout_s, env)

    environment, _ = _environment(tmp_path, _Littering(), WITH_PLUGINS)

    environment.ensure("R")

    assert environment.ensure("R") is None


def test_plugins_survive_into_a_new_process(tmp_path: Path) -> None:
    """A resumed run has only the manifest, and it trusts it after comparing."""
    runner = _Builder()
    environment, _ = _environment(tmp_path, runner, WITH_PLUGINS)
    environment.ensure("R")
    resumed, _ = _environment(tmp_path, runner, WITH_PLUGINS)

    assert resumed.ensure("R") is None

    assert runner.builds == 1
    assert resumed.command_env()["CARGA"] == "propio,plugin_a,plugin_b"


def test_a_forged_plugin_list_is_tampering(tmp_path: Path) -> None:
    """The hash stays right: only the list says something the environment doesn't."""
    runner = _Builder()
    environment, event_log = _environment(tmp_path, runner, WITH_PLUGINS)
    environment.ensure("R")
    recorded = json.loads(environment.manifest.read_text(encoding="utf-8"))
    recorded["plugins"].append("trampa")
    environment.manifest.write_text(json.dumps(recorded), encoding="utf-8")

    problem = environment.ensure("R")

    assert problem is not None
    assert "trampa" in problem
    assert runner.builds == 2
    assert _events(event_log).count("lictor.env.tampered.v1") == 1
    assert "trampa" not in environment.command_env()["CARGA"]


def test_the_manifest_is_not_read_between_two_checks(tmp_path: Path) -> None:
    """Project code runs between commands: what they load can't depend on a file it can reach."""
    environment, _ = _environment(tmp_path, _Builder(), WITH_PLUGINS)
    environment.ensure("R")
    environment.manifest.write_text('{"plugins": ["trampa"]}', encoding="utf-8")

    assert "trampa" not in environment.command_env()["CARGA"]


def test_a_tampered_tree_is_not_asked_for_its_plugins(tmp_path: Path) -> None:
    runner = _Builder()
    environment, _ = _environment(tmp_path, runner, WITH_PLUGINS)
    environment.ensure("R")
    (environment.env_dir / "nuevo.py").write_text("x", encoding="utf-8")
    runner.calls.clear()

    assert environment.ensure("R") is not None

    assert [call["cmd"][0] for call in runner.calls] == ["build", "plugins"]


def test_a_rebuild_forgets_the_previous_plugins(tmp_path: Path) -> None:
    runner = _Builder()
    environment, _ = _environment(tmp_path, runner, WITH_PLUGINS)
    environment.ensure("R")
    (environment.env_dir / "nuevo.py").write_text("x", encoding="utf-8")
    runner.plugins = "plugin_c\n"

    environment.ensure("R")

    assert environment.command_env()["CARGA"] == "propio,plugin_c"


def test_failing_to_list_plugins_is_an_environment_failure(tmp_path: Path) -> None:
    runner = _Builder()
    runner.plugins_returncode = 3
    environment, event_log = _environment(tmp_path, runner, WITH_PLUGINS)

    with pytest.raises(EnvironmentUnavailableError, match="sin lista"):
        environment.ensure("R")

    assert _events(event_log) == ["lictor.env.failed.v1"]
    assert event_log.append.call_args.args[0].data["exit_code"] == 3
    assert not environment.manifest.exists()


@pytest.mark.parametrize("name", ["modulo:objeto", "a,b", "a b", "-p", "../a"])
def test_a_plugin_that_cannot_be_loaded_by_name_is_refused(tmp_path: Path, name: str) -> None:
    runner = _Builder()
    runner.plugins = f"bueno\n{name}\n"
    environment, _ = _environment(tmp_path, runner, WITH_PLUGINS)

    with pytest.raises(EnvironmentUnavailableError, match="por nombre") as error:
        environment.ensure("R")

    assert name in str(error.value)
    assert not environment.manifest.exists()


def test_without_a_plugin_command_only_the_declared_ones_load(tmp_path: Path) -> None:
    spec = replace(WITH_PLUGINS, plugins_command="")
    runner = _Builder()
    environment, _ = _environment(tmp_path, runner, spec)

    environment.ensure("R")

    assert [call["cmd"][0] for call in runner.calls] == ["build"]
    assert environment.command_env()["CARGA"] == "propio"


def test_no_plugins_means_no_variable(tmp_path: Path) -> None:
    runner = _Builder()
    runner.plugins = ""
    spec = replace(WITH_PLUGINS, plugins=())
    environment, _ = _environment(tmp_path, runner, spec)

    environment.ensure("R")

    assert "CARGA" not in environment.command_env()


def test_run_injects_the_environment(tmp_path: Path) -> None:
    runner = FakeRunner()
    environment, _ = _environment(tmp_path, runner)

    environment.run(["suite"], tmp_path, 5)

    assert runner.calls[0]["env"] == environment.command_env()
    assert runner.calls[0]["timeout_s"] == 5


def test_hash_tree_covers_path_mode_link_and_content(tmp_path: Path) -> None:
    root = tmp_path / "t"
    root.mkdir()
    file = root / "a.py"
    file.write_text("x", encoding="utf-8")
    base = hash_tree(root)

    file.chmod(0o755)
    executable = hash_tree(root)
    assert executable != base
    file.chmod(0o644)
    assert hash_tree(root) == base

    file.write_text("y", encoding="utf-8")
    assert hash_tree(root) != base
    file.write_text("x", encoding="utf-8")

    file.rename(root / "b.py")
    assert hash_tree(root) != base
    (root / "b.py").rename(file)

    link = root / "l"
    link.symlink_to("a.py")
    linked = hash_tree(root)
    assert linked != base
    link.unlink()
    link.symlink_to("/otro")
    assert hash_tree(root) != linked


def test_hash_tree_of_a_missing_directory_is_stable(tmp_path: Path) -> None:
    assert hash_tree(tmp_path / "falta") == hash_tree(tmp_path / "tampoco")
