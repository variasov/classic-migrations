import os
from collections.abc import Generator
from pathlib import Path

import psycopg
import pytest

from classic.migrations import MigrationsCollection, Migrator
from classic.migrations.backends.psycopg import PsycopgBackend

_env = {
    "host": os.environ.get("DB_HOST"),
    "port": os.environ.get("DB_PORT"),
    "name": os.environ.get("DB_NAME"),
    "user": os.environ.get("DB_USER"),
    "password": os.environ.get("DB_PASSWORD"),
}


def _env_value(key: str) -> str:
    value = _env[key]
    assert value is not None
    return value


def _pg_available() -> bool:
    if not all(_env.values()):
        return False
    try:
        conn = psycopg.connect(
            host=_env_value("host"),
            port=int(_env_value("port")),
            dbname=_env_value("name"),
            user=_env_value("user"),
            password=_env_value("password"),
        )
    except psycopg.OperationalError:
        return False
    conn.close()
    return True


pytestmark = pytest.mark.skipif(
    not _pg_available(),
    reason="PostgreSQL unavailable: set DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD",
)


@pytest.fixture
def backend() -> Generator[PsycopgBackend, None, None]:
    b = PsycopgBackend(
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


def test_list_tables_empty(backend: PsycopgBackend) -> None:
    assert backend.migration_table not in backend.list_tables()


def test_create_migration_table(backend: PsycopgBackend) -> None:
    backend._create_migration_table()
    assert backend.migration_table in backend.list_tables()


def test_mark_applied_inserts_event(backend: PsycopgBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.init", "APPLIED")

    history = backend._migration_history()
    assert len(history) == 1
    assert history[0][0] == "0001.init"
    assert history[0][2] == "APPLIED"


def test_unmark_appends_rolled_back_event(backend: PsycopgBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.init", "APPLIED")
    backend.mark("0001.init", "ROLLED_BACK")

    history = backend._migration_history()
    assert len(history) == 2
    assert history[0][2] == "APPLIED"
    assert history[1][2] == "ROLLED_BACK"
    assert history[0][0] == history[1][0] == "0001.init"


def test_reapply_makes_migration_applied_again(backend: PsycopgBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.init", "APPLIED")
    backend.mark("0001.init", "ROLLED_BACK")
    backend.mark("0001.init", "APPLIED")

    history = backend._migration_history()
    assert len(history) == 3
    assert [row[2] for row in history] == ["APPLIED", "ROLLED_BACK", "APPLIED"]


def test_pending_status(backend: PsycopgBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.init", "PENDING")
    backend.mark("0001.init", "APPLIED")

    history = backend._migration_history()
    assert [row[2] for row in history] == ["PENDING", "APPLIED"]


def test_rolled_back_migration_has_correct_status(backend: PsycopgBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.a", "APPLIED")
    backend.mark("0002.b", "APPLIED")
    backend.mark("0001.a", "ROLLED_BACK")

    history = backend._migration_history()
    assert len(history) == 3
    assert history[0][2] == "APPLIED"
    assert history[1][2] == "APPLIED"
    assert history[2][2] == "ROLLED_BACK"


def test_ensure_migration_table_creates(backend: PsycopgBackend) -> None:
    backend.ensure_migration_table()
    assert backend.migration_table in backend.list_tables()


def test_ensure_migration_table_idempotent(backend: PsycopgBackend) -> None:
    backend.ensure_migration_table()
    backend.ensure_migration_table()
    assert backend.migration_table in backend.list_tables()


def test_copy_versions(backend: PsycopgBackend) -> None:
    backend.execute(
        f"CREATE TABLE {backend.versions_table_quoted} ("
        "migration_hash VARCHAR(64), migration_id VARCHAR(255), "
        "applied_at_utc TIMESTAMP, PRIMARY KEY (migration_hash))"
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


def test_lock_acquire_release(backend: PsycopgBackend) -> None:
    backend._create_migration_table()
    backend.acquire_lock()
    backend.mark("0001.init", "APPLIED")
    backend.release_lock()
    history = backend._migration_history()
    assert len(history) == 1


def test_transaction_commit(backend: PsycopgBackend) -> None:
    backend._create_migration_table()
    with backend.transaction():
        backend.mark("0001.init", "APPLIED")
    history = backend._migration_history()
    assert len(history) == 1


def test_transaction_rollback(backend: PsycopgBackend) -> None:
    backend._create_migration_table()
    with pytest.raises(RuntimeError):
        with backend.transaction():
            backend.mark("0001.init", "APPLIED")
            raise RuntimeError("forced")
    history = backend._migration_history()
    assert len(history) == 0


def test_disable_transactions_noop(backend: PsycopgBackend) -> None:
    backend._create_migration_table()
    with backend.disable_transactions():
        backend.mark("0001.init", "APPLIED")
    history = backend._migration_history()
    assert len(history) == 1


def test_quote_identifier(backend: PsycopgBackend) -> None:
    assert backend.quote_identifier("my_table") == '"my_table"'


def test_quote_identifier_with_quotes(backend: PsycopgBackend) -> None:
    assert backend.quote_identifier('test"table') == '"test""table"'


def test_execute_cursor(backend: PsycopgBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.init", "APPLIED")
    cursor = backend.execute(
        f"SELECT migration_id FROM {backend.migration_table_quoted} "
        "WHERE status = 'APPLIED'"
    )
    row = cursor.fetchone()
    assert row[0] == "0001.init"


def test_migration_table_quoted_custom_name() -> None:
    b = PsycopgBackend(
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
        b.execute(f"DROP TABLE IF EXISTS {b.migration_table_quoted}")
        b.close()


def test_migration_history_returns_all_events(backend: PsycopgBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.a", "APPLIED")
    backend.mark("0002.b", "APPLIED")
    backend.mark("0001.a", "ROLLED_BACK")

    history = backend.migration_history()
    assert len(history) == 3
    assert [row[0] for row in history] == ["0001.a", "0002.b", "0001.a"]
    assert [row[2] for row in history] == ["APPLIED", "APPLIED", "ROLLED_BACK"]


def test_migration_history_no_comment_column(backend: PsycopgBackend) -> None:
    backend._create_migration_table()
    backend.mark("0001.a", "APPLIED")
    history = backend._migration_history()
    assert len(history[0]) == 3


def test_migration_schema_creates_schema() -> None:
    b = PsycopgBackend(
        db_host=_env_value("host"),
        db_port=int(_env_value("port")),
        db_name=_env_value("name"),
        db_user=_env_value("user"),
        db_password=_env_value("password"),
        migration_table="migrations",
        migration_schema="migrations_schema_test",
    )
    try:
        assert b.migration_table_quoted == '"migrations_schema_test"."migrations"'
        b.ensure_migration_table()
        assert "migrations" in b.list_tables()
        history = b.migration_history()
        assert history == []
    finally:
        b.execute('DROP SCHEMA IF EXISTS "migrations_schema_test" CASCADE')
        b.close()


class TestPsycopgLifecycle:
    def _drop_all(self, db: Migrator) -> None:
        backend = db.backend
        backend.execute(f"DROP TABLE IF EXISTS {backend.migration_table_quoted}")
        backend.execute(f"DROP TABLE IF EXISTS {backend.versions_table_quoted}")

    def test_apply_and_rollback(self, source: Path) -> None:
        def write_file(path: Path, content: str) -> Path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(content)
            return path

        write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
        write_file(source / "0002.more.sql", "INSERT INTO foo VALUES (1);\n")
        write_file(source / "0002.more.rollback.sql", "DELETE FROM foo;\n")
        write_file(source / "0001.init.rollback.sql", "DROP TABLE foo;\n")

        db = Migrator(
            driver="psycopg",
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
