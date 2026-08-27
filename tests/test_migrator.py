from pathlib import Path
from typing import Any, cast

import pytest
from classic.migrations.migrator import Migrator

from tests.backends.fake import FakeBackend


def write_file(path: Path, content: str) -> Path:
    path_ = str(path)
    with open(path_, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def _fake_backend(migrator: Migrator) -> FakeBackend:
    return cast(FakeBackend, migrator.backend)


def _make_migration(
    migration_id: str,
    statements: list[str] | None = None,
    transactional: bool | None = None,
    rollback_statements: list[str] | None = None,
    rollback_transactional: bool | None = None,
) -> Any:
    from classic.migrations.migrations import Migration

    m = Migration(migration_id, f"/fake/{migration_id}.sql")
    m.apply_statements = statements or [f"-- apply {migration_id}"]
    m.rollback_statements = rollback_statements or [f"-- rollback {migration_id}"]
    m.transactional = transactional
    m.rollback_transactional = rollback_transactional
    if transactional is True and rollback_transactional is None:
        m.rollback_transactional = True
    m._loaded = True
    return m


class TestLock:
    def test_context_manager_acquires_lock(self) -> None:
        migrator = Migrator(driver="fake")
        with migrator:
            backend = _fake_backend(migrator)
            assert backend.locked

    def test_lock_released_after_context(self) -> None:
        migrator = Migrator(driver="fake")
        with migrator:
            backend = _fake_backend(migrator)
            assert backend.locked
        assert not backend.locked

    def test_lock_can_be_used_by_methods(self) -> None:
        migrator = Migrator(driver="fake")
        with migrator:
            history = migrator.history()
            assert history == []
            migrator.apply([], {})
            migrator.rollback([], {})

    def test_methods_raise_outside_context(self) -> None:
        migrator = Migrator(driver="fake")
        with pytest.raises(RuntimeError, match="not in a context"):
            migrator.history()
        with pytest.raises(RuntimeError, match="not in a context"):
            migrator.apply([], {})
        with pytest.raises(RuntimeError, match="not in a context"):
            migrator.rollback([], {})


class TestHistory:
    def test_history_returns_event_log(self) -> None:
        migrator = Migrator(driver="fake")
        with migrator:
            backend = _fake_backend(migrator)
            backend.mark("0001.a", "APPLIED")
            backend.mark("0001.a", "ROLLED_BACK")
            history = migrator.history()

        assert len(history) == 2
        assert history[0][0] == "0001.a"
        assert history[0][2] == "APPLIED"
        assert history[1][2] == "ROLLED_BACK"

    def test_history_ensures_table(self) -> None:
        migrator = Migrator(driver="fake")
        with migrator:
            backend = _fake_backend(migrator)
            assert not backend.migration_table_ready
            migrator.history()
            assert backend.migration_table_ready


class TestApply:
    def test_apply_sequence(self) -> None:
        migrator = Migrator(driver="fake")
        m1 = _make_migration("0001.a", transactional=True)
        m2 = _make_migration("0002.b", transactional=True)

        with migrator:
            backend = _fake_backend(migrator)
            migrator.apply([m1, m2], {})

        assert backend.applied_list == ["0001.a", "0002.b"]

    def test_apply_mark_inside_transaction(self) -> None:
        migrator = Migrator(driver="fake")
        m1 = _make_migration("0001.a", transactional=True)

        with migrator:
            backend = _fake_backend(migrator)
            migrator.apply([m1], {})

        ops = backend.oplog
        begin_idx = next(i for i, (op, _) in enumerate(ops) if op == "begin")
        stmt_idx = next(i for i, (op, _) in enumerate(ops) if op == "cursor")
        mark_idx = next(i for i, (op, _) in enumerate(ops) if op == "mark")
        commit_idx = next(i for i, (op, _) in enumerate(ops) if op == "commit")
        assert begin_idx < stmt_idx < mark_idx < commit_idx

    def test_apply_non_transactional_sequence(self) -> None:
        migrator = Migrator(driver="fake")
        m1 = _make_migration("0001.a", transactional=False)

        with migrator:
            backend = _fake_backend(migrator)
            migrator.apply([m1], {})

        events = backend.events
        assert [e[2] for e in events] == ["PENDING", "APPLIED"]
        assert events[0][0] == "0001.a"
        assert events[1][0] == "0001.a"

    def test_apply_runs_pre_and_post_hooks(
        self, source: Path
    ) -> None:
        write_file(source / "pre-apply.sql", "SELECT pre;\n")
        write_file(source / "post-apply.sql", "SELECT post;\n")
        from classic.migrations.migrations import Hook

        migrator = Migrator(driver="fake")

        pre = Hook("pre-apply", str(source / "pre-apply.sql"))
        post = Hook("post-apply", str(source / "post-apply.sql"))
        m1 = _make_migration("0001.a", transactional=True)

        with migrator:
            backend = _fake_backend(migrator)
            migrator.apply(
                [m1],
                {"pre-apply": [pre], "post-apply": [post]},
            )

        assert backend.applied_list == ["0001.a"]
        cursor_ops = [op for op in backend.oplog if op[0] == "cursor"]
        assert len(cursor_ops) == 3

    def test_apply_fake_skips_sql_and_hooks(
        self, source: Path
    ) -> None:
        from classic.migrations.migrations import Hook

        migrator = Migrator(driver="fake")

        pre = Hook("pre-apply", str(write_file(source / "pre-apply.sql", "SELECT pre;\n")))
        post = Hook("post-apply", str(write_file(source / "post-apply.sql", "SELECT post;\n")))
        m1 = _make_migration("0001.a")

        with migrator:
            backend = _fake_backend(migrator)
            migrator.apply(
                [m1],
                {"pre-apply": [pre], "post-apply": [post]},
                fake=True,
            )

        assert backend.cursor_count == 0
        assert backend.applied_list == ["0001.a"]
        assert backend.events[0][2] == "APPLIED"

    def test_apply_fake_no_pending_event(self) -> None:
        migrator = Migrator(driver="fake")
        m1 = _make_migration("0001.a", transactional=False)

        with migrator:
            backend = _fake_backend(migrator)
            migrator.apply([m1], {}, fake=True)

        assert len(backend.events) == 1
        assert backend.events[0][2] == "APPLIED"

    def test_apply_empty_list_does_nothing(self) -> None:
        migrator = Migrator(driver="fake")
        with migrator:
            backend = _fake_backend(migrator)
            migrator.apply([], {})

        assert backend.events == []
        assert backend.cursor_count == 0


class TestRollback:
    def test_rollback_sequence(self) -> None:
        migrator = Migrator(driver="fake")
        m1 = _make_migration("0001.a", transactional=True)
        m2 = _make_migration("0002.b", transactional=True)

        with migrator:
            backend = _fake_backend(migrator)
            backend.mark("0001.a", "APPLIED")
            backend.mark("0002.b", "APPLIED")
            migrator.rollback([m2, m1], {})

        assert backend.applied_list == []
        assert [event[2] for event in backend.events] == [
            "APPLIED",
            "APPLIED",
            "ROLLED_BACK",
            "ROLLED_BACK",
        ]

    def test_rollback_mark_inside_transaction(self) -> None:
        migrator = Migrator(driver="fake")
        m1 = _make_migration("0001.a", transactional=True)

        with migrator:
            backend = _fake_backend(migrator)
            migrator.rollback([m1], {})

        ops = backend.oplog
        begin_idx = next(i for i, (op, _) in enumerate(ops) if op == "begin")
        stmt_idx = next(i for i, (op, _) in enumerate(ops) if op == "cursor")
        mark_idx = next(i for i, (op, _) in enumerate(ops) if op == "mark")
        commit_idx = next(i for i, (op, _) in enumerate(ops) if op == "commit")
        assert begin_idx < stmt_idx < mark_idx < commit_idx

    def test_rollback_non_transactional_sequence(self) -> None:
        migrator = Migrator(driver="fake")
        m1 = _make_migration("0001.a", transactional=False)

        with migrator:
            backend = _fake_backend(migrator)
            migrator.rollback([m1], {})

        events = backend.events
        assert [e[2] for e in events] == ["PENDING", "ROLLED_BACK"]
        assert events[0][0] == "0001.a"
        assert events[1][0] == "0001.a"

    def test_rollback_runs_pre_and_post_hooks(
        self, source: Path
    ) -> None:
        from classic.migrations.migrations import Hook

        migrator = Migrator(driver="fake")

        pre = Hook("pre-rollback", str(write_file(source / "pre-rollback.sql", "SELECT pre;\n")))
        post = Hook("post-rollback", str(write_file(source / "post-rollback.sql", "SELECT post;\n")))
        m1 = _make_migration("0001.a", transactional=True)

        with migrator:
            backend = _fake_backend(migrator)
            migrator.rollback(
                [m1],
                {"pre-rollback": [pre], "post-rollback": [post]},
            )

        assert backend.applied_list == []
        cursor_ops = [op for op in backend.oplog if op[0] == "cursor"]
        assert len(cursor_ops) == 3

    def test_rollback_fake_skips_sql_and_hooks(
        self, source: Path
    ) -> None:
        from classic.migrations.migrations import Hook

        migrator = Migrator(driver="fake")

        pre = Hook("pre-rollback", str(write_file(source / "pre-rollback.sql", "SELECT pre;\n")))
        post = Hook("post-rollback", str(write_file(source / "post-rollback.sql", "SELECT post;\n")))
        m1 = _make_migration("0001.a")

        with migrator:
            backend = _fake_backend(migrator)
            backend.mark("0001.a", "APPLIED")
            migrator.rollback(
                [m1],
                {"pre-rollback": [pre], "post-rollback": [post]},
                fake=True,
            )

        assert backend.cursor_count == 0
        assert backend.applied_list == []
        assert backend.events[-1][2] == "ROLLED_BACK"

    def test_rollback_fake_no_pending_event(self) -> None:
        migrator = Migrator(driver="fake")
        m1 = _make_migration("0001.a", transactional=False)

        with migrator:
            backend = _fake_backend(migrator)
            migrator.rollback([m1], {}, fake=True)

        assert len(backend.events) == 1
        assert backend.events[0][2] == "ROLLED_BACK"

    def test_rollback_without_rollback_statements_marks_rolled_back(
        self,
    ) -> None:
        migrator = Migrator(driver="fake")
        m1 = _make_migration("0001.a")
        m1.rollback_statements = []

        with migrator:
            backend = _fake_backend(migrator)
            migrator.rollback([m1], {})

        assert backend.applied_list == []
        assert backend.events[-1][2] == "ROLLED_BACK"


class TestLifecycle:
    def test_close_closes_backend(self) -> None:
        migrator = Migrator(driver="fake")
        with migrator:
            backend = _fake_backend(migrator)
        assert backend.closed

    def test_context_manager_closes_backend(self) -> None:
        migrator = Migrator(driver="fake")
        with migrator:
            backend = _fake_backend(migrator)
        assert backend.closed

    def test_context_manager_releases_lock(self) -> None:
        migrator = Migrator(driver="fake")
        with migrator:
            backend = _fake_backend(migrator)
            assert backend.locked
        assert not backend.locked

    def test_empty_driver_raises(self) -> None:
        with pytest.raises(ValueError):
            Migrator(driver="")