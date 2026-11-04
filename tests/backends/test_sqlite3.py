import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest

from classic.migrations.backends.sqlite3 import SQLiteBackend
from classic.migrations.exceptions import MigrationLockError


@pytest.fixture
def backend() -> Generator[SQLiteBackend, None, None]:
    b = SQLiteBackend(db_name=":memory:")
    yield b
    b.close()


def test_list_tables_empty(backend: SQLiteBackend) -> None:
    assert backend.list_tables() == []


def test_create_migration_table(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    assert backend.migration_table in backend.list_tables()


def test_mark_applied_inserts_event(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.init", "APPLIED")

    history = backend._migration_history()
    assert len(history) == 1
    assert history[0][0] == "0001.init"
    assert history[0][2] == "APPLIED"


def test_unmark_appends_rolled_back_event(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.init", "APPLIED")
    backend.mark("0001.init", "ROLLED_BACK")

    history = backend._migration_history()
    assert len(history) == 2
    assert history[0][2] == "APPLIED"
    assert history[1][2] == "ROLLED_BACK"
    assert history[0][0] == history[1][0] == "0001.init"


def test_reapply_makes_migration_applied_again(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.init", "APPLIED")
    backend.mark("0001.init", "ROLLED_BACK")
    backend.mark("0001.init", "APPLIED")

    history = backend._migration_history()
    assert len(history) == 3
    assert [row[2] for row in history] == ["APPLIED", "ROLLED_BACK", "APPLIED"]


def test_pending_status(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.init", "PENDING")
    backend.mark("0001.init", "APPLIED")

    history = backend._migration_history()
    assert [row[2] for row in history] == ["PENDING", "APPLIED"]


def test_rolled_back_migration_has_correct_status(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.a", "APPLIED")
    backend.mark("0002.b", "APPLIED")
    backend.mark("0001.a", "ROLLED_BACK")

    history = backend._migration_history()
    assert len(history) == 3
    assert history[0][2] == "APPLIED"
    assert history[1][2] == "APPLIED"
    assert history[2][2] == "ROLLED_BACK"


def test_ensure_migration_table_creates(backend: SQLiteBackend) -> None:
    backend.ensure_migration_table()
    assert backend.migration_table in backend.list_tables()


def test_ensure_migration_table_idempotent(backend: SQLiteBackend) -> None:
    backend.ensure_migration_table()
    backend.ensure_migration_table()
    assert backend.migration_table in backend.list_tables()


def test_copy_versions(tmp_path: Path) -> None:
    db_file = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(db_file))
    conn.execute(
        "CREATE TABLE versions ("
        "migration_hash VARCHAR(64), migration_id VARCHAR(255), "
        "applied_at_utc TIMESTAMP, PRIMARY KEY (migration_hash))",
    )
    conn.execute(
        "INSERT INTO versions (migration_hash, migration_id, applied_at_utc) "
        "VALUES ('abc', '0001.old', '2020-01-01 00:00:00')",
    )
    conn.commit()
    conn.close()

    b = SQLiteBackend(db_name=str(db_file))
    try:
        b.ensure_migration_table()
        history = b._migration_history()
        assert len(history) == 1
        assert history[0][0] == "0001.old"
        assert history[0][2] == "APPLIED"
    finally:
        b.close()


def test_lock_memory_is_noop(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    backend.acquire_lock()
    backend.acquire_lock()
    backend.mark("0001.init", "APPLIED")
    backend.release_lock()
    assert len(backend._migration_history()) == 1


def test_release_lock_without_acquire_is_noop(tmp_path: Path) -> None:
    b = SQLiteBackend(db_name=str(tmp_path / "db.sqlite"))
    try:
        b.release_lock()
    finally:
        b.close()


def test_lock_does_not_open_transaction(tmp_path: Path) -> None:
    b = SQLiteBackend(db_name=str(tmp_path / "db.sqlite"))
    try:
        b.acquire_lock()
        assert not b.in_transaction
        assert not b.connection.in_transaction
        b.release_lock()
    finally:
        b.close()


def test_lock_uses_companion_file(tmp_path: Path) -> None:
    db_file = tmp_path / "db.sqlite"
    b = SQLiteBackend(db_name=str(db_file), migration_table="hist")
    try:
        b.acquire_lock()
        assert (tmp_path / "db.sqlite.hist.lock").exists()
        b.release_lock()
    finally:
        b.close()


def test_lock_second_backend_fails_immediately(tmp_path: Path) -> None:
    db_file = str(tmp_path / "db.sqlite")
    first = SQLiteBackend(db_name=db_file)
    second = SQLiteBackend(db_name=db_file)
    try:
        first.acquire_lock()
        with pytest.raises(MigrationLockError):
            second.acquire_lock()
        first.release_lock()
        second.acquire_lock()
        second.release_lock()
    finally:
        first.close()
        second.close()


def test_lock_does_not_block_other_connections(tmp_path: Path) -> None:
    db_file = str(tmp_path / "db.sqlite")
    b = SQLiteBackend(db_name=db_file)
    other = sqlite3.connect(db_file, timeout=0)
    try:
        b.ensure_migration_table()
        b.acquire_lock()
        other.execute("CREATE TABLE t(id INTEGER)")
        other.execute("INSERT INTO t VALUES (1)")
        other.commit()
        assert other.execute("SELECT COUNT(*) FROM migrations").fetchone()[0] == 0
        b.release_lock()
    finally:
        other.close()
        b.close()


def test_commit_and_rollback_in_autocommit_mode(tmp_path: Path) -> None:
    db_file = str(tmp_path / "db.sqlite")
    b = SQLiteBackend(db_name=db_file)
    other = sqlite3.connect(db_file, timeout=0)
    try:
        b._create_migration_table()
        b.begin()
        b.mark("0001.a", "APPLIED")
        b.rollback()
        assert b._migration_history() == []
        b.begin()
        b.mark("0001.a", "APPLIED")
        b.commit()
        assert other.execute("SELECT COUNT(*) FROM migrations").fetchone()[0] == 1
    finally:
        other.close()
        b.close()


def test_vacuum_outside_transaction(tmp_path: Path) -> None:
    b = SQLiteBackend(db_name=str(tmp_path / "db.sqlite"))
    try:
        b.acquire_lock()
        b.execute("VACUUM")
        b.release_lock()
    finally:
        b.close()


def test_transaction_commit(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    with backend.transaction():
        backend.mark("0001.init", "APPLIED")
    history = backend._migration_history()
    assert len(history) == 1


def test_transaction_rollback(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    with pytest.raises(RuntimeError, match="forced"), backend.transaction():
        backend.mark("0001.init", "APPLIED")
        raise RuntimeError("forced")
    history = backend._migration_history()
    assert len(history) == 0


def test_disable_transactions_noop(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    with backend.disable_transactions():
        backend.mark("0001.init", "APPLIED")
    history = backend._migration_history()
    assert len(history) == 1


def test_quote_identifier(backend: SQLiteBackend) -> None:
    quoted = backend.quote_identifier("my_table")
    assert quoted == '"my_table"'


def test_quote_identifier_with_quotes(backend: SQLiteBackend) -> None:
    quoted = backend.quote_identifier('test"table')
    assert quoted == '"test""table"'


def test_execute_cursor(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.init", "APPLIED")
    cursor = backend.execute(
        f"SELECT migration_id FROM {backend.migration_table} WHERE status = 'APPLIED'",
    )
    row = cursor.fetchone()
    assert row[0] == "0001.init"


def test_migration_table_quoted_custom_name() -> None:
    b = SQLiteBackend(db_name=":memory:", migration_table="my_history")
    try:
        assert b.migration_table_quoted == '"my_history"'
        b._create_migration_table()
        assert "my_history" in b.list_tables()
    finally:
        b.close()


def test_list_tables_after_create(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    tables = backend.list_tables()
    assert backend.migration_table in tables


def test_migration_history_returns_all_events(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.a", "APPLIED")
    backend.mark("0002.b", "APPLIED")
    backend.mark("0001.a", "ROLLED_BACK")

    history = backend.migration_history()
    assert len(history) == 3
    assert [row[0] for row in history] == ["0001.a", "0002.b", "0001.a"]
    assert [row[2] for row in history] == ["APPLIED", "APPLIED", "ROLLED_BACK"]


def test_migration_history_no_comment_column(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.a", "APPLIED")
    history = backend._migration_history()
    assert len(history[0]) == 3


def test_migration_schema_ignored() -> None:
    b = SQLiteBackend(db_name=":memory:", migration_schema="foo")
    try:
        assert b.migration_table_quoted == '"migrations"'
        b.ensure_migration_table()
        assert "migrations" in b.list_tables()
        assert "foo" not in b.list_tables()
    finally:
        b.close()
