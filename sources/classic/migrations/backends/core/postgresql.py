# Copyright 2015 Oliver Cope
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

import warnings
from contextlib import contextmanager
from datetime import datetime
from datetime import timezone

from classic.migrations.backends.base import DatabaseBackend


class PostgresqlBackend(DatabaseBackend):
    """
    Backend for PostgreSQL and PostgreSQL compatible databases.
    """

    driver_module = "psycopg"
    schema = None

    @property
    def TRANSACTION_STATUS_IDLE(self):
        from psycopg.pq import TransactionStatus

        return TransactionStatus.IDLE

    def connect(self, dburi):
        kwargs = {"dbname": dburi.database, "autocommit": True}

        # Default to autocommit mode: without this psycopg sends a BEGIN before
        # every query, causing a warning when we then explicitly start a
        # transaction. This warning becomes an error in CockroachDB.
        kwargs.update(dburi.args)
        if dburi.username is not None:
            kwargs["user"] = dburi.username
        if dburi.password is not None:
            kwargs["password"] = dburi.password
        if dburi.port is not None:
            kwargs["port"] = dburi.port
        if dburi.hostname is not None:
            kwargs["host"] = dburi.hostname
        self.schema = kwargs.pop("schema", None)
        autocommit = bool(kwargs.pop("autocommit"))
        connection = self.driver.connect(**kwargs)
        connection.autocommit = autocommit
        return connection

    @contextmanager
    def disable_transactions(self):
        with super(PostgresqlBackend, self).disable_transactions():
            saved = self.connection.autocommit
            self.connection.autocommit = True
            yield
            self.connection.autocommit = saved

    def init_connection(self, connection):
        if self.schema:
            cursor = connection.cursor()
            cursor.execute("SET search_path TO {}".format(self.schema))

    def list_tables(self):
        cursor = self.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = current_schema()"
        )
        return [row[0] for row in cursor.fetchall()]

    def _create_migration_table(self):
        self.execute(
            "CREATE TABLE {0} ("
            "migration_id VARCHAR(255) PRIMARY KEY, "
            "content_hash VARCHAR(64) NULL, "
            "applied_at TIMESTAMP NOT NULL, "
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

    def commit(self):
        # The connection is in autocommit mode and ignores calls to
        # ``commit()`` and ``rollback()``, so we have to issue the SQL directly
        self.execute("COMMIT")
        super().commit()

    def rollback(self):
        self.execute("ROLLBACK")
        super().rollback()

    def begin(self):
        if self.connection.info.transaction_status != self.TRANSACTION_STATUS_IDLE:
            warnings.warn(
                "Nested transaction requested; "
                "this will raise an exception in some "
                "PostgreSQL-compatible databases"
            )
        return super().begin()
