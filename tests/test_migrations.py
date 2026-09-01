from pathlib import Path

import pytest
from classic.migrations import MigrationsCollection, exceptions
from classic.migrations.migrations import Migration


def write_file(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def make_history(*applied: str) -> list[tuple[str, str, str]]:
    return [
        (migration_id, "2020-01-01 00:00:00", "APPLIED")
        for migration_id in applied
    ]


class TestList:
    def test_list_returns_topologically_sorted(self, source: Path) -> None:
        write_file(source / "0001.base.sql", "CREATE TABLE base(id INTEGER);\n")
        write_file(
            source / "0002.child.sql",
            "-- depends: 0001.base\nCREATE TABLE child(id INTEGER);\n",
        )
        collection = MigrationsCollection(sources=str(source))

        migrations = collection.list()

        assert [m.id for m in migrations] == ["0001.base", "0002.child"]

    def test_list_loads_statements(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        write_file(source / "0001.init.rollback.sql", "DROP TABLE foo;\n")
        collection = MigrationsCollection(sources=str(source))

        migrations = collection.list()

        assert migrations[0].apply_statements == ["CREATE TABLE foo(id INTEGER);"]
        assert migrations[0].rollback_statements == ["DROP TABLE foo;"]

    def test_list_ignores_rollback_and_hook_files(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        write_file(source / "0001.init.rollback.sql", "DROP TABLE foo;\n")
        write_file(source / "pre-apply.sql", "SELECT 1;\n")
        write_file(source / "post-apply.sql", "SELECT 2;\n")
        collection = MigrationsCollection(sources=str(source))

        migrations = collection.list()

        assert [m.id for m in migrations] == ["0001.init"]

    def test_list_duplicate_ids_raise(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE a(id INTEGER);\n")
        sub = source / "sub"
        sub.mkdir()
        write_file(sub / "0001.init.sql", "CREATE TABLE b(id INTEGER);\n")
        collection = MigrationsCollection(sources=[str(source), str(sub)])

        with pytest.raises(exceptions.MigrationConflict):
            collection.list()

    def test_list_missing_dependency_raises(self, source: Path) -> None:
        write_file(
            source / "0001.child.sql",
            "-- depends: 9999.missing\nCREATE TABLE child(id INTEGER);\n",
        )
        collection = MigrationsCollection(sources=str(source))

        with pytest.raises(exceptions.NoMigration):
            collection.list()

    def test_list_reads_migrations_once(self, source: Path) -> None:
        write_file(source / "0001.a.sql", "CREATE TABLE a(id INTEGER);\n")
        collection = MigrationsCollection(sources=str(source))

        first = collection.list()
        write_file(source / "0002.b.sql", "CREATE TABLE b(id INTEGER);\n")
        second = collection.list()

        assert [m.id for m in second] == ["0001.a"]
        assert second is first

    def test_list_circular_dependency_raises(self, source: Path) -> None:
        write_file(
            source / "0001.a.sql",
            "-- depends: 0002.b\nCREATE TABLE a(id INTEGER);\n",
        )
        write_file(
            source / "0002.b.sql",
            "-- depends: 0001.a\nCREATE TABLE b(id INTEGER);\n",
        )
        collection = MigrationsCollection(sources=str(source))

        with pytest.raises(exceptions.BadMigration):
            collection.list()


class TestToApply:
    def test_returns_unapplied(self, source: Path) -> None:
        write_file(source / "0001.a.sql", "CREATE TABLE a(id INTEGER);\n")
        write_file(source / "0002.b.sql", "CREATE TABLE b(id INTEGER);\n")
        collection = MigrationsCollection(sources=str(source))

        hooks, migrations = collection.to_apply(make_history("0001.a"))

        assert [m.id for m in migrations] == ["0002.b"]
        assert set(hooks) == {
            "pre-apply",
            "post-apply",
            "pre-rollback",
            "post-rollback",
        }

    def test_returns_all_when_nothing_applied(self, source: Path) -> None:
        write_file(source / "0001.a.sql", "CREATE TABLE a(id INTEGER);\n")
        write_file(source / "0002.b.sql", "CREATE TABLE b(id INTEGER);\n")
        collection = MigrationsCollection(sources=str(source))

        _, migrations = collection.to_apply([])

        assert [m.id for m in migrations] == ["0001.a", "0002.b"]

    def test_target_exact_inclusive(self, source: Path) -> None:
        write_file(source / "0001.base.sql", "CREATE TABLE base(id INTEGER);\n")
        write_file(
            source / "0002.child.sql",
            "-- depends: 0001.base\nCREATE TABLE child(id INTEGER);\n",
        )
        collection = MigrationsCollection(sources=str(source))

        _, migrations = collection.to_apply([], target="0002.child")

        assert [m.id for m in migrations] == ["0001.base", "0002.child"]

    def test_target_exact_no_match_raises(self, source: Path) -> None:
        write_file(source / "0001.a.sql", "CREATE TABLE a(id INTEGER);\n")
        collection = MigrationsCollection(sources=str(source))

        with pytest.raises(exceptions.NoMigration):
            collection.to_apply([], target="9999.nonexistent")

    def test_target_partial_match_raises(self, source: Path) -> None:
        write_file(source / "0001.a.sql", "CREATE TABLE a(id INTEGER);\n")
        write_file(source / "0002.a.sql", "CREATE TABLE b(id INTEGER);\n")
        collection = MigrationsCollection(sources=str(source))

        with pytest.raises(exceptions.NoMigration):
            collection.to_apply([], target="a")

    def test_target_applied_returns_empty(self, source: Path) -> None:
        write_file(source / "0001.a.sql", "CREATE TABLE a(id INTEGER);\n")
        write_file(source / "0002.b.sql", "CREATE TABLE b(id INTEGER);\n")
        collection = MigrationsCollection(sources=str(source))

        _, migrations = collection.to_apply(
            make_history("0001.a", "0002.b"),
            target="0001.a",
        )

        assert migrations == []

    def test_topological_order(self, source: Path) -> None:
        write_file(source / "0001.base.sql", "CREATE TABLE base(id INTEGER);\n")
        write_file(
            source / "0002.child.sql",
            "-- depends: 0001.base\nCREATE TABLE child(id INTEGER);\n",
        )
        collection = MigrationsCollection(sources=str(source))

        _, migrations = collection.to_apply([])

        assert [m.id for m in migrations] == ["0001.base", "0002.child"]

    def test_rolled_back_migration_is_unapplied(self, source: Path) -> None:
        write_file(source / "0001.a.sql", "CREATE TABLE a(id INTEGER);\n")
        collection = MigrationsCollection(sources=str(source))

        history = [
            ("0001.a", "2020-01-01 00:00:00", "APPLIED"),
            ("0001.a", "2020-01-02 00:00:00", "ROLLED_BACK"),
        ]
        _, migrations = collection.to_apply(history)

        assert [m.id for m in migrations] == ["0001.a"]

    def test_reads_hooks(self, source: Path) -> None:
        write_file(source / "0001.a.sql", "CREATE TABLE a(id INTEGER);\n")
        write_file(source / "pre-apply.sql", "SELECT 1;\n")
        write_file(source / "post-apply.sql", "SELECT 2;\n")
        write_file(source / "pre-rollback.sql", "SELECT 3;\n")
        write_file(source / "post-rollback.sql", "SELECT 4;\n")
        collection = MigrationsCollection(sources=str(source))

        hooks, _ = collection.to_apply([])

        assert [hook.name for hook in hooks["pre-apply"]] == ["pre-apply"]
        assert [hook.name for hook in hooks["post-apply"]] == ["post-apply"]
        assert [hook.name for hook in hooks["pre-rollback"]] == ["pre-rollback"]
        assert [hook.name for hook in hooks["post-rollback"]] == ["post-rollback"]


class TestToRollback:
    def test_returns_applied_in_reverse_order(self, source: Path) -> None:
        write_file(source / "0001.base.sql", "CREATE TABLE base(id INTEGER);\n")
        write_file(
            source / "0002.child.sql",
            "-- depends: 0001.base\nCREATE TABLE child(id INTEGER);\n",
        )
        collection = MigrationsCollection(sources=str(source))

        _, migrations = collection.to_rollback(make_history("0001.base", "0002.child"))

        assert [m.id for m in migrations] == ["0002.child", "0001.base"]

    def test_returns_empty_when_nothing_applied(self, source: Path) -> None:
        write_file(source / "0001.a.sql", "CREATE TABLE a(id INTEGER);\n")
        collection = MigrationsCollection(sources=str(source))

        _, migrations = collection.to_rollback([])

        assert migrations == []

    def test_target_exact_inclusive(self, source: Path) -> None:
        write_file(source / "0001.a.sql", "CREATE TABLE a(id INTEGER);\n")
        write_file(
            source / "0002.b.sql",
            "-- depends: 0001.a\nCREATE TABLE b(id INTEGER);\n",
        )
        write_file(
            source / "0003.c.sql",
            "-- depends: 0002.b\nCREATE TABLE c(id INTEGER);\n",
        )
        collection = MigrationsCollection(sources=str(source))
        history = make_history("0001.a", "0002.b", "0003.c")

        _, migrations = collection.to_rollback(history, target="0002.b")

        assert [m.id for m in migrations] == ["0003.c", "0002.b"]

    def test_target_exact_no_match_raises(self, source: Path) -> None:
        write_file(source / "0001.a.sql", "CREATE TABLE a(id INTEGER);\n")
        collection = MigrationsCollection(sources=str(source))

        with pytest.raises(exceptions.NoMigration):
            collection.to_rollback(make_history("0001.a"), target="9999.x")

    def test_target_not_applied_returns_empty(self, source: Path) -> None:
        write_file(source / "0001.a.sql", "CREATE TABLE a(id INTEGER);\n")
        write_file(source / "0002.b.sql", "CREATE TABLE b(id INTEGER);\n")
        collection = MigrationsCollection(sources=str(source))

        _, migrations = collection.to_rollback(make_history("0001.a"), target="0002.b")

        assert migrations == []

    def test_rolled_back_migration_not_rolled_back_again(self, source: Path) -> None:
        write_file(source / "0001.a.sql", "CREATE TABLE a(id INTEGER);\n")
        collection = MigrationsCollection(sources=str(source))

        history = [
            ("0001.a", "2020-01-01 00:00:00", "APPLIED"),
            ("0001.a", "2020-01-02 00:00:00", "ROLLED_BACK"),
        ]
        _, migrations = collection.to_rollback(history)

        assert migrations == []

    def test_reads_hooks(self, source: Path) -> None:
        write_file(source / "0001.a.sql", "CREATE TABLE a(id INTEGER);\n")
        write_file(source / "pre-rollback.sql", "SELECT 3;\n")
        write_file(source / "post-rollback.sql", "SELECT 4;\n")
        collection = MigrationsCollection(sources=str(source))

        hooks, _ = collection.to_rollback(make_history("0001.a"))

        assert [hook.name for hook in hooks["pre-rollback"]] == ["pre-rollback"]
        assert [hook.name for hook in hooks["post-rollback"]] == ["post-rollback"]


class TestConstructor:
    def test_empty_sources_raises(self) -> None:
        with pytest.raises(ValueError):
            MigrationsCollection(sources="")

    def test_accepts_list_of_sources(self, source: Path) -> None:
        write_file(source / "0001.a.sql", "CREATE TABLE a(id INTEGER);\n")
        other = source / "other"
        other.mkdir()
        write_file(other / "0002.b.sql", "CREATE TABLE b(id INTEGER);\n")
        collection = MigrationsCollection(sources=[str(source), str(other)])

        migrations = collection.list()

        assert [m.id for m in migrations] == ["0001.a", "0002.b"]


class TestMigration:
    def test_load_parses_directives(self, source: Path) -> None:
        write_file(
            source / "0001.init.sql",
            "-- depends: 0000.other\n"
            "-- transactional: false\n"
            "CREATE TABLE users(id INTEGER);\n",
        )
        migration = Migration("0001.init", str(source / "0001.init.sql"))
        migration.load()

        assert migration.depends == {"0000.other"}
        assert migration.transactional is False
        assert migration.apply_statements == ["CREATE TABLE users(id INTEGER);"]

    def test_load_defaults(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE users(id INTEGER);\n")
        migration = Migration("0001.init", str(source / "0001.init.sql"))
        migration.load()

        assert migration.depends == set()
        assert migration.transactional is None

    def test_load_invalid_transactional_raises(self, source: Path) -> None:
        write_file(
            source / "0001.init.sql",
            "-- transactional: maybe\nCREATE TABLE users(id INTEGER);\n",
        )
        migration = Migration("0001.init", str(source / "0001.init.sql"))

        with pytest.raises(exceptions.BadMigration):
            migration.load()

    def test_load_depends_comma_separated(self, source: Path) -> None:
        write_file(
            source / "0001.init.sql",
            "-- depends: 0000.a, 0000.b\nCREATE TABLE users(id INTEGER);\n",
        )
        migration = Migration("0001.init", str(source / "0001.init.sql"))
        migration.load()

        assert migration.depends == {"0000.a", "0000.b"}

    def test_load_depends_comma_with_spaces(self, source: Path) -> None:
        write_file(
            source / "0001.init.sql",
            "-- depends: 0000.a , 0000.b , 0000.c\nCREATE TABLE users(id INTEGER);\n",
        )
        migration = Migration("0001.init", str(source / "0001.init.sql"))
        migration.load()

        assert migration.depends == {"0000.a", "0000.b", "0000.c"}

    def test_load_transactional_none(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE users(id INTEGER);\n")
        migration = Migration("0001.init", str(source / "0001.init.sql"))
        migration.load()

        assert migration.transactional is None

    def test_load_transactional_true(self, source: Path) -> None:
        write_file(
            source / "0001.init.sql",
            "-- transactional: true\nCREATE TABLE users(id INTEGER);\n",
        )
        migration = Migration("0001.init", str(source / "0001.init.sql"))
        migration.load()

        assert migration.transactional is True

    def test_rollback_transactional_default(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE t(id INTEGER);\n")
        write_file(source / "0001.init.rollback.sql", "DROP TABLE t;\n")
        migration = Migration("0001.init", str(source / "0001.init.sql"))
        migration.load()

        assert migration.rollback_transactional is None
        assert migration.rollback_statements == ["DROP TABLE t;"]

    def test_rollback_transactional_specified(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE t(id INTEGER);\n")
        write_file(
            source / "0001.init.rollback.sql",
            "-- transactional: false\nDROP TABLE t;\n",
        )
        migration = Migration("0001.init", str(source / "0001.init.sql"))
        migration.load()

        assert migration.rollback_transactional is False

    def test_rollback_transactional_invalid_raises(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE t(id INTEGER);\n")
        write_file(
            source / "0001.init.rollback.sql",
            "-- transactional: maybe\nDROP TABLE t;\n",
        )
        migration = Migration("0001.init", str(source / "0001.init.sql"))

        with pytest.raises(exceptions.BadMigration):
            migration.load()

    def test_rollback_other_directive_raises(self, source: Path) -> None:
        write_file(source / "0001.init.sql", "CREATE TABLE t(id INTEGER);\n")
        write_file(
            source / "0001.init.rollback.sql",
            "-- depends: 0000.x\nDROP TABLE t;\n",
        )
        migration = Migration("0001.init", str(source / "0001.init.sql"))

        with pytest.raises(exceptions.BadMigration):
            migration.load()
