"""0.2.0, F19: a real tampering of the checkpoint fails the attempt, it doesn't kill the run.

`IntegritySnapshot` puts back the bytes of `checkpoints.sqlite` and its `-wal` while the run's own
connection is still open. Before 0.2.0 that connection could stop seeing its tables
(`no such table: checkpoints`) and the run died with an exception instead of failing the attempt
at `L0-estado`, emitting `state.tampered` and carrying on.
"""

import sqlite3
from collections.abc import Callable
from pathlib import Path

import pytest

from lictor.domain.model import AgentTask
from tests.integration.repro.conftest import ProjectFactory
from tests.integration.repro.harness import (
    Agent,
    Outcome,
    events_of,
    false_greens,
    good_impl,
    green_ids,
    require,
    run_lictor,
)


def _delete_and_checkpoint(database: Path) -> None:
    """Rewrite the data and fold the WAL into the main file, as a full checkpoint does."""
    connection = sqlite3.connect(database)
    connection.execute("delete from writes")
    connection.commit()
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()


def _drop_the_tables(database: Path) -> None:
    connection = sqlite3.connect(database)
    connection.execute("drop table checkpoints")
    connection.execute("drop table writes")
    connection.commit()
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()


def _fold_and_unlink_the_wal(database: Path) -> None:
    """What closing the last writable connection does: fold the WAL, then delete it and `-shm`.

    The run's connection keeps descriptors to the files it opened, now unlinked, while the
    restore writes new ones with the old bytes.
    """
    connection = sqlite3.connect(database)
    connection.execute("delete from writes")
    connection.commit()
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    for suffix in ("-wal", "-shm"):
        Path(f"{database}{suffix}").unlink(missing_ok=True)


def _remove_the_files(database: Path) -> None:
    for suffix in ("", "-wal", "-shm"):
        Path(f"{database}{suffix}").unlink(missing_ok=True)


def _overwrite_with_garbage(database: Path) -> None:
    database.write_bytes(b"no es una base de datos" * 100)
    Path(f"{database}-wal").unlink(missing_ok=True)


@pytest.mark.parametrize(
    "attack",
    [
        _delete_and_checkpoint,
        _drop_the_tables,
        _fold_and_unlink_the_wal,
        _remove_the_files,
        _overwrite_with_garbage,
    ],
)
def test_f19_tampering_with_the_checkpoint_fails_the_attempt_and_the_run_goes_on(
    make_project: ProjectFactory, attack: Callable[[Path], None]
) -> None:
    root = make_project()
    database = root / ".lictor" / "checkpoints.sqlite"
    attacked: list[bool] = []

    def impl(task: AgentTask, scenario_id: str, attempt: int) -> None:
        good_impl(task, scenario_id, attempt)
        if scenario_id == "IVA-001" and attempt == 1:
            require(database.is_file(), "no hay checkpoint")
            attack(database)
            attacked.append(True)

    agent = Agent(impl=impl)
    outcome = run_lictor(root, agent)

    require(attacked, "el ataque debía ejecutarse")
    assert outcome == Outcome(0), outcome
    first = events_of(root, "verify.finished")[0]["data"]
    assert first["failed_at"] == "L0-estado"
    (tampered,) = events_of(root, "state.tampered")
    assert ".lictor/checkpoints.sqlite" in tampered["data"]["changes"]
    assert agent.calls.count("IVA-001") == 2
    assert green_ids(root) == ["IVA-001", "IVA-002", "IVA-003"]
    assert false_greens(root) == []
