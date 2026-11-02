"""Command Line Interface for Lictor."""

import json
import os
import shlex
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from lictor.application.anchor import observe_anchor
from lictor.application.run_files import exclusive_run, sweep_scratch
from lictor.application.usecases.build_report import BuildReportRequest
from lictor.application.usecases.check_project import (
    CheckAcceptanceSpec,
    CheckControlSpec,
    CheckProjectRequest,
)
from lictor.application.usecases.init_project import InitProjectRequest
from lictor.bootstrap import BootstrapResult, bootstrap_init, bootstrap_runtime
from lictor.domain.errors import ExitCode, LictorError
from lictor.domain.model import Event
from lictor.domain.rules import anchor_problems
from lictor.orchestration.graph import DURABILITY, create_graph, get_sqlite_saver

app = typer.Typer(
    name="lictor",
    help="Implementa escenarios Gherkin con un agente y los verifica de forma determinista.",
    no_args_is_help=True,
)

DEFAULT_CWD = Path(".")
_UNDO_OR_START_OVER = "Si fuiste tú, deshazlo y relanza, o empieza de nuevo con `lictor run --new`."


def _safe_run(cmd_func: Callable[[], int | None]) -> None:
    try:
        code = cmd_func()
        raise typer.Exit(code if code is not None else 0)
    except typer.Exit:
        raise
    except LictorError as err:
        typer.echo(f"Error ({err.exit_code}): {err}", err=True)
        raise typer.Exit(err.exit_code) from err
    except Exception as exc:
        typer.echo(f"Error interno: {exc}", err=True)
        raise typer.Exit(1) from exc


@app.command()
def init(
    profile: Annotated[
        str | None,
        typer.Option(
            "--profile",
            "-p",
            help="Nombre del perfil a utilizar",
        ),
    ] = None,
    model: Annotated[
        str | None,
        typer.Option(
            "--model",
            "-m",
            help="Identificador del modelo a utilizar",
        ),
    ] = None,
    force: Annotated[
        bool,
        typer.Option(
            "--force",
            "-f",
            help="Sobrescribir archivo de configuración existente",
        ),
    ] = False,
    cwd: Annotated[
        Path,
        typer.Option(
            "--cwd",
            help="Directorio de trabajo del proyecto",
            hidden=True,
        ),
    ] = DEFAULT_CWD,
) -> None:
    """Inicializa la configuración de Lictor en el repositorio."""

    def _execute() -> int:
        # It rewrites `lictor.json`, which a run in progress guards as its own state.
        with exclusive_run(cwd, "lictor init"):
            init_uc = bootstrap_init(cwd)
            res = init_uc.execute(
                InitProjectRequest(
                    project_root=cwd,
                    profile_name=profile,
                    model=model,
                    force=force,
                )
            )
        typer.echo(f"Configuración inicializada en '{res.config_path}'.")
        return 0

    _safe_run(_execute)


@app.command()
def doctor(
    cwd: Annotated[
        Path,
        typer.Option(
            "--cwd",
            help="Directorio de trabajo del proyecto",
            hidden=True,
        ),
    ] = DEFAULT_CWD,
) -> None:
    """Comprueba las precondiciones del entorno sin ejecutar el grafo."""

    def _execute() -> int:
        resolved_cwd = cwd.resolve()
        runtime = bootstrap_runtime(resolved_cwd)

        # 1. Configuración válida
        typer.echo(f"✓ Configuración válida: perfil '{runtime.config.profile}'")

        # 2. Árbol de trabajo git limpio
        changed = runtime.vcs.status()
        if changed:
            typer.echo(
                f"✗ El repositorio git tiene cambios sin confirmar: {changed}",
                err=True,
            )
            return 10
        typer.echo("✓ Árbol git limpio sin cambios pendientes")

        # 3. Estado del ejecutor
        health = runtime.executor.health()
        if not health.ok:
            typer.echo(
                f"✗ El ejecutor no está listo: {health.message or health.auth_status}",
                err=True,
            )
            return int(ExitCode.ENVIRONMENT_UNAVAILABLE)
        typer.echo(f"✓ Ejecutor disponible: {health.cli_version} ({health.auth_status})")

        # 4. Comandos de controles
        for ctrl in runtime.config.controls:
            cmd_str = ctrl.run.strip()
            if not cmd_str:
                typer.echo(f"✗ El control '{ctrl.id}' tiene un comando vacío", err=True)
                return 10
            parts = shlex.split(cmd_str)
            if parts:
                cmd_bin = parts[0]
                bin_path = resolved_cwd / cmd_bin
                is_available = bool(
                    shutil.which(cmd_bin) or (bin_path.is_file() and os.access(bin_path, os.X_OK))
                )
                if not is_available:
                    typer.echo(
                        f"✗ El comando del control '{ctrl.id}' ('{cmd_bin}') no está "
                        "disponible o no es ejecutable",
                        err=True,
                    )
                    return 10
        typer.echo(f"✓ {len(runtime.config.controls)} controles configurados correctamente")

        typer.echo("Doctor: todas las comprobaciones pasaron con éxito.")
        return 0

    _safe_run(_execute)


@app.command()
def check(
    level: Annotated[
        str | None,
        typer.Option(
            "--level",
            "-l",
            help="Niveles de control a ejecutar separados por coma (e.g. L1,L2)",
        ),
    ] = None,
    cwd: Annotated[
        Path,
        typer.Option(
            "--cwd",
            help="Directorio de trabajo del proyecto",
            hidden=True,
        ),
    ] = DEFAULT_CWD,
) -> None:
    """Ejecuta los controles y la aceptación sobre el árbol actual."""

    def _execute() -> int:
        resolved_cwd = cwd.resolve()
        runtime = bootstrap_runtime(resolved_cwd)

        controls = [
            CheckControlSpec(
                id=c.id,
                level=c.level,
                run=c.run,
                report=c.report,
                mode=c.mode,
                allow_empty=c.allow_empty,
                timeout_s=c.timeout_s,
                when_exists=c.when_exists,
            )
            for c in runtime.config.controls
        ]

        acceptance = (
            CheckAcceptanceSpec(
                run=runtime.config.acceptance.run,
                report=runtime.config.acceptance.report,
                timeout_s=runtime.config.acceptance.timeout_s,
            )
            if runtime.config.acceptance
            else None
        )

        levels = [lvl.strip() for lvl in level.split(",")] if level else None

        res = runtime.check_project.execute(
            CheckProjectRequest(
                project_root=resolved_cwd,
                controls=controls,
                acceptance=acceptance,
                levels=levels,
                error_max_chars=runtime.config.limits.error_max_chars,
            )
        )

        for name, cr in res.control_results.items():
            if cr.skipped:
                typer.echo(f"- {name} omitido: {cr.skipped}")
                continue
            status_char = "✓" if cr.ok else "✗"
            typer.echo(f"{status_char} {name} ({cr.duration_ms}ms)")
            if not cr.ok and cr.output_path:
                typer.echo(f"  salida: {cr.output_path}")

        if not res.ok:
            if res.condensed_error:
                typer.echo(f"\n{res.condensed_error}", err=True)
            return 10

        return 0

    _safe_run(_execute)


@app.command()
def run(
    new: Annotated[
        bool,
        typer.Option(
            "--new",
            help="Forzar una ejecución nueva descartando la anterior",
        ),
    ] = False,
    cwd: Annotated[
        Path,
        typer.Option(
            "--cwd",
            help="Directorio de trabajo del proyecto",
            hidden=True,
        ),
    ] = DEFAULT_CWD,
) -> None:
    """Implementa y verifica los escenarios de forma determinista."""

    def _execute() -> int:
        return _execute_run(cwd, force_new=new)

    _safe_run(_execute)


def _record_stop(runtime: BootstrapResult, run_id: str, code: int, reason: str) -> None:
    """Leave in the journal why a run that can still be resumed stopped."""
    event = Event(
        id=uuid.uuid4().hex,
        type="lictor.run.stopped.v1",
        source="lictor/0.1.0",
        time=runtime.clock.now_iso(),
        runid=run_id,
        data={"exit_code": code, "reason": reason},
    )
    runtime.event_log.append(event)


def _refuse_resume(runtime: BootstrapResult, run_id: str, code: int, reason: str) -> int:
    typer.echo(f"Error: {reason}", err=True)
    _record_stop(runtime, run_id, code, reason)
    return code


def _anchor_problems(runtime: BootstrapResult, resolved_cwd: Path, values: Any) -> list[str]:
    """Compare the repository with the run's record, allowing what a crash can leave behind."""
    anchor = values.get("anchor") or {}
    observed = observe_anchor(runtime.vcs, resolved_cwd)
    accepted = {anchor.get("head")}
    verified = (values.get("last_verify") or {}).get("candidate")
    if verified:
        accepted.add(verified["sha"])
    # A driver commits inside its node: the checkpoint only knows the tree it was built from.
    tree = values.get("attempt_tree")
    if tree and not values.get("seal_ok") and runtime.vcs.get_tree("HEAD") == tree:
        accepted.add(observed["head"])
    return anchor_problems(anchor, observed, {sha for sha in accepted if sha})


def _integrity_refusal(runtime: BootstrapResult, resolved_cwd: Path, values: Any) -> str | None:
    """Say why this run can't go on. Control files come first: they are read without git."""
    anchor = values.get("anchor") or {}
    if anchor.get("repo") and runtime.vcs.fingerprint() != anchor["repo"]:
        return (
            "cambiaron los ficheros de control de .git (config, hooks o info) a mitad de "
            f"ejecución, y no se ha ejecutado git con ellos. {_UNDO_OR_START_OVER}"
        )

    saved_cfg_hash = values.get("config_hash")
    if saved_cfg_hash and runtime.config_hash != saved_cfg_hash:
        return (
            "la configuración cambió a mitad de ejecución: "
            f"lictor.json, el perfil o los ficheros del perfil. {_UNDO_OR_START_OVER}"
        )

    problems = _anchor_problems(runtime, resolved_cwd, values)
    if problems:
        return f"{'; '.join(problems)}. {_UNDO_OR_START_OVER}"

    if values.get("seal_ok") and not runtime.spec_source.verify_seal(
        resolved_cwd, runtime.graph_context.paths_driver
    ):
        return (
            f"la especificación o el driver cambiaron a mitad de ejecución. {_UNDO_OR_START_OVER}"
        )

    changed = runtime.vcs.status()
    if changed:
        return (
            f"hay cambios sin confirmar: {', '.join(changed)}. No se ha tocado nada: "
            "confírmalos o descártalos y vuelve a lanzar `lictor run`."
        )
    return None


def _check_resume_guard(
    runtime: BootstrapResult,
    resolved_cwd: Path,
    run_id: str,
    state_snapshot: Any,
) -> int | None:
    reason = _integrity_refusal(runtime, resolved_cwd, state_snapshot.values)
    if reason is not None:
        return _refuse_resume(runtime, run_id, int(ExitCode.PRECONDITION), reason)

    health = runtime.executor.health()
    if not health.ok:
        reason = (
            f"el ejecutor no está disponible para reanudar: {health.message or health.auth_status}"
        )
        return _refuse_resume(runtime, run_id, int(ExitCode.ENVIRONMENT_UNAVAILABLE), reason)

    return None


def _paused_exit_code(runtime: BootstrapResult, run_id: str, state_snapshot: Any) -> int | None:
    interrupts = [i for task in state_snapshot.tasks for i in task.interrupts]
    if not interrupts:
        return None
    pause_reason = interrupts[0].value.get("reason")
    if pause_reason == "branch_blocked":
        code = int(ExitCode.PRECONDITION)
        reason = (
            "Ejecución detenida antes de avanzar la rama, sin perder nada: "
            f"{interrupts[0].value.get('detail', '')} "
            "Resuélvelo y vuelve a lanzar `lictor run`; lo verificado se conserva."
        )
        typer.echo(reason, err=True)
        _record_stop(runtime, run_id, code, reason)
        return code
    if pause_reason == "agent_unavailable":
        code = int(ExitCode.ENVIRONMENT_UNAVAILABLE)
        reason = (
            "Ejecución detenida: el agente no está disponible, sin penalizar ningún escenario. "
            "Corrige el problema y vuelve a lanzar `lictor run`."
        )
    else:
        code = int(ExitCode.RATE_LIMITED)
        reason = (
            "Ejecución pausada por límite de cuota, sin penalizar ningún escenario. "
            "Vuelve a lanzar `lictor run` cuando se renueve."
        )
    detail = str(interrupts[0].value.get("detail", ""))
    typer.echo(f"{reason}\n{detail}".strip(), err=True)
    _record_stop(runtime, run_id, code, reason)
    return code


def _finished_exit_code(values: Any) -> int:
    """Return the exit code of a finished run after explaining it."""
    code = int(values.get("exit_code", 0) or 0)
    reason = values.get("exit_reason", "")
    if reason:
        typer.echo(reason if code == 0 else f"Error: {reason}", err=code != 0)
    return code


def _resume_run(
    runtime: BootstrapResult,
    graph: Any,
    existing_run_id: str,
    resolved_cwd: Path,
    state_snapshot: Any,
) -> int:
    guard_code = _check_resume_guard(runtime, resolved_cwd, existing_run_id, state_snapshot)
    if guard_code is not None:
        return guard_code

    next_node = (
        state_snapshot.next[0]
        if state_snapshot.next
        else str(state_snapshot.values.get("resume_to", "implement"))
    )
    resumed_event = Event(
        id=uuid.uuid4().hex,
        type="lictor.run.resumed.v1",
        source="lictor/0.1.0",
        time=runtime.clock.now_iso(),
        runid=existing_run_id,
        data={"resume_node": next_node},
    )
    runtime.event_log.append(resumed_event)

    chk_config: RunnableConfig = {"configurable": {"thread_id": existing_run_id}}
    has_interrupts = any(task.interrupts for task in state_snapshot.tasks)
    if has_interrupts:
        graph.invoke(Command(resume=True), config=chk_config, durability=DURABILITY)
    else:
        graph.invoke(None, config=chk_config, durability=DURABILITY)

    updated_state = graph.get_state(config=chk_config)
    paused_code = _paused_exit_code(runtime, existing_run_id, updated_state)
    if paused_code is not None:
        return paused_code

    return _finished_exit_code(updated_state.values)


def _start_new_run(
    runtime: BootstrapResult,
    graph: Any,
    current_run_file: Path,
    resolved_cwd: Path,
) -> int:
    run_id = runtime.clock.generate_run_id()
    current_run_file.write_text(run_id, encoding="utf-8")
    init_state = {
        "run_id": run_id,
        "config_hash": runtime.config_hash,
        "driver_fresh": False,
        "driver_attempts": 0,
        "attempt": 0,
        "status": {},
        "reasons": {},
        "totals": {},
    }
    chk_config: RunnableConfig = {"configurable": {"thread_id": run_id}}
    graph.invoke(init_state, config=chk_config, durability=DURABILITY)

    final_state = graph.get_state(config=chk_config)
    paused_code = _paused_exit_code(runtime, run_id, final_state)
    if paused_code is not None:
        return paused_code

    return _finished_exit_code(final_state.values)


def _execute_run(cwd: Path, force_new: bool) -> int:
    """Run or resume, holding the project: another run would sweep this one's copy and clone.

    A refusal writes nothing: the journal and `current_run` belong to the run in progress, which
    would read any change to them as tampering.
    """
    resolved_cwd = cwd.resolve()
    with exclusive_run(resolved_cwd, "lictor run"):
        return _run_holding_project(resolved_cwd, force_new)


def _run_holding_project(resolved_cwd: Path, force_new: bool) -> int:
    lictor_dir = resolved_cwd / ".lictor"
    current_run_file = lictor_dir / "current_run"
    db_path = lictor_dir / "checkpoints.sqlite"

    runtime = bootstrap_runtime(resolved_cwd)
    saver = get_sqlite_saver(db_path)
    graph = create_graph(runtime.graph_context, checkpointer=saver)
    sweep_scratch(resolved_cwd)

    existing_run_id: str | None = None
    if current_run_file.is_file() and not force_new:
        candidate = current_run_file.read_text(encoding="utf-8").strip()
        if candidate:
            existing_run_id = candidate

    if existing_run_id is not None:
        chk_config: RunnableConfig = {"configurable": {"thread_id": existing_run_id}}
        state_snapshot = graph.get_state(config=chk_config)

        if state_snapshot and state_snapshot.values:
            has_exit = state_snapshot.values.get("exit_code") is not None
            has_next = bool(state_snapshot.next)
            if not (has_exit and not has_next):
                return _resume_run(runtime, graph, existing_run_id, resolved_cwd, state_snapshot)

    return _start_new_run(runtime, graph, current_run_file, resolved_cwd)


@app.command()
def status(
    as_json: Annotated[
        bool,
        typer.Option(
            "--json",
            help="Mostrar estado en formato JSON",
        ),
    ] = False,
    cwd: Annotated[
        Path,
        typer.Option(
            "--cwd",
            help="Directorio de trabajo del proyecto",
            hidden=True,
        ),
    ] = DEFAULT_CWD,
) -> None:
    """Muestra el estado de la ejecución actual a partir del checkpoint y la bitácora."""

    def _execute() -> int:
        resolved_cwd = cwd.resolve()
        lictor_dir = resolved_cwd / ".lictor"
        current_run_file = lictor_dir / "current_run"
        if not current_run_file.is_file():
            typer.echo("No hay ninguna ejecución registrada en el proyecto.")
            return 0

        run_id = current_run_file.read_text(encoding="utf-8").strip()
        runtime = bootstrap_runtime(resolved_cwd)
        saver = get_sqlite_saver(lictor_dir / "checkpoints.sqlite")
        graph = create_graph(runtime.graph_context, checkpointer=saver)

        chk_config: RunnableConfig = {"configurable": {"thread_id": run_id}}
        state_snapshot = graph.get_state(config=chk_config)
        state_vals = state_snapshot.values if state_snapshot else {}
        next_nodes = list(state_snapshot.next) if state_snapshot else []

        payload: dict[str, Any] = {
            "run_id": run_id,
            "next_nodes": next_nodes,
            "exit_code": state_vals.get("exit_code"),
            "exit_reason": state_vals.get("exit_reason"),
            "status": state_vals.get("status", {}),
            "reasons": state_vals.get("reasons", {}),
            "totals": state_vals.get("totals", {}),
        }

        if as_json:
            typer.echo(json.dumps(payload, indent=2))
        else:
            typer.echo(f"Ejecución: {run_id}")
            typer.echo(f"Nodos pendientes: {next_nodes or 'ninguno'}")
            typer.echo(f"Código de salida: {payload['exit_code']}")
            if payload["exit_reason"]:
                typer.echo(f"Motivo: {payload['exit_reason']}")
            typer.echo("Escenarios:")
            for sid, st in payload["status"].items():
                typer.echo(f"  - {sid}: {st}")
        return 0

    _safe_run(_execute)


@app.command()
def report(
    run_id: Annotated[
        str | None,
        typer.Option(
            "--run",
            help="Identificador de la ejecución (por defecto la actual)",
        ),
    ] = None,
    cwd: Annotated[
        Path,
        typer.Option(
            "--cwd",
            help="Directorio de trabajo del proyecto",
            hidden=True,
        ),
    ] = DEFAULT_CWD,
) -> None:
    """Regenera el informe de una ejecución y muestra su ruta."""

    def _execute() -> int:
        resolved_cwd = cwd.resolve()
        lictor_dir = resolved_cwd / ".lictor"

        target_run = run_id
        if not target_run:
            current_run_file = lictor_dir / "current_run"
            if current_run_file.is_file():
                target_run = current_run_file.read_text(encoding="utf-8").strip()

        runtime = bootstrap_runtime(resolved_cwd)
        res = runtime.build_report.execute(
            BuildReportRequest(project_root=resolved_cwd, run_id=target_run)
        )
        typer.echo(f"Informe generado en: {res.report_path}")
        return 0

    _safe_run(_execute)


if __name__ == "__main__":
    app()
