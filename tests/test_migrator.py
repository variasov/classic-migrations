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
        with pytest.raises(RuntimeError, match=r"^Migrator is not in a context$"):
            migrator.history()
        with pytest.raises(RuntimeError, match=r"^Migrator is not in a context$"):
            migrator.apply([], {})
        with pytest.raises(RuntimeError, match=r"^Migrator is not in a context$"):
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
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        failing["acquire_lock"] = KeyError("lock")
        failing["close"] = OSError("close")
        migrator = Migrator(driver="fake")
        with (
            caplog.at_level(logging.WARNING, logger="classic.migrations"),
            pytest.raises(KeyError, match="lock"),
            migrator,
        ):
            pytest.fail("body must not run")
        assert migrator._backend is None
        records = [
            r
            for r in caplog.records
            if r.msg == "Failed to close connection after lock failure"
        ]
        assert len(records) >= 1
        assert records[0].exc_info

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
        records = [r for r in caplog.records if r.msg == "Releasing the lock failed"]
        assert len(records) >= 1
        assert records[0].exc_info

    def test_body_error_wins_over_rollback_failure(
        self,
        failing: dict[str, Exception],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        failing["rollback"] = OSError("rollback")
        migrator = Migrator(driver="fake")
        with (
            caplog.at_level(logging.WARNING, logger="classic.migrations"),
            pytest.raises(RuntimeError, match="boom"),
            migrator,
        ):
            raise RuntimeError("boom")
        backend = FakeBackend.instances[0]
        assert ("release_lock", None) in backend.oplog
        assert backend.close_calls == 1
        assert migrator._backend is None
        records = [r for r in caplog.records if r.msg == "Rollback failed"]
        assert len(records) >= 1
        assert records[0].exc_info

    def test_body_error_wins_over_close_failure(
        self,
        failing: dict[str, Exception],
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        failing["close"] = OSError("close")
        migrator = Migrator(driver="fake")
        with (
            caplog.at_level(logging.WARNING, logger="classic.migrations"),
            pytest.raises(RuntimeError, match="boom"),
            migrator,
        ):
            raise RuntimeError("boom")
        assert FakeBackend.instances[0].close_calls == 1
        assert migrator._backend is None
        records = [
            r
            for r in caplog.records
            if r.msg == "Closing the connection failed"
        ]
        assert len(records) >= 1
        assert records[0].exc_info

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
        caplog: pytest.LogCaptureFixture,
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

        with (
            caplog.at_level(logging.WARNING, logger="classic.migrations"),
            migrator,
        ):
            backend = _fake_backend(migrator)
            migrator.apply(
                [m1],
                {"pre-apply": [pre], "post-apply": [post]},
                fake=True,
            )

        assert backend.cursor_count == 0
        assert backend.applied_list == ["0001.a"]
        assert backend.events[0][2] == "APPLIED"
        assert any(
            r.msg == "Fake mode: only history records will be written"
            for r in caplog.records
        )

    def test_apply_fake_no_pending_event(self) -> None:
        migrator = Migrator(driver="fake")
        m1 = _make_migration("0001.a", transactional=False)

        with migrator:
            backend = _fake_backend(migrator)
            migrator.apply([m1], {}, fake=True)

        assert len(backend.events) == 1
        assert backend.events[0][2] == "APPLIED"

    def test_apply_empty_list_does_nothing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        migrator = Migrator(driver="fake")
        with (
            caplog.at_level(logging.INFO, logger="classic.migrations"),
            migrator,
        ):
            backend = _fake_backend(migrator)
            migrator.apply([], {})

        assert backend.events == []
        assert backend.cursor_count == 0
        assert any(r.msg == "Nothing to apply" for r in caplog.records)


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
        caplog: pytest.LogCaptureFixture,
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

        with (
            caplog.at_level(logging.WARNING, logger="classic.migrations"),
            migrator,
        ):
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
        assert any(
            r.msg == "Fake mode: only history records will be written"
            for r in caplog.records
        )

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

    def test_lock_timeout_and_db_args_passed_to_backend(self) -> None:
        migrator = Migrator(
            driver="fake",
            db_host="myhost",
            db_port=5432,
            db_name="mydb",
            db_user="myuser",
            db_pass="mypass",  # noqa: S106
            db_args={"connect_timeout": 5},
            lock_timeout=2.5,
        )
        with migrator:
            backend = _fake_backend(migrator)
            assert backend.db_host == "myhost"
            assert backend.db_port == 5432
            assert backend.db_name == "mydb"
            assert backend.db_user == "myuser"
            assert backend.db_password == "mypass"  # noqa: S105
            assert backend.lock_timeout == 2.5
            assert backend.db_args == {"connect_timeout": 5}

    def test_lock_timeout_and_db_args_default(self) -> None:
        migrator = Migrator(driver="fake")
        with migrator:
            backend = _fake_backend(migrator)
            assert backend.lock_timeout is None
            assert not backend.db_args

    def test_zero_lock_timeout_is_valid(self) -> None:
        migrator = Migrator(driver="fake", lock_timeout=0)
        with migrator:
            assert _fake_backend(migrator).lock_timeout == 0

    def test_negative_lock_timeout_raises(self) -> None:
        with pytest.raises(ValueError, match=r"^lock_timeout must not be negative$"):
            Migrator(driver="fake", lock_timeout=-1)

    def test_empty_driver_raises(self) -> None:
        with pytest.raises(ValueError, match=r"^driver must not be empty$"):
            Migrator(driver="")


@pytest.mark.parametrize("value", [-1.0, float("nan")])
def test_invalid_lock_timeout_raises(value: float) -> None:
    with pytest.raises(ValueError, match="lock_timeout must not be negative"):
        Migrator(driver="fake", lock_timeout=value)


class TestConstructorAttributes:
    """Verify that Migrator.__init__ stores attributes correctly."""

    def test_migration_schema_stored(self) -> None:
        migrator = Migrator(driver="fake", migration_schema="myschema")
        assert migrator._migration_schema == "myschema"

    def test_versions_schema_stored(self) -> None:
        migrator = Migrator(driver="fake", versions_schema="vers")
        assert migrator._versions_schema == "vers"

    def test_migration_table_default(self) -> None:
        migrator = Migrator(driver="fake")
        assert migrator._migration_table == "migrations"

    def test_custom_migration_table(self) -> None:
        migrator = Migrator(driver="fake", migration_table="my_migrations")
        assert migrator._migration_table == "my_migrations"


class TestLogMessages:
    """Verify exact log message templates emitted by Migrator."""

    def test_enter_debug_logs(self, caplog: pytest.LogCaptureFixture) -> None:
        migrator = Migrator(driver="fake", db_name="testdb")
        with caplog.at_level(logging.DEBUG, logger="classic.migrations"), migrator:
            pass
        records = caplog.records

        connect = [r for r in records if r.msg == "Connecting to %s database %s"]
        assert len(connect) >= 1
        assert connect[0].getMessage() == "Connecting to fake database testdb"

        lock_acq = [r for r in records if r.msg == "Advisory lock acquired via %s"]
        assert len(lock_acq) >= 1
        assert "FakeBackend" in lock_acq[0].getMessage()

    def test_history_debug_logs(self, caplog: pytest.LogCaptureFixture) -> None:
        migrator = Migrator(driver="fake")
        with caplog.at_level(logging.DEBUG, logger="classic.migrations"), migrator:
            migrator.history()
        records = caplog.records
        assert any(r.msg == "Reading migration history from table %r" for r in records)
        assert any(r.msg == "Read %d history event(s)" for r in records)

    def test_apply_info_logs(self, caplog: pytest.LogCaptureFixture) -> None:
        migrator = Migrator(driver="fake")
        m1 = _make_migration("0001.a", transactional=True)
        with caplog.at_level(logging.INFO, logger="classic.migrations"), migrator:
            migrator.apply([m1], {})
        records = caplog.records
        apply_count = [r for r in records if r.msg == "Applying %d migration(s)"]
        assert len(apply_count) >= 1
        assert apply_count[0].getMessage() == "Applying 1 migration(s)"
        apply_id = [r for r in records if r.msg == "Applying %s"]
        assert len(apply_id) >= 1
        assert apply_id[0].getMessage() == "Applying 0001.a"

    def test_apply_nothing_log(self, caplog: pytest.LogCaptureFixture) -> None:
        migrator = Migrator(driver="fake")
        with caplog.at_level(logging.INFO, logger="classic.migrations"), migrator:
            migrator.apply([], {})
        assert any(r.msg == "Nothing to apply" for r in caplog.records)

    def test_apply_fake_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        migrator = Migrator(driver="fake")
        m1 = _make_migration("0001.a")
        with caplog.at_level(logging.WARNING, logger="classic.migrations"), migrator:
            migrator.apply([m1], {}, fake=True)
        assert any(
            r.msg == "Fake mode: only history records will be written"
            for r in caplog.records
        )

    def test_apply_one_debug_log(self, caplog: pytest.LogCaptureFixture) -> None:
        migrator = Migrator(driver="fake")
        m1 = _make_migration("0001.a", transactional=False)
        with caplog.at_level(logging.DEBUG, logger="classic.migrations"), migrator:
            migrator.apply([m1], {})
        records = caplog.records
        apply_one = [
            r
            for r in records
            if r.msg == "Applying %s: %d statement(s), transactional=%s"
        ]
        assert len(apply_one) >= 1
        assert "0001.a" in apply_one[0].getMessage()
        assert "transactional=False" in apply_one[0].getMessage()

    def test_rollback_info_logs(self, caplog: pytest.LogCaptureFixture) -> None:
        migrator = Migrator(driver="fake")
        m1 = _make_migration("0001.a", transactional=True)
        with caplog.at_level(logging.INFO, logger="classic.migrations"), migrator:
            migrator.rollback([m1], {})
        records = caplog.records
        rb_count = [r for r in records if r.msg == "Rolling back %d migration(s)"]
        assert len(rb_count) >= 1
        assert rb_count[0].getMessage() == "Rolling back 1 migration(s)"
        rb_id = [r for r in records if r.msg == "Rolling back %s"]
        assert len(rb_id) >= 1
        assert rb_id[0].getMessage() == "Rolling back 0001.a"

    def test_rollback_nothing_log(self, caplog: pytest.LogCaptureFixture) -> None:
        migrator = Migrator(driver="fake")
        with caplog.at_level(logging.INFO, logger="classic.migrations"), migrator:
            migrator.rollback([], {})
        assert any(r.msg == "Nothing to rollback" for r in caplog.records)

    def test_rollback_fake_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        migrator = Migrator(driver="fake")
        m1 = _make_migration("0001.a")
        with caplog.at_level(logging.WARNING, logger="classic.migrations"), migrator:
            migrator.rollback([m1], {}, fake=True)
        assert any(
            r.msg == "Fake mode: only history records will be written"
            for r in caplog.records
        )

    def test_rollback_one_debug_log(self, caplog: pytest.LogCaptureFixture) -> None:
        migrator = Migrator(driver="fake")
        m1 = _make_migration("0001.a", transactional=False)
        with caplog.at_level(logging.DEBUG, logger="classic.migrations"), migrator:
            migrator.rollback([m1], {})
        records = caplog.records
        rb_one = [
            r
            for r in records
            if r.msg == "Rolling back %s: %d statement(s), transactional=%s"
        ]
        assert len(rb_one) >= 1
        assert "0001.a" in rb_one[0].getMessage()
        assert "transactional=False" in rb_one[0].getMessage()

    def test_execute_statement_debug_log(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        migrator = Migrator(driver="fake")
        m1 = _make_migration("0001.a", transactional=True)
        with caplog.at_level(logging.DEBUG, logger="classic.migrations"), migrator:
            migrator.apply([m1], {})
        records = caplog.records
        exec_sql = [r for r in records if r.msg == "Executing SQL: %s"]
        assert len(exec_sql) >= 1
        assert exec_sql[0].getMessage() == "Executing SQL: -- apply 0001.a"

    def test_rollback_no_rollback_warning_msg(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        migrator = Migrator(driver="fake")
        m1 = _make_migration("0001.a")
        m1.rollback_statements = []
        with caplog.at_level(logging.WARNING, logger="classic.migrations"), migrator:
            migrator.rollback([m1], {})
        records = caplog.records
        assert any(
            r.msg
            == "Migration %s has no rollback file; only marking it as rolled back"
            for r in records
        )

    def test_hook_debug_logs(
        self, source: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        write_file(source / "pre-apply.sql", "SELECT 1;\n")
        write_file(source / "post-apply.sql", "SELECT 2;\n")

        migrator = Migrator(driver="fake")
        pre = Hook("pre-apply", str(source / "pre-apply.sql"))
        post = Hook("post-apply", str(source / "post-apply.sql"))
        m1 = _make_migration("0001.a", transactional=True)

        with caplog.at_level(logging.DEBUG, logger="classic.migrations"), migrator:
            migrator.apply([m1], {"pre-apply": [pre], "post-apply": [post]})

        records = caplog.records
        run_hook = [
            r
            for r in records
            if r.msg == "Running hook %s (%s): %d statement(s)"
        ]
        assert len(run_hook) >= 2
        assert "pre-apply" in run_hook[0].getMessage()
        assert "post-apply" in run_hook[1].getMessage()

    def test_hook_skips_empty(
        self, source: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        write_file(source / "pre-apply.sql", "")
        migrator = Migrator(driver="fake")
        pre = Hook("pre-apply", str(source / "pre-apply.sql"))
        m1 = _make_migration("0001.a", transactional=True)

        with caplog.at_level(logging.DEBUG, logger="classic.migrations"), migrator:
            migrator.apply([m1], {"pre-apply": [pre]})

        records = caplog.records
        assert any(r.msg == "Skipping empty hook %s (%s)" for r in records)

    def test_exit_debug_logs_on_error(
        self, failing: dict[str, Exception], caplog: pytest.LogCaptureFixture
    ) -> None:
        """On body error, __exit__ logs a rolling-back message at debug level."""
        failing["release_lock"] = OSError("release")
        migrator = Migrator(driver="fake")
        with (
            caplog.at_level(logging.DEBUG, logger="classic.migrations"),
            pytest.raises(RuntimeError, match="boom"),
            migrator,
        ):
            raise RuntimeError("boom")
        records = caplog.records
        rb_tx = [
            r
            for r in records
            if r.msg == "Rolling back transaction after error: %s"
        ]
        assert len(rb_tx) >= 1
        assert "boom" in rb_tx[0].getMessage()


class TestExecutedStatements:
    """Verify that SQL statements are correctly forwarded to the backend."""

    def test_apply_executes_statements(self) -> None:
        migrator = Migrator(driver="fake")
        statements = ["SELECT 1", "SELECT 2"]
        m1 = _make_migration("0001.a", statements=statements, transactional=True)
        with migrator:
            backend = _fake_backend(migrator)
            migrator.apply([m1], {})
        # The first cursor from begin, last from commit - filter for actual SQL
        cursor_ops = [op for op in backend.oplog if op[0] == "cursor"]
        # We expect at least as many cursors as statements
        assert len(cursor_ops) >= len(statements)

    def test_non_transactional_apply_executes_statements(self) -> None:
        migrator = Migrator(driver="fake")
        statements = ["SELECT 1", "SELECT 2"]
        m1 = _make_migration("0001.a", statements=statements, transactional=False)
        with migrator:
            backend = _fake_backend(migrator)
            migrator.apply([m1], {})
        cursor_ops = [op for op in backend.oplog if op[0] == "cursor"]
        assert len(cursor_ops) >= len(statements)

    def test_rollback_executes_statements(self) -> None:
        migrator = Migrator(driver="fake")
        rollback_stmts = ["DROP TABLE t", "DELETE FROM log"]
        m1 = _make_migration(
            "0001.a", statements=["CREATE TABLE t (x int)"],
            rollback_statements=rollback_stmts,
            transactional=True,
        )
        with migrator:
            backend = _fake_backend(migrator)
            migrator.rollback([m1], {})
        cursor_ops = [op for op in backend.oplog if op[0] == "cursor"]
        assert len(cursor_ops) >= len(rollback_stmts)

    def test_rollback_marks_event_with_migration_id(self) -> None:
        """Verify backend.mark receives the correct migration_id."""
        migrator = Migrator(driver="fake")
        m1 = _make_migration("0001.a", transactional=True)
        with migrator:
            backend = _fake_backend(migrator)
            migrator.rollback([m1], {})
        # Last event should be ROLLED_BACK for the correct migration
        assert backend.events[-1][0] == "0001.a"
        assert backend.events[-1][2] == "ROLLED_BACK"

    def test_rollback_no_stmts_mark_id(self) -> None:
        """rollback without rollback statements still marks with correct id."""
        migrator = Migrator(driver="fake")
        m1 = _make_migration("0001.a")
        m1.rollback_statements = []
        with migrator:
            backend = _fake_backend(migrator)
            migrator.rollback([m1], {})
        assert backend.events[-1][0] == "0001.a"
        assert backend.events[-1][2] == "ROLLED_BACK"


class TestTargetedMutations:
    """Targeted tests for specific mutation scenarios."""

    def test_rollback_exec_sql_logged(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify actual SQL logged during non-transactional rollback.

        Catches _execute_statement(None) mutation.
        """
        migrator = Migrator(driver="fake")
        m1 = _make_migration("0001.a", transactional=False)
        with caplog.at_level(logging.DEBUG, logger="classic.migrations"), migrator:
            migrator.rollback([m1], {})
        exec_records = [
            r for r in caplog.records if r.msg == "Executing SQL: %s"
        ]
        assert any(
            r.getMessage() == "Executing SQL: -- rollback 0001.a"
            for r in exec_records
        )

    def test_rollback_for_rollback_uses_rollback_tx(self) -> None:
        """Verify that _rollback_one uses for_rollback=True.

        When transactional=True and rollback_transactional=False,
        rollback must NOT be wrapped in a transaction.
        Catches for_rollback=True -> for_rollback=False mutation.
        """
        migrator = Migrator(driver="fake")
        m1 = _make_migration(
            "0001.a",
            transactional=True,
            rollback_transactional=False,
        )
        with migrator:
            backend = _fake_backend(migrator)
            migrator.rollback([m1], {})
        assert ("begin", None) not in backend.oplog
        for event in backend.events:
            assert event[0] == "0001.a"
        assert backend.events[-1][2] == "ROLLED_BACK"


class TestConstructorPassthrough:
    """Verify that all args from Migrator.__init__ reach the Backend constructor."""

    def test_migration_table_passed_to_backend(self) -> None:
        migrator = Migrator(driver="fake", migration_table="my_migrations")
        with migrator:
            backend = _fake_backend(migrator)
            assert backend.migration_table == "my_migrations"

    def test_migration_table_default_passed(self) -> None:
        migrator = Migrator(driver="fake")
        with migrator:
            backend = _fake_backend(migrator)
            assert backend.migration_table == "migrations"

    def test_migration_schema_passed_to_backend(self) -> None:
        migrator = Migrator(driver="fake", migration_schema="myschema")
        with migrator:
            backend = _fake_backend(migrator)
            assert backend.migration_schema == "myschema"

    def test_migration_schema_default_none(self) -> None:
        migrator = Migrator(driver="fake")
        with migrator:
            backend = _fake_backend(migrator)
            assert backend.migration_schema is None

    def test_versions_schema_passed_to_backend(self) -> None:
        migrator = Migrator(driver="fake", versions_schema="vers")
        with migrator:
            backend = _fake_backend(migrator)
            assert backend.versions_schema == "vers"

    def test_versions_schema_default_none(self) -> None:
        migrator = Migrator(driver="fake")
        with migrator:
            backend = _fake_backend(migrator)
            assert backend.versions_schema is None


class TestHistoryLogMutationSurvival:
    """Targeted tests for history() log message argument mutants.

    mutmut_3: backend.migration_table -> None in %r
    mutmut_5: backend.migration_table arg removed entirely
    mutmut_13: len(rows) arg removed from second debug log
    """

    def test_history_log_table_name_rendered(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify the actual table name appears in the log message.

        Kills mutmut_3 (None instead of backend.migration_table).
        """
        migrator = Migrator(driver="fake")
        with caplog.at_level(logging.DEBUG, logger="classic.migrations"), migrator:
            migrator.history()
        records = caplog.records
        table_records = [
            r for r in records
            if r.msg == "Reading migration history from table %r"
        ]
        assert len(table_records) >= 1
        msg = table_records[0].getMessage()
        # With %r the value is repr'd: 'migrations' or None
        assert "'migrations'" in msg

    def test_history_log_event_count_rendered(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify the event count appears in the log message.

        Kills mutmut_13 (len(rows) arg removed).
        """
        migrator = Migrator(driver="fake")
        with caplog.at_level(logging.DEBUG, logger="classic.migrations"), migrator:
            migrator.history()
        records = caplog.records
        count_records = [
            r for r in records if r.msg == "Read %d history event(s)"
        ]
        assert len(count_records) >= 1
        msg = count_records[0].getMessage()
        assert "0" in msg


class TestRollbackOneMutationSurvival:
    """Targeted tests for _rollback_one() debug log argument mutants.

    mutmut_26: for_rollback=True -> for_rollback=None
    mutmut_28: for_rollback=True -> args dropped (default False)
    mutmut_29: for_rollback=True -> for_rollback=False
    """

    def test_rollback_log_shows_rollback_transactional_when_set(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify the log shows transactional=True when rollback_transactional=True.

        When a migration has rollback_transactional=True and transactional=False,
        the debug log must reflect the rollback-specific setting.
        Any mutation that changes for_rollback would cause the log to show
        transactional=False instead.
        """
        migrator = Migrator(driver="fake")
        m1 = _make_migration(
            "0001.a",
            transactional=False,
            rollback_transactional=True,
        )
        with caplog.at_level(logging.DEBUG, logger="classic.migrations"), migrator:
            migrator.rollback([m1], {})

        records = caplog.records
        rb_one = [
            r
            for r in records
            if r.msg == "Rolling back %s: %d statement(s), transactional=%s"
        ]
        assert len(rb_one) >= 1
        msg = rb_one[0].getMessage()
        assert "transactional=True" in msg


class TestHookMutationSurvival:
    """Targeted tests for _run_hooks() argument and control flow mutants.

    mutmut_4:  hook.name -> None in "Skipping empty hook" message
    mutmut_5:  hook.path -> None in "Skipping empty hook" message
    mutmut_12: continue -> break (second hook skipped after empty first)
    mutmut_14: hook.name -> None in "Running hook" message
    mutmut_15: hook.path -> None in "Running hook" message
    """

    def test_skip_empty_hook_log_has_name_and_path(
        self, source: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify "Skipping empty hook" log contains actual name and path.

        Kills mutmut_4 (hook.name->None) and mutmut_5 (hook.path->None).
        """
        write_file(source / "pre-apply.sql", "")
        migrator = Migrator(driver="fake")
        pre = Hook("pre-apply", str(source / "pre-apply.sql"))
        m1 = _make_migration("0001.a", transactional=True)

        with caplog.at_level(logging.DEBUG, logger="classic.migrations"), migrator:
            migrator.apply([m1], {"pre-apply": [pre]})

        records = caplog.records
        skip_records = [
            r for r in records if r.msg == "Skipping empty hook %s (%s)"
        ]
        assert len(skip_records) >= 1
        msg = skip_records[0].getMessage()
        assert "pre-apply" in msg
        assert str(source / "pre-apply.sql") in msg

    def test_hook_after_empty_still_runs(
        self, source: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify that after an empty hook, the next hook still runs.

        Kills mutmut_12 (continue -> break).
        """
        write_file(source / "empty-hook.sql", "")
        write_file(source / "real-hook.sql", "SELECT 1;\n")
        migrator = Migrator(driver="fake")
        empty_hook = Hook("pre-apply", str(source / "empty-hook.sql"))
        real_hook = Hook("pre-apply-2", str(source / "real-hook.sql"))
        m1 = _make_migration("0001.a", transactional=True)

        with caplog.at_level(logging.DEBUG, logger="classic.migrations"), migrator:
            migrator.apply(
                [m1],
                {"pre-apply": [empty_hook, real_hook]},
            )

        records = caplog.records
        skip_records = [
            r for r in records if r.msg == "Skipping empty hook %s (%s)"
        ]
        assert len(skip_records) >= 1
        run_records = [
            r for r in records
            if r.msg == "Running hook %s (%s): %d statement(s)"
        ]
        assert len(run_records) >= 1
        # The second hook's "Running hook" message must contain its name
        assert "pre-apply-2" in run_records[0].getMessage()

    def test_running_hook_log_has_name_and_path(
        self, source: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Verify "Running hook" log contains actual name and path.

        Kills mutmut_14 (hook.name->None) and mutmut_15 (hook.path->None).
        """
        write_file(source / "pre-apply.sql", "SELECT 1;\n")
        migrator = Migrator(driver="fake")
        pre = Hook("pre-apply", str(source / "pre-apply.sql"))
        m1 = _make_migration("0001.a", transactional=True)

        with caplog.at_level(logging.DEBUG, logger="classic.migrations"), migrator:
            migrator.apply([m1], {"pre-apply": [pre]})

        records = caplog.records
        run_records = [
            r for r in records
            if r.msg == "Running hook %s (%s): %d statement(s)"
        ]
        assert len(run_records) >= 1
        msg = run_records[0].getMessage()
        assert "pre-apply" in msg
        assert str(source / "pre-apply.sql") in msg
