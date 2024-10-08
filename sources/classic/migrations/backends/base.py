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

from collections.abc import Mapping
from datetime import datetime
from contextlib import contextmanager
from importlib import import_module
from itertools import count
from logging import getLogger
from typing import Dict
from importlib_metadata import entry_points

import getpass
import os
import pickle
import socket
import time
import uuid

from classic.migrations import exceptions
from classic.migrations import utils
from classic.migrations.migrations import topological_sort, get_migration_hash

logger = getLogger("classic.migrations")


class TransactionManager:
    """
    Returned by the :meth:`~classic.migrations.backends.DatabaseBackend.transaction`
    context manager.

    If rollback is called, the transaction is flagged to be rolled back
    when the context manager block closes
    """

    def __init__(self, backend, rollback_on_exit=False):
        self.backend = backend
        self.rollback_on_exit = rollback_on_exit

    def __enter__(self):
        self._do_begin()
        return self

    def __exit__(self, exc_type, value, traceback):
        if exc_type:
            self._do_rollback()
            return None

        if self.rollback_on_exit:
            self._do_rollback()
        else:
            self._do_commit()

    def _do_begin(self):
        """
        Instruct the backend to begin a transaction
        """
        self.backend.begin()

    def _do_commit(self):
        """
        Instruct the backend to commit the transaction
        """
        self.backend.commit()

    def _do_rollback(self):
        """
        Instruct the backend to roll back the transaction
        """
        self.backend.rollback()


class SavepointTransactionManager(TransactionManager):

    id = None
    id_generator = count(1)

    def _do_begin(self):
        assert self.id is None
        self.id = "sp_{}".format(next(self.id_generator))
        self.backend.savepoint(self.id)

    def _do_commit(self):
        """
        This does nothing.

        Trying to the release savepoint here could cause an database error in
        databases where DDL queries cause the transaction to be committed
        and all savepoints released.
        """

    def _do_rollback(self):
        self.backend.savepoint_rollback(self.id)


class DatabaseBackend:

    driver_module = ""

    log_table = "history"
    lock_table = "lock"
    list_tables_sql = "SELECT table_name FROM information_schema.tables"
    migration_table = "versions"
    is_applied_sql = (
        "SELECT COUNT(1) FROM {0.migrations_schema_name_quoted}.{0.migration_table_quoted}"
        "WHERE id=:id"
    )
    mark_migration_sql = (
        "INSERT INTO {0.migrations_schema_name_quoted}.{0.migration_table_quoted} "
        "(migration_hash, migration_id, applied_at_utc) "
        "VALUES (:migration_hash, :migration_id, :when)"
    )
    unmark_migration_sql = (
        "DELETE FROM {0.migrations_schema_name_quoted}.{0.migration_table_quoted} WHERE "
        "migration_hash = :migration_hash"
    )
    applied_migrations_sql = (
        "SELECT migration_hash FROM "
        "{0.migrations_schema_name_quoted}.{0.migration_table_quoted} "
        "ORDER by applied_at_utc"
    )
    create_test_table_sql = "CREATE TABLE {table_name_quoted} " "(id INT PRIMARY KEY)"
    log_migration_sql = (
        "INSERT INTO {0.migrations_schema_name_quoted}.{0.log_table_quoted} "
        "(id, migration_hash, migration_id, operation, "
        "username, hostname, created_at_utc, comment) "
        "VALUES (:id, :migration_hash, :migration_id, "
        ":operation, :username, :hostname, :created_at_utc, :comment)"
    )
    create_lock_table_sql = (
        "CREATE TABLE {0.migrations_schema_name_quoted}.{0.lock_table_quoted} ("
        "locked INT DEFAULT 1, "
        "ctime TIMESTAMP,"
        "pid INT NOT NULL,"
        "PRIMARY KEY (locked))"
    )
    insert_log_table_sql = (
        "INSERT INTO {0.migrations_schema_name_quoted}.{0.log_table_quoted} "
        "(id, migration_hash, migration_id, operation, created_at_utc, "
        "username, hostname, comment) "
        "VALUES "
        "(:id, :migration_hash, :migration_id, 'apply', :created_at_utc, "
        ":username, :hostname, :comment)"
    )
    insert_migration_table_from_log_table_sql = (
        "INSERT INTO {0.migrations_schema_name_quoted}.{0.migration_table_quoted} "
        "SELECT migration_hash, migration_id, created_at_utc "
        "FROM {0.migrations_schema_name_quoted}.{0.log_table_quoted}"
    )
    create_migration_table_sql = (
        "CREATE TABLE {0.migrations_schema_name_quoted}.{0.migration_table_quoted} ( "
        # sha256 hash of the migration id
        "migration_hash VARCHAR(64), "
        # The migration id (ie path basename without extension)
        "migration_id VARCHAR(255), "
        # When this id was applied
        "applied_at_utc TIMESTAMP, "
        "PRIMARY KEY (migration_hash))"
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
        "created_at_utc TIMESTAMP, "
        "PRIMARY KEY (id))"
    )
    migrations_schema_exists_sql = ""
    create_migrations_schema_sql = "CREATE SCHEMA {0.migrations_schema_name_quoted};"
    _driver = None
    _is_locked = False
    _in_transaction = False
    _internal_schema_updated = False
    _transactional_ddl_cache: Dict[bytes, bool] = {}
    migrations_schema_name = "migrations"

    def __init__(self, dburi, migration_table):
        self.uri = dburi
        self.DatabaseError = self.driver.DatabaseError
        self._connection = self.connect(dburi)
        self.init_connection(self._connection)
        self.migration_table = migration_table
        self.has_transactional_ddl = self._transactional_ddl_cache.get(
            pickle.dumps(self.uri), True
        )

    def init_database(self):
        self._create_migrations_schema()
        self.create_lock_table()
        self.has_transactional_ddl = self._check_transactional_ddl()
        self._transactional_ddl_cache[
            pickle.dumps(self.uri)
        ] = self.has_transactional_ddl
        if self.needs_upgrading():
            self._upgrade()

    def needs_upgrading(self):
        list_tables = self.list_tables()
        return (self.log_table not in list_tables
            or self.lock_table not in list_tables
            or self.migration_table not in list_tables
        )

    def _upgrade(self):
        self._create_log_table()
        self._create_migration_table()
        cursor = self.execute(
            "SELECT migration_id, applied_at_utc "
            f"FROM {self.migrations_schema_name_quoted}.{self.migration_table_quoted}"
        )
        migration_id = ""
        created_at = datetime(1970, 1, 1)
        for migration_id, created_at in iter(cursor.fetchone,
                                             None):  # type: ignore
            migration_hash = get_migration_hash(migration_id)
            log_data = dict(
                self.get_log_data(),
                operation="apply",
                comment=(
                    "this log entry created automatically by an internal schema upgrade"
                ),
                created_at_utc=created_at,
                migration_hash=migration_hash,
                migration_id=migration_id,
            )
            self.execute(
                self.insert_log_table_sql.format(self),
                log_data,
            )

        self.execute(f"DROP TABLE {self.migrations_schema_name_quoted}.{self.migration_table_quoted}")
        self._create_migration_table()
        self.execute(
            self.insert_migration_table_from_log_table_sql.format(self)
        )

    def _create_migration_table(self):
        if self.migration_table not in self.list_tables():
            self.execute(self.create_migration_table_sql.format(self))

    def _create_log_table(self):
        if self.log_table not in self.list_tables():
            self.execute(self.create_log_table_sql.format(self))

    def _create_migrations_schema(self):
        try:
            self.execute(self.create_migrations_schema_sql.format(self))
        except self.DatabaseError:
             pass

    def _load_driver_module(self):
        """
        Load the dbapi driver module and register the base exception class
        """
        driver = get_dbapi_module(self.driver_module)
        exceptions.register(driver.DatabaseError)
        return driver

    @property
    def driver(self):
        if self._driver:
            return self._driver
        self._driver = self._load_driver_module()
        return self._driver

    @property
    def connection(self):
        return self._connection

    def init_connection(self, connection):
        """
        Called when creating a connection or after a rollback. May do any
        db specific tasks required to make the connection ready for use.
        """

    def copy(self):
        """
        Return a copy of the backend with a independent db
        connection.
        """
        return self.__class__(self.uri, self.migration_table)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.connection.close()

    def __getattr__(self, attrname):
        if attrname.endswith("_quoted"):
            unquoted = getattr(self, attrname.rsplit("_quoted")[0])
            return self.quote_identifier(unquoted)
        raise AttributeError(attrname)

    def connect(self, dburi):
        raise NotImplementedError()

    def quote_identifier(self, s):
        assert "\x00" not in s
        quoted = s.replace('"', '""')
        return f'"{quoted}"'

    def _check_transactional_ddl(self):
        """
        Return True if the database supports committing/rolling back
        DDL statements within a transaction
        """
        table_name_quoted = (self.migrations_schema_name_quoted+"."+
                      self.quote_identifier("migration_tmp_{}".format(utils.get_random_string(10))))
        sql = self.create_test_table_sql.format(table_name_quoted=table_name_quoted)
        try:
            with self.transaction(rollback_on_exit=True):
                self.execute(sql)
        except self.DatabaseError:
            return False

        try:
            with self.transaction():
                self.execute(f"DROP TABLE {table_name_quoted}")
        except self.DatabaseError:
            return True
        return False

    def list_tables(self, **kwargs):
        """
        Return a list of tables present in the backend.
        This is used by the test suite to clean up tables
        generated during testing
        """
        cursor = self.execute(
            self.list_tables_sql.format(self),
            dict({"database": self.uri.database}, **kwargs),
        )
        return [row[0] for row in cursor.fetchall()]

    def transaction(self, rollback_on_exit=False):
        if not self._in_transaction:
            return TransactionManager(self, rollback_on_exit=rollback_on_exit)
        else:
            return SavepointTransactionManager(self, rollback_on_exit=rollback_on_exit)

    def cursor(self):
        return self.connection.cursor()

    def commit(self):
        try:
            self.connection.commit()
        except Exception as e:
            print(str(e))
        self._in_transaction = False

    def rollback(self):
        self.connection.rollback()
        self.init_connection(self.connection)
        self._in_transaction = False

    def begin(self):
        """
        Begin a new transaction
        """
        assert not self._in_transaction
        self._in_transaction = True
        self.execute("BEGIN")

    def savepoint(self, id):
        """
        Create a new savepoint with the given id
        """
        self.execute("SAVEPOINT {}".format(id))

    def savepoint_release(self, id):
        """
        Release (commit) the savepoint with the given id
        """
        self.execute("RELEASE SAVEPOINT {}".format(id))

    def savepoint_rollback(self, id):
        """
        Rollback the savepoint with the given id
        """
        self.execute("ROLLBACK TO SAVEPOINT {}".format(id))

    @contextmanager
    def disable_transactions(self):
        """
        Disable the connection's transaction support, for example by
        setting the isolation mode to 'autocommit'
        """
        self.rollback()
        yield

    @contextmanager
    def lock(self, timeout=10):
        """
        Create a lock to prevent concurrent migrations.

        :param timeout: duration in seconds before raising a LockTimeout error.
        """
        if self._is_locked:
            yield
            return

        pid = os.getpid()
        self._insert_lock_row(pid, timeout)
        try:
            self._is_locked = True
            yield
            self._is_locked = False
        finally:
            self._delete_lock_row(pid)

    def _insert_lock_row(self, pid, timeout, poll_interval=0.5):
        poll_interval = min(poll_interval, timeout)
        started = time.time()
        while True:
            try:
                with self.transaction():
                    self.execute(
                        "INSERT INTO " +
                        self.migrations_schema_name_quoted.format(self) + "." +
                        self.lock_table_quoted+
                        " (locked, ctime, pid) " +
                        "VALUES (1, :when, :pid)",
                        {"when": datetime.utcnow(), "pid": pid},
                    )
            except self.DatabaseError:
                if timeout and time.time() > started + timeout:
                    cursor = self.execute(
                        "SELECT pid FROM "+
                        self.migrations_schema_name_quoted.format(self)+
                        ".{}".format(self.lock_table_quoted)
                    )
                    row = cursor.fetchone()
                    if row:
                        raise exceptions.LockTimeout(
                            "Process {} has locked this database "
                            "(run yoyo break-lock to remove this lock)".format(row[0])
                        )
                    else:
                        raise exceptions.LockTimeout(
                            "Database locked "
                            "(run yoyo break-lock to remove this lock)"
                        )
                time.sleep(poll_interval)
            else:
                return

    def _delete_lock_row(self, pid):
        with self.transaction():
            self.execute(
                f"DELETE FROM {self.migrations_schema_name_quoted.format(self)}.{self.lock_table_quoted} WHERE pid=:pid;",
                {"pid": pid},
            )

    def break_lock(self):
        with self.transaction():
            self.execute("DELETE FROM "+self.migrations_schema_name_quoted.format(self)+".{}".format(self.lock_table_quoted))

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

    def create_lock_table(self):
        """
        Create the lock table if it does not already exist.
        """
        if self.lock_table not in self.list_tables():
                self.execute(self.create_lock_table_sql.format(self))
                self.commit()

    def ensure_internal_schema_updated(self):
        """
        Check and upgrade yoyo's internal schema.
        """
        if self._internal_schema_updated:
            return
        if self.needs_upgrading():
            assert not self._in_transaction
            with self.lock():
                self._upgrade()
                self.connection.commit()
                self._internal_schema_updated = True

    def is_applied(self, migration):
        return migration.hash in self.get_applied_migration_hashes()

    def get_applied_migration_hashes(self):
        """
        Return the list of migration hashes in the order in which they
        were applied
        """
        self.ensure_internal_schema_updated()
        sql = self.applied_migrations_sql.format(self)
        return [row[0] for row in self.execute(sql).fetchall()]

    def to_apply(self, migrations):
        """
        Return the subset of migrations not already applied.
        """
        applied = self.get_applied_migration_hashes()
        ms = (m for m in migrations if m.hash not in applied)
        return migrations.__class__(topological_sort(ms), migrations.post_apply)

    def to_rollback(self, migrations):
        """
        Return the subset of migrations already applied and which may be
        rolled back.

        The order of migrations will be reversed.
        """
        applied = self.get_applied_migration_hashes()
        ms = (m for m in migrations if m.hash in applied)
        return migrations.__class__(
            reversed(list(topological_sort(ms))), migrations.post_apply
        )

    def apply_migrations(self, migrations, force=False):
        if migrations:
            self.apply_migrations_only(migrations, force=force)
            self.run_post_apply(migrations, force=force)

    def apply_migrations_only(self, migrations, force=False):
        """
        Apply the list of migrations, but do not run any post-apply hooks
        present.
        """
        if not migrations:
            return
        for m in migrations:
            try:
                self.apply_one(m, force=force)
            except exceptions.BadMigration:
                continue

    def run_post_apply(self, migrations, force=False):
        """
        Run any post-apply migrations present in ``migrations``
        """
        for m in migrations.post_apply:
            self.apply_one(m, mark=False, force=force)

    def rollback_migrations(self, migrations, force=False):
        self.ensure_internal_schema_updated()
        if not migrations:
            return
        for m in migrations:
            try:
                self.rollback_one(m, force)
            except exceptions.BadMigration:
                continue

    def mark_migrations(self, migrations):
        self.ensure_internal_schema_updated()
        with self.transaction():
            for m in migrations:
                try:
                    self.mark_one(m)
                except exceptions.BadMigration:
                    continue

    def unmark_migrations(self, migrations):
        self.ensure_internal_schema_updated()
        with self.transaction():
            for m in migrations:
                try:
                    self.unmark_one(m)
                except exceptions.BadMigration:
                    continue

    def apply_one(self, migration, force=False, mark=True):
        """
        Apply a single migration
        """
        logger.info("Applying %s", migration.id)
        self.ensure_internal_schema_updated()
        with self.copy() as migration_backend:
            migration.process_steps(migration_backend, "apply", force=force)
        self.log_migration(migration, "apply")
        if mark:
            with self.transaction():
                self.mark_one(migration, log=False)

    def rollback_one(self, migration, force=False):
        """
        Rollback a single migration
        """
        logger.info("Rolling back %s", migration.id)
        self.ensure_internal_schema_updated()
        with self.copy() as migration_backend:
            migration.process_steps(migration_backend, "rollback", force=force)
        self.log_migration(migration, "rollback")
        with self.transaction():
            self.unmark_one(migration, log=False)

    def unmark_one(self, migration, log=True):
        self.ensure_internal_schema_updated()
        sql = self.unmark_migration_sql.format(self)
        self.execute(sql, {"migration_hash": migration.hash})
        if log:
            self.log_migration(migration, "unmark")

    def mark_one(self, migration, log=True):
        self.ensure_internal_schema_updated()
        logger.info("Marking %s applied", migration.id)
        sql = self.mark_migration_sql.format(self)
        self.execute(
            sql,
            {
                "migration_hash": migration.hash,
                "migration_id": migration.id,
                "when": datetime.utcnow(),
            },
        )
        if log:
            self.log_migration(migration, "mark")

    def log_migration(self, migration, operation, comment=None):
        sql = self.log_migration_sql.format(self)
        self.execute(sql, self.get_log_data(migration, operation, comment))

    def get_log_data(self, migration=None, operation="apply", comment=None):
        """
        Return a dict of data for insertion into the ``_yoyo_log`` table
        """
        assert operation in {"apply", "rollback", "mark", "unmark"}
        return {
            "id": str(uuid.uuid1()),
            "migration_id": migration.id if migration else None,
            "migration_hash": migration.hash if migration else None,
            "username": getpass.getuser(),
            "hostname": socket.getfqdn(),
            "created_at_utc": datetime.utcnow(),
            "operation": operation,
            "comment": (comment if comment else
                            (migration.module.inside_sql_comment
                             if migration.module.inside_sql_comment else None
                            )
                        ),
        }


def get_backend_class(name):
    backend_eps = entry_points(group="migrations.backends")
    return backend_eps[name].load()


def get_dbapi_module(name):
    """
    Import and return the named DB-API driver module
    """
    return import_module(name)
