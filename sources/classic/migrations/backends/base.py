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
from types import TracebackType
from typing import Any, ClassVar, Self

from classic.migrations.exceptions import BadConnectionURI

STATUS_PENDING = "PENDING"
STATUS_APPLIED = "APPLIED"
STATUS_ROLLED_BACK = "ROLLED_BACK"
ALL_STATUSES = (STATUS_PENDING, STATUS_APPLIED, STATUS_ROLLED_BACK)


class TransactionManager:
    def __init__(self, backend: "Backend") -> None:
        self.backend = backend
        self._started = False

    def __enter__(self) -> Self:
        if not self.backend._in_transaction:
            self.backend.begin()
            self._started = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        if exc_type:
            if self._started:
                self.backend.rollback()
            return
        if self._started:
            self.backend.commit()


class Lock:
    """Represents an acquired advisory lock.

    After exiting the ``with lock:`` block the lock is no longer
    considered acquired.
    """

    def __init__(self) -> None:
        self._acquired = False

    @property
    def is_acquired(self) -> bool:
        return self._acquired

    def __enter__(self) -> Self:
        self._acquired = True
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool | None:
        self._acquired = False


class Backend:
    implementations: ClassVar[dict[str, type["Backend"]]] = {}

    driver: Any

    versions_table = "versions"

    transactional_ddl: ClassVar[bool] = False

    def __init_subclass__(cls, driver: Any = None, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if driver is not None:
            cls.driver = driver
            cls.implementations[driver.__name__] = cls

    @classmethod
    def get_implementation(cls, name: str) -> type["Backend"]:
        try:
            return cls.implementations[name]
        except KeyError:
            raise BadConnectionURI(f"Unrecognised database driver {name!r}")

    def __init__(
        self,
        db_host: str | None = None,
        db_port: int | None = None,
        db_name: str | None = None,
        db_user: str | None = None,
        db_password: str | None = None,
        db_args: dict[str, Any] | None = None,
        migration_table: str = "migrations",
    ) -> None:
        self.db_host = db_host
        self.db_port = db_port
        self.db_name = db_name
        self.db_user = db_user
        self.db_password = db_password
        self.db_args = db_args or {}
        self.migration_table = migration_table
        self.DatabaseError = self.driver.DatabaseError
        self._connection = self.connect()
        self.init_connection(self._connection)
        self._in_transaction = False

    # ------------------------------------------------------------------
    # Abstract query methods — implemented by each backend.
    # ------------------------------------------------------------------
    def list_tables(self) -> list[str]:
        raise NotImplementedError()

    def _create_migration_table(self) -> None:
        raise NotImplementedError()

    def _copy_versions(self) -> None:
        raise NotImplementedError()

    def _migration_history(self) -> list[tuple[Any, ...]]:
        raise NotImplementedError()

    def mark(self, migration_id: str, status: str) -> None:
        raise NotImplementedError()

    # ------------------------------------------------------------------
    # Generic orchestration (no SQL).
    # ------------------------------------------------------------------
    def ensure_migration_table(self) -> None:
        if self.migration_table in self.list_tables():
            return
        if self.versions_table in self.list_tables():
            self._create_migration_table()
            self._copy_versions()
        else:
            self._create_migration_table()

    def migration_history(self) -> list[tuple[Any, ...]]:
        """
        Return the full log of migration history events, in the order they
        were recorded.

        Each row is ``(migration_id, created_at, status)``.
        """
        self.ensure_migration_table()
        return self._migration_history()

    @property
    def migration_table_quoted(self) -> str:
        return self.quote_identifier(self.migration_table)

    @property
    def versions_table_quoted(self) -> str:
        return self.quote_identifier(self.versions_table)

    # ------------------------------------------------------------------
    # Connection and transaction plumbing.
    # ------------------------------------------------------------------
    @property
    def connection(self) -> Any:
        return self._connection

    def close(self) -> None:
        self.connection.close()

    def init_connection(self, connection: Any) -> None:
        pass

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def connect(self) -> Any:
        raise NotImplementedError()

    def quote_identifier(self, s: str) -> str:
        assert "\x00" not in s
        quoted = s.replace('"', '""')
        return f'"{quoted}"'

    def transaction(self) -> TransactionManager:
        return TransactionManager(self)

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
        self._in_transaction = True
        self.execute("BEGIN")

    @contextmanager
    def disable_transactions(self) -> Generator[None]:
        self.rollback()
        yield

    @contextmanager
    def lock(self, timeout: int | None = None) -> Generator["Lock"]:
        raise NotImplementedError(
            "Native session locking is not implemented for this backend"
        )

    def execute(self, sql: str, params: Any = None) -> Any:
        cursor = self.cursor()
        if params is None:
            cursor.execute(sql)
        else:
            cursor.execute(sql, params)
        return cursor
