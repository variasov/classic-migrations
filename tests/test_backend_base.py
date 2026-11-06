"""Tests for classic.migrations.backends.base — covers all surviving mutants."""

from __future__ import annotations

import hashlib
import logging
from typing import Any, ClassVar

import pytest

from classic.migrations.backends.base import (
    Backend,
    TransactionManager,
    lock_hash,
)
from classic.migrations.exceptions import MigrationLockError


class _TestDatabaseError(Exception):
    pass


class _TestDriver:
    __name__ = "test_backend_base"
    DatabaseError = _TestDatabaseError
    paramstyle = "qmark"


class _TestCursor:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def execute(self, sql: str, _params: Any = None) -> None:
        self.executed.append(sql)

    def close(self) -> None:
        pass


class _TestConnection:
    def __init__(self) -> None:
        self.committed = False
        self.rolled_back = False
        self.closed = False

    def cursor(self) -> _TestCursor:
        return _TestCursor()

    def commit(self) -> None:
        self.committed = True

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


class _MinimalBackend(Backend, driver=_TestDriver):
    """Minimal Backend that calls real Backend.__init__ and uses base
    implementations of begin/commit/rollback/execute/ensure_migration_table."""

    supports_schemas: ClassVar[bool] = True
    transactional_ddl: ClassVar[bool] = False

    def __init__(self, **kwargs: Any) -> None:
        self.init_connection_arg: Any = None
        self.executed_sql: list[str] = []
        super().__init__(**kwargs)

    def connect(self) -> _TestConnection:
        return _TestConnection()

    def init_connection(self, connection: Any) -> None:
        self.init_connection_arg = connection

    def list_tables(self) -> list[str]:
        return []

    def _create_migration_table(self) -> None:
        pass

    def _copy_versions(self) -> None:
        pass

    def _migration_history(self) -> list[tuple[Any, ...]]:
        return []

    def mark(self, migration_id: str, status: str) -> None:
        pass

    def execute(self, sql: str, params: Any = None) -> Any:
        self.executed_sql.append(sql)
        return super().execute(sql, params)


class _ControllableBackend(_MinimalBackend):
    """Like _MinimalBackend but list_tables is driven by constructor arg."""

    def __init__(
        self,
        existing_tables: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        self._existing_tables = list(existing_tables or [])
        super().__init__(**kwargs)

    def list_tables(self) -> list[str]:
        return list(self._existing_tables)


# ---------------------------------------------------------------------------
# lock_hash
# ---------------------------------------------------------------------------


class TestLockHash:
    def test_same_input_same_output(self) -> None:
        assert lock_hash("mydb") == lock_hash("mydb")

    def test_different_input_different_output(self) -> None:
        assert lock_hash("a") != lock_hash("b")

    def test_deterministic_across_runs(self) -> None:
        expected = int.from_bytes(hashlib.sha256(b"mydb").digest()[:8], "big")
        assert lock_hash("mydb") == expected


# ---------------------------------------------------------------------------
# TransactionManager
# ---------------------------------------------------------------------------


class TestTransactionManagerInit:
    """mutmut_2, mutmut_3: _started = False -> None / True."""

    def test_started_is_false_after_init(self) -> None:
        tm = TransactionManager(_MinimalBackend())
        assert tm._started is False


# ---------------------------------------------------------------------------
# Backend.__init_subclass__
# ---------------------------------------------------------------------------


class TestInitSubclass:
    """mutmut_1: driver is not None -> is None
    mutmut_2: cls.driver = driver -> None
    mutmut_3: implementations[..] = cls -> None
    """

    def test_registered_in_implementations(self) -> None:
        assert _TestDriver.__name__ in Backend.implementations
        assert Backend.implementations[_TestDriver.__name__] is _MinimalBackend

    def test_driver_attribute(self) -> None:
        assert _MinimalBackend.driver is _TestDriver


# ---------------------------------------------------------------------------
# Backend.__init__
# ---------------------------------------------------------------------------


class TestInitAttributes:
    """mutmut_1/2/4/5/24/26/33: init attributes set to None."""

    def test_db_host(self) -> None:
        assert _MinimalBackend(db_host="myhost").db_host == "myhost"

    def test_db_port(self) -> None:
        assert _MinimalBackend(db_port=5432).db_port == 5432

    def test_db_user(self) -> None:
        assert _MinimalBackend(db_user="admin").db_user == "admin"

    def test_db_password(self) -> None:
        assert _MinimalBackend(db_password="secret").db_password == "secret"  # noqa: S105, S106

    def test_migration_schema(self) -> None:
        assert (
            _MinimalBackend(
                migration_schema="myschema",
            ).migration_schema
            == "myschema"
        )

    def test_versions_schema(self) -> None:
        assert (
            _MinimalBackend(
                versions_schema="legacy",
                migration_schema=None,
            ).versions_schema
            == "legacy"
        )

    def test_in_transaction_is_false(self) -> None:
        backend = _MinimalBackend()
        assert backend._in_transaction is False
        assert backend.in_transaction is False

    def test_init_connection_receives_connection(self) -> None:
        """mutmut_29: init_connection(None) would fail this."""
        backend = _MinimalBackend()
        assert backend.init_connection_arg is backend._connection


class TestInitLockTimeout:
    """mutmut_14/16/18."""

    def test_negative_raises_correct_message(self) -> None:
        with pytest.raises(ValueError) as exc:
            _MinimalBackend(lock_timeout=-1)
        assert "lock_timeout must not be negative" in str(exc.value)

    def test_negative_not_xx_prefixed(self) -> None:
        with pytest.raises(ValueError) as exc:
            _MinimalBackend(lock_timeout=-1)
        assert "XXlock_timeout" not in str(exc.value)

    def test_nan_raises(self) -> None:
        with pytest.raises(ValueError, match="lock_timeout"):
            _MinimalBackend(lock_timeout=float("nan"))

    def test_finite_preserved(self) -> None:
        """mutmut_16/18: would make this None."""
        backend = _MinimalBackend(lock_timeout=5.0)
        assert backend.lock_timeout == 5.0

    def test_infinite_is_none(self) -> None:
        backend = _MinimalBackend(lock_timeout=float("inf"))
        assert backend.lock_timeout is None

    def test_zero_valid(self) -> None:
        backend = _MinimalBackend(lock_timeout=0)
        assert backend.lock_timeout == 0

    def test_none_default(self) -> None:
        assert _MinimalBackend().lock_timeout is None


# ---------------------------------------------------------------------------
# Backend.quote_identifier
# ---------------------------------------------------------------------------


class TestQuoteIdentifier:
    """mutmut_1: \\x00 in s check changed."""

    def test_nul_byte_raises(self) -> None:
        with pytest.raises(ValueError, match="NUL"):
            _MinimalBackend().quote_identifier("ab\x00c")

    def test_double_quote_escaped(self) -> None:
        assert _MinimalBackend().quote_identifier('a"b') == '"a""b"'

    def test_normal(self) -> None:
        assert _MinimalBackend().quote_identifier("foo") == '"foo"'


# ---------------------------------------------------------------------------
# Backend.quote_versions_table
# ---------------------------------------------------------------------------


class TestQuoteVersionsTable:
    """mutmut_1: and -> or."""

    def test_no_schema_when_none(self) -> None:
        """and vs or: with supports_schemas=True and versions_schema=None,
        'and' returns plain, 'or' would try to dereference None."""
        backend = _MinimalBackend(versions_schema=None, migration_schema=None)
        assert backend.quote_versions_table() == '"versions"'

    def test_schema_qualifies(self) -> None:
        backend = _MinimalBackend(versions_schema="legacy")
        assert backend.quote_versions_table() == '"legacy"."versions"'


# ---------------------------------------------------------------------------
# Backend.begin
# ---------------------------------------------------------------------------


class TestBegin:
    """mutmut_1: _in_transaction = True -> None
    mutmut_2: _in_transaction = True -> False
    mutmut_5: execute("BEGIN") -> execute("begin")
    """

    def test_in_transaction_is_true(self) -> None:
        backend = _MinimalBackend()
        backend.begin()
        assert backend.in_transaction is True
        assert backend._in_transaction is True

    def test_executes_begin_uppercase(self) -> None:
        backend = _MinimalBackend()
        backend.begin()
        assert "BEGIN" in backend.executed_sql


# ---------------------------------------------------------------------------
# Backend.commit
# ---------------------------------------------------------------------------


class TestCommit:
    """mutmut_1: _in_transaction = False -> None."""

    def test_in_transaction_is_false(self) -> None:
        backend = _MinimalBackend()
        backend.begin()
        backend.commit()
        assert backend.in_transaction is False
        assert backend._in_transaction is False

    def test_connection_commit_called(self) -> None:
        backend = _MinimalBackend()
        backend.begin()
        conn = backend.connection
        backend.commit()
        assert conn.committed  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Backend.rollback
# ---------------------------------------------------------------------------


class TestRollback:
    """mutmut_1: init_connection(self.connection) -> init_connection(None)
    mutmut_2: _in_transaction = False -> None
    mutmut_3: _in_transaction = False -> True
    """

    def test_in_transaction_is_false(self) -> None:
        backend = _MinimalBackend()
        backend.begin()
        backend.rollback()
        assert backend.in_transaction is False
        assert backend._in_transaction is False

    def test_calls_init_connection_with_connection(self) -> None:
        backend = _MinimalBackend()
        backend.init_connection_arg = None
        backend.begin()
        backend.rollback()
        assert backend.init_connection_arg is backend._connection

    def test_connection_rollback_called(self) -> None:
        backend = _MinimalBackend()
        backend.begin()
        conn = backend.connection
        backend.rollback()
        assert conn.rolled_back  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Backend.lock_error
# ---------------------------------------------------------------------------


class TestLockError:
    """mutmut_5: message += -> message = (overwrites instead of appends)."""

    def test_with_timeout_includes_both_parts(self) -> None:
        backend = _MinimalBackend(lock_timeout=2.5)
        msg = str(backend.lock_error())
        assert "Could not acquire" in msg
        assert "2.5" in msg

    def test_without_timeout_has_no_suffix(self) -> None:
        backend = _MinimalBackend()
        msg = str(backend.lock_error())
        assert "Could not acquire" in msg
        assert "within" not in msg

    def test_returns_migration_lock_error(self) -> None:
        assert isinstance(
            _MinimalBackend(lock_timeout=5.0).lock_error(),
            MigrationLockError,
        )


# ---------------------------------------------------------------------------
# Backend.ensure_migration_table — logger.info
# ---------------------------------------------------------------------------


class TestEnsureMigrationTableCreationLog:
    """mutmut_2..8: various distortions of the logger.info call."""

    def test_logs_creating_message(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO)
        _ControllableBackend().ensure_migration_table()
        records = [r.getMessage() for r in caplog.records]
        expected = "Creating migration table 'migrations'"
        assert expected in records, f"not found in {records}"

    def test_no_log_when_table_exists(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO)
        _ControllableBackend(existing_tables=["migrations"]).ensure_migration_table()
        messages = [r.getMessage() for r in caplog.records]
        assert all("Creating migration table" not in m for m in messages)

    def test_not_xx_prefixed(self, caplog: pytest.LogCaptureFixture) -> None:
        """mutmut_6."""
        caplog.set_level(logging.INFO)
        _ControllableBackend().ensure_migration_table()
        messages = [r.getMessage() for r in caplog.records]
        assert not any("XXCreating" in m for m in messages)
        assert any("Creating migration table" in m for m in messages)

    def test_not_lowercased(self, caplog: pytest.LogCaptureFixture) -> None:
        """mutmut_7."""
        caplog.set_level(logging.INFO)
        _ControllableBackend().ensure_migration_table()
        messages = [r.getMessage() for r in caplog.records]
        creating = [m for m in messages if "Creating" in m]
        assert any(m.startswith("Creating") for m in creating)

    def test_not_allcaps(self, caplog: pytest.LogCaptureFixture) -> None:
        """mutmut_8."""
        caplog.set_level(logging.INFO)
        _ControllableBackend().ensure_migration_table()
        messages = [r.getMessage() for r in caplog.records]
        assert not any("CREATING" in m for m in messages)


# ---------------------------------------------------------------------------
# Backend.ensure_migration_table — logger.warning (versions migration)
# ---------------------------------------------------------------------------


class TestEnsureMigrationTableVersionsWarning:
    """mutmut_11..15: distortions of the logger.warning call."""

    def test_warning_logged_when_versions_exists(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.WARNING)
        _ControllableBackend(existing_tables=["versions"]).ensure_migration_table()
        records = [r.getMessage() for r in caplog.records]
        expected = (
            "Legacy 'versions' table found; copying its records as APPLIED events"
        )
        assert expected in records, f"not found in {records}"

    def test_no_warning_without_versions(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        caplog.set_level(logging.WARNING)
        _ControllableBackend().ensure_migration_table()
        assert not any("Legacy" in r.getMessage() for r in caplog.records)

    def test_warning_not_just_table_name(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """mutmut_12: format string removed, uses table_name as fmt."""
        caplog.set_level(logging.WARNING)
        _ControllableBackend(existing_tables=["versions"]).ensure_migration_table()
        records = [r.getMessage() for r in caplog.records]
        assert any("Legacy" in r for r in records), "must contain 'Legacy'"

    def test_warning_not_empty(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """mutmut_13: logger.warning() with no args."""
        caplog.set_level(logging.WARNING)
        _ControllableBackend(existing_tables=["versions"]).ensure_migration_table()
        assert len(caplog.records) >= 1

    def test_warning_not_xx_prefixed(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """mutmut_14."""
        caplog.set_level(logging.WARNING)
        _ControllableBackend(existing_tables=["versions"]).ensure_migration_table()
        assert not any("XXLegacy" in r.getMessage() for r in caplog.records)

    def test_warning_not_lowercased(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """mutmut_15."""
        caplog.set_level(logging.WARNING)
        _ControllableBackend(existing_tables=["versions"]).ensure_migration_table()
        legacy = [r for r in caplog.records if "Legacy" in r.getMessage()]
        assert any(r.getMessage().startswith("Legacy") for r in legacy)
