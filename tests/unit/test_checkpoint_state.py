"""What a run's checkpoint holds, read alone: no graph and no `lictor.json` (G8 of 0.23.2)."""

from pathlib import Path

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import empty_checkpoint

from lictor.orchestration.graph import checkpoint_state, get_sqlite_saver


def _store(db_path: Path, run_id: str, values: dict[str, object]) -> None:
    saver = get_sqlite_saver(db_path)
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = values
    config: RunnableConfig = {"configurable": {"thread_id": run_id, "checkpoint_ns": ""}}
    saver.put(config, checkpoint, {}, {})
    saver.conn.close()


def test_no_file_holds_no_run(tmp_path: Path) -> None:
    assert checkpoint_state(tmp_path / "checkpoints.sqlite", "r1") == ("none", "")
    assert not (tmp_path / "checkpoints.sqlite").exists()


def test_a_file_without_tables_holds_no_run_yet(tmp_path: Path) -> None:
    """M1: a kill between creating the file and `setup()` leaves it empty."""
    db_path = tmp_path / "checkpoints.sqlite"
    db_path.write_bytes(b"")
    assert checkpoint_state(db_path, "r1") == ("none", "")


def test_a_run_that_set_its_exit_code_finished_and_one_without_goes_on(tmp_path: Path) -> None:
    db_path = tmp_path / "checkpoints.sqlite"
    _store(db_path, "acabado", {"run_id": "acabado", "exit_code": 0})
    _store(db_path, "pausado", {"run_id": "pausado", "current": "IVA-001"})

    assert checkpoint_state(db_path, "acabado") == ("finished", "")
    assert checkpoint_state(db_path, "pausado") == ("open", "")
    assert checkpoint_state(db_path, "otro") == ("none", "")


def test_bytes_that_arent_a_checkpoint_are_unreadable_and_say_why(tmp_path: Path) -> None:
    db_path = tmp_path / "checkpoints.sqlite"
    db_path.write_bytes(b"esto no es una base de datos" * 200)

    state, problem = checkpoint_state(db_path, "r1")

    assert state == "unreadable"
    assert problem
