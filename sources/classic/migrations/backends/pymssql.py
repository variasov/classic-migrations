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

"""Microsoft SQL Server backend implemented with the ``pymssql`` driver."""

from datetime import datetime, timezone
from typing import Any

import pymssql
from classic.migrations.backends.base import Backend
from classic.migrations.exceptions import MigrationLockError


class PyMSSQLBackend(Backend, driver=pymssql):
    """SQL Server migration backend using the ``pymssql`` driver."""

    transactional_ddl = True
    supports_schemas = True

    def connect(self) -> Any:
        """Open a SQL Server connection in autocommit mode."""
        kwargs: dict[str, Any] = {}
        if self.db_host is not None:
            kwargs["server"] = self.db_host
        if self.db_port is not None:
            kwargs["port"] = str(self.db_port)
        if self.db_name is not None:
            kwargs["database"] = self.db_name
        if self.db_user is not None:
            kwargs["user"] = self.db_user
        if self.db_password is not None:
            kwargs["password"] = self.db_password
        kwargs.update(self.db_args)
        conn = self.driver.connect(**kwargs)
        conn.autocommit(True)  # noqa: FBT003
        return conn

    def ensure_migration_table(self) -> None:
        """Create the migration schema (if any) and the history table."""
        if self.migration_schema:
            self.execute(
                "IF SCHEMA_ID(%s) IS NULL EXEC('CREATE SCHEMA "
                f"{self.quote_identifier(self.migration_schema)}')",
                (self.migration_schema,),
            )
        super().ensure_migration_table()

    def list_tables(self) -> list[str]:
        """Return the names of tables in the migration schema."""
        if self.migration_schema:
            cursor = self.execute(
                "SELECT table_name FROM INFORMATION_SCHEMA.TABLES "
                "WHERE table_schema = %s",
                (self.migration_schema,),
            )
        else:
            cursor = self.execute(
                "SELECT table_name FROM INFORMATION_SCHEMA.TABLES "
                "WHERE table_schema = SCHEMA_NAME()",
            )
        return [row[0] for row in cursor.fetchall()]

    def _create_migration_table(self) -> None:
        self.execute(
            f"CREATE TABLE {self.migration_table_quoted} ("
            "id INT IDENTITY(1,1) PRIMARY KEY, "
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
        utcnow = datetime.now(timezone.utc).replace(tzinfo=None)
        self.execute(
            "INSERT INTO "
            f"{self.migration_table_quoted} (migration_id, created_at, status) "
            "VALUES (%s, %s, %s)",
            (migration_id, utcnow, status),
        )

    def acquire_lock(self) -> None:
        """Acquire a SQL Server application lock for this migration table."""
        cursor = self.execute(
            "DECLARE @res int; "
            "EXEC @res = sp_getapplock @Resource=%s, @LockMode='Exclusive', "
            "@LockOwner='Session', @LockTimeout=0; "
            "SELECT @res",
            (self.migration_table,),
        )
        row = cursor.fetchone()
        if row is None or row[0] != 0:
            raise MigrationLockError(
                "Could not acquire advisory lock on the database",
            )

    def release_lock(self) -> None:
        """Release the SQL Server application lock."""
        self.execute(
            "DECLARE @res int; "
            "EXEC @res = sp_releaseapplock @Resource=%s, @LockOwner='Session'; "
            "SELECT @res",
            (self.migration_table,),
        )

    def begin(self) -> None:
        """Begin an explicit transaction."""
        self._in_transaction = True
        self.execute("BEGIN TRANSACTION")

    def commit(self) -> None:
        """Commit the current transaction."""
        if not self._in_transaction:
            return
        self.execute("COMMIT TRANSACTION")
        self._in_transaction = False

    def rollback(self) -> None:
        """Rollback the current transaction."""
        if not self._in_transaction:
            return
        self.execute("ROLLBACK TRANSACTION")
        self._in_transaction = False
