import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from classic.migrations import Migrations, exceptions
from classic.migrations.backends.fake import FakeBackend
from classic.migrations.migrations import Migration


def write_file(path: Path, content: str) -> Path:
    path_ = str(path)
    os.makedirs(os.path.dirname(path_), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def migration_hash(source: Path, filename: str) -> str | None:
    m = Migration(filename, str(source / filename), source_dir=str(source))
    m.load()
    return m.content_hash


def _patch_get_backend(backend: FakeBackend) -> Any:
    def _factory(**kw: object) -> FakeBackend:
        return backend

    return patch(
        "classic.migrations.migrations.DatabaseBackend.get_backend_class",
        return_value=_factory,
    )


class TestApply:
    def test_apply_calls_backend_sequence(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        backend = FakeBackend()

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            m.apply()

        assert backend.locked
        assert backend.migration_table_ready
        assert len(backend.applied_list) == 1
        row = backend.applied_list[0]
        assert row[0] == "0001.init"
        assert row[1] == migration_hash(source, "0001.init.sql")
        assert row[2] == ""
        assert backend.closed

    def test_apply_with_one_flag(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        backend = FakeBackend()

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            m.apply(one=True)

        assert len(backend.applied_list) == 1

    def test_apply_with_match(self, source: Path) -> None:
        write_file(source / "0001.a.sql", "CREATE TABLE a(id INTEGER);\n")
        write_file(source / "0002.b.sql", "CREATE TABLE b(id INTEGER);\n")
        backend = FakeBackend()

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            m.apply(match="0001")

        assert len(backend.applied_list) == 1
        assert backend.applied_list[0][0] == "0001.a"

    def test_apply_with_revision(self, source: Path) -> None:
        write_file(source / "0001.base.sql", "CREATE TABLE base(id INTEGER);\n")
        write_file(
            source / "0002.child.sql",
            "-- depends: 0001.base\nCREATE TABLE child(id INTEGER);\n",
        )
        backend = FakeBackend()

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            m.apply(revision="0002")

        assert len(backend.applied_list) == 2
        ids = [r[0] for r in backend.applied_list]
        assert ids == ["0001.base", "0002.child"]

    def test_apply_hash_check_enabled(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        backend = FakeBackend(applied=[("0001.init", "wronghash", None, None)])

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            with pytest.raises(exceptions.MigrationHashMismatch):
                m.apply()

    def test_apply_hash_check_disabled(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        backend = FakeBackend()

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            m.apply(check_hashes=False)

        assert backend.applied_list[0][1] is None

    def test_apply_calls_hooks(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        write_file(source / "pre-apply.sql", "SELECT 1;\n")
        write_file(source / "post-apply.sql", "SELECT 2;\n")
        backend = FakeBackend()

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            m.apply()

        assert backend.cursor_count >= 3

    def test_apply_all_flag(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        backend = FakeBackend(
            applied=[("0001.init", migration_hash(source, "0001.init.sql"), None, None)]
        )

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            m.apply(all=True)

        assert len(backend.applied_list) == 1

    def test_apply_missing_dependency_raises(self, source: Path) -> None:
        write_file(
            source / "0001.child.sql",
            "-- depends: 9999.missing\nCREATE TABLE child(id INTEGER);\n",
        )
        backend = FakeBackend()

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            with pytest.raises(exceptions.BadMigration):
                m.apply()

    def test_apply_conflicting_ids_raise(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE a(id INTEGER);\n")
        sub = source / "sub"
        sub.mkdir()
        write_file(sub / "0001.init.sql", "CREATE TABLE b(id INTEGER);\n")
        backend = FakeBackend()

        with _patch_get_backend(backend):
            m = Migrations(
                sources=[str(source), str(sub)], driver="sqlite3"
            )
            with pytest.raises(exceptions.MigrationConflict):
                m.apply()


class TestRollback:
    def test_rollback_calls_backend_sequence(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        write_file(source / "0001.init.rollback.sql", "DROP TABLE foo;\n")
        backend = FakeBackend(applied=[("0001.init", "somehash", None, None)])

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            m.rollback()

        assert backend.locked
        assert backend.migration_table_ready
        assert len(backend.applied_list) == 0
        assert backend.closed

    def test_rollback_with_match(self, source: Path) -> None:
        write_file(source / "0001.a.sql", "CREATE TABLE a(id INTEGER);\n")
        write_file(source / "0002.b.sql", "CREATE TABLE b(id INTEGER);\n")
        write_file(source / "0001.a.rollback.sql", "DROP TABLE a;\n")
        write_file(source / "0002.b.rollback.sql", "DROP TABLE b;\n")
        backend = FakeBackend(
            applied=[("0001.a", "h1", None, None), ("0002.b", "h2", None, None)]
        )

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            m.rollback(match="a")

        assert len(backend.applied_list) == 1
        assert backend.applied_list[0][0] == "0002.b"

    def test_rollback_all(self, source: Path) -> None:
        write_file(source / "0001.a.sql", "CREATE TABLE a(id INTEGER);\n")
        write_file(source / "0002.b.sql", "CREATE TABLE b(id INTEGER);\n")
        backend = FakeBackend(
            applied=[("0001.a", "h1", None, None), ("0002.b", "h2", None, None)]
        )

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            m.rollback(all=True)

        assert len(backend.applied_list) == 0

    def test_rollback_no_rollback_file_skips(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        backend = FakeBackend(applied=[("0001.init", "somehash", None, None)])

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            m.rollback()

        assert len(backend.applied_list) == 0

    def test_rollback_calls_hooks(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        write_file(source / "0001.init.rollback.sql", "DROP TABLE foo;\n")
        write_file(source / "pre-rollback.sql", "SELECT 1;\n")
        write_file(source / "post-rollback.sql", "SELECT 2;\n")
        backend = FakeBackend(applied=[("0001.init", "somehash", None, None)])

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            m.rollback()

        assert backend.cursor_count >= 3

    def test_rollback_single_default_when_multiple(self, source: Path) -> None:
        write_file(source / "0001.a.sql", "CREATE TABLE a(id INTEGER);\n")
        write_file(source / "0002.b.sql", "CREATE TABLE b(id INTEGER);\n")
        write_file(source / "0002.b.rollback.sql", "DROP TABLE b;\n")
        backend = FakeBackend(
            applied=[("0001.a", "h1", None, None), ("0002.b", "h2", None, None)]
        )

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            m.rollback()

        assert len(backend.applied_list) == 1
        assert backend.applied_list[0][0] == "0001.a"


class TestList:
    def test_list_calls_backend(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        backend = FakeBackend()

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            result = m.list()

        assert backend.migration_table_ready
        assert backend.closed
        assert result == [("U", "0001.init", str(source))]

    def test_list_shows_applied(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        backend = FakeBackend(applied=[("0001.init", "somehash", None, None)])

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            result = m.list()

        assert result[0][0] == "A"


class TestIsApplied:
    def test_is_applied_calls_backend(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        backend = FakeBackend(applied=[("0001.init", "somehash", None, None)])

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            assert m.is_applied("0001.init")

        assert backend.migration_table_ready
        assert backend.closed

    def test_is_applied_not_found(self, source: Path) -> None:
        backend = FakeBackend()

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            assert not m.is_applied("0001.init")


class TestMark:
    def test_mark_calls_backend(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        backend = FakeBackend()

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            m.mark()

        assert backend.locked
        assert backend.migration_table_ready
        assert len(backend.applied_list) == 1
        assert backend.applied_list[0][0] == "0001.init"
        assert backend.closed


class TestUnmark:
    def test_unmark_calls_backend(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        backend = FakeBackend(applied=[("0001.init", "somehash", None, None)])

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            m.unmark()

        assert backend.locked
        assert backend.migration_table_ready
        assert len(backend.applied_list) == 0
        assert backend.closed


class TestReapply:
    def test_reapply_calls_backend_sequence(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        write_file(source / "0001.init.rollback.sql", "DROP TABLE foo;\n")
        backend = FakeBackend(
            applied=[("0001.init", migration_hash(source, "0001.init.sql"), None, None)]
        )

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            m.reapply()

        assert backend.locked
        assert backend.migration_table_ready
        assert len(backend.applied_list) == 1
        assert backend.applied_list[0][0] == "0001.init"
        assert backend.closed

    def test_reapply_hash_check(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        backend = FakeBackend(applied=[("0001.init", "wronghash", None, None)])

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            with pytest.raises(exceptions.MigrationHashMismatch):
                m.reapply()


class TestDevelop:
    def test_develop_applies_last_when_all_applied(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        write_file(source / "0001.init.rollback.sql", "DROP TABLE foo;\n")
        backend = FakeBackend(
            applied=[("0001.init", migration_hash(source, "0001.init.sql"), None, None)]
        )

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            m.develop()

        assert len(backend.applied_list) == 1
        assert backend.applied_list[0][0] == "0001.init"

    def test_develop_applies_unapplied(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        backend = FakeBackend()

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            m.develop()

        assert len(backend.applied_list) == 1
        assert backend.applied_list[0][0] == "0001.init"

    def test_develop_hash_check(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        backend = FakeBackend(applied=[("0001.init", "wronghash", None, None)])

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            with pytest.raises(exceptions.MigrationHashMismatch):
                m.develop()


class TestNew:
    def test_new_creates_file(self, source: Path) -> None:
        m = Migrations(sources=str(source), driver="sqlite3")
        filename = m.new(message="add stuff")

        assert filename.endswith(".sql")
        assert os.path.isfile(filename)


class TestDependencyResolution:
    def test_topological_order_is_maintained(self, source: Path) -> None:
        write_file(source / "0001.base.sql", "CREATE TABLE base(id INTEGER);\n")
        write_file(
            source / "0002.child.sql",
            "-- depends: 0001.base\nCREATE TABLE child(id INTEGER);\n",
        )
        backend = FakeBackend()

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            m.apply()

        ids = [r[0] for r in backend.applied_list]
        assert ids == ["0001.base", "0002.child"]

    def test_circular_dependency_raises(self, source: Path) -> None:
        write_file(
            source / "0001.a.sql",
            "-- depends: 0002.b\nCREATE TABLE a(id INTEGER);\n",
        )
        write_file(
            source / "0002.b.sql",
            "-- depends: 0001.a\nCREATE TABLE b(id INTEGER);\n",
        )
        backend = FakeBackend()

        with _patch_get_backend(backend):
            m = Migrations(sources=str(source), driver="sqlite3")
            with pytest.raises(exceptions.BadMigration):
                m.apply()


class TestConstructor:
    def test_empty_sources_raises(self) -> None:
        with pytest.raises(ValueError):
            Migrations(sources="", driver="sqlite3")

    def test_empty_driver_raises(self) -> None:
        with pytest.raises(ValueError):
            Migrations(sources="/tmp", driver="")

    def test_default_migration_table(self) -> None:
        m = Migrations(sources="/tmp", driver="sqlite3")
        assert m.migration_table == "migrations"

    def test_custom_migration_table(self) -> None:
        m = Migrations(sources="/tmp", driver="sqlite3", migration_table="my_history")
        assert m.migration_table == "my_history"

    def test_custom_migration_table_none_uses_default(self) -> None:
        m = Migrations(sources="/tmp", driver="sqlite3", migration_table=None)
        assert m.migration_table == "migrations"