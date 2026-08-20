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


class ODBCBackend(DatabaseBackend):
    driver_module = "pyodbc"

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
            "VALUES (?, ?, ?, ?)".format(self.migration_table_quoted),
            (migration_id, content_hash, applied_at, comment),
        )

    def unmark(self, migration_id):
        self.execute(
            "DELETE FROM {0} WHERE migration_id = ?".format(
                self.migration_table_quoted
            ),
            (migration_id,),
        )
