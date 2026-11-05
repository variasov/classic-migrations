"""
Integration test of the public API against a real in-memory SQLite database.
"""

import gc
import sqlite3
import warnings
from pathlib import Path
from typing import Any

import pytest

from classic.migrations import MigrationLockError, MigrationsCollection, Migrator


def write_file(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


class TestPublicApiSqlite:
    def test_spec_example(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        write_file(source / "0002.more.sql", "INSERT INTO foo VALUES (1);\n")
        write_file(source / "0002.more.rollback.sql", "DELETE FROM foo;\n")
        write_file(source / "0001.init.rollback.sql", "DROP TABLE foo;\n")

        db = Migrator(driver="sqlite3", db_name=":memory:")
        migrations = MigrationsCollection(str(source))

        with db:
            history = db.history()
            hooks, unapplied = migrations.to_apply(history)
            db.apply(unapplied, hooks)

            history = db.history()
            hooks, unapplied = migrations.to_apply(history)
            assert unapplied == []

            history = db.history()
            hooks, to_rollback = migrations.to_rollback(history)
            db.rollback(to_rollback, hooks)

            history = db.history()
            _, to_rollback = migrations.to_rollback(history)
            assert to_rollback == []

    def test_apply_creates_table_and_data(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        write_file(source / "0002.more.sql", "INSERT INTO foo VALUES (42);\n")

        db = Migrator(driver="sqlite3", db_name=":memory:")
        migrations = MigrationsCollection(str(source))

        with db:
            history = db.history()
            hooks, unapplied = migrations.to_apply(history)
            db.apply(unapplied, hooks)

            cursor = db.backend.execute("SELECT id FROM foo")
            assert cursor.fetchone()[0] == 42

    def test_history_records_events(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        write_file(source / "0001.init.rollback.sql", "DROP TABLE foo;\n")

        db = Migrator(driver="sqlite3", db_name=":memory:")
        migrations = MigrationsCollection(str(source))

        with db:
            history = db.history()
            hooks, unapplied = migrations.to_apply(history)
            db.apply(unapplied, hooks)

            history = db.history()
            hooks, to_rollback = migrations.to_rollback(history)
            db.rollback(to_rollback, hooks)

            history = db.history()
            assert len(history) == 2
            assert history[0][0] == "0001.init"
            assert history[0][2] == "APPLIED"
            assert history[1][0] == "0001.init"
            assert history[1][2] == "ROLLED_BACK"

    def test_fake_apply_only_records_history(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")

        db = Migrator(driver="sqlite3", db_name=":memory:")
        migrations = MigrationsCollection(str(source))

        with db:
            history = db.history()
            hooks, unapplied = migrations.to_apply(history)
            db.apply(unapplied, hooks, fake=True)

            tables = db.backend.list_tables()
            assert "foo" not in tables

            history = db.history()
            assert len(history) == 1
            assert history[0][2] == "APPLIED"

    def test_apply_with_target(self, source: Path) -> None:
        write_file(source / "0001.a.sql", "CREATE TABLE a(id INTEGER);\n")
        write_file(source / "0002.b.sql", "CREATE TABLE b(id INTEGER);\n")

        db = Migrator(driver="sqlite3", db_name=":memory:")
        migrations = MigrationsCollection(str(source))

        with db:
            history = db.history()
            hooks, unapplied = migrations.to_apply(history, target="0001.a")
            db.apply(unapplied, hooks)

            tables = db.backend.list_tables()
            assert "a" in tables
            assert "b" not in tables

    def test_hooks_run(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        write_file(source / "pre-apply.sql", "CREATE TABLE pre_hook(id INTEGER);\n")
        write_file(source / "post-apply.sql", "CREATE TABLE post_hook(id INTEGER);\n")

        db = Migrator(driver="sqlite3", db_name=":memory:")
        migrations = MigrationsCollection(str(source))

        with db:
            history = db.history()
            hooks, unapplied = migrations.to_apply(history)
            db.apply(unapplied, hooks)

            tables = db.backend.list_tables()
            assert "pre_hook" in tables
            assert "post_hook" in tables

    def test_non_transactional_migration(self, source: Path) -> None:
        write_file(
            source / "0001.init.sql",
            "-- transactional: false\nCREATE TABLE foo(id INTEGER);\n",
        )

        db = Migrator(driver="sqlite3", db_name=":memory:")
        migrations = MigrationsCollection(str(source))

        with db:
            history = db.history()
            hooks, unapplied = migrations.to_apply(history)
            db.apply(unapplied, hooks)

            history = db.history()
            assert len(history) == 2
            assert history[0][2] == "PENDING"
            assert history[1][2] == "APPLIED"

    def test_rollback_non_transactional_migration(self, source: Path) -> None:
        write_file(
            source / "0001.init.sql",
            "-- transactional: false\nCREATE TABLE foo(id INTEGER);\n",
        )
        write_file(source / "0001.init.rollback.sql", "DROP TABLE foo;\n")

        db = Migrator(driver="sqlite3", db_name=":memory:")
        migrations = MigrationsCollection(str(source))

        with db:
            history = db.history()
            hooks, unapplied = migrations.to_apply(history)
            db.apply(unapplied, hooks)

            history = db.history()
            hooks, to_rollback = migrations.to_rollback(history)
            db.rollback(to_rollback, hooks)

            history = db.history()
            statuses = [row[2] for row in history]
            assert statuses.count("PENDING") == 1
            assert statuses[0] == "PENDING"
            assert statuses[1] == "APPLIED"
            assert statuses[2] == "ROLLED_BACK"


class TestFileDbPersistence:
    def test_changes_persist_after_context_exit(
        self,
        source: Path,
        tmp_path: Path,
    ) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        write_file(source / "0002.more.sql", "INSERT INTO foo VALUES (42);\n")

        db_file = tmp_path / "db.sqlite"
        migrations = MigrationsCollection(str(source))

        db = Migrator(driver="sqlite3", db_name=str(db_file))
        with db:
            history = db.history()
            hooks, unapplied = migrations.to_apply(history)
            db.apply(unapplied, hooks)

        db2 = Migrator(driver="sqlite3", db_name=str(db_file))
        with db2:
            history = db2.history()
            assert [row[2] for row in history] == ["APPLIED", "APPLIED"]
            assert "foo" in db2.backend.list_tables()
            cursor = db2.backend.execute("SELECT id FROM foo")
            assert cursor.fetchone()[0] == 42

    def test_error_rolls_back_failed_migration(
        self,
        source: Path,
        tmp_path: Path,
    ) -> None:
        write_file(
            source / "0001.bad.sql",
            "CREATE TABLE foo(id INTEGER);\nNOT VALID SQL;\n",
        )

        db_file = tmp_path / "db.sqlite"
        migrations = MigrationsCollection(str(source))

        db = Migrator(driver="sqlite3", db_name=str(db_file))
        with pytest.raises(sqlite3.OperationalError), db:
            history = db.history()
            hooks, unapplied = migrations.to_apply(history)
            db.apply(unapplied, hooks)

        con = sqlite3.connect(str(db_file))
        try:
            tables = {
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'",
                )
            }
            events = con.execute("SELECT * FROM migrations").fetchall()
        finally:
            con.close()
        assert "foo" not in tables
        assert events == []


def _run_apply(db: Migrator, migrations: MigrationsCollection) -> None:
    history = db.history()
    hooks, unapplied = migrations.to_apply(history)
    db.apply(unapplied, hooks)


def _query(db_file: Path, sql: str) -> list[tuple[Any, ...]]:
    con = sqlite3.connect(str(db_file))
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


class TestFileDbTransactions:
    def test_error_in_second_migration_keeps_first(
        self,
        source: Path,
        tmp_path: Path,
    ) -> None:
        write_file(source / "0001.ok.sql", "CREATE TABLE a(id INTEGER);\n")
        write_file(
            source / "0002.bad.sql",
            "CREATE TABLE b(id INTEGER);\nNOT VALID SQL;\n",
        )
        db_file = tmp_path / "db.sqlite"
        migrations = MigrationsCollection(str(source))

        db = Migrator(driver="sqlite3", db_name=str(db_file))
        with pytest.raises(sqlite3.OperationalError), db:
            _run_apply(db, migrations)

        tables = {r[0] for r in _query(db_file, "SELECT name FROM sqlite_master")}
        assert "a" in tables
        assert "migrations" in tables
        assert "b" not in tables
        assert _query(db_file, "SELECT migration_id, status FROM migrations") == [
            ("0001.ok", "APPLIED"),
        ]

    def test_non_transactional_vacuum_migration(
        self,
        source: Path,
        tmp_path: Path,
    ) -> None:
        write_file(
            source / "0001.vacuum.sql",
            "-- transactional: false\nVACUUM;\n",
        )
        db_file = tmp_path / "db.sqlite"
        migrations = MigrationsCollection(str(source))

        db = Migrator(driver="sqlite3", db_name=str(db_file))
        with db:
            _run_apply(db, migrations)

        statuses = [r[0] for r in _query(db_file, "SELECT status FROM migrations")]
        assert statuses == ["PENDING", "APPLIED"]

    def test_hook_runs_outside_transaction(
        self,
        source: Path,
        tmp_path: Path,
    ) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        write_file(source / "pre-apply.sql", "VACUUM;\n")
        db_file = tmp_path / "db.sqlite"
        migrations = MigrationsCollection(str(source))

        db = Migrator(driver="sqlite3", db_name=str(db_file))
        with db:
            _run_apply(db, migrations)

        assert _query(db_file, "SELECT status FROM migrations") == [("APPLIED",)]

    def test_second_migrator_fails_while_locked(self, tmp_path: Path) -> None:
        db_file = tmp_path / "db.sqlite"
        first = Migrator(driver="sqlite3", db_name=str(db_file))
        second = Migrator(driver="sqlite3", db_name=str(db_file))

        with first, pytest.raises(MigrationLockError), second:
            pass
        with second:
            pass

    def test_failed_lock_does_not_leak_connection(self, tmp_path: Path) -> None:
        db_file = tmp_path / "db.sqlite"
        first = Migrator(driver="sqlite3", db_name=str(db_file))
        second = Migrator(driver="sqlite3", db_name=str(db_file))

        with first:
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                with pytest.raises(MigrationLockError), second:
                    pass
                gc.collect()
            assert second._backend is None
        leaks = [w for w in caught if issubclass(w.category, ResourceWarning)]
        assert leaks == []
        with second:
            pass

    def test_main_database_not_locked_by_migrator(
        self,
        source: Path,
        tmp_path: Path,
    ) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        db_file = tmp_path / "db.sqlite"
        migrations = MigrationsCollection(str(source))

        db = Migrator(driver="sqlite3", db_name=str(db_file))
        with db:
            _run_apply(db, migrations)
            other = sqlite3.connect(str(db_file), timeout=0)
            try:
                other.execute("INSERT INTO foo VALUES (7)")
                other.commit()
                assert other.execute("SELECT id FROM foo").fetchall() == [(7,)]
            finally:
                other.close()
