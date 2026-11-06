Changelog
=========

3.0.0 (unreleased)
------------------

Исправления по результатам ревью 2.1.0.

* Порядок независимых миграций стал стабильным (лексикографическим);
  ``target`` в ``apply``/``rollback`` вычисляется по графу зависимостей:
  ``apply X`` применяет X и её зависимости, ``rollback X`` откатывает X и
  зависящие от неё.
* Директивы ``-- depends`` и ``-- transactional`` разбираются строго: другой
  регистр, неизвестные директивы и несколько строк ``depends`` теперь ошибка,
  а не молчаливое игнорирование.
* SQLite: каждая миграция выполняется в собственной транзакции; блокировка
  берётся через файл-спутник ``<db>.<table>.lock``. Хуки выполняются вне
  транзакции.
* MySQL: ``transactional_ddl`` установлен в ``False`` (DDL в MySQL неявно
  коммитит). Oracle: соединение в autocommit; нетранзакционный путь
  фиксирует события истории и DML.
* ``Migrator`` принимает ``db_args`` (дополнительные параметры подключения) и
  ``lock_timeout`` (таймаут advisory-lock: ``None`` — ждать бесконечно, ``0`` —
  не ждать).
* Миграция в статусе ``PENDING`` блокирует ``apply``/``rollback`` до ручного
  снятия через ``--fake``; ``list`` показывает её.
* ``SOURCES`` разделяется через ``os.pathsep``; несуществующий каталог —
  ошибка; ``MigrationsCollection`` принимает ``Path``.
* CLI создаёт коллекцию и мигратор лениво; ошибки печатаются одной строкой
  без трейсбека (кроме ``-vv``) и дают ненулевой код выхода.
* ``Migrator``: соединение закрывается даже при сбое взятия блокировки,
  ``ensure_migration_table`` вызывается один раз, а ошибки очистки при выходе
  не маскируют исходное исключение.
* ``Self`` берётся из ``typing`` на Python 3.11+; ``typing_extensions`` нужен
  только для Python < 3.11.
* Удалён мёртвый код; ``Migration`` и ``Hook`` экспортированы из пакета.

2.1.0
-----

* Добавлен класс ``CLI`` для встраивания в код приложения; исправлено
  освобождение advisory-lock.
* Исправлен отсутствующий импорт ``Settings``.
* CI: запуск тестов бэкендов и скрипт выдачи права ``DBMS_LOCK`` для Oracle.
* Исправлены тайпхинты для совместимости с Python 3.10; улучшено логирование.

2.0.0
-----

**This version introduces backwards incompatible changes.**

2.0.0 is a complete rewrite of the library. Only SQL migrations are supported;
Python-based migrations are removed. The public API is split into two layers
with no SQL outside the backends.

Public API
~~~~~~~~~~

* Rename ``Migrations`` to ``MigrationsCollection``. It now only reads
  migration sources (directories with ``.sql`` files) and has no access to the
  database. It takes the history event log and returns migrations and hooks.
* Add a new ``Migrator`` class: it takes database connection parameters,
  selects a backend by driver module name and executes migrations. Public
  methods: ``history()``, ``apply()``, ``rollback()``, ``close()``.
  ``Migrator`` contains no SQL — all queries live in the backend.
* Rename ``DatabaseBackend`` to ``Backend``.
* ``Migrator`` is a context manager: entering it opens the connection and takes
  an advisory lock; leaving it closes the connection.

Backends
~~~~~~~~

* Backends are one class per DBMS, registered in ``Backend.implementations``
  keyed by driver module name and selected via ``Backend.get_implementation``.
* The base ``Backend`` manages only connections/transactions and declares
  abstract query methods; each backend writes SQL in its native ``paramstyle``.
* Supported drivers: ``sqlite3``, ``psycopg``, ``pymysql``, ``pymssql``,
  ``oracledb``.
* Removed the ``odbc``, ``snowflake`` and ``redshift`` backends.

History table
~~~~~~~~~~~~~

* The migration history table is now a single append-only event log:
  ``(id, migration_id, created_at, status)`` where status is ``PENDING``,
  ``APPLIED`` or ``ROLLED_BACK``.
* Every apply/rollback appends an event row; the current status of a migration
  is determined by its latest event. Nothing is ever deleted.
* Remove content hash verification of applied migrations.
* Legacy ``versions`` table (yoyo-migrations) is copied once on first run as
  ``APPLIED`` events and is not deleted.

Hooks
~~~~~

* Add reserved hook files in the migration directory: ``pre-apply.sql``,
  ``post-apply.sql``, ``pre-rollback.sql``, ``post-rollback.sql``. They are not
  migrations, are never recorded in history and always run outside a
  transaction.

CLI
~~~

* CLI reduced to ``list`` (with ``--history``), ``apply`` and ``rollback``
  (both with an optional positional ``migration_name``, ``--fake`` and
  ``--plan``).
* Remove ``develop``, ``reapply``, ``mark``, ``unmark``, ``new`` and ``init``
  commands.
* The entry point is ``migrations = classic.migrations.cli:main``; settings are
  read from environment variables and a ``.env`` file.

Pre-2.0.0 (fork from yoyo-migrations)
-------------------------------------

classic-migrations is a fork of `yoyo-migrations 8.2.0
<https://pypi.org/project/yoyo-migrations/>`_ (initial fork commit
2024-09-05). Before 2.0.0 the project was released as a series of
``0.0.x``/``0.1b.x`` versions while the yoyo codebase was gradually reworked:

* Renamed the package from ``yoyo-migrations`` to ``classic-migrations`` and
  moved the sources under ``sources/``.
* Replaced the legacy yoyo configuration with environment-variable/``.env``
  based settings (pydantic-settings), including naming fixes, support for
  domain accounts, special characters in passwords and a schema name for the
  SQLite backend.
* Redesigned the backend layer around an abstract base class with per-DBMS
  implementations.
* Packaging and dependency fixes (setuptools, pydantic version bumps), version
  bumps from ``0.0.1`` to ``0.1b.1`` and experiments with Psycopg 3.
* Removed obsolete documentation and tidied up licenses.
