"""
Integration test of the public API against a real in-memory SQLite database.
"""

from pathlib import Path

from classic.migrations import MigrationsCollection, Migrator


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
