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

from datetime import datetime
from datetime import timezone

from classic.migrations.backends.base import DatabaseBackend


class OracleBackend(DatabaseBackend):

    driver_module = "cx_Oracle"

    def begin(self):
        """Oracle is always in a transaction, and has no "BEGIN" statement."""
        self._in_transaction = True

    def connect(self, dburi):
        kwargs = dburi.args
        if dburi.username is not None:
            kwargs["user"] = dburi.username
        if dburi.password is not None:
            kwargs["password"] = dburi.password
        # Oracle combines the hostname, port and database into a single DSN.
        # The DSN can also be a "net service name"
        kwargs["dsn"] = ""
        if dburi.hostname is not None:
            kwargs["dsn"] = dburi.hostname
        if dburi.port is not None:
            kwargs["dsn"] += ":{0}".format(dburi.port)
        if dburi.database is not None:
            if kwargs["dsn"]:
                kwargs["dsn"] += "/{0}".format(dburi.database)
            else:
                kwargs["dsn"] = dburi.database

        return self.driver.connect(**kwargs)

    def list_tables(self):
        cursor = self.execute("SELECT table_name FROM all_tables WHERE owner = user")
        return [row[0] for row in cursor.fetchall()]

    def _create_migration_table(self):
        self.execute(
            "CREATE TABLE {0} ("
            "migration_id VARCHAR2(255) PRIMARY KEY, "
            "content_hash VARCHAR2(64) NULL, "
            "applied_at TIMESTAMP NOT NULL, "
            "comment VARCHAR2(255) NULL)".format(self.migration_table_quoted)
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
            "VALUES (:migration_id, :content_hash, :applied_at, :comment)".format(
                self.migration_table_quoted
            ),
            {
                "migration_id": migration_id,
                "content_hash": content_hash,
                "applied_at": applied_at,
                "comment": comment,
            },
        )

    def unmark(self, migration_id):
        self.execute(
            "DELETE FROM {0} WHERE migration_id = :migration_id".format(
                self.migration_table_quoted
            ),
            {"migration_id": migration_id},
        )
