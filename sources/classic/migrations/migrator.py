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
    ) -> None:
        if not driver:
            raise ValueError("driver must not be empty")
        backend_class = Backend.get_implementation(driver)
        self.backend: Backend = backend_class(
            db_host=db_host,
            db_port=db_port,
            db_name=db_name,
            db_user=db_user,
            db_password=db_pass,
            migration_table=migration_table,
        )

    def close(self) -> None:
        self.backend.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.backend.close()

    def lock(self, timeout: int | None = None) -> Any:
        return self.backend.lock(timeout)

    def history(self, lock: Any = None) -> list[tuple[Any, ...]]:
        """Return the migration history event log.

        Each row is ``(migration_id, created_at, status)``.
        """
        assert lock is not None and lock.is_acquired, "Lock must be acquired"
        self.backend.ensure_migration_table()
        return self.backend.migration_history()

    def apply(
        self,
        lock: Any,
        hooks: dict[str, HookList],
        migrations: MigrationList,
        fake: bool = False,
    ) -> None:
        """Apply ``migrations`` in the given order.

        Runs ``pre-apply`` hooks before and ``post-apply`` hooks after.
        With ``fake=True`` only one ``APPLIED`` history event is recorded
        per migration, without any migration SQL or hooks.
        """
        assert lock is not None and lock.is_acquired, "Lock must be acquired"
        if not fake:
            self._run_hooks(hooks.get("pre-apply", []))
        for migration in migrations:
            logger.info("Applying %s", migration.id)
            if fake:
                self.backend.mark(migration.id, STATUS_APPLIED)
            else:
                self._apply_one(migration)
        if not fake:
            self._run_hooks(hooks.get("post-apply", []))

    def rollback(
        self,
        lock: Any,
        hooks: dict[str, HookList],
        migrations: MigrationList,
        fake: bool = False,
    ) -> None:
        """Rollback ``migrations`` in the given order.

        Runs ``pre-rollback`` hooks before and ``post-rollback`` hooks after.
        With ``fake=True`` only one ``ROLLED_BACK`` history event is recorded
        per migration, without any migration SQL or hooks.
        """
        assert lock is not None and lock.is_acquired, "Lock must be acquired"
        if not fake:
            self._run_hooks(hooks.get("pre-rollback", []))
        for migration in migrations:
            logger.info("Rolling back %s", migration.id)
            if fake:
                self.backend.mark(migration.id, STATUS_ROLLED_BACK)
            else:
                self._rollback_one(migration)
        if not fake:
            self._run_hooks(hooks.get("post-rollback", []))

    # ------------------------------------------------------------------
    # Execution helpers
    # ------------------------------------------------------------------
    def _effective_transactional(self, migration: Migration, for_rollback: bool = False) -> bool:
        tx_setting = migration.rollback_transactional if for_rollback else migration.transactional
        if tx_setting is not None:
            return tx_setting
        return self.backend.transactional_ddl

    def _apply_one(self, migration: Migration) -> None:
        if self._effective_transactional(migration):
            with self.backend.transaction():
                for stmt in migration.apply_statements:
                    self._execute_statement(stmt)
                self.backend.mark(migration.id, STATUS_APPLIED)
        else:
            self.backend.mark(migration.id, STATUS_PENDING)
            for stmt in migration.apply_statements:
                self._execute_statement(stmt)
            self.backend.mark(migration.id, STATUS_APPLIED)

    def _rollback_one(self, migration: Migration) -> None:
        if not migration.rollback_statements:
            self.backend.mark(migration.id, STATUS_ROLLED_BACK)
            return
        if self._effective_transactional(migration, for_rollback=True):
            with self.backend.transaction():
                for stmt in migration.rollback_statements:
                    self._execute_statement(stmt)
                self.backend.mark(migration.id, STATUS_ROLLED_BACK)
        else:
            self.backend.mark(migration.id, STATUS_PENDING)
            for stmt in migration.rollback_statements:
                self._execute_statement(stmt)
            self.backend.mark(migration.id, STATUS_ROLLED_BACK)

    def _execute_statement(self, stmt: str) -> None:
        cursor = self.backend.cursor()
        try:
            cursor.execute(stmt)
        finally:
            cursor.close()

    def _run_hooks(self, hooks: HookList) -> None:
        for hook in hooks:
            hook.load()
            if not hook.statements:
                continue
            for stmt in hook.statements:
                cursor = self.backend.cursor()
                try:
                    cursor.execute(stmt)
                finally:
                    cursor.close()
