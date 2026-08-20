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

from datetime import datetime
from datetime import timezone

from classic.migrations.backends.base import DatabaseBackend


class MySQLBackend(DatabaseBackend):

    def connect(self, dburi):
        kwargs = {"db": dburi.database}
        kwargs.update(dburi.args)
        if dburi.username is not None:
            kwargs["user"] = dburi.username
        if dburi.password is not None:
            kwargs["passwd"] = dburi.password
        if dburi.hostname is not None:
            kwargs["host"] = dburi.hostname
        if dburi.port is not None:
            kwargs["port"] = dburi.port
        if "unix_socket" in dburi.args:
            kwargs["unix_socket"] = dburi.args["unix_socket"]
        if "ssl" in dburi.args:
            kwargs["ssl"] = {}

            if "sslca" in dburi.args:
                kwargs["ssl"]["ca"] = dburi.args["sslca"]

            if "sslcapath" in dburi.args:
                kwargs["ssl"]["capath"] = dburi.args["sslcapath"]

            if "sslcert" in dburi.args:
                kwargs["ssl"]["cert"] = dburi.args["sslcert"]

            if "sslkey" in dburi.args:
                kwargs["ssl"]["key"] = dburi.args["sslkey"]

            if "sslcipher" in dburi.args:
                kwargs["ssl"]["cipher"] = dburi.args["sslcipher"]

        kwargs["db"] = dburi.database
        return self.driver.connect(**kwargs)

    def quote_identifier(self, identifier):
        sql_mode = self.execute("SHOW VARIABLES LIKE 'sql_mode'").fetchone()[1]
        if "ansi_quotes" in sql_mode.lower():
            return super(MySQLBackend, self).quote_identifier(identifier)
        return "`{}`".format(identifier)

    def list_tables(self):
        cursor = self.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = %(database)s",
            {"database": self.uri.database},
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


class MySQLdbBackend(MySQLBackend):
    pass
