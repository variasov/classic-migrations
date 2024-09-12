from collections.abc import Mapping
from datetime import datetime
import time

from classic.migrations import utils
from classic.migrations.backends.base import DatabaseBackend

class PyMSSQLBackend(DatabaseBackend):
    driver_module = "pymssql"

    create_migration_table_sql = (
        "CREATE TABLE {0.migrations_schema_name_quoted}.{0.migration_table_quoted} ( "
        # sha256 hash of the migration id
        "migration_hash VARCHAR(64), "
        # The migration id (ie path basename without extension)
        "migration_id VARCHAR(255), "
        # When this id was applied
        "applied_at_utc DATETIME, "
        "PRIMARY KEY (migration_hash))"
    )
    insert_migration_table_from_log_table_sql = (
        "INSERT INTO {0.migrations_schema_name_quoted}.{0.migration_table_quoted} "
        "SELECT migration_hash, migration_id, created_at_utc "
        "FROM {0.migrations_schema_name_quoted}.{0.log_table_quoted}"
    )
    create_lock_table_sql = (
        "CREATE TABLE {0.migrations_schema_name_quoted}.{0.lock_table_quoted} ("
        "locked INT DEFAULT 1, "
        "ctime DATETIME,"
        "pid INT NOT NULL,"
        "PRIMARY KEY (locked))"
    )
    create_log_table_sql = (
        "CREATE TABLE {0.migrations_schema_name_quoted}.{0.log_table_quoted} ( "
        "id VARCHAR(36), "
        "migration_hash VARCHAR(64), "
        "migration_id VARCHAR(255), "
        "operation VARCHAR(10), "
        "username VARCHAR(255), "
        "hostname VARCHAR(255), "
        "comment VARCHAR(255), "
        "created_at_utc DATETIME, "
        "PRIMARY KEY (id))"
    )
    migrations_schema_exists_sql = "SELECT 1 FROM INFORMATION_SCHEMA.SCHEMATA WHERE SCHEMA_NAME = {0.migrations_schema_name_quoted};"

    def connect(self, dburi):
        return self.driver.connect(
            server=dburi.hostname,
            user=dburi.username,
            password=dburi.password,
            database=dburi.database,
            port=dburi.port,
        )

    def execute(self, sql, params=None):
        """
        Create a new cursor, execute a single statement and return the cursor
        object.

        :param sql: A single SQL statement, optionally with named parameters
                    (eg 'SELECT * FROM foo WHERE :bar IS NULL')
        :param params: A dictionary of parameters
        """
        if params and not isinstance(params, Mapping):
            raise TypeError("Expected dict or other mapping object")
        cursor = self.cursor()
        sql, params = utils.change_param_style(self.driver.paramstyle, sql, params)
        cursor.execute(sql, params)
        return cursor

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

