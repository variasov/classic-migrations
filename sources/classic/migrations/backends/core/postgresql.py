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

import warnings
from contextlib import contextmanager
from datetime import datetime, timezone

from classic.migrations.backends.base import Backend


class PostgresqlBackend(Backend):

    schema = None

    @property
    def TRANSACTION_STATUS_IDLE(self):
        from psycopg.pq import TransactionStatus

        return TransactionStatus.IDLE

    def connect(self, dburi):
        kwargs = {"dbname": dburi.database, "autocommit": True}
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
            "id SERIAL PRIMARY KEY, "
            "migration_id VARCHAR(255) NOT NULL, "
            "created_at TIMESTAMP NOT NULL, "
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

    def commit(self):
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