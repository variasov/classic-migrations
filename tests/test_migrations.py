from pathlib import Path

import pytest

from classic.migrations import MigrationsCollection, exceptions
from classic.migrations.migrations import (
    Hook,
    Migration,
    parse_metadata_from_sql_comments,
)


def write_file(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def make_history(*applied: str) -> list[tuple[str, str, str]]:
    return [
        (migration_id, "2020-01-01 00:00:00", "APPLIED") for migration_id in applied
    ]


def write_graph(source: Path, graph: dict[str, str]) -> None:
    """Write migrations; ``graph`` maps id to a comma-separated depends list."""
    for migration_id, depends in graph.items():
        header = f"-- depends: {depends}\n" if depends else ""
        write_file(source / f"{migration_id}.sql", header + "SELECT 1;\n")


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

    def test_list_independent_in_lexicographic_order(self, source: Path) -> None:
        write_graph(source, {"0003.c": "", "0001.a": "", "0002.b": ""})
        collection = MigrationsCollection(sources=str(source))

        assert [m.id for m in collection.list()] == ["0001.a", "0002.b", "0003.c"]

    def test_list_is_deterministic_with_dependency_on_first(
        self,
        source: Path,
    ) -> None:
        # graphlib.static_order() gives ['0001.a', '0003.c', '0002.b'] here.
        write_graph(source, {"0001.a": "", "0002.b": "", "0003.c": "0001.a"})
        collection = MigrationsCollection(sources=str(source))

        assert [m.id for m in collection.list()] == ["0001.a", "0002.b", "0003.c"]

    def test_list_dependency_changes_order(self, source: Path) -> None:
        write_graph(source, {"0001.b": "0002.a", "0002.a": ""})
        collection = MigrationsCollection(sources=str(source))

        assert [m.id for m in collection.list()] == ["0002.a", "0001.b"]

    def test_list_takes_smallest_ready_id(self, source: Path) -> None:
        write_graph(
            source,
            {"0001.x": "0004.d", "0002.y": "", "0003.z": "", "0004.d": ""},
        )
        collection = MigrationsCollection(sources=str(source))

        assert [m.id for m in collection.list()] == [
            "0002.y",
            "0003.z",
            "0004.d",
            "0001.x",
        ]

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

        with pytest.raises(exceptions.MigrationConflict, match=r"0001\.init"):
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

    def test_target_applies_only_target_and_dependencies(self, source: Path) -> None:
        write_graph(source, {"0001.a": "", "0002.b": "", "0003.c": "0001.a"})
        collection = MigrationsCollection(sources=str(source))

        _, migrations = collection.to_apply([], target="0002.b")

        assert [m.id for m in migrations] == ["0002.b"]

    def test_target_applies_transitive_dependencies(self, source: Path) -> None:
        write_graph(
            source,
            {
                "0001.a": "",
                "0002.b": "0001.a",
                "0003.c": "0002.b",
                "0004.d": "",
                "0005.e": "0003.c",
            },
        )
        collection = MigrationsCollection(sources=str(source))

        _, migrations = collection.to_apply([], target="0003.c")

        assert [m.id for m in migrations] == ["0001.a", "0002.b", "0003.c"]

    def test_target_skips_already_applied_dependencies(self, source: Path) -> None:
        write_graph(source, {"0001.a": "", "0002.b": "0001.a", "0003.c": "0002.b"})
        collection = MigrationsCollection(sources=str(source))

        _, migrations = collection.to_apply(make_history("0001.a"), target="0003.c")

        assert [m.id for m in migrations] == ["0002.b", "0003.c"]

    def test_target_applied_returns_only_unapplied_dependencies(
        self,
        source: Path,
    ) -> None:
        write_graph(source, {"0001.a": "", "0002.b": "0001.a"})
        collection = MigrationsCollection(sources=str(source))

        _, migrations = collection.to_apply(make_history("0002.b"), target="0002.b")

        assert [m.id for m in migrations] == ["0001.a"]

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

    def test_target_rolls_back_dependents_not_independent(
        self,
        source: Path,
    ) -> None:
        write_graph(source, {"0001.a": "", "0002.b": "", "0003.c": "0001.a"})
        collection = MigrationsCollection(sources=str(source))
        history = make_history("0001.a", "0002.b", "0003.c")

        _, migrations = collection.to_rollback(history, target="0001.a")
        assert [m.id for m in migrations] == ["0003.c", "0001.a"]

        _, migrations = collection.to_rollback(history, target="0003.c")
        assert [m.id for m in migrations] == ["0003.c"]

    def test_target_independent_not_rolled_back(self, source: Path) -> None:
        write_graph(source, {"0001.a": "", "0002.b": "", "0003.c": ""})
        collection = MigrationsCollection(sources=str(source))
        history = make_history("0001.a", "0002.b", "0003.c")

        _, migrations = collection.to_rollback(history, target="0002.b")

        assert [m.id for m in migrations] == ["0002.b"]

    def test_target_rolls_back_transitive_dependents(self, source: Path) -> None:
        write_graph(
            source,
            {
                "0001.a": "",
                "0002.b": "0001.a",
                "0003.c": "0002.b",
                "0004.d": "",
                "0005.e": "0003.c",
            },
        )
        collection = MigrationsCollection(sources=str(source))
        history = make_history("0001.a", "0002.b", "0003.c", "0004.d", "0005.e")

        _, migrations = collection.to_rollback(history, target="0002.b")

        assert [m.id for m in migrations] == ["0005.e", "0003.c", "0002.b"]

    def test_target_skips_not_applied_dependents(self, source: Path) -> None:
        write_graph(source, {"0001.a": "", "0002.b": "0001.a", "0003.c": "0001.a"})
        collection = MigrationsCollection(sources=str(source))

        _, migrations = collection.to_rollback(
            make_history("0001.a", "0002.b"),
            target="0001.a",
        )

        assert [m.id for m in migrations] == ["0002.b", "0001.a"]

    def test_target_not_applied_rolls_back_applied_dependents(
        self,
        source: Path,
    ) -> None:
        write_graph(source, {"0001.a": "", "0002.b": "0001.a"})
        collection = MigrationsCollection(sources=str(source))

        _, migrations = collection.to_rollback(make_history("0002.b"), target="0001.a")

        assert [m.id for m in migrations] == ["0002.b"]

    def test_order_is_reverse_of_list(self, source: Path) -> None:
        write_graph(
            source,
            {"0001.b": "0002.a", "0002.a": "", "0003.c": "", "0004.d": "0003.c"},
        )
        collection = MigrationsCollection(sources=str(source))
        history = make_history("0001.b", "0002.a", "0003.c", "0004.d")

        _, migrations = collection.to_rollback(history)

        assert [m.id for m in migrations] == [m.id for m in reversed(collection.list())]
        assert [m.id for m in migrations] == ["0004.d", "0003.c", "0001.b", "0002.a"]

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


def make_events(*events: tuple[str, str]) -> list[tuple[str, str, str]]:
    """Build history rows from (migration_id, status) pairs."""
    return [
        (migration_id, f"2020-01-01 00:00:{i:02d}", status)
        for i, (migration_id, status) in enumerate(events)
    ]


class TestStatuses:
    def test_statuses_latest_event_wins(self) -> None:
        history = make_events(
            ("0001.a", "PENDING"),
            ("0001.a", "APPLIED"),
            ("0002.b", "APPLIED"),
            ("0002.b", "PENDING"),
        )

        assert MigrationsCollection.statuses(history) == {
            "0001.a": "APPLIED",
            "0002.b": "PENDING",
        }

    def test_statuses_empty(self) -> None:
        assert MigrationsCollection.statuses([]) == {}

    def test_pending_ids(self) -> None:
        history = make_events(
            ("0001.a", "APPLIED"),
            ("0002.b", "PENDING"),
            ("0003.c", "PENDING"),
            ("0003.c", "APPLIED"),
            ("0004.d", "APPLIED"),
            ("0004.d", "PENDING"),
            ("0004.d", "ROLLED_BACK"),
        )

        assert MigrationsCollection.pending_ids(history) == {"0002.b"}

    def test_applied_ids_ignores_pending(self) -> None:
        history = make_events(
            ("0001.a", "APPLIED"),
            ("0002.b", "PENDING"),
            ("0001.a", "PENDING"),
            ("0003.c", "APPLIED"),
        )

        assert MigrationsCollection.applied_ids(history) == {"0003.c"}


class TestPending:
    def test_to_apply_raises_on_pending(self, source: Path) -> None:
        write_graph(source, {"0001.a": "", "0002.b": ""})
        collection = MigrationsCollection(sources=str(source))

        with pytest.raises(exceptions.MigrationPending):
            collection.to_apply(make_events(("0001.a", "PENDING")))

    def test_to_rollback_raises_on_pending(self, source: Path) -> None:
        write_graph(source, {"0001.a": "", "0002.b": ""})
        collection = MigrationsCollection(sources=str(source))

        with pytest.raises(exceptions.MigrationPending):
            collection.to_rollback(
                make_events(("0001.a", "APPLIED"), ("0002.b", "PENDING")),
            )

    def test_to_apply_raises_with_target(self, source: Path) -> None:
        write_graph(source, {"0001.a": "", "0002.b": "0001.a"})
        collection = MigrationsCollection(sources=str(source))

        with pytest.raises(exceptions.MigrationPending):
            collection.to_apply(
                make_events(("0001.a", "PENDING")),
                target="0002.b",
            )

    def test_to_rollback_raises_with_target(self, source: Path) -> None:
        write_graph(source, {"0001.a": "", "0002.b": "0001.a"})
        collection = MigrationsCollection(sources=str(source))

        with pytest.raises(exceptions.MigrationPending):
            collection.to_rollback(
                make_events(("0001.a", "APPLIED"), ("0002.b", "PENDING")),
                target="0001.a",
            )

    def test_blocks_even_if_unrelated_to_target(self, source: Path) -> None:
        write_graph(source, {"0001.a": "", "0002.b": ""})
        collection = MigrationsCollection(sources=str(source))
        history = make_events(("0001.a", "PENDING"))

        with pytest.raises(exceptions.MigrationPending):
            collection.to_apply(history, target="0002.b")
        with pytest.raises(exceptions.MigrationPending):
            collection.to_rollback(history, target="0002.b")

    def test_blocks_if_pending_id_is_not_in_collection(self, source: Path) -> None:
        write_graph(source, {"0001.a": ""})
        collection = MigrationsCollection(sources=str(source))
        history = make_events(("9999.gone", "PENDING"))

        with pytest.raises(exceptions.MigrationPending, match=r"9999\.gone"):
            collection.to_apply(history)
        with pytest.raises(exceptions.MigrationPending, match=r"9999\.gone"):
            collection.to_rollback(history)
        with pytest.raises(exceptions.MigrationPending, match="file is missing"):
            collection.to_apply(history)

    def test_pending_after_applied_blocks(self, source: Path) -> None:
        # Rollback of an applied migration failed midway.
        write_graph(source, {"0001.a": ""})
        collection = MigrationsCollection(sources=str(source))
        history = make_events(("0001.a", "APPLIED"), ("0001.a", "PENDING"))

        with pytest.raises(exceptions.MigrationPending):
            collection.to_rollback(history)

    def test_resolved_pending_does_not_block(self, source: Path) -> None:
        write_graph(source, {"0001.a": ""})
        collection = MigrationsCollection(sources=str(source))
        history = make_events(("0001.a", "PENDING"), ("0001.a", "APPLIED"))

        _, to_apply = collection.to_apply(history)
        _, to_rollback = collection.to_rollback(history)

        assert to_apply == []
        assert [m.id for m in to_rollback] == ["0001.a"]

    def test_message_has_id_and_both_commands(self, source: Path) -> None:
        write_graph(source, {"0003.big": ""})
        collection = MigrationsCollection(sources=str(source))

        with pytest.raises(exceptions.MigrationPending) as exc_info:
            collection.to_apply(make_events(("0003.big", "PENDING")))

        assert str(exc_info.value) == (
            "Migration '0003.big' is PENDING: a previous run failed midway. "
            "Fix the database manually, then mark it with "
            '"migrations apply --fake 0003.big" '
            'or "migrations rollback --fake 0003.big".'
        )

    def test_message_lists_all_pending(self, source: Path) -> None:
        write_graph(source, {"0001.a": "", "0002.b": "", "0003.c": ""})
        collection = MigrationsCollection(sources=str(source))
        history = make_events(("0002.b", "PENDING"), ("0001.a", "PENDING"))

        with pytest.raises(exceptions.MigrationPending) as exc_info:
            collection.to_rollback(history)

        message = str(exc_info.value)
        for migration_id in ("0001.a", "0002.b"):
            assert f"migrations apply --fake {migration_id}" in message
            assert f"migrations rollback --fake {migration_id}" in message
        assert "0003.c" not in message

    def test_unknown_target_checked_before_pending(self, source: Path) -> None:
        write_graph(source, {"0001.a": ""})
        collection = MigrationsCollection(sources=str(source))
        history = make_events(("0001.a", "PENDING"))

        with pytest.raises(exceptions.NoMigration):
            collection.to_apply(history, target="9999.nope")
        with pytest.raises(exceptions.NoMigration):
            collection.to_rollback(history, target="9999.nope")


class TestFake:
    def test_to_apply_fake_includes_pending(self, source: Path) -> None:
        write_graph(source, {"0001.a": "", "0002.b": ""})
        collection = MigrationsCollection(sources=str(source))
        history = make_events(("0001.a", "APPLIED"), ("0002.b", "PENDING"))

        _, migrations = collection.to_apply(history, fake=True)

        assert [m.id for m in migrations] == ["0002.b"]

    def test_to_apply_fake_target_closure(self, source: Path) -> None:
        write_graph(source, {"0001.a": "", "0002.b": "0001.a", "0003.c": ""})
        collection = MigrationsCollection(sources=str(source))
        history = make_events(("0001.a", "APPLIED"), ("0002.b", "PENDING"))

        _, migrations = collection.to_apply(history, target="0002.b", fake=True)

        assert [m.id for m in migrations] == ["0002.b"]

    def test_to_apply_fake_ignores_unknown_pending(self, source: Path) -> None:
        write_graph(source, {"0001.a": ""})
        collection = MigrationsCollection(sources=str(source))

        _, migrations = collection.to_apply(
            make_events(("9999.gone", "PENDING")),
            target="0001.a",
            fake=True,
        )

        assert [m.id for m in migrations] == ["0001.a"]

    def test_to_rollback_fake_includes_pending(self, source: Path) -> None:
        write_graph(source, {"0001.a": "", "0002.b": "0001.a"})
        collection = MigrationsCollection(sources=str(source))
        history = make_events(("0001.a", "APPLIED"), ("0002.b", "PENDING"))

        _, migrations = collection.to_rollback(history, fake=True)

        assert [m.id for m in migrations] == ["0002.b", "0001.a"]

    def test_to_rollback_fake_target_closure(self, source: Path) -> None:
        write_graph(source, {"0001.a": "", "0002.b": "0001.a", "0003.c": ""})
        collection = MigrationsCollection(sources=str(source))
        history = make_events(
            ("0001.a", "APPLIED"),
            ("0002.b", "PENDING"),
            ("0003.c", "APPLIED"),
        )

        _, migrations = collection.to_rollback(history, target="0001.a", fake=True)
        _, only_pending = collection.to_rollback(history, target="0002.b", fake=True)

        assert [m.id for m in migrations] == ["0002.b", "0001.a"]
        assert [m.id for m in only_pending] == ["0002.b"]


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


def _load(source: Path, header: str, rollback: str | None = None) -> Migration:
    write_file(source / "0001.init.sql", header + "CREATE TABLE t(id INTEGER);\n")
    if rollback is not None:
        write_file(source / "0001.init.rollback.sql", rollback + "DROP TABLE t;\n")
    migration = Migration("0001.init", str(source / "0001.init.sql"))
    migration.load()
    return migration


class TestDirectiveParsing:
    def test_names_are_case_insensitive(self, source: Path) -> None:
        migration = _load(
            source,
            "-- Depends: 0000.a\n-- TRANSACTIONAL: False\n",
        )

        assert migration.depends == {"0000.a"}
        assert migration.transactional is False

    @pytest.mark.parametrize(
        ("line", "hint"),
        [
            ("-- depend: 0000.a", "depends"),
            ("-- transactonal: false", "transactional"),
            ("-- depends_on: 0000.a", "depends"),
        ],
    )
    def test_typo_raises_with_hint(self, source: Path, line: str, hint: str) -> None:
        with pytest.raises(exceptions.BadMigration) as exc:
            _load(source, f"-- Author: me\n{line}\n")

        message = str(exc.value)
        assert "0001.init.sql" in message
        assert f"did you mean '{hint}'?" in message
        assert ":2" in message

    def test_ordinary_comments_are_ignored(self, source: Path) -> None:
        migration = _load(
            source,
            "-- TODO: fix\n-- Author: me\n-- Note: x\n-- Description: y\n"
            "-- depends: 0000.a\n",
        )

        assert migration.depends == {"0000.a"}

    def test_directive_after_sql_is_ignored(self, source: Path) -> None:
        write_file(
            source / "0001.init.sql",
            "CREATE TABLE t(id INTEGER);\n-- depend: 0000.a\n",
        )
        migration = Migration("0001.init", str(source / "0001.init.sql"))
        migration.load()

        assert migration.depends == set()

    def test_several_depends_lines_are_merged(self, source: Path) -> None:
        migration = _load(source, "-- depends: 0000.a\n-- depends: 0000.b\n")

        assert migration.depends == {"0000.a", "0000.b"}

    def test_depends_separated_by_spaces(self, source: Path) -> None:
        migration = _load(source, "-- depends: 0000.a 0000.b\n")

        assert migration.depends == {"0000.a", "0000.b"}

    def test_depends_mixed_separators(self, source: Path) -> None:
        migration = _load(source, "-- depends: 0000.a, 0000.b 0000.c\n")

        assert migration.depends == {"0000.a", "0000.b", "0000.c"}

    def test_duplicate_transactional_raises(self, source: Path) -> None:
        with pytest.raises(exceptions.BadMigration, match="Duplicate"):
            _load(source, "-- transactional: true\n-- transactional: false\n")

    def test_rollback_directive_case_insensitive(self, source: Path) -> None:
        migration = _load(source, "", "-- Transactional: false\n")

        assert migration.rollback_transactional is False

    def test_rollback_depends_raises(self, source: Path) -> None:
        with pytest.raises(exceptions.BadMigration):
            _load(source, "", "-- Depends: 0000.x\n")

    def test_rollback_typo_raises(self, source: Path) -> None:
        with pytest.raises(exceptions.BadMigration, match=r"rollback.sql"):
            _load(source, "", "-- transactonal: false\n")

    def test_hook_ignores_directives_and_typos(self, source: Path) -> None:
        write_file(
            source / "pre-apply.sql",
            "-- depend: x\n-- depends: y\nSELECT 1;\n",
        )
        hook = Hook("pre-apply", str(source / "pre-apply.sql"))
        hook.load()

        assert hook.statements == ["SELECT 1;"]


class TestSources:
    def test_accepts_path(self, source: Path) -> None:
        write_file(source / "0001.a.sql", "SELECT 1;\n")
        collection = MigrationsCollection(source)
        assert [m.id for m in collection.list()] == ["0001.a"]
        assert collection.sources == [str(source)]

    def test_accepts_list_of_paths(self, source: Path) -> None:
        sub = source / "sub"
        write_file(source / "0001.a.sql", "SELECT 1;\n")
        write_file(sub / "0002.b.sql", "SELECT 1;\n")
        collection = MigrationsCollection([source, sub])
        assert [m.id for m in collection.list()] == ["0001.a", "0002.b"]

    def test_missing_directory(self, source: Path) -> None:
        collection = MigrationsCollection(source / "nope")
        with pytest.raises(exceptions.MissingSource, match="does not match"):
            collection.list()

    def test_missing_directory_among_existing(self, source: Path) -> None:
        collection = MigrationsCollection([source, source / "nope"])
        with pytest.raises(exceptions.MissingSource):
            collection.to_apply([])

    def test_glob_without_matches(self, source: Path) -> None:
        collection = MigrationsCollection(str(source / "mig-*"))
        with pytest.raises(exceptions.MissingSource):
            collection.list()

    def test_path_to_file(self, source: Path) -> None:
        file = write_file(source / "0001.a.sql", "SELECT 1;\n")
        collection = MigrationsCollection(file)
        with pytest.raises(exceptions.MissingSource):
            collection.list()

    def test_glob_with_matches(self, source: Path) -> None:
        write_file(source / "mig-1" / "0001.a.sql", "SELECT 1;\n")
        write_file(source / "mig-2" / "0002.b.sql", "SELECT 1;\n")
        collection = MigrationsCollection(str(source / "mig-*"))
        assert [m.id for m in collection.list()] == ["0001.a", "0002.b"]

    def test_empty_existing_directory(self, source: Path) -> None:
        assert MigrationsCollection(source).list() == []

    def test_empty_sources(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            MigrationsCollection([])


# ---------------------------------------------------------------------------
# Mutation-killing tests
# ---------------------------------------------------------------------------


class TestCrLfLineEnding:
    """Kill mutants that break \r\n/\r detection in parse_metadata.

    Mutants: mutmut_3 (re.search returns None), mutmut_8 (broken regex).
    """

    def test_r_returns_correct_sql_line_ending(self) -> None:
        """CR-only content must preserve \r line endings in returned SQL."""
        _directives, sql = parse_metadata_from_sql_comments("SELECT 1;\r")
        assert sql == "SELECT 1;\r"

    def test_rlf_with_directives_parses_correctly(self) -> None:
        """CRLF content: directives extract and SQL ends with \r\n."""
        directives, sql = parse_metadata_from_sql_comments(
            "-- depends: 0000.a\r\nSELECT 1;\r\n",
        )
        assert directives["depends"] == ["0000.a"]
        assert sql == "SELECT 1;\r\n"

    def test_r_only_with_directives_parses_correctly(self) -> None:
        """CR-only content: directives must still be extractable."""
        directives, sql = parse_metadata_from_sql_comments(
            "-- depends: 0000.a\rSELECT 1;\r",
        )
        assert directives["depends"] == ["0000.a"]
        assert sql == "SELECT 1;\r"


class TestTransactionalErrorMessagePaths:
    """Kill mutants that change args to _parse_transactional in Migration.load.

    Mutants: mutmut_20 (self.path -> None), mutmut_21 ("" -> None),
    mutmut_25 ("" -> "XXXX").
    """

    def test_invalid_transactional_messages_include_source_path(
        self,
        source: Path,
    ) -> None:
        """The actual source path must appear right after 'in '."""
        p = source / "0001.init.sql"
        write_file(p, "-- transactional: maybe\nSELECT 1;\n")
        m = Migration("0001.init", str(p))
        with pytest.raises(exceptions.BadMigration) as exc:
            m.load()
        message = str(exc.value)
        # Doc: expected path after 'in '
        assert "in " + str(p) in message, (
            f"Expected path {p!r} right after 'in ', got: {message}"
        )


class TestRollbackDirectiveErrorPath:
    """Kill mutants that change args to _parse_transactional in
    _load_rollback_directives.

    Mutants: mutmut_7 (self.rollback_path() -> None),
            mutmut_8 ("rollback file " -> None),
            mutmut_12 ("rollback file " -> "XXrollback file XX"),
            mutmut_13 ("rollback file " -> "ROLLBACK FILE ").
    """

    def test_invalid_rollback_transactional_messages_include_rollback_file(
        self,
        source: Path,
    ) -> None:
        """The rollback file path and 'rollback file ' prefix must be in the error."""
        p = source / "0001.a.sql"
        rp = source / "0001.a.rollback.sql"
        write_file(p, "SELECT 1;\n")
        write_file(rp, "-- transactional: maybe\nDROP TABLE t;\n")
        m = Migration("0001.a", str(p))
        with pytest.raises(exceptions.BadMigration) as exc:
            m.load()
        message = str(exc.value)
        # Doc: kill mutmut_7 (rollback path replaced by None)
        assert str(rp) in message, (
            f"Expected rollback path {rp!r} in error, got: {message}"
        )
        # Doc: kill mutmut_8/12/13 (rollback prefix malformed)
        assert " in rollback file " in message, (
            f"Expected ' in rollback file ' in error, got: {message}"
        )


class TestReadMigrationsDirectoryBreak:
    """Kill mutmut_11 (continue -> break for directory) in _read_migrations.
    A directory before a .sql file should be skipped, not break the loop.
    """

    def test_subdirectory_before_migration_does_not_break(self, source: Path) -> None:
        # "0000_sub" sorts before "0001.a.sql" -> directory first, file second
        (source / "0000_sub").mkdir()
        write_file(source / "0001.a.sql", "SELECT 1;\n")
        collection = MigrationsCollection(str(source))
        assert [m.id for m in collection.list()] == ["0001.a"]


class TestReadMigrationsHookBreak:
    """Kill mutmut_22 (continue -> break for hooks) in _read_migrations.
    If a hook file sorts between two .sql files, the second .sql must still be found.
    """

    def test_hook_between_migrations_does_not_break(self, source: Path) -> None:
        # "z0001.a.sql" sorts after 'pre-apply.sql' (p < z)
        write_file(source / "pre-apply.sql", "SELECT 1;\n")
        write_file(source / "z0001.a.sql", "SELECT 1;\n")
        collection = MigrationsCollection(str(source))
        assert [m.id for m in collection.list()] == ["z0001.a"]


class TestMigrationSourceDir:
    """Kill mutmut_28 (source_dir=str(None)), mutmut_31 (removed),
    mutmut_33 (str(None) -> "None") in _read_migrations.

    The Migration's source_dir must be the directory path, not None or "None".
    """

    def test_loaded_migration_has_source_dir(self, source: Path) -> None:
        write_file(source / "0001.a.sql", "SELECT 1;\n")
        collection = MigrationsCollection(str(source))
        m = collection.list()[0]
        assert m.source_dir == str(source)


class TestCircularDependencyMessageFormat:
    """Kill _topological mutmut_12, mutmut_16.

    mutmut_12: adds XX prefix/suffix to error message.
    mutmut_16: changes join separator to "XX, XX".
    """

    def test_message_starts_with_Circular(self, source: Path) -> None:
        write_file(
            source / "0001.a.sql",
            "-- depends: 0002.b\nCREATE TABLE a(id INTEGER);\n",
        )
        write_file(
            source / "0002.b.sql",
            "-- depends: 0001.a\nCREATE TABLE b(id INTEGER);\n",
        )
        collection = MigrationsCollection(sources=str(source))
        with pytest.raises(exceptions.BadMigration) as exc:
            collection.list()
        message = str(exc.value)
        assert message.startswith("Circular"), f"Message starts with XX: {message}"

    def test_comma_separator_in_cycle_message(self, source: Path) -> None:
        write_file(
            source / "0001.a.sql",
            "-- depends: 0002.b\nCREATE TABLE a(id INTEGER);\n",
        )
        write_file(
            source / "0002.b.sql",
            "-- depends: 0001.a\nCREATE TABLE b(id INTEGER);\n",
        )
        collection = MigrationsCollection(sources=str(source))
        with pytest.raises(exceptions.BadMigration) as exc:
            collection.list()
        message = str(exc.value)
        assert ", \\d" in message or ", " in message, (
            f"Expected comma-space separator in cycle message, got: {message}"
        )

    def test_no_XX_in_cycle_message(self, source: Path) -> None:
        write_file(
            source / "0001.a.sql",
            "-- depends: 0002.b\nCREATE TABLE a(id INTEGER);\n",
        )
        write_file(
            source / "0002.b.sql",
            "-- depends: 0001.a\nCREATE TABLE b(id INTEGER);\n",
        )
        collection = MigrationsCollection(sources=str(source))
        with pytest.raises(exceptions.BadMigration) as exc:
            collection.list()
        message = str(exc.value)
        assert "X" not in message, f"Message contains XX marker: {message}"
