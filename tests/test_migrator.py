from pathlib import Path
from typing import Any, cast

import pytest
from classic.migrations.backends.base import Lock
from classic.migrations.migrator import Migrator

from tests.backends.fake import FakeBackend

_LOCK = Lock().__enter__()


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
    def test_lock_returns_context_manager(self) -> None:
        migrator = Migrator(driver="fake")
        backend = _fake_backend(migrator)
        with migrator.lock() as lock:
            assert lock.is_acquired
            assert backend.locked

    def test_lock_unacquired_outside_context(self) -> None:
        migrator = Migrator(driver="fake")
        with migrator.lock() as lock:
            assert lock.is_acquired
        assert not lock.is_acquired

    def test_lock_object_passed_to_methods(self) -> None:
        migrator = Migrator(driver="fake")
        with migrator.lock() as lock:
            history = migrator.history(lock)
            assert history == []
            migrator.apply(lock, {}, [])
            migrator.rollback(lock, {}, [])

    def test_asserts_if_lock_not_acquired(self) -> None:
        migrator = Migrator(driver="fake")
        with pytest.raises(AssertionError):
            migrator.history(Lock())

    def test_asserts_if_lock_expired(self) -> None:
        migrator = Migrator(driver="fake")
        with migrator.lock() as lock:
            pass
        with pytest.raises(AssertionError):
            migrator.apply(lock, {}, [])


class TestHistory:
    def test_history_returns_event_log(self) -> None:
        migrator = Migrator(driver="fake")
        backend = _fake_backend(migrator)
        backend.mark("0001.a", "APPLIED")
        backend.mark("0001.a", "ROLLED_BACK")

        history = migrator.history(_LOCK)

        assert len(history) == 2
        assert history[0][0] == "0001.a"
        assert history[0][2] == "APPLIED"
        assert history[1][2] == "ROLLED_BACK"

    def test_history_ensures_table(self) -> None:
        migrator = Migrator(driver="fake")
        backend = _fake_backend(migrator)
        assert not backend.migration_table_ready
        migrator.history(_LOCK)
        assert backend.migration_table_ready


class TestApply:
    def test_apply_sequence(self) -> None:
        migrator = Migrator(driver="fake")
        backend = _fake_backend(migrator)
        m1 = _make_migration("0001.a", transactional=True)
        m2 = _make_migration("0002.b", transactional=True)

        migrator.apply(_LOCK, {}, [m1, m2])

        assert backend.applied_list == ["0001.a", "0002.b"]

    def test_apply_mark_inside_transaction(self) -> None:
        migrator = Migrator(driver="fake")
        backend = _fake_backend(migrator)
        m1 = _make_migration("0001.a", transactional=True)

        migrator.apply(_LOCK, {}, [m1])

        ops = backend.oplog
        begin_idx = next(i for i, (op, _) in enumerate(ops) if op == "begin")
        stmt_idx = next(i for i, (op, _) in enumerate(ops) if op == "cursor")
        mark_idx = next(i for i, (op, _) in enumerate(ops) if op == "mark")
        commit_idx = next(i for i, (op, _) in enumerate(ops) if op == "commit")
        assert begin_idx < stmt_idx < mark_idx < commit_idx

    def test_apply_non_transactional_sequence(self) -> None:
        migrator = Migrator(driver="fake")
        backend = _fake_backend(migrator)
        m1 = _make_migration("0001.a", transactional=False)

        migrator.apply(_LOCK, {}, [m1])

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
        backend = _fake_backend(migrator)

        pre = Hook("pre-apply", str(source / "pre-apply.sql"))
        post = Hook("post-apply", str(source / "post-apply.sql"))
        m1 = _make_migration("0001.a", transactional=True)

        migrator.apply(
            _LOCK,
            {"pre-apply": [pre], "post-apply": [post]},
            [m1],
        )

        assert backend.applied_list == ["0001.a"]
        cursor_ops = [op for op in backend.oplog if op[0] == "cursor"]
        assert len(cursor_ops) == 3

    def test_apply_fake_skips_sql_and_hooks(
        self, source: Path
    ) -> None:
        from classic.migrations.migrations import Hook

        migrator = Migrator(driver="fake")
        backend = _fake_backend(migrator)

        pre = Hook("pre-apply", str(write_file(source / "pre-apply.sql", "SELECT pre;\n")))
        post = Hook("post-apply", str(write_file(source / "post-apply.sql", "SELECT post;\n")))
        m1 = _make_migration("0001.a")

        migrator.apply(
            _LOCK,
            {"pre-apply": [pre], "post-apply": [post]},
            [m1],
            fake=True,
        )

        assert backend.cursor_count == 0
        assert backend.applied_list == ["0001.a"]
        assert backend.events[0][2] == "APPLIED"

    def test_apply_fake_no_pending_event(self) -> None:
        migrator = Migrator(driver="fake")
        backend = _fake_backend(migrator)
        m1 = _make_migration("0001.a", transactional=False)

        migrator.apply(_LOCK, {}, [m1], fake=True)

        assert len(backend.events) == 1
        assert backend.events[0][2] == "APPLIED"

    def test_apply_empty_list_does_nothing(self) -> None:
        migrator = Migrator(driver="fake")
        backend = _fake_backend(migrator)
        migrator.apply(_LOCK, {}, [])

        assert backend.events == []
        assert backend.cursor_count == 0


class TestRollback:
    def test_rollback_sequence(self) -> None:
        migrator = Migrator(driver="fake")
        backend = _fake_backend(migrator)
        backend.mark("0001.a", "APPLIED")
        backend.mark("0002.b", "APPLIED")
        m1 = _make_migration("0001.a", transactional=True)
        m2 = _make_migration("0002.b", transactional=True)

        migrator.rollback(_LOCK, {}, [m2, m1])

        assert backend.applied_list == []
        assert [event[2] for event in backend.events] == [
            "APPLIED",
            "APPLIED",
            "ROLLED_BACK",
            "ROLLED_BACK",
        ]

    def test_rollback_mark_inside_transaction(self) -> None:
        migrator = Migrator(driver="fake")
        backend = _fake_backend(migrator)
        m1 = _make_migration("0001.a", transactional=True)

        migrator.rollback(_LOCK, {}, [m1])

        ops = backend.oplog
        begin_idx = next(i for i, (op, _) in enumerate(ops) if op == "begin")
        stmt_idx = next(i for i, (op, _) in enumerate(ops) if op == "cursor")
        mark_idx = next(i for i, (op, _) in enumerate(ops) if op == "mark")
        commit_idx = next(i for i, (op, _) in enumerate(ops) if op == "commit")
        assert begin_idx < stmt_idx < mark_idx < commit_idx

    def test_rollback_non_transactional_sequence(self) -> None:
        migrator = Migrator(driver="fake")
        backend = _fake_backend(migrator)
        m1 = _make_migration("0001.a", transactional=False)

        migrator.rollback(_LOCK, {}, [m1])

        events = backend.events
        assert [e[2] for e in events] == ["PENDING", "ROLLED_BACK"]
        assert events[0][0] == "0001.a"
        assert events[1][0] == "0001.a"

    def test_rollback_runs_pre_and_post_hooks(
        self, source: Path
    ) -> None:
        from classic.migrations.migrations import Hook

        migrator = Migrator(driver="fake")
        backend = _fake_backend(migrator)

        pre = Hook("pre-rollback", str(write_file(source / "pre-rollback.sql", "SELECT pre;\n")))
        post = Hook("post-rollback", str(write_file(source / "post-rollback.sql", "SELECT post;\n")))
        m1 = _make_migration("0001.a", transactional=True)

        migrator.rollback(
            _LOCK,
            {"pre-rollback": [pre], "post-rollback": [post]},
            [m1],
        )

        assert backend.applied_list == []
        cursor_ops = [op for op in backend.oplog if op[0] == "cursor"]
        assert len(cursor_ops) == 3

    def test_rollback_fake_skips_sql_and_hooks(
        self, source: Path
    ) -> None:
        from classic.migrations.migrations import Hook

        migrator = Migrator(driver="fake")
        backend = _fake_backend(migrator)
        backend.mark("0001.a", "APPLIED")

        pre = Hook("pre-rollback", str(write_file(source / "pre-rollback.sql", "SELECT pre;\n")))
        post = Hook("post-rollback", str(write_file(source / "post-rollback.sql", "SELECT post;\n")))
        m1 = _make_migration("0001.a")

        migrator.rollback(
            _LOCK,
            {"pre-rollback": [pre], "post-rollback": [post]},
            [m1],
            fake=True,
        )

        assert backend.cursor_count == 0
        assert backend.applied_list == []
        assert backend.events[-1][2] == "ROLLED_BACK"

    def test_rollback_fake_no_pending_event(self) -> None:
        migrator = Migrator(driver="fake")
        backend = _fake_backend(migrator)
        m1 = _make_migration("0001.a", transactional=False)

        migrator.rollback(_LOCK, {}, [m1], fake=True)

        assert len(backend.events) == 1
        assert backend.events[0][2] == "ROLLED_BACK"

    def test_rollback_without_rollback_statements_marks_rolled_back(
        self,
    ) -> None:
        migrator = Migrator(driver="fake")
        backend = _fake_backend(migrator)
        m1 = _make_migration("0001.a")
        m1.rollback_statements = []

        migrator.rollback(_LOCK, {}, [m1])

        assert backend.applied_list == []
        assert backend.events[-1][2] == "ROLLED_BACK"


class TestLifecycle:
    def test_close_closes_backend(self) -> None:
        migrator = Migrator(driver="fake")
        backend = _fake_backend(migrator)
        migrator.close()
        assert backend.closed

    def test_context_manager_closes_backend(self) -> None:
        migrator = Migrator(driver="fake")
        backend = _fake_backend(migrator)
        with migrator:
            migrator.history(_LOCK)
        assert backend.closed

    def test_empty_driver_raises(self) -> None:
        with pytest.raises(ValueError):
            Migrator(driver="")