from collections.abc import Generator
from contextlib import contextmanager
from typing import Any

from classic.migrations.backends.base import DatabaseBackend


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


class FakeBackend(DatabaseBackend, driver=_FakeDriver):

    def __init__(
        self,
        *,
        applied: list[tuple[str, str | None, str | None, Any]] | None = None,
        **kwargs: Any,
    ) -> None:
        self._applied: list[tuple[str, str | None, str | None, Any]] = list(applied) if applied else []
        self._migration_table_ready = False
        self._locked_flag = False
        self._closed = False
        self._cursor_count = 0
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

    # ------------------------------------------------------------------
    # Public state for test inspection.
    # ------------------------------------------------------------------
    @property
    def applied_list(self) -> list[tuple[str, str | None, str | None, Any]]:
        return list(self._applied)

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

    # ------------------------------------------------------------------
    # Connection plumbing.
    # ------------------------------------------------------------------
    def connect(self) -> _FakeConnection:
        return _FakeConnection()

    def init_connection(self, connection: Any) -> None:
        pass

    def cursor(self) -> _FakeCursor:
        self._cursor_count += 1
        return _FakeCursor()

    def begin(self) -> None:
        self._in_transaction = True

    def commit(self) -> None:
        self._in_transaction = False

    def rollback(self) -> None:
        self._in_transaction = False

    def savepoint(self, id: str) -> None:
        pass

    def savepoint_rollback(self, id: str) -> None:
        pass

    def close(self) -> None:
        self._closed = True

    @contextmanager
    def lock(self, timeout: int | None = None) -> Generator[None]:
        self._locked_flag = True
        yield

    @contextmanager
    def disable_transactions(self) -> Generator[None]:
        self.rollback()
        yield

    # ------------------------------------------------------------------
    # Table / migrations history.
    # ------------------------------------------------------------------
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

    def _applied_migrations(self) -> list[tuple[Any, ...]]:
        return list(self._applied)

    def mark_applied(self, migration_id: str, content_hash: str | None, comment: str | None = None, applied_at: Any = None) -> None:
        self._applied = [r for r in self._applied if r[0] != migration_id]
        self._applied.append((migration_id, content_hash, comment, applied_at))

    def unmark(self, migration_id: str) -> None:
        self._applied = [r for r in self._applied if r[0] != migration_id]

    def is_applied(self, migration_id: str) -> bool:
        return any(r[0] == migration_id for r in self._applied)