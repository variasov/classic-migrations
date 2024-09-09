from collections.abc import Mapping
from datetime import datetime
import time

from yoyo import internalmigrations
from yoyo import utils
from yoyo.backends.base import DatabaseBackend
from yoyo import exceptions
import pymssql

class PyMSSQLBackend(DatabaseBackend):
    driver_module = "pymssql"

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
        if sql == """CREATE TABLE "_yoyo_migration" ( migration_hash VARCHAR(64), migration_id VARCHAR(255), applied_at_utc TIMESTAMP, PRIMARY KEY (migration_hash))""":
            sql = sql.replace('TIMESTAMP','DATETIME')
        if sql == """CREATE TABLE "_yoyo_version" (version INT NOT NULL PRIMARY KEY, installed_at_utc TIMESTAMP)""":
            sql = sql.replace('TIMESTAMP', 'DATETIME')
        if sql == """INSERT INTO "_yoyo_migration" SELECT migration_hash, migration_id, created_at_utc FROM "_yoyo_log""":
            sql = sql.replace(', created_at_utc', '')
        if sql == """CREATE TABLE "_yoyo_log" ( id VARCHAR(36), migration_hash VARCHAR(64), migration_id VARCHAR(255), operation VARCHAR(10), username VARCHAR(255), hostname VARCHAR(255), comment VARCHAR(255), created_at_utc TIMESTAMP, PRIMARY KEY (id))""":
            sql = sql.replace('TIMESTAMP', 'DATETIME')
        if sql == """CREATE TABLE "yoyo_lock" (locked INT DEFAULT 1, ctime TIMESTAMP,pid INT NOT NULL,PRIMARY KEY (locked))""":
            sql = sql.replace('TIMESTAMP', 'DATETIME')
        from rich import print
        print(sql, str(params))
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

    def _insert_lock_row(self, pid, timeout, poll_interval=0.5):
        poll_interval = min(poll_interval, timeout)
        started = time.time()
        self.execute(
            "INSERT INTO {} (locked, ctime, pid) "
            "VALUES (1, :when, :pid);".format(self.lock_table_quoted),
            {"when": datetime.utcnow(), "pid": pid},
        )

    def create_lock_table(self):
        """
        Create the lock table if it does not already exist.
        """
        try:
            self.execute(self.create_lock_table_sql.format(self))
            self.commit()
        except:
            pass

    def commit(self):
        try:
            self.connection.commit()
        except Exception as e:
            print(str(e))
        self._in_transaction = False

