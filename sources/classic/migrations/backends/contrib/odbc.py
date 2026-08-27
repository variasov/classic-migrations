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


class ODBCBackend(Backend):

    def connect(self, dburi):
        args = [
            ("UID", dburi.username),
            ("PWD", dburi.password),
            ("ServerName", dburi.hostname),
            ("Port", dburi.port),
            ("Database", dburi.database),
        ]
        args.extend(dburi.args.items())
        s = ";".join("{}={}".format(k, v) for k, v in args if v is not None)
        return self.driver.connect(s)

    def list_tables(self):
        cursor = self.execute("SELECT table_name FROM INFORMATION_SCHEMA.TABLES")
        return [row[0] for row in cursor.fetchall()]

    def _create_migration_table(self):
        self.execute(
            "CREATE TABLE {0} ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
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
            "VALUES (?, ?, ?)".format(self.migration_table_quoted),
            (migration_id, datetime.now(timezone.utc).replace(tzinfo=None), status),
        )