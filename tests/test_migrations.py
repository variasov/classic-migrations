import os
import sqlite3

import pytest
from classic.migrations import Migrations, exceptions


def write_file(path, content):
    path = str(path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def make_uri(db_path):
    return "sqlite:///{}".format(str(db_path).replace("\\", "/"))


def make_migrations(source, database, **kwargs):
    return Migrations(source=str(source), database=make_uri(database), **kwargs)


def db_tables(db_path):
    conn = sqlite3.connect(str(db_path))
    try:
        return [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )]
    finally:
        conn.close()


def db_rows(db_path, sql, params=()):
    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


@pytest.fixture
def source(tmp_path):
    d = tmp_path / "migrations"
    d.mkdir()
    return d


@pytest.fixture
def db_path(tmp_path):
    return tmp_path / "test.db"


def test_apply_creates_table_and_records(source, db_path):
    write_file(
        source / "0001.init.sql",
        "CREATE TABLE foo(id INTEGER PRIMARY KEY);\n",
    )
    m = make_migrations(source, db_path)
    m.apply()

    assert "foo" in db_tables(db_path)
    assert m.is_applied("0001.init")

    rows = db_rows(db_path, "SELECT migration_id, content_hash, comment FROM migrations")
    assert len(rows) == 1
    migration_id, content_hash, comment = rows[0]
    assert migration_id == "0001.init"
    assert content_hash is not None and len(content_hash) == 64
    assert comment == ""


def test_list_shows_applied_status(source, db_path):
    write_file(source / "0001.a.sql", "CREATE TABLE a(id INTEGER);\n")
    write_file(source / "0002.b.sql", "CREATE TABLE b(id INTEGER);\n")
    m = make_migrations(source, db_path)
    m.apply()

    result = {id: status for status, id, _ in m.list()}
    assert result == {"0001.a": "A", "0002.b": "A"}


def test_rollback_runs_rollback_file(source, db_path):
    write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER PRIMARY KEY);\n")
    write_file(source / "0001.init.rollback.sql", "DROP TABLE foo;\n")
    m = make_migrations(source, db_path)
    m.apply()
    assert "foo" in db_tables(db_path)

    m.rollback()
    assert "foo" not in db_tables(db_path)
    assert not m.is_applied("0001.init")


def test_rollback_single_vs_all(source, db_path):
    write_file(source / "0001.a.sql", "CREATE TABLE a(id INTEGER);\n")
    write_file(source / "0001.a.rollback.sql", "DROP TABLE a;\n")
    write_file(source / "0002.b.sql", "CREATE TABLE b(id INTEGER);\n")
    write_file(source / "0002.b.rollback.sql", "DROP TABLE b;\n")
    m = make_migrations(source, db_path)
    m.apply()
    assert {"a", "b"} <= set(db_tables(db_path))

    m.rollback()
    assert "a" in db_tables(db_path)
    assert "b" not in db_tables(db_path)
    assert m.is_applied("0001.a")
    assert not m.is_applied("0002.b")

    m.rollback(all=True)
    assert "a" not in db_tables(db_path)


def test_dependencies_are_applied_in_order(source, db_path):
    write_file(source / "0001.base.sql", "CREATE TABLE base(id INTEGER PRIMARY KEY);\n")
    write_file(
        source / "0002.child.sql",
        "-- depends: 0001.base\n"
        "INSERT INTO base(id) VALUES (1);\n"
        "CREATE TABLE child(id INTEGER);\n",
    )
    m = make_migrations(source, db_path)
    m.apply()

    assert "base" in db_tables(db_path)
    assert "child" in db_tables(db_path)
    assert db_rows(db_path, "SELECT id FROM base") == [(1,)]


def test_hash_mismatch_detected(source, db_path):
    write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
    m = make_migrations(source, db_path)
    m.apply()

    write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER, extra TEXT);\n")
    with pytest.raises(exceptions.MigrationHashMismatch) as exc:
        m.apply()
    assert "0001.init" in exc.value.changed


def test_comment_change_does_not_change_hash(source, db_path):
    write_file(
        source / "0001.init.sql",
        "-- comment: first\nCREATE TABLE foo(id INTEGER);\n",
    )
    m = make_migrations(source, db_path)
    m.apply()

    write_file(
        source / "0001.init.sql",
        "-- comment: changed\nCREATE TABLE foo(id INTEGER);\n",
    )
    m.apply()  # should not raise


def test_skip_hash_check_writes_null(source, db_path):
    write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
    m = make_migrations(source, db_path)
    m.apply(check_hashes=False)

    rows = db_rows(db_path, "SELECT content_hash FROM migrations")
    assert rows == [(None,)]

    # changing the body now does not raise, since the stored hash is NULL
    write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER, extra TEXT);\n")
    m.apply()


def test_mark_and_unmark(source, db_path):
    write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
    m = make_migrations(source, db_path)

    m.mark()
    assert m.is_applied("0001.init")
    assert "foo" not in db_tables(db_path)

    rows = db_rows(db_path, "SELECT content_hash FROM migrations")
    assert rows[0][0] is not None

    m.unmark()
    assert not m.is_applied("0001.init")


def test_hooks_run_on_apply_and_rollback(source, db_path):
    write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
    write_file(source / "0001.init.rollback.sql", "DROP TABLE foo;\n")
    write_file(source / "pre-apply.sql", "CREATE TABLE pre_apply(id INTEGER);\n")
    write_file(source / "post-apply.sql", "CREATE TABLE post_apply(id INTEGER);\n")
    write_file(source / "pre-rollback.sql", "CREATE TABLE pre_rollback(id INTEGER);\n")
    write_file(source / "post-rollback.sql", "CREATE TABLE post_rollback(id INTEGER);\n")

    m = make_migrations(source, db_path)
    m.apply()

    tables = set(db_tables(db_path))
    assert {"pre_apply", "post_apply", "foo"} <= tables
    assert "pre_rollback" not in tables
    assert "post_rollback" not in tables

    m.rollback()
    tables = set(db_tables(db_path))
    assert {"pre_rollback", "post_rollback"} <= tables
    assert "foo" not in tables


def test_new_creates_sql_file_with_depends(source):
    write_file(source / "0001.base.sql", "CREATE TABLE base(id INTEGER);\n")
    m = Migrations(source=str(source))
    filename = m.new(message="add stuff")

    assert filename.endswith(".sql")
    assert os.path.isfile(filename)
    with open(filename, encoding="utf-8") as file:
        content = file.read()
    assert "-- add stuff" in content
    assert "-- depends: 0001.base" in content


def test_new_requires_source(tmp_path):
    m = Migrations(source=None)
    with pytest.raises(ValueError):
        m.new(message="x")


def test_versions_table_is_migrated(source, db_path):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE versions ("
        "migration_hash VARCHAR(64), migration_id VARCHAR(255), "
        "applied_at_utc TIMESTAMP, PRIMARY KEY (migration_hash))"
    )
    conn.execute(
        "INSERT INTO versions (migration_hash, migration_id, applied_at_utc) "
        "VALUES ('abc', '0001.old', '2020-01-01 00:00:00')"
    )
    conn.commit()
    conn.close()

    write_file(source / "0001.old.sql", "CREATE TABLE old(id INTEGER);\n")
    m = make_migrations(source, db_path)
    m.apply()

    rows = db_rows(db_path, "SELECT migration_id, content_hash FROM migrations")
    assert rows == [("0001.old", None)]


def test_custom_migration_table(source, db_path):
    write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
    m = Migrations(
        source=str(source),
        database=make_uri(db_path),
        migration_table="my_history",
    )
    m.apply()

    assert "my_history" in db_tables(db_path)
    assert "migrations" not in db_tables(db_path)


def test_develop_reapplies_last(source, db_path):
    write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
    write_file(source / "0001.init.rollback.sql", "DROP TABLE foo;\n")
    m = make_migrations(source, db_path)
    m.apply()

    m.develop()
    assert "foo" in db_tables(db_path)
    assert m.is_applied("0001.init")


def test_reapply(source, db_path):
    write_file(source / "0001.init.sql", "CREATE TABLE foo(id INTEGER);\n")
    write_file(source / "0001.init.rollback.sql", "DROP TABLE foo;\n")
    m = make_migrations(source, db_path)
    m.apply()
    assert "foo" in db_tables(db_path)

    m.reapply()
    assert "foo" in db_tables(db_path)
    assert m.is_applied("0001.init")


def test_apply_one(source, db_path):
    write_file(source / "0001.a.sql", "CREATE TABLE a(id INTEGER);\n")
    write_file(source / "0002.b.sql", "CREATE TABLE b(id INTEGER);\n")
    m = make_migrations(source, db_path)
    m.apply(one=True)

    assert m.is_applied("0001.a")
    assert not m.is_applied("0002.b")


def test_missing_dependency_raises(source, db_path):
    write_file(
        source / "0001.child.sql",
        "-- depends: 9999.missing\nCREATE TABLE child(id INTEGER);\n",
    )
    m = make_migrations(source, db_path)
    with pytest.raises(exceptions.BadMigration):
        m.apply()


def test_conflicting_ids_raise(source, db_path):
    write_file(source / "0001.init.sql", "CREATE TABLE a(id INTEGER);\n")
    sub = source / "sub"
    sub.mkdir()
    write_file(sub / "0001.init.sql", "CREATE TABLE b(id INTEGER);\n")
    m = Migrations(source=[str(source), str(sub)], database=make_uri(db_path))
    with pytest.raises(exceptions.MigrationConflict):
        m.apply()
