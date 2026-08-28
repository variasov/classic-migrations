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

"""PostgreSQL backend implemented with the ``psycopg`` driver."""

from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import psycopg
from classic.migrations.backends.base import Backend


class PsycopgBackend(Backend, driver=psycopg):
    """PostgreSQL migration backend using the ``psycopg`` driver."""

    transactional_ddl = True
    supports_schemas = True

    def connect(self) -> psycopg.Connection:
        """Open a PostgreSQL connection in autocommit mode."""
        kwargs: dict[str, Any] = {}
        if self.db_host is not None:
            kwargs["host"] = self.db_host
        if self.db_port is not None:
            kwargs["port"] = self.db_port
        if self.db_name is not None:
            kwargs["dbname"] = self.db_name
        if self.db_user is not None:
            kwargs["user"] = self.db_user
        if self.db_password is not None:
            kwargs["password"] = self.db_password
        kwargs.update(self.db_args)
        conn = self.driver.connect(**kwargs)
        conn.autocommit = True
        return conn

    def list_tables(self) -> list[str]:
        """Return table names from the migration schema."""
        if self.migration_schema:
            cursor = self.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = %s",
                (self.migration_schema,),
            )
        else:
            cursor = self.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = current_schema()",
            )
        return [row[0] for row in cursor.fetchall()]

    def ensure_migration_table(self) -> None:
        """Create the migration schema (if any) and the history table."""
        if self.migration_schema:
            self.execute(
                "CREATE SCHEMA IF NOT EXISTS "
                f"{self.quote_identifier(self.migration_schema)}",
            )
        super().ensure_migration_table()

    def _create_migration_table(self) -> None:
        self.execute(
            f"CREATE TABLE {self.migration_table_quoted} ("
            "id SERIAL PRIMARY KEY, "
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
            f"SELECT migration_id, created_at, status "
            f"FROM {self.migration_table_quoted} ORDER BY id",
        )
        return [tuple(row) for row in cursor.fetchall()]

    def mark(self, migration_id: str, status: str) -> None:
        """Append a migration history event."""
        self.execute(
            "INSERT INTO "
            f"{self.migration_table_quoted} (migration_id, created_at, status) "
            "VALUES (%s, %s, %s)",
            (migration_id, datetime.now(timezone.utc).replace(tzinfo=None), status),
        )

    def acquire_lock(self) -> None:
        """Acquire a PostgreSQL advisory lock for this migration table."""
        lock_id = hash(self.migration_table)
        key1 = lock_id & 0x7FFFFFFF
        key2 = (lock_id >> 32) & 0x7FFFFFFF
        self.execute("SELECT pg_advisory_lock(%s, %s)", (key1, key2))

    def release_lock(self) -> None:
        """Release the PostgreSQL advisory lock."""
        lock_id = hash(self.migration_table)
        key1 = lock_id & 0x7FFFFFFF
        key2 = (lock_id >> 32) & 0x7FFFFFFF
        self.execute("SELECT pg_advisory_unlock(%s, %s)", (key1, key2))

    @contextmanager
    def disable_transactions(self) -> Generator[None]:
        """Rollback any open transaction for the duration of the context."""
        self.rollback()
        yield

    def commit(self) -> None:
        """Commit the current transaction."""
        self.execute("COMMIT")
        self._in_transaction = False

    def rollback(self) -> None:
        """Rollback the current transaction."""
        self.execute("ROLLBACK")
        self._in_transaction = False
