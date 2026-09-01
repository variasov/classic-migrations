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

"""MySQL backend implemented with the ``PyMySQL`` driver."""

from datetime import datetime, timezone
from typing import Any

import pymysql
from classic.migrations.backends.base import Backend
from classic.migrations.exceptions import MigrationLockError


def _utcnow() -> datetime:
    """Return the current UTC time as a naive ``datetime``."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


class PyMySQLBackend(Backend, driver=pymysql):
    """MySQL migration backend using the ``PyMySQL`` driver."""

    transactional_ddl = True

    def connect(self) -> pymysql.connections.Connection:
        """Open a MySQL connection in autocommit mode."""
        kwargs: dict[str, Any] = {}
        if self.db_host is not None:
            kwargs["host"] = self.db_host
        if self.db_port is not None:
            kwargs["port"] = self.db_port
        if self.db_name is not None:
            kwargs["database"] = self.db_name
        if self.db_user is not None:
            kwargs["user"] = self.db_user
        if self.db_password is not None:
            kwargs["password"] = self.db_password
        kwargs.update(self.db_args)
        conn = self.driver.connect(**kwargs)
        conn.autocommit = True
        return conn

    def quote_identifier(self, s: str) -> str:
        """Return ``s`` backtick-quoted for MySQL.

        Uses ANSI double quotes when ``ANSI_QUOTES`` SQL mode is active.
        """
        if "\x00" in s:
            raise ValueError("identifier must not contain NUL bytes")
        if "ansi_quotes" in self._sql_mode().lower():
            return super().quote_identifier(s)
        return f"`{s.replace('`', '``')}`"

    def _sql_mode(self) -> str:
        """Return the current session's ``sql_mode``."""
        cursor = self.execute("SELECT @@SESSION.sql_mode")
        row = cursor.fetchone()
        return row[0] if row else ""

    def list_tables(self) -> list[str]:
        """Return the names of tables in the current database."""
        cursor = self.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = DATABASE()",
        )
        return [row[0] for row in cursor.fetchall()]

    def _create_migration_table(self) -> None:
        self.execute(
            f"CREATE TABLE {self.migration_table_quoted} ("
            "id BIGINT AUTO_INCREMENT PRIMARY KEY, "
            "migration_id VARCHAR(255) NOT NULL, "
            "created_at DATETIME NOT NULL, "
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
            "VALUES (%s, %s, %s)",
            (migration_id, _utcnow(), status),
        )

    def acquire_lock(self) -> None:
        """Acquire a named MySQL advisory lock for this migration table."""
        cursor = self.execute("SELECT GET_LOCK(%s, -1)", (self.migration_table,))
        acquired = cursor.fetchone()[0]
        if not acquired:
            raise MigrationLockError(
                "Could not acquire advisory lock on the database",
            )

    def release_lock(self) -> None:
        """Release the named MySQL advisory lock."""
        self.execute("SELECT RELEASE_LOCK(%s)", (self.migration_table,))

    def init_connection(self, connection: Any) -> None:
        """Configure the MySQL session's time zone and character set."""
        cursor = connection.cursor()
        try:
            cursor.execute("SET time_zone = '+00:00'")
            cursor.execute("SET NAMES utf8mb4")
        finally:
            cursor.close()
