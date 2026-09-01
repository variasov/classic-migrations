from collections.abc import Generator
from datetime import datetime, timezone
from pathlib import Path

import oracledb
import pytest
from classic.migrations import MigrationsCollection, Migrator
from classic.migrations.backends.oracle import OracleBackend

from tests.conftest import get_credentials

_env = get_credentials("ORACLE_")


def _env_value(key: str) -> str:
    value = _env[key]
    assert value is not None
    return value


def _oracle_available() -> bool:
    if not all(_env.values()):
        return False
    try:
        conn = oracledb.connect(
            host=_env_value("host"),
            port=int(_env_value("port")),
            service_name=_env_value("name"),
            user=_env_value("user"),
            password=_env_value("password"),
        )
    except oracledb.Error:
        return False
    conn.close()
    return True


pytestmark = pytest.mark.skipif(
    not _oracle_available(),
    reason=(
        "Oracle unavailable: set ORACLE_DATABASE_HOST, ORACLE_DATABASE_PORT, "
        "ORACLE_DATABASE_NAME, ORACLE_DATABASE_USER, ORACLE_DATABASE_PASSWORD"
    ),
)


def _drop_table(backend: OracleBackend, quoted: str) -> None:
    backend.execute(
        f"BEGIN EXECUTE IMMEDIATE 'DROP TABLE {quoted}'; "
        "EXCEPTION WHEN OTHERS THEN NULL; END;",
    )


@pytest.fixture
def backend() -> Generator[OracleBackend, None, None]:
    b = OracleBackend(
        db_host=_env_value("host"),
        db_port=int(_env_value("port")),
        db_name=_env_value("name"),
        db_user=_env_value("user"),
        db_password=_env_value("password"),
        migration_table="migrations_test",
    )
    _drop_table(b, b.migration_table_quoted)
    yield b
    try:
        _drop_table(b, b.migration_table_quoted)
        _drop_table(b, b.versions_table_quoted)
    finally:
        b.close()


def test_list_tables_empty(backend: OracleBackend) -> None:
    assert backend.migration_table not in backend.list_tables()


def test_create_migration_table(backend: OracleBackend) -> None:
    backend._create_migration_table()
    assert backend.migration_table in backend.list_tables()


def test_mark_applied_inserts_event(backend: OracleBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.init", "APPLIED")

    history = backend._migration_history()
    assert len(history) == 1
    assert history[0][0] == "0001.init"
    assert history[0][2] == "APPLIED"


def test_unmark_appends_rolled_back_event(backend: OracleBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.init", "APPLIED")
    backend.mark("0001.init", "ROLLED_BACK")

    history = backend._migration_history()
    assert len(history) == 2
    assert history[0][2] == "APPLIED"
    assert history[1][2] == "ROLLED_BACK"
    assert history[0][0] == history[1][0] == "0001.init"


def test_reapply_makes_migration_applied_again(backend: OracleBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.init", "APPLIED")
    backend.mark("0001.init", "ROLLED_BACK")
    backend.mark("0001.init", "APPLIED")

    history = backend._migration_history()
    assert len(history) == 3
    assert [row[2] for row in history] == ["APPLIED", "ROLLED_BACK", "APPLIED"]


def test_pending_status(backend: OracleBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.init", "PENDING")
    backend.mark("0001.init", "APPLIED")

    history = backend._migration_history()
    assert [row[2] for row in history] == ["PENDING", "APPLIED"]


def test_rolled_back_migration_has_correct_status(backend: OracleBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.a", "APPLIED")
    backend.mark("0002.b", "APPLIED")
    backend.mark("0001.a", "ROLLED_BACK")

    history = backend._migration_history()
    assert len(history) == 3
    assert history[0][2] == "APPLIED"
    assert history[1][2] == "APPLIED"
    assert history[2][2] == "ROLLED_BACK"


def test_ensure_migration_table_creates(backend: OracleBackend) -> None:
    backend.ensure_migration_table()
    assert backend.migration_table in backend.list_tables()


def test_ensure_migration_table_idempotent(backend: OracleBackend) -> None:
    backend.ensure_migration_table()
    backend.ensure_migration_table()
    assert backend.migration_table in backend.list_tables()


def test_copy_versions(backend: OracleBackend) -> None:
    backend.execute(
        f"CREATE TABLE {backend.versions_table_quoted} ("
        "migration_hash VARCHAR2(64), migration_id VARCHAR2(255), "
        "applied_at_utc TIMESTAMP, PRIMARY KEY (migration_hash))",
    )
    backend.execute(
        f"INSERT INTO {backend.versions_table_quoted} "
        "(migration_hash, migration_id, applied_at_utc) "
        "VALUES (:hash, :id, :applied)",
        {
            "hash": "abc",
            "id": "0001.old",
            "applied": datetime(2020, 1, 1, tzinfo=timezone.utc),
        },
    )

    backend.ensure_migration_table()
    history = backend._migration_history()
    assert len(history) == 1
    assert history[0][0] == "0001.old"
    assert history[0][2] == "APPLIED"


def test_lock_acquire_release(backend: OracleBackend) -> None:
    backend._create_migration_table()
    backend.acquire_lock()
    backend.mark("0001.init", "APPLIED")
    backend.release_lock()
    history = backend._migration_history()
    assert len(history) == 1


def test_lock_rollback_on_exception(backend: OracleBackend) -> None:
    backend._create_migration_table()
    backend.acquire_lock()
    backend.mark("0001.init", "APPLIED")
    backend.rollback()
    history = backend._migration_history()
    assert len(history) == 0


def test_transaction_commit(backend: OracleBackend) -> None:
    backend._create_migration_table()
    with backend.transaction():
        backend.mark("0001.init", "APPLIED")
    history = backend._migration_history()
    assert len(history) == 1


def test_transaction_rollback(backend: OracleBackend) -> None:
    backend._create_migration_table()
    with pytest.raises(RuntimeError, match="forced"), backend.transaction():
        backend.mark("0001.init", "APPLIED")
        raise RuntimeError("forced")
    history = backend._migration_history()
    assert len(history) == 0


def test_disable_transactions_noop(backend: OracleBackend) -> None:
    backend._create_migration_table()
    with backend.disable_transactions():
        backend.mark("0001.init", "APPLIED")
    history = backend._migration_history()
    assert len(history) == 1


def test_quote_identifier(backend: OracleBackend) -> None:
    assert backend.quote_identifier("my_table") == '"my_table"'


def test_quote_identifier_with_quotes(backend: OracleBackend) -> None:
    assert backend.quote_identifier('test"table') == '"test""table"'


def test_execute_cursor(backend: OracleBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.init", "APPLIED")
    cursor = backend.execute(
        f"SELECT migration_id FROM {backend.migration_table_quoted} "
        "WHERE status = 'APPLIED'",
    )
    row = cursor.fetchone()
    assert row[0] == "0001.init"


def test_migration_table_quoted_custom_name() -> None:
    b = OracleBackend(
        db_host=_env_value("host"),
        db_port=int(_env_value("port")),
        db_name=_env_value("name"),
        db_user=_env_value("user"),
        db_password=_env_value("password"),
        migration_table="my_history",
    )
    try:
        assert b.migration_table_quoted == '"my_history"'
        b._create_migration_table()
        assert "my_history" in b.list_tables()
    finally:
        _drop_table(b, b.migration_table_quoted)
        b.close()


def test_migration_history_returns_all_events(backend: OracleBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.a", "APPLIED")
    backend.mark("0002.b", "APPLIED")
    backend.mark("0001.a", "ROLLED_BACK")

    history = backend.migration_history()
    assert len(history) == 3
    assert [row[0] for row in history] == ["0001.a", "0002.b", "0001.a"]
    assert [row[2] for row in history] == ["APPLIED", "APPLIED", "ROLLED_BACK"]


def test_migration_history_no_comment_column(backend: OracleBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.a", "APPLIED")
    history = backend._migration_history()
    assert len(history[0]) == 3


class TestOracleLifecycle:
    def _drop_all(self, db: Migrator) -> None:
        backend = db.backend
        assert isinstance(backend, OracleBackend)
        _drop_table(backend, backend.migration_table_quoted)
        _drop_table(backend, backend.versions_table_quoted)

    def test_apply_and_rollback(self, source: Path) -> None:
        def write_file(path: Path, content: str) -> Path:
            path.write_text(content, encoding="utf-8")
            return path

        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        write_file(source / "0002.more.sql", "INSERT INTO foo VALUES (1);\n")
        write_file(source / "0002.more.rollback.sql", "DELETE FROM foo;\n")
        write_file(source / "0001.init.rollback.sql", "DROP TABLE foo;\n")

        db = Migrator(
            driver="oracledb",
            db_host=_env_value("host"),
            db_port=int(_env_value("port")),
            db_name=_env_value("name"),
            db_user=_env_value("user"),
            db_pass=_env_value("password"),
            migration_table="migrations_it",
        )
        migrations = MigrationsCollection(str(source))

        try:
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
                self._drop_all(db)
        finally:
            pass
