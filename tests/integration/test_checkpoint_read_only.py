"""Whoever only reads the checkpoint leaves every byte a run guards as it was (F13)."""

import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.base import empty_checkpoint

from lictor.orchestration.graph import get_sqlite_saver, read_only_sqlite_saver

THREAD: RunnableConfig = {"configurable": {"thread_id": "run-1", "checkpoint_ns": ""}}
GUARDED = ("checkpoints.sqlite", "checkpoints.sqlite-wal")


def _guarded_bytes(lictor_dir: Path) -> dict[str, bytes | None]:
    return {
        name: (lictor_dir / name).read_bytes() if (lictor_dir / name).is_file() else None
        for name in GUARDED
    }


def test_no_checkpoint_gives_no_saver_and_creates_nothing(tmp_path: Path) -> None:
    db_path = tmp_path / ".lictor" / "checkpoints.sqlite"

    assert read_only_sqlite_saver(db_path) is None
    assert not (tmp_path / ".lictor").exists()


READ_IN_ANOTHER_PROCESS = """
import sys
from pathlib import Path
from lictor.orchestration.graph import read_only_sqlite_saver
reader = read_only_sqlite_saver(Path(sys.argv[1]))
found = reader.get_tuple({"configurable": {"thread_id": "run-1", "checkpoint_ns": ""}})
reader.conn.close()
print(found.config["configurable"]["checkpoint_id"])
"""


def test_reading_while_a_run_holds_the_checkpoint_changes_nothing_it_guards(
    tmp_path: Path,
) -> None:
    """In another process, as `lictor status` is: within one, SQLite shares the file's locks."""
    lictor_dir = tmp_path / ".lictor"
    writer = get_sqlite_saver(lictor_dir / "checkpoints.sqlite")
    stored = writer.put(THREAD, empty_checkpoint(), {}, {})
    before = _guarded_bytes(lictor_dir)
    assert before["checkpoints.sqlite-wal"], "the run's writes must still be in the WAL"

    read = subprocess.run(
        [sys.executable, "-c", READ_IN_ANOTHER_PROCESS, str(lictor_dir / "checkpoints.sqlite")],
        capture_output=True,
        text=True,
        check=True,
    )

    assert read.stdout.strip() == stored["configurable"]["checkpoint_id"]
    assert _guarded_bytes(lictor_dir) == before
    writer.conn.close()


def test_the_reader_cannot_write(tmp_path: Path) -> None:
    db_path = tmp_path / ".lictor" / "checkpoints.sqlite"
    get_sqlite_saver(db_path).conn.close()
    reader = read_only_sqlite_saver(db_path)
    assert reader is not None

    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        reader.put(THREAD, empty_checkpoint(), {}, {})
    reader.conn.close()
