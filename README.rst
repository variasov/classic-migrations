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

Тот же интерфейс доступен как ``python -m classic.migrations``.


Настройка
---------

Все настройки читаются из переменных окружения или из ``.env`` файла
в текущем каталоге. Переменные окружения имеют приоритет над значениями
из ``.env``.

Формат ``.env`` — по одной записи ``КЛЮЧ=ЗНАЧЕНИЕ`` на строку. Пустые строки
и строки, начинающиеся с ``#``, игнорируются. Значения берутся как есть:
кавычки не снимаются, inline-комментарии после ``#`` не поддерживаются.

Пример ``.env``:

.. code-block:: ini

    # каталоги с файлами миграций; на Windows разделитель «;», на Unix «:»
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
     - пути к каталогам миграций, разделённые ``os.pathsep`` (``;`` на
       Windows, ``:`` на POSIX); каждый путь может быть glob-шаблоном
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
   * - ``MIGRATIONS_LOCK_TIMEOUT``
     - таймаут ожидания advisory-lock в секундах; пусто — ждать бесконечно,
       ``0`` — не ждать
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
* ``-- transactional: true|false`` — выполнять ли миграцию в транзакции.
  Если не указано — поведение определяется СУБД: ``true`` для SQLite,
  PostgreSQL и SQL Server (DDL транзакционен), ``false`` для MySQL и Oracle
  (DDL неявно коммитит).

Директивы разбираются строго: имя директивы регистронезависимо, но опечатка
или неизвестная директива в шапке файла — ошибка ``BadMigration``. Несколько
строк ``-- depends:`` объединяются в один список зависимостей.

Для отката миграции используется файл ``<имя>.rollback.sql`` — имя с вставкой
``.rollback`` перед расширением, размещённый рядом с основной миграцией.
У rollback-файла допускается только директива ``-- transactional``.

Правила имён файлов
~~~~~~~~~~~~~~~~~~~

* Идентификатор миграции — имя файла без расширения ``.sql``.
* Миграциями считаются только файлы с расширением ``.sql`` в нижнем регистре;
  ``*.SQL`` игнорируются.
* Дубликаты идентификаторов внутри источников — ошибка ``MigrationConflict``.
* Файлы ``*.rollback.sql`` не являются миграциями; ``.rollback.sql`` без
  основной миграции игнорируется.
* Зарезервированные имена ``pre-apply.sql``, ``post-apply.sql``,
  ``pre-rollback.sql``, ``post-rollback.sql`` — хуки, а не миграции.

Порядок и зависимости
~~~~~~~~~~~~~~~~~~~~~

Миграции выполняются в детерминированном топологическом порядке: каждая
миграция идёт после тех, от которых зависит. Среди готовых к выполнению
выбирается наименьший идентификатор (обычное строковое сравнение), поэтому
без зависимостей порядок — лексикографический по id. Порядок не определяется
номером в имени файла: чтобы задать последовательность, используйте
``-- depends``.

Аргумент ``migration_name`` («target») команд ``apply``/``rollback`` считается
по графу зависимостей, а не по позиции в списке:

* ``apply X`` — X и все её транзитивные зависимости (неприменённые);
  независимые миграции не затрагиваются;
* ``rollback X`` — X и все миграции, транзитивно зависящие от неё (применённые);
  независимые миграции не затрагиваются.

Хуки
~~~~

В каталоге миграций могут находиться зарезервированные файлы-хуки. Они не
являются миграциями, не попадают в таблицу истории и выполняются вне
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
  ``-v`` → INFO, ``-vv`` → DEBUG (по умолчанию WARNING).

``list``
~~~~~~~~

Показывает миграции источника и их статус в текущей БД:

.. code-block:: console

    migrations list

* ``--history`` — показать только миграции, затронувшие базу (статусы
  ``APPLIED`` и ``PENDING``).

Статусы в выводе: ``A`` — применена, ``P`` — зависла (``PENDING``: предыдущий
запуск упал посередине), ``U`` — не применена.

``apply``
~~~~~~~~~

Применяет неприменённые миграции (в топологическом порядке):

.. code-block:: console

    migrations apply [migration_name]

* ``migration_name`` — позиционный необязательный аргумент: применить миграции
  до указанной включительно (см. «Порядок и зависимости»); без него — все
  доступные;
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
  до указанной включительно (см. «Порядок и зависимости»); без него — все
  применённые;
* ``--fake`` — только создать записи в истории, без выполнения SQL отката
  и хуков;
* ``--plan`` — не откатывать миграции, а только вывести список тех, которые
  можно откатить в текущей БД.

Если у миграции нет ``.rollback.sql`` файла, при откате записывается только
событие ``ROLLED_BACK`` в историю — SQL не выполняется (в лог выводится
предупреждение).


Использование как библиотеки
----------------------------

Публичный API::

    from classic.migrations import (
        BadConnectionURI,
        BadMigration,
        CLI,
        Hook,
        Migration,
        MigrationConflict,
        MigrationLockError,
        MigrationPending,
        MigrationsCollection,
        Migrator,
        MissingSource,
        NoMigration,
        Settings,
    )

``MigrationsCollection`` работает только с источниками миграций (каталогами
``.sql`` файлов) и не имеет доступа к БД:

* ``MigrationsCollection(sources)`` — ``sources`` — путь к каталогу миграций
  (строка или ``os.PathLike``) или список путей; каждый путь может быть
  glob-шаблоном. Читает миграции и хуки один раз и переиспользует их при
  последующих вызовах;
* ``list()`` — список миграций источника в детерминированном топологическом
  порядке;
* ``to_apply(history, target=None, fake=False)`` — по логу событий истории
  вычисляет применённые и возвращает ``(hooks, migrations)``: хуки и
  неприменённые миграции; ``target`` — «до указанной включительно» (по графу
  зависимостей);
* ``to_rollback(history, target=None, fake=False)`` — аналогично, но
  возвращает применённые миграции в обратном топологическом порядке;
* ``applied_ids(history)`` — статический метод: возвращает множество id
  миграций, чей последний статус в истории — ``APPLIED``;
* ``statuses(history)`` — статический метод: последний статус каждой миграции.

Классы ``Migration`` и ``Hook`` возвращаются методами коллекции и могут
использоваться для интроспекции (``migration.id``, ``migration.depends``,
``migration.path``, ``hook.name``).

``Migrator`` принимает параметры БД, выбирает ``Backend`` по имени драйвера
и выполняет миграции (сам не содержит SQL). Параметры конструктора:

* ``driver`` — имя модуля драйвера (``sqlite3``, ``psycopg``, ``pymysql``,
  ``oracledb``, ``pymssql``);
* ``db_host``, ``db_port``, ``db_name``, ``db_user``, ``db_pass`` — параметры
  подключения;
* ``db_args=None`` — дополнительные именованные параметры, передаваемые в
  ``connect()`` драйвера (например ``sslmode``, ``connect_timeout``);
* ``lock_timeout=None`` — таймаут ожидания advisory-lock в секундах: ``None`` —
  ждать бесконечно, ``0`` — не ждать, ``N > 0`` — ждать до ``N`` секунд; при
  неудаче выбрасывается ``MigrationLockError``;
* ``migration_table='migrations'`` — имя таблицы истории;
* ``migration_schema=None`` — схема таблицы истории;
* ``versions_schema=None`` — схема legacy-таблицы ``versions``.

``Migrator`` — контекстный менеджер: при входе устанавливает соединение
и берёт advisory-lock, при выходе снимает его и закрывает соединение. Методы
должны вызываться только внутри ``with``:

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


CLI с готовыми коллекцией и мигратором
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

По умолчанию класс ``CLI`` создаёт коллекцию миграций и мигратор из настроек
окружения (как консольная команда ``migrations``). Если объекты уже собраны
в коде — например, пути к миграциям и параметры БД приходят из настроек
приложения, а не из переменных окружения — их можно передать напрямую:

.. code-block:: python

    from classic.migrations import CLI, MigrationsCollection, Migrator

    collection = MigrationsCollection(["./migrations", "./shared/migrations"])
    migrator = Migrator(
        driver="sqlite3",
        db_name="./db.sqlite",
        migration_table="my_migrations",
    )

    cli = CLI(collection=collection, migrator=migrator)

    cli(["list"])
    cli(["apply"])
    cli(["rollback", "20260822_02_users"])

``CLI(argv)`` разбирает аргументы так же, как консольная команда, и возвращает
код завершения: ``0`` — успех, ``1`` — ошибка (конфигурации, библиотеки или
драйвера БД) либо команда не указана, ``2`` — неверные аргументы командной
строки (ошибки парсера argparse). Каждый из аргументов ``collection`` и
``migrator`` необязателен: отсутствующий объект создаётся из настроек
окружения.


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


Сбои и PENDING
--------------

Если запуск упал посередине нетранзакционной миграции, её последним событием
в истории остаётся ``PENDING``: SQL мог быть выполнен частично, поэтому
библиотека не решает за вас, что делать дальше. Миграция в статусе ``PENDING``
блокирует ``apply``/``rollback`` (исключение ``MigrationPending``), а ``list``
показывает её как ``P``.

Чтобы снять пометку после ручного исправления базы, выполните одну из команд:

.. code-block:: console

    migrations apply --fake <id>
    migrations rollback --fake <id>

``--fake`` только дописывает событие в историю (``APPLIED`` или
``ROLLED_BACK``), не выполняя SQL.


Особенности бэкендов
--------------------

Схемы
~~~~~

* PostgreSQL и SQL Server при ``MIGRATIONS_SCHEMA`` сами создают схему, если
  её нет; Oracle схему не создаёт — её должен создать администратор.
* Схему объектов приложения библиотека не создаёт и ``search_path`` не меняет:
  это делает ваша миграция.
* Если ``MIGRATIONS_SCHEMA`` совпадает со схемой объектов приложения,
  ``CREATE SCHEMA <имя>`` в первой миграции упадёт — схема уже существует.

Блокировки и таймауты
~~~~~~~~~~~~~~~~~~~~~~

При входе в ``with migrator:`` берётся advisory-lock. Поведение при занятой
блокировке зависит от ``lock_timeout`` (в CLI — ``MIGRATIONS_LOCK_TIMEOUT``):

* ``None`` (по умолчанию) — ждать бесконечно;
* ``0`` — не ждать и сразу выбросить ``MigrationLockError``;
* ``N > 0`` — ждать до ``N`` секунд, затем ``MigrationLockError``.

Ключ блокировки привязан к имени таблицы истории (``MIGRATIONS_TABLE``).
Проекты с одинаковой таблицей истории и одной базой блокируют друг друга;
используйте разные ``MIGRATIONS_TABLE`` (или ``MIGRATIONS_SCHEMA``).

MySQL
~~~~~

DDL в MySQL неявно коммитит транзакцию, поэтому миграции по умолчанию
выполняются нетранзакционно (``transactional_ddl = False``): каждое выражение
и запись истории фиксируются сразу. Миграцию из чистого DML можно выполнить
в транзакции директивой ``-- transactional: true``.

SQLite
~~~~~~

Каждая миграция выполняется в собственной транзакции; межпроцессная
блокировка берётся через файл-спутник ``<db>.<migration_table>.lock``.
Хуки выполняются вне транзакции. Директива ``-- transactional: false``
выполняет миграцию в autocommit (это необходимо для ``VACUUM`` и подобных
команд).

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


Рецепты
-------

Запуск из кода в тестах (pytest)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Для интеграционных тестов удобно запускать миграции на временной базе SQLite
и в конце откатывать/чистить схему:

.. code-block:: python

    from classic.migrations import Migrator, MigrationsCollection

    def test_migrations(tmp_path):
        db = Migrator(
            driver="sqlite3",
            db_name=str(tmp_path / "db.sqlite"),
        )
        migrations = MigrationsCollection("./migrations")

        with db:
            history = db.history()
            hooks, unapplied = migrations.to_apply(history)
            db.apply(unapplied, hooks)

        # ... проверки ...

        with db:
            history = db.history()
            hooks, applied = migrations.to_rollback(history)
            db.rollback(applied, hooks)

Откат до конкретной миграции
~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: console

    migrations rollback 20260822_02_users

Откатываются указанная миграция и всё, что от неё зависит (по графу
зависимостей); независимые миграции остаются применёнными.

Альтернативный запуск CLI
~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: console

    python -m classic.migrations list
    python -m classic.migrations apply
    python -m classic.migrations rollback 20260822_02_users

Коды выхода
~~~~~~~~~~~

* ``0`` — успех;
* ``1`` — ошибка конфигурации, библиотеки или драйвера БД, либо команда не
  указана;
* ``2`` — неверные аргументы командной строки (ошибки парсера argparse).
