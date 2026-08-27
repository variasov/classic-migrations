# Copyright 2026 Sergey Variasov

from datetime import datetime, timezone

from classic.migrations.backends.base import Backend


class PyMSSQLBackend(Backend):

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
            "id INT IDENTITY(1,1) PRIMARY KEY, "
            "migration_id VARCHAR(255) NOT NULL, "
            "created_at DATETIME NOT NULL, "
            "status VARCHAR(16) NOT NULL)".format(self.migration_table_quoted)
        )

    def _copy_versions(self):
        self.execute(
            "INSERT INTO {0} (migration_id, created_at, status) "
            "SELECT migration_id, applied_at_utc, 'APPLIED' "
            "FROM {1}".format(self.migration_table_quoted, self.versions_table_quoted)
        )

    def _migration_history(self):
        cursor = self.execute(
            "SELECT migration_id, created_at, status "
            "FROM {0} ORDER BY id".format(self.migration_table_quoted)
        )
        return [tuple(row) for row in cursor.fetchall()]

    def mark(self, migration_id, status):
        self.execute(
            "INSERT INTO {0} (migration_id, created_at, status) "
            "VALUES (%(migration_id)s, %(created_at)s, %(status)s)".format(
                self.migration_table_quoted
            ),
            {
                "migration_id": migration_id,
                "created_at": datetime.now(timezone.utc).replace(tzinfo=None),
                "status": status,
            },
        )

    def begin(self):
        assert not self._in_transaction
        self._in_transaction = True

    def savepoint(self, id):
        pass

    def savepoint_rollback(self, id):
        self.connection.commit()

    def commit(self):
        try:
            self.connection.commit()
        except Exception as e:
            print(str(e))
        self._in_transaction = False