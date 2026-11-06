import sqlite3
import threading
import time
from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

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
    second = SQLiteBackend(db_name=db_file, lock_timeout=0)
    try:
        first.acquire_lock()
        start = time.monotonic()
        with pytest.raises(MigrationLockError) as exc_info:
            second.acquire_lock()
        assert time.monotonic() - start < 0.2
        assert str(exc_info.value) == "Could not acquire advisory lock on the database"
        first.release_lock()
        second.acquire_lock()
        second.release_lock()
    finally:
        first.close()
        second.close()


def test_lock_timeout_waits_then_fails(tmp_path: Path) -> None:
    db_file = str(tmp_path / "db.sqlite")
    first = SQLiteBackend(db_name=db_file)
    second = SQLiteBackend(db_name=db_file, lock_timeout=0.3)
    try:
        first.acquire_lock()
        start = time.monotonic()
        with pytest.raises(MigrationLockError, match=r"within 0.3 s"):
            second.acquire_lock()
        assert time.monotonic() - start >= 0.25
    finally:
        first.release_lock()
        first.close()
        second.close()


def test_lock_default_waits_until_released(tmp_path: Path) -> None:
    db_file = str(tmp_path / "db.sqlite")
    lock_path = f"{db_file}.migrations.lock"
    holder = sqlite3.connect(lock_path, isolation_level=None, check_same_thread=False)
    holder.execute("BEGIN IMMEDIATE")

    def release() -> None:
        holder.rollback()
        holder.close()

    second = SQLiteBackend(db_name=db_file)
    assert second.lock_timeout is None
    timer = threading.Timer(0.3, release)
    try:
        timer.start()
        start = time.monotonic()
        second.acquire_lock()
        assert time.monotonic() - start >= 0.2
        second.release_lock()
    finally:
        timer.join()
        second.close()


def test_negative_lock_timeout_raises() -> None:
    with pytest.raises(ValueError, match="lock_timeout"):
        SQLiteBackend(db_name=":memory:", lock_timeout=-1)


def test_db_args_passed_to_connect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spy = MagicMock(wraps=sqlite3.connect)
    monkeypatch.setattr(sqlite3, "connect", spy)
    b = SQLiteBackend(
        db_name=str(tmp_path / "db.sqlite"),
        db_args={"cached_statements": 7},
    )
    b.close()
    assert spy.call_args.kwargs["cached_statements"] == 7


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


def test_infinite_lock_timeout_means_wait_forever() -> None:
    b = SQLiteBackend(db_name=":memory:", lock_timeout=float("inf"))
    try:
        assert b.lock_timeout is None
    finally:
        b.close()


# ---- Mutant-killing tests for classic.migrations.backends.sqlite3 ----


def test_utcnow_str_uses_utc() -> None:
    """Kills mutmut_2: timezone.utc -> None (local time instead of UTC).

    ``_utcnow_str()`` must return a UTC timestamp. On this system (UTC+7),
    the mutant would produce a result ~7 hours off.
    """
    import classic.migrations.backends.sqlite3 as _sqlite3  # noqa: PLC0415

    result = _sqlite3._utcnow_str()
    result_dt = datetime.strptime(result, "%Y-%m-%d %H:%M:%S.%f").replace(
        tzinfo=timezone.utc
    )
    utc_now_aware = datetime.now(timezone.utc)
    diff = abs((utc_now_aware - result_dt).total_seconds())
    assert diff < 10, (
        f"_utcnow_str() returned {result!r}, "
        f"which is {diff:.0f}s away from UTC now ({utc_now_aware.isoformat(sep=' ')}). "
        "Possible local-time mutant (timezone.utc -> None)."
    )


def test_connect_passes_uri_true(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Kills mutmut_3/5/7: ``uri=True`` → None / removed / False.

    Captures the ``connect()`` call made during ``__init__`` and asserts that
    ``uri=True`` is passed.
    """
    spy = MagicMock(wraps=sqlite3.connect)
    monkeypatch.setattr(sqlite3, "connect", spy)
    b = SQLiteBackend(db_name=str(tmp_path / "db.sqlite"))
    b.close()
    assert spy.call_args.kwargs.get("uri") is True, (
        f"Expected uri=True, got {spy.call_args.kwargs.get('uri')!r}"
    )


def test_lock_path_returns_none_for_memory(backend: SQLiteBackend) -> None:
    """Kills mutmut_1 (or→and), mutmut_4 (":memory:"→"XX:memory:XX"),
    mutmut_5 (":memory:"→":MEMORY:").

    For an in-memory backend, ``_lock_path()`` must return ``None``.
    """
    assert backend._lock_path() is None


def test_lock_acquire_uses_correct_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kills mutmut_5: ``(self.lock_timeout is None) or True`` always takes MAX.

    With ``lock_timeout=0.5`` the original passes ``timeout=0.5`` to the lock
    connection; the mutant would pass ``_MAX_LOCK_TIMEOUT``.
    """
    from classic.migrations.backends.sqlite3 import _MAX_LOCK_TIMEOUT  # noqa: PLC0415

    db_file = str(tmp_path / "db.sqlite")
    b = SQLiteBackend(db_name=db_file, lock_timeout=0.5)
    try:
        connect_calls: list[tuple[str, dict[str, Any]]] = []
        original = sqlite3.connect

        def spy(path: str, **kwargs: Any) -> sqlite3.Connection:
            connect_calls.append((path, kwargs))
            return original(path, **kwargs)

        monkeypatch.setattr(sqlite3, "connect", spy)
        b.acquire_lock()
        b.release_lock()

        # The last connect call is for the lock file
        assert connect_calls, "No connect call was made during acquire_lock"
        _path, kwargs = connect_calls[-1]
        actual = kwargs.get("timeout")
        assert actual == 0.5, (
            f"Expected timeout=0.5, got {actual!r} "
            f"(mutant would give {_MAX_LOCK_TIMEOUT})"
        )
    finally:
        b.close()


def test_double_release_no_crash(tmp_path: Path) -> None:
    """Kills mutmut_3: ``self._lock_connection = ""`` instead of ``None``.

    After the first ``release_lock`` the mutant sets ``_lock_connection`` to
    ``""``; a second call would try ``"".rollback()`` -> ``AttributeError``.
    """
    b = SQLiteBackend(db_name=str(tmp_path / "db.sqlite"))
    try:
        b.acquire_lock()
        b.release_lock()
        b.release_lock()  # must be a no-op without the mutant
    finally:
        b.close()


def _record_execute_calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Return a recorder for the SQL strings passed to ``execute``."""
    calls: list[str] = []
    original_execute = SQLiteBackend.execute

    def recording(self: SQLiteBackend, sql: str, params: Any = None) -> Any:
        calls.append(sql)
        return original_execute(self, sql, params)

    monkeypatch.setattr(SQLiteBackend, "execute", recording)
    return calls


def test_list_tables_uses_exact_select_sql(
    backend: SQLiteBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kills mutmut_4: ``SELECT`` -> ``select`` in list_tables.

    SQL keywords are case-insensitive, so the exact SQL text passed to
    ``execute`` must be asserted.
    """
    calls = _record_execute_calls(monkeypatch)
    backend.list_tables()
    assert calls == ["SELECT name FROM sqlite_master WHERE type = 'table'"]


def test_mark_uses_exact_insert_sql(
    backend: SQLiteBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kills mutmut_6: ``INSERT INTO`` -> ``insert into`` in mark."""
    backend._create_migration_table()
    calls = _record_execute_calls(monkeypatch)
    backend.mark("0001.init", "APPLIED")
    assert calls == [
        (
            'INSERT INTO "migrations" (migration_id, created_at, status) '
            "VALUES (?, ?, ?)"
        ),
    ]


def test_migration_history_uses_exact_select_sql(
    backend: SQLiteBackend, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kills mutmut_4/5 in _migration_history.

    Lowercasing the keyword (mutmut_4) or uppercasing the column names
    (mutmut_5) is functionally identical for SQLite, so the exact SQL text
    passed to ``execute`` must be asserted.
    """
    backend._create_migration_table()
    backend.mark("0001.a", "APPLIED")
    calls = _record_execute_calls(monkeypatch)
    backend._migration_history()
    select_calls = [c for c in calls if c.startswith("SELECT migration_id")]
    assert select_calls == [
        'SELECT migration_id, created_at, status FROM "migrations" ORDER BY id',
    ]


def test_copy_versions_uses_exact_insert_sql(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kills mutmut_3: ``INSERT INTO`` -> ``insert into`` in _copy_versions."""
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
        b._create_migration_table()
        calls = _record_execute_calls(monkeypatch)
        b._copy_versions()
    finally:
        b.close()
    assert calls == [
        (
            'INSERT INTO "migrations" (migration_id, created_at, status) '
            "SELECT migration_id, applied_at_utc, 'APPLIED' FROM \"versions\""
        ),
    ]


def test_lock_connection_autocommit_and_begin_immediate(tmp_path: Path) -> None:
    """Kills mutmut_16 and mutmut_19 in acquire_lock.

    mutmut_16 replaces ``conn.isolation_level = None`` with ``""``; mutmut_19
    lowercases ``"BEGIN IMMEDIATE"``. Both changes are behaviourally
    equivalent, so the exact values are asserted via a fake lock driver.
    """

    class FakeLockConn:
        def __init__(self) -> None:
            self.isolation_level: Any = None
            self.executed: list[str] = []

        def execute(self, sql: str) -> None:
            self.executed.append(sql)

        def rollback(self) -> None:
            pass

        def close(self) -> None:
            pass

    class FakeLockDriver:
        def connect(self, path: str, **kwargs: Any) -> FakeLockConn:
            return FakeLockConn()

    b = SQLiteBackend(db_name=str(tmp_path / "db.sqlite"))
    try:
        b.driver = FakeLockDriver()
        b.acquire_lock()
        lock_conn: Any = b._lock_connection
        assert lock_conn.isolation_level is None
        assert lock_conn.executed == ["BEGIN IMMEDIATE"]
        b.release_lock()
    finally:
        b.close()


