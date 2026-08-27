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


class OracleBackend(Backend):

    def begin(self):
        self._in_transaction = True

    def connect(self, dburi):
        kwargs = dburi.args
        if dburi.username is not None:
            kwargs["user"] = dburi.username
        if dburi.password is not None:
            kwargs["password"] = dburi.password
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
            "id NUMBER GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
            "migration_id VARCHAR2(255) NOT NULL, "
            "created_at TIMESTAMP NOT NULL, "
            "status VARCHAR2(16) NOT NULL)".format(self.migration_table_quoted)
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
            "VALUES (:migration_id, :created_at, :status)".format(
                self.migration_table_quoted
            ),
            {
                "migration_id": migration_id,
                "created_at": datetime.now(timezone.utc).replace(tzinfo=None),
                "status": status,
            },
        )