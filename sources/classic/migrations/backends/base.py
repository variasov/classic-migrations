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

from collections.abc import Generator
from contextlib import contextmanager
from itertools import count
from types import TracebackType
from typing import Any, ClassVar, Self

from classic.migrations.exceptions import BadConnectionURI


class TransactionManager:
    """
    Returned by the :meth:`~classic.migrations.backends.DatabaseBackend.transaction`
    context manager.

    If rollback is called, the transaction is flagged to be rolled back
    when the context manager block closes
    """

    def __init__(self, backend: "DatabaseBackend") -> None:
        self.backend = backend

    def __enter__(self) -> Self:
        self._do_begin()
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None) -> bool | None:
        if exc_type:
            self._do_rollback()
            return

        self._do_commit()

    def _do_begin(self) -> None:
        """
        Instruct the backend to begin a transaction
        """
        self.backend.begin()

    def _do_commit(self) -> None:
        """
        Instruct the backend to commit the transaction
        """
        self.backend.commit()

    def _do_rollback(self) -> None:
        """
        Instruct the backend to roll back the transaction
        """
        self.backend.rollback()


class SavepointTransactionManager(TransactionManager):

    id: str | None = None
    id_generator = count(1)

    def _do_begin(self) -> None:
        assert self.id is None
        self.id = f"sp_{next(self.id_generator)}"
        self.backend.savepoint(self.id)

    def _do_commit(self) -> None:
        """
        This does nothing.

        Trying to the release savepoint here could cause an database error in
        databases where DDL queries cause the transaction to be committed
        and all savepoints released.
        """

    def _do_rollback(self) -> None:
        assert self.id is not None
        self.backend.savepoint_rollback(self.id)


class DatabaseBackend:

    # Registry of concrete backends, keyed by the URI scheme they handle.
    # Populated automatically by ``__init_subclass__``.
    implementations: ClassVar[dict[str, type["DatabaseBackend"]]] = {}

    # The DB-API driver module this backend uses. Set on each concrete backend
    # class by passing the module to the ``driver`` keyword argument.
    driver: Any

    migration_table = "migrations"
    versions_table = "versions"

    _in_transaction = False

    def __init_subclass__(cls, driver: Any = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if driver is not None:
            cls.driver = driver
            cls.implementations[driver.__name__] = cls

    @classmethod
    def get_backend_class(cls, name: str) -> type["DatabaseBackend"]:
        try:
            return cls.implementations[name]
        except KeyError:
            raise BadConnectionURI(
                f"Unrecognised database driver {name!r}"
            )

    def __init__(self, db_host: str | None = None, db_port: int | None = None, db_name: str | None = None, db_user: str | None = None, db_password: str | None = None, db_args: dict[str, Any] | None = None, migration_table: str = "migrations") -> None:
        self.db_host = db_host
        self.db_port = db_port
        self.db_name = db_name
        self.db_user = db_user
        self.db_password = db_password
        self.db_args = db_args or {}
        self.migration_table = migration_table or "migrations"
        self.DatabaseError = self.driver.DatabaseError
        self._connection = self.connect()
        self.init_connection(self._connection)

    @property
    def migration_table_quoted(self) -> str:
        return self.quote_identifier(self.migration_table)

    @property
    def versions_table_quoted(self) -> str:
        return self.quote_identifier(self.versions_table)

    # ------------------------------------------------------------------
    # Query methods.
    #
    # Each backend implements its own version of every query using its own
    # SQL dialect and native ``paramstyle``. There is deliberately no shared
    # SQL in this base class.
    # ------------------------------------------------------------------
    def list_tables(self) -> list[str]:
        """
        Return the list of tables present in the backend.
        """
        raise NotImplementedError()

    def _create_migration_table(self) -> None:
        raise NotImplementedError()

    def _copy_versions(self) -> None:
        raise NotImplementedError()

    def _applied_migrations(self) -> list[tuple[Any, ...]]:
        raise NotImplementedError()

    def mark_applied(self, migration_id: str, content_hash: str | None, comment: str | None = None, applied_at: Any = None) -> None:
        """
        Record ``migration_id`` in the migration history table.
        """
        raise NotImplementedError()

    def unmark(self, migration_id: str) -> None:
        """
        Remove ``migration_id`` from the migration history table.
        """
        raise NotImplementedError()

    # ------------------------------------------------------------------
    # Generic orchestration (no SQL).
    # ------------------------------------------------------------------
    def ensure_migration_table(self) -> None:
        """
        Create the migration history table if it does not exist, migrating
        data from the legacy ``versions`` table where necessary.
        """
        if self.migration_table in self.list_tables():
            return
        if self.versions_table in self.list_tables():
            self._create_migration_table()
            self._copy_versions()
        else:
            self._create_migration_table()

    def applied_migrations(self) -> list[tuple[Any, ...]]:
        """
        Return the list of applied migrations, in the order they were applied.

        Each row is a tuple ``(migration_id, content_hash, applied_at, comment)``.
        """
        self.ensure_migration_table()
        return self._applied_migrations()

    def is_applied(self, migration_id: str) -> bool:
        return migration_id in {row[0] for row in self.applied_migrations()}

    # ------------------------------------------------------------------
    # Connection and transaction plumbing.
    # ------------------------------------------------------------------
    @property
    def connection(self) -> Any:
        return self._connection

    def close(self) -> None:
        self.connection.close()

    def init_connection(self, connection: Any) -> None:
        """
        Called when creating a connection or after a rollback. May do any
        db specific tasks required to make the connection ready for use.
        """

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_value: BaseException | None, traceback: TracebackType | None) -> None:
        self.close()

    def connect(self) -> Any:
        raise NotImplementedError()

    def quote_identifier(self, s: str) -> str:
        assert "\x00" not in s
        quoted = s.replace('"', '""')
        return f'"{quoted}"'

    def transaction(self) -> TransactionManager:
        if not self._in_transaction:
            return TransactionManager(self)
        else:
            return SavepointTransactionManager(self)

    def cursor(self) -> Any:
        return self.connection.cursor()

    def commit(self) -> None:
        self.connection.commit()
        self._in_transaction = False

    def rollback(self) -> None:
        self.connection.rollback()
        self.init_connection(self.connection)
        self._in_transaction = False

    def begin(self) -> None:
        """
        Begin a new transaction
        """
        assert not self._in_transaction
        self._in_transaction = True
        self.execute("BEGIN")

    def savepoint(self, id: str) -> None:
        """
        Create a new savepoint with the given id
        """
        self.execute(f"SAVEPOINT {id}")

    def savepoint_rollback(self, id: str) -> None:
        """
        Rollback the savepoint with the given id
        """
        self.execute(f"ROLLBACK TO SAVEPOINT {id}")

    @contextmanager
    def disable_transactions(self) -> Generator[None]:
        """
        Disable the connection's transaction support, for example by
        setting the isolation mode to 'autocommit'
        """
        self.rollback()
        yield

    @contextmanager
    def lock(self, timeout: int | None = None) -> Generator[None]:
        """
        Acquire a session-scoped lock to prevent concurrent migrations.

        The lock must be released automatically when the connection (and thus
        the session) is closed, including on abnormal process termination.
        """
        raise NotImplementedError(
            "Native session locking is not implemented for this backend"
        )

    def execute(self, sql: str, params: Any = None) -> Any:
        """
        Create a new cursor, execute a single statement and return the cursor
        object.

        :param sql: A single SQL statement using the driver's native
                    parameter style
        :param params: Parameters in the format required by the driver
        """
        cursor = self.cursor()
        if params is None:
            cursor.execute(sql)
        else:
            cursor.execute(sql, params)
        return cursor
