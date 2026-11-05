"""Command Line Interface for Lictor."""

import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Annotated, Any

import typer
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command

from lictor import __version__
from lictor.adapters.config.models import ControlConfig
from lictor.application.anchor import observe_anchor
from lictor.application.events import new_event
from lictor.application.run_files import (
    DOCTOR_DIR,
    aside_dir,
    exclusive_run,
    holding_project,
    prune_runs,
    sweep_scratch,
)
from lictor.application.usecases.build_report import BuildReportRequest
from lictor.application.usecases.check_project import CheckAcceptanceSpec, CheckControlSpec
from lictor.application.usecases.check_working_tree import CheckWorkingTreeRequest
from lictor.application.usecases.init_project import InitProjectRequest
from lictor.application.usecases.preconditions import PreconditionsRequest
from lictor.bootstrap import BootstrapResult, bootstrap_init, bootstrap_runtime
from lictor.domain.errors import ExitCode, LictorError
from lictor.domain.rules import (
    anchor_problems,
    baseline_head_accepted,
    describe_score,
    precondition_exit_code,
)
from lictor.orchestration.graph import DURABILITY, create_graph, get_sqlite_saver

app = typer.Typer(
    name="lictor",
    help="Implementa escenarios Gherkin con un agente y los verifica de forma determinista.",
    no_args_is_help=True,
)

DEFAULT_CWD = Path(".")


def _print_version(value: bool) -> None:
    if value:
        typer.echo(f"lictor {__version__}")
        raise typer.Exit()


@app.callback()
def _main(
    _version: Annotated[
        bool,
        typer.Option(
            "--version",
            callback=_print_version,
            is_eager=True,
            help="Muestra la versión de Lictor y sale.",
        ),
    ] = False,
) -> None:
    """Implementa escenarios Gherkin con un agente y los verifica de forma determinista."""


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
    """Comprueba las precondiciones de `lictor run` sin llamar al agente."""

    def _execute() -> int:
        resolved_cwd = cwd.resolve()
        runtime = bootstrap_runtime(resolved_cwd)
        typer.echo(f"✓ Configuración válida: perfil '{runtime.config.profile}'")

        # Building takes the temporary directory, which a run starting now would sweep.
        with holding_project(resolved_cwd) as held:
            log_dir = aside_dir(resolved_cwd, DOCTOR_DIR)
            if held:
                shutil.rmtree(log_dir, ignore_errors=True)
            request = PreconditionsRequest(
                run_id=f"doctor-{runtime.clock.generate_run_id()}",
                environment="throwaway" if held else "none",
                log_dir=log_dir,
                skip_reason="no comprobado: hay una ejecución en curso",
            )
            report = runtime.preconditions.execute(request)

        for check in report.checks:
            if check.skipped:
                typer.echo(f"- {check.name}: {check.skipped}")
            elif check.problems:
                for problem in check.problems:
                    typer.echo(f"✗ {check.name}: {problem.message}", err=True)
            else:
                typer.echo(f"✓ {check.name}")

        code = precondition_exit_code(report.problems)
        if code:
            typer.echo("Doctor: hay que resolver lo marcado con ✗.", err=True)
        else:
            typer.echo("Doctor: todas las comprobaciones pasaron.")
        return code

    _safe_run(_execute)


@app.command()
def check(
    level: Annotated[
        str | None,
        typer.Option(
            "--level",
            "-l",
            help="Niveles de control a ejecutar separados por coma (p. ej. L1,L2)",
        ),
    ] = None,
    control: Annotated[
        list[str] | None,
        typer.Option(
            "--control",
            "-c",
            help="Ejecutar solo este control (repetible); la aceptación es 'acceptance'",
        ),
    ] = None,
    keep_going: Annotated[
        bool,
        typer.Option(
            "--keep-going",
            help="Seguir tras el primer fallo y mostrar el resultado de todos los controles",
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
    """Verifica el proyecto como `lictor run`: en un clon limpio y en el entorno de verificación.

    Lo que está sin confirmar se verifica como el commit que resultaría de confirmarlo.
    """

    def _execute() -> int:
        resolved_cwd = cwd.resolve()
        # It builds in the temporary directory, which a run starting now would sweep.
        with exclusive_run(resolved_cwd, "lictor check"):
            runtime = bootstrap_runtime(resolved_cwd)
            config = runtime.config
            res = runtime.check_working_tree.execute(
                CheckWorkingTreeRequest(
                    run_id=f"check-{runtime.clock.generate_run_id()}",
                    controls=[_check_spec(c) for c in config.controls],
                    acceptance=CheckAcceptanceSpec(
                        run=config.acceptance.run,
                        report=config.acceptance.report,
                        timeout_s=config.acceptance.timeout_s,
                    ),
                    levels=[lvl.strip() for lvl in level.split(",")] if level else None,
                    control_ids=control or None,
                    keep_going=keep_going,
                    error_max_chars=config.limits.error_max_chars,
                )
            )

        typer.echo(f"Verificado: {res.subject}")
        for name, cr in res.check.control_results.items():
            if cr.skipped:
                typer.echo(f"- {name} omitido: {cr.skipped}")
                continue
            status_char = "✓" if cr.ok else "✗"
            score = f": {describe_score(cr.score)}" if cr.score else ""
            typer.echo(f"{status_char} {name} ({cr.duration_ms}ms){score}")
            if not cr.ok and cr.output_path:
                typer.echo(f"  salida: {cr.output_path}")

        if res.tampered:
            typer.echo(f"\nL0-entorno: {res.tampered}", err=True)
            return int(ExitCode.ENVIRONMENT_UNAVAILABLE)
        if not res.check.ok:
            if res.check.condensed_error:
                typer.echo(f"\n{res.check.condensed_error}", err=True)
            return int(ExitCode.PRECONDITION)
        return 0

    _safe_run(_execute)


def _check_spec(control: ControlConfig) -> CheckControlSpec:
    return CheckControlSpec(
        id=control.id,
        level=control.level,
        run=control.run,
        report=control.report,
        mode=control.mode,
        empty_exit_codes=control.empty_exit_codes,
        timeout_s=control.timeout_s,
        when_exists=control.when_exists,
        min_score=control.min_score,
    )


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
    event = new_event(
        runtime.clock,
        "lictor.run.stopped.v1",
        run_id,
        data={"exit_code": code, "reason": reason},
    )
    runtime.event_log.append(event)


def _refuse_resume(runtime: BootstrapResult, run_id: str, code: int, reason: str) -> int:
    typer.echo(f"Error: {reason}", err=True)
    _record_stop(runtime, run_id, code, reason)
    return code


def _anchor_problems(
    runtime: BootstrapResult, resolved_cwd: Path, values: Any, pending_node: str | None
) -> list[str]:
    """Compare the repository with the run's record, allowing what a crash can leave behind."""
    anchor = values.get("anchor") or {}
    observed = observe_anchor(runtime.vcs, resolved_cwd)
    accepted = {anchor.get("head")}
    # A red check registers greens with an empty commit; repeating it tests them all again.
    if (
        anchor.get("head")
        and observed["head"] != anchor["head"]
        and baseline_head_accepted(
            pending_node=pending_node,
            head_parent=runtime.vcs.parent_of("HEAD"),
            head_tree=runtime.vcs.get_tree("HEAD"),
            anchored_head=anchor["head"],
            anchored_tree=runtime.vcs.get_tree(anchor["head"]),
        )
    ):
        accepted.add(observed["head"])
    verified = (values.get("last_verify") or {}).get("candidate")
    if verified:
        accepted.add(verified["sha"])
    # A driver commits inside its node: the checkpoint only knows the tree it was built from.
    tree = values.get("attempt_tree")
    if tree and not values.get("seal_ok") and runtime.vcs.get_tree("HEAD") == tree:
        accepted.add(observed["head"])
    return anchor_problems(anchor, observed, {sha for sha in accepted if sha})


def _integrity_refusal(
    runtime: BootstrapResult, resolved_cwd: Path, values: Any, pending_node: str | None = None
) -> str | None:
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

    problems = _anchor_problems(runtime, resolved_cwd, values, pending_node)
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
    pending_node = state_snapshot.next[0] if state_snapshot.next else None
    reason = _integrity_refusal(runtime, resolved_cwd, state_snapshot.values, pending_node)
    if reason is not None:
        return _refuse_resume(runtime, run_id, int(ExitCode.PRECONDITION), reason)

    # The tree was checked above, with what a crash may leave; the environment is the run's own.
    report = runtime.preconditions.execute(
        PreconditionsRequest(run_id=run_id, clean_tree=False, environment="none")
    )
    if report.problems:
        reason = "no se puede reanudar: " + " ".join(p.message for p in report.problems)
        return _refuse_resume(runtime, run_id, precondition_exit_code(report.problems), reason)

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
    resumed_event = new_event(
        runtime.clock,
        "lictor.run.resumed.v1",
        existing_run_id,
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


def _retire_old_runs(
    runtime: BootstrapResult, resolved_cwd: Path, run_id: str, current_run_file: Path
) -> None:
    """Keep the most recent runs' evidence, the new one counting, and never the last run's."""
    keep = runtime.config.retention.keep_runs
    previous = (
        current_run_file.read_text(encoding="utf-8").strip() if current_run_file.is_file() else ""
    )

    def record(pruned: list[str]) -> None:
        runtime.event_log.append(
            new_event(
                runtime.clock,
                "lictor.runs.pruned.v1",
                run_id,
                data={"pruned": pruned, "keep_runs": keep},
            )
        )

    prune_runs(resolved_cwd, keep - 1, {previous}, record)


def _start_new_run(
    runtime: BootstrapResult,
    graph: Any,
    current_run_file: Path,
    resolved_cwd: Path,
) -> int:
    run_id = runtime.clock.generate_run_id()
    _retire_old_runs(runtime, resolved_cwd, run_id, current_run_file)
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

    resumable = None if force_new else _resumable_run(graph, current_run_file)
    if resumable is not None:
        run_id, state_snapshot = resumable
        return _resume_run(runtime, graph, run_id, resolved_cwd, state_snapshot)

    return _start_new_run(runtime, graph, current_run_file, resolved_cwd)


def _resumable_run(graph: Any, current_run_file: Path) -> tuple[str, Any] | None:
    """Return the run `lictor run` would resume, with its state, if there is one."""
    if not current_run_file.is_file():
        return None
    run_id = current_run_file.read_text(encoding="utf-8").strip()
    if not run_id:
        return None
    chk_config: RunnableConfig = {"configurable": {"thread_id": run_id}}
    state_snapshot = graph.get_state(config=chk_config)
    if not (state_snapshot and state_snapshot.values):
        return None
    finished = state_snapshot.values.get("exit_code") is not None and not state_snapshot.next
    return None if finished else (run_id, state_snapshot)


@app.command()
def register(
    scenarios: Annotated[
        list[str],
        typer.Argument(help="IDs de los escenarios que el código ya cumple (p. ej. IVA-001)"),
    ],
    yes: Annotated[
        bool,
        typer.Option("--yes", help="Confirmar sin preguntar lo que se afirma"),
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
    """Registra en verde escenarios que el código ya cumple, como afirmación tuya.

    Lictor no puede distinguir un test que pasa porque el código ya cumple el escenario de uno que
    no comprueba nada. Úsalo al adoptar Lictor en un proyecto con código, antes de `lictor run`.
    """

    def _execute() -> int:
        resolved_cwd = cwd.resolve()
        lictor_dir = resolved_cwd / ".lictor"
        # It moves the branch, which a run in progress has anchored.
        with exclusive_run(resolved_cwd, "lictor register"):
            runtime = bootstrap_runtime(resolved_cwd)
            graph = create_graph(
                runtime.graph_context,
                checkpointer=get_sqlite_saver(lictor_dir / "checkpoints.sqlite"),
            )
            if _resumable_run(graph, lictor_dir / "current_run") is not None:
                typer.echo(
                    "Error: hay una ejecución a medias y registrar movería la rama que ancló. "
                    "Termínala con `lictor run` o empieza otra con `lictor run --new`.",
                    err=True,
                )
                return int(ExitCode.PRECONDITION)
            plan = runtime.register_scenarios.plan(scenarios)
            if plan.problems:
                for problem in plan.problems:
                    typer.echo(f"✗ {problem.message}", err=True)
                return precondition_exit_code(plan.problems)
            for statement in plan.statements():
                typer.echo(f"- {statement}")
            if not yes and not _confirmed():
                typer.echo("No se ha registrado nada.", err=True)
                return int(ExitCode.PRECONDITION)
            sha = runtime.register_scenarios.execute(
                plan, f"register-{runtime.clock.generate_run_id()}"
            )
        typer.echo(
            f"Registrados en {sha[:12]}: {', '.join(plan.scenarios)}. "
            "El informe los mostrará como registrados a mano."
        )
        return 0

    _safe_run(_execute)


def _confirmed() -> bool:
    """Ask in a terminal; without one, refuse rather than assert on someone's behalf."""
    if not sys.stdin.isatty():
        typer.echo("Sin terminal para confirmarlo: vuelve a lanzarlo con --yes.", err=True)
        return False
    return typer.confirm("¿Lo afirmas?", default=False)


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
            # Informative: any green whose commit lacks Lictor-Run; anyone can write a trailer.
            "registered_by_hand": sorted(
                sid
                for sid, record in runtime.spec_history.records().items()
                if not record.by_lictor
            ),
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
            by_hand = set(payload["registered_by_hand"])
            for sid, st in payload["status"].items():
                mark = " (registrado a mano)" if sid in by_hand and st == "green" else ""
                typer.echo(f"  - {sid}: {st}{mark}")
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
