from collections.abc import Generator

import pymysql
import pytest
from classic.migrations.backends.pymysql import PyMySQLBackend

from tests.conftest import get_credentials

_env = get_credentials("MYSQL_")


def _env_value(key: str) -> str:
    value = _env[key]
    assert value is not None
    return value


def _mysql_available() -> bool:
    if not all(_env.values()):
        return False
    try:
        conn = pymysql.connect(
            host=_env_value("host"),
            port=int(_env_value("port")),
            database=_env_value("name"),
            user=_env_value("user"),
            password=_env_value("password"),
        )
    except pymysql.OperationalError:
        return False
    conn.close()
    return True


pytestmark = pytest.mark.skipif(
    not _mysql_available(),
    reason=(
        "MySQL unavailable: set MYSQL_DATABASE_HOST, MYSQL_DATABASE_PORT, "
        "MYSQL_DATABASE_NAME, MYSQL_DATABASE_USER, MYSQL_DATABASE_PASSWORD"
    ),
)


@pytest.fixture
def backend() -> Generator[PyMySQLBackend, None, None]:
    b = PyMySQLBackend(
        db_host=_env_value("host"),
        db_port=int(_env_value("port")),
        db_name=_env_value("name"),
        db_user=_env_value("user"),
        db_password=_env_value("password"),
        migration_table="migrations_test",
    )
    b.execute(f"DROP TABLE IF EXISTS {b.migration_table_quoted}")
    yield b
    try:
        b.execute(f"DROP TABLE IF EXISTS {b.migration_table_quoted}")
        b.execute(f"DROP TABLE IF EXISTS {b.versions_table_quoted}")
    finally:
        b.close()


def test_list_tables_empty(backend: PyMySQLBackend) -> None:
    assert backend.migration_table not in backend.list_tables()


def test_create_migration_table(backend: PyMySQLBackend) -> None:
    backend._create_migration_table()
    assert backend.migration_table in backend.list_tables()


def test_mark_applied_inserts_event(backend: PyMySQLBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.init", "APPLIED")

    history = backend._migration_history()
    assert len(history) == 1
    assert history[0][0] == "0001.init"
    assert history[0][2] == "APPLIED"


def test_unmark_appends_rolled_back_event(backend: PyMySQLBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.init", "APPLIED")
    backend.mark("0001.init", "ROLLED_BACK")

    history = backend._migration_history()
    assert len(history) == 2
    assert history[0][2] == "APPLIED"
    assert history[1][2] == "ROLLED_BACK"
    assert history[0][0] == history[1][0] == "0001.init"


def test_reapply_makes_migration_applied_again(backend: PyMySQLBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.init", "APPLIED")
    backend.mark("0001.init", "ROLLED_BACK")
    backend.mark("0001.init", "APPLIED")

    history = backend._migration_history()
    assert len(history) == 3
    assert [row[2] for row in history] == ["APPLIED", "ROLLED_BACK", "APPLIED"]


def test_pending_status(backend: PyMySQLBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.init", "PENDING")
    backend.mark("0001.init", "APPLIED")

    history = backend._migration_history()
    assert [row[2] for row in history] == ["PENDING", "APPLIED"]


def test_rolled_back_migration_has_correct_status(backend: PyMySQLBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.a", "APPLIED")
    backend.mark("0002.b", "APPLIED")
    backend.mark("0001.a", "ROLLED_BACK")

    history = backend._migration_history()
    assert len(history) == 3
    assert history[0][2] == "APPLIED"
    assert history[1][2] == "APPLIED"
    assert history[2][2] == "ROLLED_BACK"


def test_ensure_migration_table_creates(backend: PyMySQLBackend) -> None:
    backend.ensure_migration_table()
    assert backend.migration_table in backend.list_tables()


def test_ensure_migration_table_idempotent(backend: PyMySQLBackend) -> None:
    backend.ensure_migration_table()
    backend.ensure_migration_table()
    assert backend.migration_table in backend.list_tables()


def test_copy_versions(backend: PyMySQLBackend) -> None:
    backend.execute(
        f"CREATE TABLE {backend.versions_table_quoted} ("
        "migration_hash VARCHAR(64), migration_id VARCHAR(255), "
        "applied_at_utc DATETIME, PRIMARY KEY (migration_hash))",
    )
    backend.execute(
        f"INSERT INTO {backend.versions_table_quoted} "
        "(migration_hash, migration_id, applied_at_utc) "
        "VALUES (%s, %s, %s)",
        ("abc", "0001.old", "2020-01-01 00:00:00"),
    )

    backend.ensure_migration_table()
    history = backend._migration_history()
    assert len(history) == 1
    assert history[0][0] == "0001.old"
    assert history[0][2] == "APPLIED"


def test_lock_acquire_release(backend: PyMySQLBackend) -> None:
    backend._create_migration_table()
    backend.acquire_lock()
    backend.mark("0001.init", "APPLIED")
    backend.release_lock()
    history = backend._migration_history()
    assert len(history) == 1


def test_transaction_commit(backend: PyMySQLBackend) -> None:
    backend._create_migration_table()
    with backend.transaction():
        backend.mark("0001.init", "APPLIED")
    history = backend._migration_history()
    assert len(history) == 1


def test_transaction_rollback(backend: PyMySQLBackend) -> None:
    backend._create_migration_table()
    with pytest.raises(RuntimeError, match="forced"), backend.transaction():
        backend.mark("0001.init", "APPLIED")
        raise RuntimeError("forced")
    history = backend._migration_history()
    assert len(history) == 0


def test_disable_transactions_noop(backend: PyMySQLBackend) -> None:
    backend._create_migration_table()
    with backend.disable_transactions():
        backend.mark("0001.init", "APPLIED")
    history = backend._migration_history()
    assert len(history) == 1


def test_quote_identifier(backend: PyMySQLBackend) -> None:
    assert backend.quote_identifier("my_table") == "`my_table`"


def test_quote_identifier_with_backticks(backend: PyMySQLBackend) -> None:
    assert backend.quote_identifier("test`table") == "`test``table`"


def test_execute_cursor(backend: PyMySQLBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.init", "APPLIED")
    cursor = backend.execute(
        f"SELECT migration_id FROM {backend.migration_table_quoted} "
        "WHERE status = 'APPLIED'",
    )
    row = cursor.fetchone()
    assert row[0] == "0001.init"


def test_migration_table_quoted_custom_name() -> None:
    b = PyMySQLBackend(
        db_host=_env_value("host"),
        db_port=int(_env_value("port")),
        db_name=_env_value("name"),
        db_user=_env_value("user"),
        db_password=_env_value("password"),
        migration_table="my_history",
    )
    try:
        assert b.migration_table_quoted == "`my_history`"
        b._create_migration_table()
        assert "my_history" in b.list_tables()
    finally:
        b.execute(f"DROP TABLE IF EXISTS {b.migration_table_quoted}")
        b.close()


def test_migration_history_returns_all_events(backend: PyMySQLBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.a", "APPLIED")
    backend.mark("0002.b", "APPLIED")
    backend.mark("0001.a", "ROLLED_BACK")

    history = backend.migration_history()
    assert len(history) == 3
    assert [row[0] for row in history] == ["0001.a", "0002.b", "0001.a"]
    assert [row[2] for row in history] == ["APPLIED", "APPLIED", "ROLLED_BACK"]


def test_migration_history_no_comment_column(backend: PyMySQLBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.a", "APPLIED")
    history = backend._migration_history()
    assert len(history[0]) == 3
