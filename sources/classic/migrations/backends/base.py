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

"""Base classes for database backends and transaction management."""

import hashlib
from collections.abc import Generator
from contextlib import contextmanager
from types import TracebackType
from typing import Any, ClassVar, Self

from classic.migrations.exceptions import BadConnectionURI

STATUS_PENDING = "PENDING"
STATUS_APPLIED = "APPLIED"
STATUS_ROLLED_BACK = "ROLLED_BACK"
ALL_STATUSES = (STATUS_PENDING, STATUS_APPLIED, STATUS_ROLLED_BACK)


def lock_hash(identifier: str) -> int:
    """Return a stable 64-bit hash of ``identifier``.

    Unlike ``hash()``, this value is identical across Python processes
    (``hash()`` is randomized per process via ``PYTHONHASHSEED``), so
    advisory locks derived from it collide correctly across processes.
    """
    digest = hashlib.sha256(identifier.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big")


class TransactionManager:
    """Context manager that wraps a block of work in a backend transaction."""

    def __init__(self, backend: "Backend") -> None:
        """Initialize a transaction manager for ``backend``."""
        self.backend = backend
        self._started = False

    def __enter__(self) -> Self:
        """Begin a transaction if one is not already open."""
        if not self.backend.in_transaction:
            self.backend.begin()
            self._started = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        """Commit on success, rollback on error."""
        if exc_type:
            if self._started:
                self.backend.rollback()
            return None
        if self._started:
            self.backend.commit()
        return None


class Backend:
    """Base class for database backends implementing migration operations.

    Subclasses are registered via the ``driver=`` keyword argument, keyed by
    ``driver.__name__`` in :attr:`implementations`.
    """

    implementations: ClassVar[dict[str, type["Backend"]]] = {}

    driver: Any

    versions_table = "versions"

    transactional_ddl: ClassVar[bool] = False

    supports_schemas: ClassVar[bool] = False

    def __init_subclass__(cls, driver: Any = None, **kwargs: Any) -> None:
        """Register concrete backends in :attr:`implementations` by driver."""
        super().__init_subclass__(**kwargs)
        if driver is not None:
            cls.driver = driver
            cls.implementations[driver.__name__] = cls

    @classmethod
    def get_implementation(cls, name: str) -> type["Backend"]:
        """Return the backend class registered for ``name``."""
        try:
            return cls.implementations[name]
        except KeyError as exc:
            raise BadConnectionURI(
                f"Unrecognised database driver {name!r}",
            ) from exc

    def __init__(
        self,
        db_host: str | None = None,
        db_port: int | None = None,
        db_name: str | None = None,
        db_user: str | None = None,
        db_password: str | None = None,
        db_args: dict[str, Any] | None = None,
        migration_table: str = "migrations",
        migration_schema: str | None = None,
        versions_table: str = "versions",
        versions_schema: str | None = None,
    ) -> None:
        """Connect to the database and prepare quoted table identifiers."""
        self.db_host = db_host
        self.db_port = db_port
        self.db_name = db_name
        self.db_user = db_user
        self.db_password = db_password
        self.db_args = db_args or {}
        self.migration_table = migration_table
        self.migration_schema = migration_schema
        self.versions_table = versions_table
        self.versions_schema = versions_schema
        self.DatabaseError = self.driver.DatabaseError
        self._connection = self.connect()
        self.init_connection(self._connection)
        self.migration_table_quoted = self.quote_table(self.migration_table)
        self.versions_table_quoted = self.quote_versions_table()
        self._in_transaction = False

    # ------------------------------------------------------------------
    # Abstract query methods — implemented by each backend.
    # ------------------------------------------------------------------
    def list_tables(self) -> list[str]:
        """Return the names of tables in the target schema/database."""
        raise NotImplementedError

    def _create_migration_table(self) -> None:
        raise NotImplementedError

    def _copy_versions(self) -> None:
        raise NotImplementedError

    def _migration_history(self) -> list[tuple[Any, ...]]:
        raise NotImplementedError

    def mark(self, migration_id: str, status: str) -> None:
        """Append a history event for ``migration_id`` with ``status``."""
        raise NotImplementedError

    # ------------------------------------------------------------------
    # Generic orchestration (no SQL).
    # ------------------------------------------------------------------
    def ensure_migration_table(self) -> None:
        """Create the migration table, migrating a legacy ``versions`` table."""
        if self.migration_table in self.list_tables():
            return
        if self.versions_table in self.list_tables():
            self._create_migration_table()
            self._copy_versions()
        else:
            self._create_migration_table()

    def migration_history(self) -> list[tuple[Any, ...]]:
        """Return the full history event log.

        Each row is ``(migration_id, created_at, status)``.
        """
        self.ensure_migration_table()
        return self._migration_history()

    # ------------------------------------------------------------------
    # Connection and transaction plumbing.
    # ------------------------------------------------------------------
    @property
    def connection(self) -> Any:
        """Return the underlying database connection."""
        return self._connection

    @property
    def in_transaction(self) -> bool:
        """Return whether a transaction is currently open."""
        return self._in_transaction

    def close(self) -> None:
        """Close the underlying connection."""
        self.connection.close()

    def init_connection(self, connection: Any) -> None:
        """Apply any per-backend connection configuration (no-op by default)."""

    def connect(self) -> Any:
        """Establish and return a database connection."""
        raise NotImplementedError

    def quote_identifier(self, s: str) -> str:
        """Return ``s`` quoted as a database identifier."""
        if "\x00" in s:
            raise ValueError("identifier must not contain NUL bytes")
        quoted = s.replace('"', '""')
        return f'"{quoted}"'

    def quote_table(self, name: str) -> str:
        """Return ``name`` quoted, qualified by the migration schema if any."""
        if self.supports_schemas and self.migration_schema:
            return (
                f"{self.quote_identifier(self.migration_schema)}."
                f"{self.quote_identifier(name)}"
            )
        return self.quote_identifier(name)

    def quote_versions_table(self) -> str:
        """Return the legacy ``versions`` table name, schema-qualified."""
        if self.supports_schemas and self.versions_schema:
            return (
                f"{self.quote_identifier(self.versions_schema)}."
                f"{self.quote_identifier(self.versions_table)}"
            )
        return self.quote_identifier(self.versions_table)

    def transaction(self) -> TransactionManager:
        """Return a transaction context manager."""
        return TransactionManager(self)

    def cursor(self) -> Any:
        """Return a new database cursor."""
        return self.connection.cursor()

    def commit(self) -> None:
        """Commit the current transaction."""
        self.connection.commit()
        self._in_transaction = False

    def rollback(self) -> None:
        """Rollback the current transaction."""
        self.connection.rollback()
        self.init_connection(self.connection)
        self._in_transaction = False

    def begin(self) -> None:
        """Begin a transaction."""
        self._in_transaction = True
        self.execute("BEGIN")

    @contextmanager
    def disable_transactions(self) -> Generator[None]:
        """Provide a context in which transactions are disabled."""
        self.rollback()
        yield

    def acquire_lock(self) -> None:
        """Acquire a session-level lock on the database."""
        raise NotImplementedError(
            "Native session locking is not implemented for this backend",
        )

    def release_lock(self) -> None:
        """Release the session-level lock on the database."""
        raise NotImplementedError(
            "Native session locking is not implemented for this backend",
        )

    def execute(self, sql: str, params: Any = None) -> Any:
        """Execute ``sql`` and return the cursor."""
        cursor = self.cursor()
        if params is None:
            cursor.execute(sql)
        else:
            cursor.execute(sql, params)
        return cursor
