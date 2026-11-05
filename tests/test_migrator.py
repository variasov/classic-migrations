import logging
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import pytest

from classic.migrations.migrations import Hook, Migration
from classic.migrations.migrator import Migrator
from tests.backends.fake import FakeBackend


def write_file(path: Path, content: str) -> Path:
    path.write_text(content, encoding="utf-8")
    return path


@pytest.fixture
def failing() -> Iterator[dict[str, Exception]]:
    """Operations that newly created FakeBackends must fail on."""
    FakeBackend.instances.clear()
    FakeBackend.default_fail_on.clear()
    yield FakeBackend.default_fail_on
    FakeBackend.default_fail_on.clear()
    FakeBackend.instances.clear()


def _fake_backend(migrator: Migrator) -> FakeBackend:
    return cast("FakeBackend", migrator.backend)


def _make_migration(
    migration_id: str,
    statements: list[str] | None = None,
    transactional: bool | None = None,
    rollback_statements: list[str] | None = None,
    rollback_transactional: bool | None = None,
) -> Migration:
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
        assert ("release_lock", None) in backend.oplog

    def test_error_rolls_back_then_releases_lock(self) -> None:
        migrator = Migrator(driver="fake")
        backend: FakeBackend | None = None
        with pytest.raises(RuntimeError, match="boom"), migrator:
            backend = _fake_backend(migrator)
            raise RuntimeError("boom")
        assert backend is not None
        assert not backend.locked
        ops = backend.oplog
        rollback_idx = ops.index(("rollback", None))
        release_idx = ops.index(("release_lock", None))
        assert rollback_idx < release_idx

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


class TestCleanup:
    def test_acquire_lock_failure_closes_and_resets(
        self,
        failing: dict[str, Exception],
    ) -> None:
        failing["acquire_lock"] = KeyError("lock")
        migrator = Migrator(driver="fake")
        with pytest.raises(KeyError, match="lock"), migrator:
            pytest.fail("body must not run")
        assert FakeBackend.instances[0].close_calls == 1
        assert migrator._backend is None
        with pytest.raises(RuntimeError):
            _ = migrator.backend

        failing.clear()
        with migrator:
            assert _fake_backend(migrator).locked
        assert migrator._backend is None

    def test_acquire_lock_failure_survives_close_failure(
        self,
        failing: dict[str, Exception],
    ) -> None:
        failing["acquire_lock"] = KeyError("lock")
        failing["close"] = OSError("close")
        migrator = Migrator(driver="fake")
        with pytest.raises(KeyError, match="lock"), migrator:
            pytest.fail("body must not run")
        assert migrator._backend is None

    def test_constructor_failure_leaves_no_backend(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def boom(self: FakeBackend, **kwargs: object) -> None:
            raise ValueError("ctor")

        monkeypatch.setattr(FakeBackend, "__init__", boom)
        migrator = Migrator(driver="fake")
        with pytest.raises(ValueError, match="ctor"), migrator:
            pytest.fail("body must not run")
        assert migrator._backend is None

    def test_body_error_wins_over_release_lock_failure(
        self,
        failing: dict[str, Exception],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        failing["release_lock"] = OSError("release")
        migrator = Migrator(driver="fake")
        with (
            caplog.at_level(logging.WARNING, logger="classic.migrations"),
            pytest.raises(RuntimeError, match="boom"),
            migrator,
        ):
            raise RuntimeError("boom")
        backend = FakeBackend.instances[0]
        assert backend.close_calls == 1
        assert migrator._backend is None
        assert "Releasing the lock failed" in caplog.text

    def test_body_error_wins_over_rollback_failure(
        self,
        failing: dict[str, Exception],
    ) -> None:
        failing["rollback"] = OSError("rollback")
        migrator = Migrator(driver="fake")
        with pytest.raises(RuntimeError, match="boom"), migrator:
            raise RuntimeError("boom")
        backend = FakeBackend.instances[0]
        assert ("release_lock", None) in backend.oplog
        assert backend.close_calls == 1
        assert migrator._backend is None

    def test_body_error_wins_over_close_failure(
        self,
        failing: dict[str, Exception],
    ) -> None:
        failing["close"] = OSError("close")
        migrator = Migrator(driver="fake")
        with pytest.raises(RuntimeError, match="boom"), migrator:
            raise RuntimeError("boom")
        assert FakeBackend.instances[0].close_calls == 1
        assert migrator._backend is None

    def test_release_lock_failure_raised_without_body_error(
        self,
        failing: dict[str, Exception],
    ) -> None:
        failing["release_lock"] = OSError("release")
        migrator = Migrator(driver="fake")
        with pytest.raises(OSError, match="release"), migrator:
            pass
        assert FakeBackend.instances[0].close_calls == 1
        assert migrator._backend is None

    def test_close_failure_raised_without_body_error(
        self,
        failing: dict[str, Exception],
    ) -> None:
        failing["close"] = OSError("close")
        migrator = Migrator(driver="fake")
        with pytest.raises(OSError, match="close"), migrator:
            pass
        assert migrator._backend is None

    def test_reusable_after_failed_body(self) -> None:
        migrator = Migrator(driver="fake")
        with pytest.raises(RuntimeError, match="boom"), migrator:
            raise RuntimeError("boom")
        with migrator:
            assert migrator.history() == []
        with migrator:
            assert migrator.history() == []


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

    def test_history_ensures_table_once(self) -> None:
        migrator = Migrator(driver="fake")
        with migrator:
            backend = _fake_backend(migrator)
            migrator.history()
            assert backend.ensure_calls == 1


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
        self,
        source: Path,
    ) -> None:
        write_file(source / "pre-apply.sql", "SELECT pre;\n")
        write_file(source / "post-apply.sql", "SELECT post;\n")

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
        self,
        source: Path,
    ) -> None:

        migrator = Migrator(driver="fake")

        pre = Hook(
            "pre-apply",
            str(write_file(source / "pre-apply.sql", "SELECT pre;\n")),
        )
        post = Hook(
            "post-apply",
            str(write_file(source / "post-apply.sql", "SELECT post;\n")),
        )
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
        self,
        source: Path,
    ) -> None:

        migrator = Migrator(driver="fake")

        pre = Hook(
            "pre-rollback",
            str(write_file(source / "pre-rollback.sql", "SELECT pre;\n")),
        )
        post = Hook(
            "post-rollback",
            str(write_file(source / "post-rollback.sql", "SELECT post;\n")),
        )
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
        self,
        source: Path,
    ) -> None:

        migrator = Migrator(driver="fake")

        pre = Hook(
            "pre-rollback",
            str(write_file(source / "pre-rollback.sql", "SELECT pre;\n")),
        )
        post = Hook(
            "post-rollback",
            str(write_file(source / "post-rollback.sql", "SELECT post;\n")),
        )
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

    def test_rollback_without_rollback_statements_warns(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        migrator = Migrator(driver="fake")
        m1 = _make_migration("0001.a")
        m1.rollback_statements = []

        with caplog.at_level(logging.DEBUG, logger="classic.migrations"), migrator:
            migrator.rollback([m1], {})

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        assert [r.getMessage() for r in warnings] == [
            "Migration 0001.a has no rollback file; only marking it as rolled back",
        ]


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
        assert ("release_lock", None) in backend.oplog

    def test_empty_driver_raises(self) -> None:
        with pytest.raises(ValueError):
            Migrator(driver="")
