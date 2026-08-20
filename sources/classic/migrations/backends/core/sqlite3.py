import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from classic.migrations.backends.base import DatabaseBackend


class SQLiteBackend(DatabaseBackend, driver=sqlite3, scheme="sqlite"):

    def connect(self, dburi):
        # Ensure that multiple connections share the same data
        # https://sqlite.org/sharedcache.html
        conn = self.driver.connect(
            f"file:{dburi.database}?cache=shared",
            uri=True,
            detect_types=self.driver.PARSE_DECLTYPES,
        )
        conn.isolation_level = None
        return conn

    def list_tables(self):
        cursor = self.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        return [row[0] for row in cursor.fetchall()]

    def _create_migration_table(self):
        self.execute(
            f"CREATE TABLE {self.migration_table_quoted} ("
            "migration_id VARCHAR(255) PRIMARY KEY, "
            "content_hash VARCHAR(64) NULL, "
            "applied_at TIMESTAMP NOT NULL, "
            "comment VARCHAR(255) NULL)"
        )

    def _copy_versions(self):
        self.execute(
            f"INSERT INTO {self.migration_table_quoted} (migration_id, content_hash, applied_at, comment) "
            "SELECT migration_id, NULL, applied_at_utc, NULL "
            f"FROM {self.versions_table_quoted}"
        )

    def _applied_migrations(self):
        cursor = self.execute(
            "SELECT migration_id, content_hash, applied_at, comment "
            f"FROM {self.migration_table_quoted} ORDER BY applied_at, migration_id"
        )
        return [tuple(row) for row in cursor.fetchall()]

    def mark_applied(self, migration_id, content_hash, comment=None, applied_at=None):
        applied_at = applied_at or datetime.now(timezone.utc).replace(tzinfo=None)
        self.execute(
            f"INSERT INTO {self.migration_table_quoted} (migration_id, content_hash, applied_at, comment) "
            "VALUES (?, ?, ?, ?)",
            (migration_id, content_hash, applied_at, comment),
        )

    def unmark(self, migration_id):
        self.execute(
            f"DELETE FROM {self.migration_table_quoted} WHERE migration_id = ?",
            (migration_id,),
        )

    @contextmanager
    def lock(self, timeout=None):
        """
        Acquire a session-scoped lock by starting an immediate (write)
        transaction on the connection and holding it for the duration of the
        block. SQLite releases the underlying file lock automatically when the
        connection is closed, including on abnormal process termination.
        """
        self.begin_immediate()
        try:
            yield
            self.commit()
        except BaseException:
            self.rollback()
            raise

    def begin_immediate(self):
        assert not self._in_transaction
        self._in_transaction = True
        self.execute("BEGIN IMMEDIATE")

    @contextmanager
    def disable_transactions(self):
        """
        SQLite DDL is always transactional and cannot run outside the lock
        transaction, so this is a no-op.
        """
        yield
