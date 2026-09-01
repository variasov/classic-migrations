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

"""The :class:`Migrator` that executes migrations through a backend."""

from logging import getLogger
from typing import Any, Self

from classic.migrations.backends.base import (
    STATUS_APPLIED,
    STATUS_PENDING,
    STATUS_ROLLED_BACK,
    Backend,
)
from classic.migrations.migrations import Hook, Migration

logger = getLogger("classic.migrations")

HookList = list[Hook]
MigrationList = list[Migration]


class Migrator:
    """Executes migrations against a real database via a :class:`Backend`."""

    def __init__(
        self,
        driver: str,
        db_host: str | None = None,
        db_port: int | None = None,
        db_name: str | None = None,
        db_user: str | None = None,
        db_pass: str | None = None,
        migration_table: str = "migrations",
        migration_schema: str | None = None,
        versions_schema: str | None = None,
    ) -> None:
        """Store connection settings; the backend is created on context entry."""
        if not driver:
            raise ValueError("driver must not be empty")
        self._driver = driver
        self._db_host = db_host
        self._db_port = db_port
        self._db_name = db_name
        self._db_user = db_user
        self._db_pass = db_pass
        self._migration_table = migration_table
        self._migration_schema = migration_schema
        self._versions_schema = versions_schema
        self._backend: Backend | None = None

    def close(self) -> None:
        """Close and forget the backend connection, if any."""
        if self._backend is not None:
            self._backend.close()
            self._backend = None

    def _require_backend(self) -> Backend:
        if self._backend is None:
            raise RuntimeError("Migrator is not in a context")
        return self._backend

    @property
    def backend(self) -> Backend:
        """Return the active backend, raising if not in a context."""
        return self._require_backend()

    def __enter__(self) -> Self:
        """Connect and acquire a lock on the database."""
        logger.debug("Connecting to %s database %s", self._driver, self._db_name)
        backend_class = Backend.get_implementation(self._driver)
        self._backend = backend_class(
            db_host=self._db_host,
            db_port=self._db_port,
            db_name=self._db_name,
            db_user=self._db_user,
            db_password=self._db_pass,
            migration_table=self._migration_table,
            migration_schema=self._migration_schema,
            versions_schema=self._versions_schema,
        )
        self._backend.acquire_lock()
        logger.debug("Advisory lock acquired via %s", backend_class.__name__)
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        """Rollback on error and close the connection."""
        if exc_type is not None and self._backend is not None:
            logger.warning("Rolling back transaction after error: %s", exc_value)
            self._backend.rollback()
        self.close()

    def history(self) -> list[tuple[Any, ...]]:
        """Return the migration history event log.

        Each row is ``(migration_id, created_at, status)``.
        """
        backend = self._require_backend()
        logger.debug(
            "Reading migration history from table %r",
            backend.migration_table,
        )
        backend.ensure_migration_table()
        rows = backend.migration_history()
        logger.debug("Read %d history event(s)", len(rows))
        return rows

    def apply(
        self,
        migrations: MigrationList,
        hooks: dict[str, HookList],
        fake: bool = False,
    ) -> None:
        """Apply ``migrations`` in the given order.

        Runs ``pre-apply`` hooks before and ``post-apply`` hooks after.
        With ``fake=True`` only one ``APPLIED`` history event is recorded
        per migration, without any migration SQL or hooks.
        """
        if fake:
            logger.warning("Fake mode: only history records will be written")
        elif migrations:
            logger.info("Applying %d migration(s)", len(migrations))
        else:
            logger.info("Nothing to apply")

        if not fake:
            self._run_hooks(hooks.get("pre-apply", []))
        backend = self._require_backend()
        for migration in migrations:
            logger.info("Applying %s", migration.id)
            if fake:
                backend.mark(migration.id, STATUS_APPLIED)
            else:
                self._apply_one(migration)
        if not fake:
            self._run_hooks(hooks.get("post-apply", []))

    def rollback(
        self,
        migrations: MigrationList,
        hooks: dict[str, HookList],
        fake: bool = False,
    ) -> None:
        """Rollback ``migrations`` in the given order.

        Runs ``pre-rollback`` hooks before and ``post-rollback`` hooks after.
        With ``fake=True`` only one ``ROLLED_BACK`` history event is recorded
        per migration, without any migration SQL or hooks.
        """
        if fake:
            logger.warning("Fake mode: only history records will be written")
        elif migrations:
            logger.info("Rolling back %d migration(s)", len(migrations))
        else:
            logger.info("Nothing to rollback")

        if not fake:
            self._run_hooks(hooks.get("pre-rollback", []))
        backend = self._require_backend()
        for migration in migrations:
            logger.info("Rolling back %s", migration.id)
            if fake:
                backend.mark(migration.id, STATUS_ROLLED_BACK)
            else:
                self._rollback_one(migration)
        if not fake:
            self._run_hooks(hooks.get("post-rollback", []))

    # ------------------------------------------------------------------
    # Execution helpers
    # ------------------------------------------------------------------
    def _effective_transactional(
        self,
        migration: Migration,
        for_rollback: bool = False,
    ) -> bool:
        tx_setting = (
            migration.rollback_transactional
            if for_rollback
            else migration.transactional
        )
        if tx_setting is not None:
            return tx_setting
        return self._require_backend().transactional_ddl

    def _apply_one(self, migration: Migration) -> None:
        backend = self._require_backend()
        logger.debug(
            "Applying %s: %d statement(s), transactional=%s",
            migration.id,
            len(migration.apply_statements),
            self._effective_transactional(migration),
        )
        if self._effective_transactional(migration):
            with backend.transaction():
                for stmt in migration.apply_statements:
                    self._execute_statement(stmt)
                backend.mark(migration.id, STATUS_APPLIED)
        else:
            backend.mark(migration.id, STATUS_PENDING)
            for stmt in migration.apply_statements:
                self._execute_statement(stmt)
            backend.mark(migration.id, STATUS_APPLIED)

    def _rollback_one(self, migration: Migration) -> None:
        backend = self._require_backend()
        if not migration.rollback_statements:
            logger.debug(
                "Rolling back %s: no rollback file, marking only",
                migration.id,
            )
            backend.mark(migration.id, STATUS_ROLLED_BACK)
            return
        logger.debug(
            "Rolling back %s: %d statement(s), transactional=%s",
            migration.id,
            len(migration.rollback_statements),
            self._effective_transactional(migration, for_rollback=True),
        )
        if self._effective_transactional(migration, for_rollback=True):
            with backend.transaction():
                for stmt in migration.rollback_statements:
                    self._execute_statement(stmt)
                backend.mark(migration.id, STATUS_ROLLED_BACK)
        else:
            backend.mark(migration.id, STATUS_PENDING)
            for stmt in migration.rollback_statements:
                self._execute_statement(stmt)
            backend.mark(migration.id, STATUS_ROLLED_BACK)

    def _execute_statement(self, stmt: str) -> None:
        backend = self._require_backend()
        logger.debug("Executing SQL: %s", stmt)
        cursor = backend.cursor()
        try:
            cursor.execute(stmt)
        finally:
            cursor.close()

    def _run_hooks(self, hooks: HookList) -> None:
        backend = self._require_backend()
        for hook in hooks:
            hook.load()
            if not hook.statements:
                logger.debug("Skipping empty hook %s (%s)", hook.name, hook.path)
                continue
            logger.debug(
                "Running hook %s (%s): %d statement(s)",
                hook.name,
                hook.path,
                len(hook.statements),
            )
            for stmt in hook.statements:
                cursor = backend.cursor()
                try:
                    cursor.execute(stmt)
                finally:
                    cursor.close()
