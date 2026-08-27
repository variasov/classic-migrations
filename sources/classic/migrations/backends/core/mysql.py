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

from datetime import datetime, timezone

from classic.migrations.backends.base import Backend


class MySQLBackend(Backend):

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
            "id INT AUTO_INCREMENT PRIMARY KEY, "
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


class MySQLdbBackend(MySQLBackend):
    pass