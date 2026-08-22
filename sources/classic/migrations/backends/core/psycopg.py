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

import time
from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any

import psycopg

from classic.migrations.backends.base import DatabaseBackend


class PsycopgBackend(DatabaseBackend, driver=psycopg):

    def connect(self) -> psycopg.Connection:
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
        cursor = self.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema()"
        )
        return [row[0] for row in cursor.fetchall()]

    def _create_migration_table(self) -> None:
        self.execute(
            f"CREATE TABLE {self.migration_table_quoted} ("
            "migration_id VARCHAR(255) PRIMARY KEY, "
            "content_hash VARCHAR(64) NULL, "
            "applied_at TIMESTAMP NOT NULL, "
            "comment VARCHAR(255) NULL)"
        )

    def _copy_versions(self) -> None:
        self.execute(
            f"INSERT INTO {self.migration_table_quoted} (migration_id, content_hash, applied_at, comment) "
            "SELECT migration_id, NULL, applied_at_utc, NULL "
            f"FROM {self.versions_table_quoted}"
        )

    def _applied_migrations(self) -> list[tuple[Any, ...]]:
        cursor = self.execute(
            "SELECT migration_id, content_hash, applied_at, comment "
            f"FROM {self.migration_table_quoted} ORDER BY applied_at, migration_id"
        )
        return [tuple(row) for row in cursor.fetchall()]

    def mark_applied(self, migration_id: str, content_hash: str | None, comment: str | None = None, applied_at: datetime | None = None) -> None:
        applied_at = applied_at or datetime.now(timezone.utc).replace(tzinfo=None)
        self.execute(
            f"INSERT INTO {self.migration_table_quoted} (migration_id, content_hash, applied_at, comment) "
            "VALUES (%s, %s, %s, %s)",
            (migration_id, content_hash, applied_at, comment),
        )

    def unmark(self, migration_id: str) -> None:
        self.execute(
            f"DELETE FROM {self.migration_table_quoted} WHERE migration_id = %s",
            (migration_id,),
        )

    @contextmanager
    def lock(self, timeout: int | None = None) -> Generator[None]:
        lock_id = hash(self.migration_table)
        key1 = lock_id & 0x7FFFFFFF
        key2 = (lock_id >> 32) & 0x7FFFFFFF

        if timeout is not None:
            deadline = time.monotonic() + timeout
            while True:
                acquired = self.execute(
                    "SELECT pg_try_advisory_lock(%s, %s)", (key1, key2)
                ).fetchone()[0]
                if acquired:
                    break
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        f"Could not acquire advisory lock within {timeout}s"
                    )
                time.sleep(0.1)
        else:
            self.execute("SELECT pg_advisory_lock(%s, %s)", (key1, key2))

        try:
            yield
        finally:
            self.execute("SELECT pg_advisory_unlock(%s, %s)", (key1, key2))

    @contextmanager
    def disable_transactions(self) -> Generator[None]:
        self.rollback()
        yield

    def commit(self) -> None:
        self.execute("COMMIT")
        self._in_transaction = False

    def rollback(self) -> None:
        self.execute("ROLLBACK")
        self._in_transaction = False