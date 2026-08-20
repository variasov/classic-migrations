from datetime import datetime
from datetime import timezone

from classic.migrations.backends.base import DatabaseBackend


class PyMSSQLBackend(DatabaseBackend):
    driver_module = "pymssql"
    supports_transactional_ddl = True

    def connect(self, dburi):
        return self.driver.connect(
            server=dburi.hostname,
            user=dburi.username,
            password=dburi.password,
            database=dburi.database,
            port=dburi.port,
        )

    def list_tables(self):
        cursor = self.execute("SELECT table_name FROM INFORMATION_SCHEMA.TABLES")
        return [row[0] for row in cursor.fetchall()]

    def _create_migration_table(self):
        self.execute(
            "CREATE TABLE {0} ("
            "migration_id VARCHAR(255) PRIMARY KEY, "
            "content_hash VARCHAR(64) NULL, "
            "applied_at DATETIME NOT NULL, "
            "comment VARCHAR(255) NULL)".format(self.migration_table_quoted)
        )

    def _copy_versions(self):
        self.execute(
            "INSERT INTO {0} (migration_id, content_hash, applied_at, comment) "
            "SELECT migration_id, NULL, applied_at_utc, NULL "
            "FROM {1}".format(self.migration_table_quoted, self.versions_table_quoted)
        )

    def _applied_migrations(self):
        cursor = self.execute(
            "SELECT migration_id, content_hash, applied_at, comment "
            "FROM {0} ORDER BY applied_at, migration_id".format(
                self.migration_table_quoted
            )
        )
        return [tuple(row) for row in cursor.fetchall()]

    def mark_applied(self, migration_id, content_hash, comment=None, applied_at=None):
        applied_at = applied_at or datetime.now(timezone.utc).replace(tzinfo=None)
        self.execute(
            "INSERT INTO {0} (migration_id, content_hash, applied_at, comment) "
            "VALUES (%(migration_id)s, %(content_hash)s, %(applied_at)s, "
            "%(comment)s)".format(self.migration_table_quoted),
            {
                "migration_id": migration_id,
                "content_hash": content_hash,
                "applied_at": applied_at,
                "comment": comment,
            },
        )

    def unmark(self, migration_id):
        self.execute(
            "DELETE FROM {0} WHERE migration_id = %(migration_id)s".format(
                self.migration_table_quoted
            ),
            {"migration_id": migration_id},
        )

    def begin(self):
        """
        Begin a new transaction
        """
        assert not self._in_transaction
        self._in_transaction = True

    def savepoint(self, id):
        """
        Create a new savepoint with the given id
        """
        pass

    def savepoint_release(self, id):
        """
        Release (commit) the savepoint with the given id
        """
        pass

    def savepoint_rollback(self, id):
        """
        Rollback the savepoint with the given id
        """
        self.connection.commit()

    def commit(self):
        try:
            self.connection.commit()
        except Exception as e:
            print(str(e))
        self._in_transaction = False
