# Copyright 2015 Oliver Cope
# Copyright 2026 Sergey Variasov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""SQLite backend implemented with the standard ``sqlite3`` driver."""

import sqlite3
from datetime import datetime, timezone
from typing import Any

from classic.migrations.backends.base import Backend

# SQLite has no infinite busy timeout (it is an int of ms); ~24 days.
_MAX_LOCK_TIMEOUT = 2**31 // 1000 - 1


def _utcnow_str() -> str:
    """Return the current UTC time as an ISO-formatted string."""
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")


class SQLiteBackend(Backend, driver=sqlite3):
    """SQLite migration backend using the standard ``sqlite3`` driver.

    The main connection works in autocommit; transactions are opened
    explicitly by :class:`TransactionManager`.
    """

    transactional_ddl = True

    _lock_connection: sqlite3.Connection | None = None

    def connect(self) -> sqlite3.Connection:
        """Open a shared-cache SQLite connection with autocommit."""
        conn = self.driver.connect(
            f"file:{self.db_name}?cache=shared",
            uri=True,
            **self.db_args,
        )
        conn.isolation_level = None
        return conn

    def list_tables(self) -> list[str]:
        """Return the names of all tables in the database."""
        cursor = self.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return [row[0] for row in cursor.fetchall()]

    def _create_migration_table(self) -> None:
        self.execute(
            f"CREATE TABLE {self.migration_table_quoted} ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "migration_id VARCHAR(255) NOT NULL, "
            "created_at TIMESTAMP NOT NULL, "
            "status VARCHAR(16) NOT NULL)",
        )

    def _copy_versions(self) -> None:
        self.execute(
            "INSERT INTO "
            f"{self.migration_table_quoted} (migration_id, created_at, status) "
            "SELECT migration_id, applied_at_utc, 'APPLIED' "
            f"FROM {self.versions_table_quoted}",
        )

    def _migration_history(self) -> list[tuple[Any, ...]]:
        cursor = self.execute(
            "SELECT migration_id, created_at, status "
            f"FROM {self.migration_table_quoted} ORDER BY id",
        )
        return [tuple(row) for row in cursor.fetchall()]

    def mark(self, migration_id: str, status: str) -> None:
        """Append a migration history event."""
        self.execute(
            "INSERT INTO "
            f"{self.migration_table_quoted} (migration_id, created_at, status) "
            "VALUES (?, ?, ?)",
            (migration_id, _utcnow_str(), status),
        )

    def _lock_path(self) -> str | None:
        """Return the companion lock file path, or ``None`` if locking is off."""
        if not self.db_name or self.db_name == ":memory:":
            return None
        return f"{self.db_name}.{self.migration_table}.lock"

    def acquire_lock(self) -> None:
        """Acquire an inter-process lock via a companion lock file.

        A separate connection to ``<db_name>.<migration_table>.lock`` runs
        ``BEGIN IMMEDIATE`` with the busy timeout set to :attr:`lock_timeout`
        (``None`` waits ~24 days, effectively forever; ``0`` fails
        immediately), so a concurrent migrator waits and then fails with
        :class:`MigrationLockError`. The main
        database is not locked and the main connection stays in autocommit:
        each transactional migration opens its own transaction, while
        non-transactional migrations and hooks run outside transactions.

        An in-memory database lives in a single process, so no lock is
        taken for it.
        """
        path = self._lock_path()
        if path is None:
            return
        timeout = _MAX_LOCK_TIMEOUT if self.lock_timeout is None else self.lock_timeout
        conn = self.driver.connect(path, timeout=min(timeout, _MAX_LOCK_TIMEOUT))
        conn.isolation_level = None
        try:
            conn.execute("BEGIN IMMEDIATE")
        except self.DatabaseError as e:
            conn.close()
            raise self.lock_error() from e
        self._lock_connection = conn

    def release_lock(self) -> None:
        """Release the lock by closing the lock connection (no-op if not held).

        The lock file is intentionally left on disk: deleting it would race
        with another process that has just opened it to take the lock.
        """
        conn = self._lock_connection
        if conn is None:
            return
        self._lock_connection = None
        try:
            conn.rollback()
        finally:
            conn.close()
