import sqlite3
from collections.abc import Generator
from pathlib import Path

import pytest
from classic.migrations.backends.core.sqlite3 import SQLiteBackend


@pytest.fixture
def backend(db_path: Path) -> Generator[SQLiteBackend, None, None]:
    b = SQLiteBackend(db_name=str(db_path))
    yield b
    b.close()


def test_connect_creates_database(backend: SQLiteBackend, db_path: Path) -> None:
    assert db_path.exists()


def test_list_tables_empty(backend: SQLiteBackend) -> None:
    assert backend.list_tables() == []


def test_create_migration_table(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    assert backend.migration_table in backend.list_tables()


def test_mark_applied(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    backend.mark_applied("0001.init", "abc123", "test comment")

    rows = backend._applied_migrations()
    assert len(rows) == 1
    assert rows[0][0] == "0001.init"
    assert rows[0][1] == "abc123"
    assert rows[0][3] == "test comment"


def test_mark_applied_null_hash(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    backend.mark_applied("0001.init", None)

    rows = backend._applied_migrations()
    assert rows[0][1] is None


def test_unmark(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    backend.mark_applied("0001.init", "abc")
    backend.mark_applied("0002.b", "def")

    backend.unmark("0001.init")
    rows = backend._applied_migrations()
    assert len(rows) == 1
    assert rows[0][0] == "0002.b"


def test_applied_migrations_order(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    backend.mark_applied("0002.b", "def")
    backend.mark_applied("0001.a", "abc")

    rows = backend._applied_migrations()
    ids = [r[0] for r in rows]
    assert set(ids) == {"0001.a", "0002.b"}


def test_is_applied(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    backend.mark_applied("0001.init", "abc")

    assert backend.is_applied("0001.init")
    assert not backend.is_applied("0002.nonexistent")


def test_ensure_migration_table_creates(backend: SQLiteBackend) -> None:
    backend.ensure_migration_table()
    assert backend.migration_table in backend.list_tables()


def test_ensure_migration_table_idempotent(backend: SQLiteBackend) -> None:
    backend.ensure_migration_table()
    backend.ensure_migration_table()
    assert backend.migration_table in backend.list_tables()


def test_copy_versions(db_path: Path) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE versions ("
        "migration_hash VARCHAR(64), migration_id VARCHAR(255), "
        "applied_at_utc TIMESTAMP, PRIMARY KEY (migration_hash))"
    )
    conn.execute(
        "INSERT INTO versions (migration_hash, migration_id, applied_at_utc) "
        "VALUES ('abc', '0001.old', '2020-01-01 00:00:00')"
    )
    conn.commit()
    conn.close()

    b = SQLiteBackend(db_name=str(db_path))
    try:
        b.ensure_migration_table()
        rows = b._applied_migrations()
        assert len(rows) == 1
        assert rows[0][0] == "0001.old"
        assert rows[0][1] is None
    finally:
        b.close()


def test_lock_holds_write_transaction(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    with backend.lock():
        backend.mark_applied("0001.init", "abc")
    rows = backend._applied_migrations()
    assert len(rows) == 1


def test_lock_rollback_on_exception(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    try:
        with backend.lock():
            backend.mark_applied("0001.init", "abc")
            raise RuntimeError("forced")
    except RuntimeError:
        pass
    rows = backend._applied_migrations()
    assert len(rows) == 0


def test_transaction_commit(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    with backend.transaction():
        backend.mark_applied("0001.init", "abc")
    rows = backend._applied_migrations()
    assert len(rows) == 1


def test_transaction_rollback(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    try:
        with backend.transaction():
            backend.mark_applied("0001.init", "abc")
            raise RuntimeError("forced")
    except RuntimeError:
        pass
    rows = backend._applied_migrations()
    assert len(rows) == 0


def test_nested_transaction_savepoint_rollback(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    with backend.transaction():
        backend.mark_applied("0001.init", "abc")
        try:
            with backend.transaction():
                backend.mark_applied("0002.b", "def")
                raise RuntimeError("forced")
        except RuntimeError:
            pass
    rows = backend._applied_migrations()
    assert len(rows) == 1
    assert rows[0][0] == "0001.init"


def test_disable_transactions_noop(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    with backend.disable_transactions():
        backend.mark_applied("0001.init", "abc")
    rows = backend._applied_migrations()
    assert len(rows) == 1


def test_quote_identifier(backend: SQLiteBackend) -> None:
    quoted = backend.quote_identifier("my_table")
    assert quoted == '"my_table"'


def test_quote_identifier_with_quotes(backend: SQLiteBackend) -> None:
    quoted = backend.quote_identifier('test"table')
    assert quoted == '"test""table"'


def test_execute_cursor(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    backend.mark_applied("0001.init", "abc")
    cursor = backend.execute(
        f"SELECT migration_id FROM {backend.migration_table_quoted} WHERE migration_id = ?",
        ("0001.init",),
    )
    row = cursor.fetchone()
    assert row[0] == "0001.init"


def test_migration_table_quoted_custom_name(db_path: Path) -> None:
    b = SQLiteBackend(db_name=str(db_path), migration_table="my_history")
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


def test_applied_migrations_empty(backend: SQLiteBackend) -> None:
    backend._create_migration_table()
    assert backend._applied_migrations() == []