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
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

from classic.migrations.backends.base import Backend
from classic.migrations.exceptions import MigrationLockError


def _utcnow_str() -> str:
    """Return the current UTC time as an ISO-formatted string."""
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(sep=" ")


class SQLiteBackend(Backend, driver=sqlite3):
    """SQLite migration backend using the standard ``sqlite3`` driver."""

    transactional_ddl = True

    def connect(self) -> sqlite3.Connection:
        """Open a shared-cache SQLite connection with autocommit."""
        conn = self.driver.connect(
            f"file:{self.db_name}?cache=shared",
            uri=True,
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

    def acquire_lock(self) -> None:
        """Acquire the SQLite write lock."""
        try:
            self.begin_immediate()
        except self.DatabaseError as e:
            raise MigrationLockError(
                "Could not acquire advisory lock on the database",
            ) from e

    def release_lock(self) -> None:
        """Release the SQLite write lock."""
        self.commit()

    def begin_immediate(self) -> None:
        """Begin an immediate write transaction."""
        if self._in_transaction:
            raise RuntimeError("already in a transaction")
        self._in_transaction = True
        self.execute("BEGIN IMMEDIATE")

    @contextmanager
    def disable_transactions(self) -> Generator[None]:
        """Provide a transaction-disabled context (no-op for SQLite)."""
        yield
