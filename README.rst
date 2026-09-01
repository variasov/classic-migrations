==================
classic-migrations
==================

Библиотека SQL-миграций для баз данных. Миграции — это обычные ``.sql`` файлы.

Требования
----------

* Python >= 3.10.
* Драйвер целевой СУБД (см. таблицу ниже). Для SQLite ничего дополнительно
  устанавливать не нужно — используется модуль стандартной библиотеки
  ``sqlite3``.

Установка
---------

Базовый пакет (включает только поддержку SQLite):

.. code-block:: console

    pip install classic-migrations

Поддержка конкретных СУБД подключается опциональными зависимостями (extras):

.. list-table::
   :header-rows: 1

   * - СУБД
     - extra
     - драйвер (значение ``DATABASE_DRIVER``)
   * - SQLite
     - —
     - ``sqlite3``
   * - PostgreSQL
     - ``postgres``
     - ``psycopg``
   * - MySQL
     - ``mysql``
     - ``pymysql``
   * - Oracle
     - ``oracle``
     - ``oracledb``
   * - Microsoft SQL Server
     - ``pymssql``
     - ``pymssql``

.. code-block:: console

    pip install classic-migrations[postgres]
    pip install classic-migrations[mysql]

Или через ``uv``:

.. code-block:: console

    uv add classic-migrations
    uv add "classic-migrations[postgres]"

Пакет предоставляет исполняемую команду ``migrations``:

.. code-block:: console

    migrations --help


Настройка
---------

Все настройки читаются из переменных окружения или из ``.env`` файла
в текущем каталоге. Переменные окружения имеют приоритет над значениями
из ``.env``.

Пример ``.env``:

.. code-block:: ini

    # каталоги с файлами миграций, разделённые двоеточием
    SOURCES=./migrations

    # имя таблицы истории (опционально, по умолчанию "migrations")
    MIGRATIONS_TABLE=migrations

    # настройки подключения к базе данных
    DATABASE_DRIVER=sqlite3
    DATABASE_USER=
    DATABASE_USER_DOMAIN=
    DATABASE_PASSWORD=
    DATABASE_HOST=
    DATABASE_PORT=
    DATABASE_NAME=./db.sqlite

Если задан ``DATABASE_USER_DOMAIN``, то имя пользователя формируется как
``DOMAIN\USER``.

Полный список настроек:

.. list-table::
   :header-rows: 1

   * - Переменная
     - Назначение
     - Значение по умолчанию
   * - ``SOURCES``
     - пути к каталогам миграций, разделённые двоеточием
     - *(пусто)*
   * - ``DATABASE_DRIVER``
     - имя модуля драйвера подключения (``sqlite3``, ``psycopg``, ``pymysql``,
       ``oracledb``, ``pymssql``)
     - *(пусто)*
   * - ``DATABASE_USER``
     - имя пользователя БД
     - *(пусто)*
   * - ``DATABASE_USER_DOMAIN``
     - домен пользователя БД (опционально)
     - *(пусто)*
   * - ``DATABASE_PASSWORD``
     - пароль
     - *(пусто)*
   * - ``DATABASE_HOST``
     - хост
     - *(пусто)*
   * - ``DATABASE_PORT``
     - порт (преобразуется в целое число)
     - *(пусто)*
   * - ``DATABASE_NAME``
     - имя БД (или путь к файлу для SQLite)
     - *(пусто)*
   * - ``MIGRATIONS_TABLE``
     - имя таблицы истории
     - ``migrations``
   * - ``MIGRATIONS_SCHEMA``
     - схема, в которой размещается таблица истории (игнорируется для БД без
       поддержки схем)
     - *(пусто)*
   * - ``OLD_MIGRATIONS_SCHEMA``
     - схема legacy-таблицы ``versions`` (yoyo-migrations)
     - *(пусто)*


Файлы миграций
--------------

Миграция — это ``.sql`` файл в каталоге миграций. Имя файла (без расширения)
становится идентификатором миграции.

В начальном комментарии файла можно указать директивы:

.. code-block:: sql

    -- depends: 20260821_01_init, 20260822_02_users
    -- transactional: true

    CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT);

Директивы:

* ``-- depends: <id>, <id>, ...`` — список миграций, от которых зависит
  текущая, через запятую. Имена должны соответствовать реальным миграциям,
  иначе выбрасывается исключение ``NoMigration``;
* ``-- transactional: true|false`` — выполнять ли миграцию в транзакции
  (по умолчанию ``true``; если не указано — поведение определяется
  возможностями СУБД).

Для отката миграции используется файл ``<имя>.rollback.sql`` — имя с вставкой
``.rollback`` перед расширением, размещённый рядом с основной миграцией.
У rollback-файла допускается только директива ``-- transactional``.

Хуки
~~~~

В каталоге миграций могут находиться зарезервированные файлы-хуки. Они не
являются миграциями, не попадают в таблицу истории и всегда выполняются вне
транзакции:

* ``pre-apply.sql`` — выполняется перед применением набора миграций;
* ``post-apply.sql`` — после применения набора миграций;
* ``pre-rollback.sql`` — перед откатом набора миграций;
* ``post-rollback.sql`` — после отката набора миграций.


Команды
-------

Доступные команды: ``list``, ``apply``, ``rollback``.

Общий параметр, принимаемый всеми командами:

* ``-v`` — увеличить подробность вывода. Можно повторять:
  ``-v`` → WARNING, ``-vv`` → INFO, ``-vvv`` → DEBUG (по умолчанию ERROR).

``list``
~~~~~~~~

Показывает миграции источника и их статус в текущей БД:

.. code-block:: console

    migrations list

* ``--history`` — показать только применённые миграции.

``apply``
~~~~~~~~~

Применяет неприменённые миграции (в топологическом порядке):

.. code-block:: console

    migrations apply [migration_name]

* ``migration_name`` — позиционный необязательный аргумент: применить миграции
  до указанной включительно; без него — все доступные;
* ``--fake`` — только создать записи в истории, без выполнения SQL миграций
  и хуков;
* ``--plan`` — не применять миграции, а только вывести список тех, которые
  можно применить к текущей БД.

``rollback``
~~~~~~~~~~~~

Откатывает применённые миграции (в обратном топологическом порядке):

.. code-block:: console

    migrations rollback [migration_name]

* ``migration_name`` — позиционный необязательный аргумент: откатить миграции
  до указанной включительно; без него — все применённые;
* ``--fake`` — только создать записи в истории, без выполнения SQL отката
  и хуков;
* ``--plan`` — не откатывать миграции, а только вывести список тех, которые
  можно откатить в текущей БД.

Если у миграции нет ``.rollback.sql`` файла, при откате записывается только
событие ``ROLLED_BACK`` в историю — SQL не выполняется.


Использование как библиотеки
----------------------------

Публичный API::

    from classic.migrations import (
        BadConnectionURI,
        BadMigration,
        InvalidArgument,
        MigrationConflict,
        MigrationLockError,
        MigrationsCollection,
        Migrator,
        NoMigration,
    )

``MigrationsCollection`` работает только с источниками миграций (каталогами
``.sql`` файлов) и не имеет доступа к БД:

* ``MigrationsCollection(sources)`` — ``sources`` — путь к каталогу миграций
  (строка) или список путей. Читает миграции и хуки один раз и переиспользует
  их при последующих вызовах;
* ``list()`` — список миграций источника, отсортированный топологически;
* ``to_apply(history, target=None)`` — по логу событий истории вычисляет
  применённые и возвращает ``(hooks, migrations)``: хуки и неприменённые
  миграции в топологическом порядке; ``target`` — «до указанной включительно»;
* ``to_rollback(history, target=None)`` — аналогично, но возвращает
  применённые миграции в обратном топологическом порядке;
* ``applied_ids(history)`` — статический метод: возвращает множество id
  миграций, чей последний статус в истории — ``APPLIED``.

``Migrator`` принимает параметры БД, выбирает ``Backend`` по имени драйвера
и выполняет миграции (сам не содержит SQL). Параметры конструктора:

* ``driver`` — имя модуля драйвера (``sqlite3``, ``psycopg``, ``pymysql``,
  ``oracledb``, ``pymssql``);
* ``db_host``, ``db_port``, ``db_name``, ``db_user``, ``db_pass`` — параметры
  подключения;
* ``migration_table='migrations'`` — имя таблицы истории;
* ``migration_schema=None`` — схема таблицы истории;
* ``versions_schema=None`` — схема legacy-таблицы ``versions``.

``Migrator`` — контекстный менеджер: при входе устанавливает соединение
и берёт advisory-lock, при выходе закрывает соединение. Методы должны
вызываться только внутри ``with``:

* ``history()`` — лог событий таблицы истории (список кортежей
  ``(migration_id, created_at, status)``);
* ``apply(migrations, hooks, fake=False)`` — применяет миграции; ``fake=True``
  — только записи в истории, без SQL и хуков;
* ``rollback(migrations, hooks, fake=False)`` — откатывает миграции
  (аналогично ``apply``);
* ``close()`` — закрывает соединение (обычно не нужен — соединение
  закрывается при выходе из ``with``).

Применение миграций:

.. code-block:: python

    from classic.migrations import Migrator, MigrationsCollection

    db = Migrator(
        driver='psycopg',
        db_host='localhost',
        db_port=5432,
        db_name='tests',
        db_user='test',
        db_pass='test',
    )
    migrations = MigrationsCollection('./migrations')

    with db:
        history = db.history()
        hooks, unapplied = migrations.to_apply(history)
        db.apply(unapplied, hooks)

Откат миграций:

.. code-block:: python

    with db:
        history = db.history()
        hooks, applied = migrations.to_rollback(history)
        db.rollback(applied, hooks)


Схема таблицы истории
---------------------

Библиотека создаёт одну служебную таблицу — append-only лог событий:

.. code-block:: sql

    CREATE TABLE {migration_table} (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        migration_id VARCHAR(255) NOT NULL,
        created_at   TIMESTAMP NOT NULL,
        status       VARCHAR(16) NOT NULL  -- 'APPLIED' | 'ROLLED_BACK' | 'PENDING'
    );

Каждое применение или откат миграции дописывает строку-событие. Актуальный
статус миграции определяется её последним событием. Сверка хешей применённых
миграций не выполняется.

Для нетранзакционных СУБД запись в историю производится до применения
(статус ``PENDING``), а после успешного выполнения — ``APPLIED``.

При первом запуске данные из legacy-таблицы ``versions`` (yoyo-migrations)
переносятся как события ``APPLIED``; сама таблица не удаляется.


Особенности бэкендов
--------------------

Oracle
~~~~~~

Бэкенд Oracle берёт advisory-lock через ``SYS.DBMS_LOCK.REQUEST``. Пользователю,
под которым выполняются миграции, необходимо право ``EXECUTE`` на
``SYS.DBMS_LOCK``; без него вход в ``with migrator:`` завершится ошибкой
``MigrationLockError``.

В контейнерных образах ``gvenzl/oracle-*`` это право выдаётся скриптом
инициализации ``docker/oracle-init/01_grant_dbms_lock.sql``. Локально он
монтируется в ``/container-entrypoint-initdb.d`` (см. ``docker-compose.yml``)
и выполняется при первом старте контейнера. В CI (job ``test-oracle`` в
``.github/workflows/test.yml``) скрипт выполняется после checkout через
``docker exec``, поскольку сервис-контейнеры GitHub Actions стартуют до
checkout и не позволяют смонтировать каталог из репозитория.