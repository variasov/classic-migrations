from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from classic.migrations.backends.base import Backend


class FakeDatabaseError(Exception):
    pass


class _FakeDriver:
    __name__ = "fake"
    DatabaseError = FakeDatabaseError
    paramstyle = "qmark"


class _FakeCursor:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, sql: str, params: Any = None) -> None:
        self.executed.append(sql)

    def close(self) -> None:
        pass


class _FakeConnection:
    def __init__(self) -> None:
        pass

    def cursor(self) -> _FakeCursor:
        return _FakeCursor()

    def commit(self) -> None:
        pass

    def rollback(self) -> None:
        pass

    def close(self) -> None:
        pass


class FakeBackend(Backend, driver=_FakeDriver):
    """Test-only backend with in-memory event log and operation journal."""

    def __init__(
        self,
        *,
        applied: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        self._events: list[tuple[str, str, str]] = []
        for migration_id in applied or []:
            self._events.append((migration_id, "2000-01-01 00:00:00", "APPLIED"))
        self._migration_table_ready = False
        self._locked_flag = False
        self._closed = False
        self._cursor_count = 0
        self._oplog: list[tuple[str, str | None]] = []
        self.DatabaseError = FakeDatabaseError
        self.db_host = kwargs.get("db_host")
        self.db_port = kwargs.get("db_port")
        self.db_name = kwargs.get("db_name")
        self.db_user = kwargs.get("db_user")
        self.db_password = kwargs.get("db_password")
        self.db_args = kwargs.get("db_args", {})
        self.migration_table = kwargs.get("migration_table", "migrations")
        self.versions_table = "versions"
        self._in_transaction = False
        self._connection = _FakeConnection()

    @property
    def applied_list(self) -> list[str]:
        latest: dict[str, tuple[str, str, str]] = {}
        for event in self._events:
            latest[event[0]] = event
        return [
            migration_id
            for migration_id, event in latest.items()
            if event[2] == "APPLIED"
        ]

    @property
    def events(self) -> list[tuple[str, str, str]]:
        return list(self._events)

    @property
    def oplog(self) -> list[tuple[str, str | None]]:
        """Operation log: list of (operation, key)."""
        return list(self._oplog)

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def locked(self) -> bool:
        return self._locked_flag

    @property
    def cursor_count(self) -> int:
        return self._cursor_count

    @property
    def migration_table_ready(self) -> bool:
        return self._migration_table_ready

    def connect(self) -> _FakeConnection:
        return _FakeConnection()

    def init_connection(self, connection: Any) -> None:
        pass

    def cursor(self) -> _FakeCursor:
        self._cursor_count += 1
        self._oplog.append(("cursor", None))
        return _FakeCursor()

    def begin(self) -> None:
        self._in_transaction = True
        self._oplog.append(("begin", None))

    def commit(self) -> None:
        self._in_transaction = False
        self._oplog.append(("commit", None))

    def rollback(self) -> None:
        self._in_transaction = False
        self._oplog.append(("rollback", None))

    def close(self) -> None:
        self._closed = True
        self._locked_flag = False

    def acquire_lock(self) -> None:
        self._locked_flag = True

    def release_lock(self) -> None:
        self._locked_flag = False

    @contextmanager
    def disable_transactions(self) -> Generator[None]:
        self.rollback()
        yield

    def list_tables(self) -> list[str]:
        return [self.migration_table] if self._migration_table_ready else []

    def ensure_migration_table(self) -> None:
        if self._migration_table_ready:
            return
        self._migration_table_ready = True

    def _create_migration_table(self) -> None:
        pass

    def _copy_versions(self) -> None:
        pass

    def _migration_history(self) -> list[tuple[Any, ...]]:
        return list(self._events)

    def mark(self, migration_id: str, status: str) -> None:
        self._events.append((migration_id, "2000-01-01 00:00:00", status))
        self._oplog.append(("mark", migration_id))
