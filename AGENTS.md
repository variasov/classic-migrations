# AGENTS.md

Инструкции для агентов, работающих с этим репозиторием.

## Проект

`classic-migrations` — библиотека SQL-миграций (форк yoyo-migrations).
Версия **2.0.0**: только SQL-миграции, разделение на `MigrationsCollection`
(чтение источников) и `Migrator` (выполнение через `Backend`), append-only
таблица истории, pre/post-хуки. Спецификация — `spec.md`, план работ —
`tasks.md` (в конец задач дописывается статус/пометка о выполнении).

## Ограничения

- **Нельзя ничего коммитить в git.** Только рабочие изменения в дереве; никаких
  `git commit`/`git push`. Не создавай коммиты даже по просьбе.
- **Работает только backend SQLite.** Остальные СУБД (`postgresql`, `mysql`,
  `psycopg`, `pymssql`, `odbc`, `oracle`, `redshift`, `snowflake`) будут
  добавлены позже. Не подключай и не полагайся на их драйверы; их SQL-запросы
  не проверены (в ruff для них есть исторические замечания, в pyright они
  исключены).
- **Нельзя ставить новые пакеты.** Если пакет нужен — спроси у пользователя.
- **Тесты `SQLiteBackend` используют SQLite в RAM** (`db_name=":memory:"`).

## Команды

Зависимости и окружение — через `uv` (`uv.lock` в корне), dev-группа
`pytest`. Запуск тестов:

```powershell
uv run pytest
```

Отдельный тест:

```powershell
uv run pytest tests/test_migrator.py
```

Линтер — **ruff**, тайпчекер — **pyright**. Конфигурация в `pyproject.toml`
(ruff) и `pyrightconfig.json`. Запуск:

```powershell
uv run ruff check sources/ tests/
uv run pyright
```

Пакет ставится из `sources/` (`pyproject.toml`, `package_dir={'': 'sources'}`),
точка входа `migrations = classic.migrations.cli:main`.

## Структура

```
spec.md                                # спецификация (не менять)
tasks.md                               # задачи рефакторинга (менять только статусы)
sources/classic/migrations/
    __init__.py                        # публичный API: MigrationsCollection, Migrator, исключения, __version__
    migrations.py                      # MigrationsCollection + Migration/Hook + чтение/парсинг SQL
    migrator.py                        # Migrator: lock/history/apply/rollback, БЕЗ SQL
    cli.py                             # argparse + main(): list/apply/rollback
    settings.py                        # Settings (чтение из os.environ и .env)
    exceptions.py                      # BadMigration, MigrationConflict, BadConnectionURI, InvalidArgument
    backends/
        base.py                        # Backend: оркестрация, БЕЗ SQL-запросов
        core/sqlite3.py                # SQLiteBackend — единственный работающий бэкенд
        core/psycopg.py                # не проверен
        core/postgresql.py             # не проверен
        core/mysql.py                  # не проверен
        contrib/*.py                   # pymssql/odbc/oracle/redshift/snowflake — не проверены
tests/
    conftest.py                        # фикстура source (tmp_path)
    test_cli.py                        # тесты CLI: парсинг + cmd_* с Mock(Migrator)/Mock(MigrationsCollection)
    test_migrations.py                 # тесты MigrationsCollection без бэкенда
    test_migrator.py                   # тесты Migrator с FakeBackend
    test_integration.py                # публичный API на реальной SQLite в RAM
    backends/
        fake.py                        # FakeBackend (тестовый, регистрируется как driver 'fake')
        test_sqlite3.py                # тесты SQLiteBackend с реальной SQLite БД в RAM
```

## Ключевые архитектурные правила

1. **Разделение слоёв.** `MigrationsCollection` не имеет доступа к БД:
   принимает историю (лог событий) и возвращает миграции/хуки. `Migrator`
   не содержит SQL — работает через `Backend`. CLI собирает их вместе:
   `lock()` → `history()` → `to_apply`/`to_rollback` → `apply`/`rollback`.

2. **Бэкенды — отдельные классы, по файлу на СУБД.** `Migrator` выбирает
   бэкенд по имени драйвера через реестр
   `Backend.implementations` (ключ — `driver.__name__` в `__init_subclass__`;
   сейчас `sqlite3` и тестовый `fake`) и класс-метод
   `Backend.get_implementation`. Базовый `Backend` **не содержит SQL** —
   только управление соединением/транзакциями и абстрактные методы
   запросов. Каждый бэкенд пишет SQL в своём нативном `paramstyle`.

3. **Одна таблица истории** `{migration_table}` — append-only лог событий:
   `id` (PK), `migration_id`, `applied_at`, `comment`, `status`
   (`applied` | `rolled_back`). Каждое apply/rollback дописывает событие;
   актуальный статус миграции — её последнее событие. Старая таблица
   `versions` при первом запуске переносится (`_copy_versions`) как события
   `applied` и не удаляется. Сверка хешей не выполняется (удалена в 2.0.0).

4. **Хуки** — зарезервированные имена `pre-apply.sql`, `post-apply.sql`,
   `pre-rollback.sql`, `post-rollback.sql` в каталоге миграций. Не являются
   миграциями, не попадают в историю. Читаются `MigrationsCollection`,
   выполняются `Migrator` (`pre-apply`/`post-apply` в `apply`,
   `pre-rollback`/`post-rollback` в `rollback`).

5. **FakeBackend** (`tests/backends/fake.py`) — тестовый инструмент для
   `Migrator`: хранит лог событий в памяти, предоставляет `applied_list`,
   `events` и другие методы проверки состояния. Регистрируется через
   `driver=_FakeDriver` (имя `fake`), в тестах `Migrator(driver='fake')`
   выбирает его без патчинга реестра.

## Конвенции кода

- Python ≥ 3.10, современный синтаксис (`str | None`, аннотации типов).
- Миграционные SQL-файлы в тестах создаются вспомогательной функцией
  `write_file` (в `tests/test_migrations.py` и других тест-файлах).

## Структура тестов

Тесты разделены по слоям:

- **`tests/test_cli.py`** — только парсинг аргументов CLI и вызовы `cmd_*`.
  `Migrator` и `MigrationsCollection` подменяются `Mock`, проверяется что
  вызываются правильные методы с правильными параметрами
  (`assert_called_once_with(...)`). Настоящие миграции и БД не используются.

- **`tests/test_migrations.py`** — чистые тесты `MigrationsCollection`
  без бэкенда: `list()`, `to_apply`/`to_rollback` (target включительно,
  топологическая сортировка, разрешение зависимостей, циклы, дубликаты id),
  чтение хуков, парсинг директив.

- **`tests/test_migrator.py`** — тесты `Migrator` с `FakeBackend`
  (`tests/backends/fake.py`, `Migrator(driver='fake')`): последовательность
  (хуки → statements → события истории → хуки), fake-режим, передача lock,
  история, жизненный цикл.

- **`tests/test_integration.py`** — сценарий публичного API из spec.md на
  реальной SQLite в RAM.

- **`tests/backends/test_sqlite3.py`** — тесты `SQLiteBackend` с реальной
  SQLite БД в RAM (`:memory:`). Проверяется append-only события вместо
  удаления, вычисление текущего статуса, порядок, `_copy_versions`, лок и
  транзакции. Для каждого будущего бэкенда должен быть аналогичный файл
  в `tests/backends/`.

Фикстура `source` — в `tests/conftest.py`.

## Проверка изменений

После правок обязательно:

```powershell
uv run ruff check sources/ tests/
uv run pyright
uv run pytest
```

Все тесты должны проходить, ruff и pyright — без ошибок (исторические
замечания ruff в непроверяемых бэкендах `contrib/`, `core/mysql.py`,
`core/postgresql.py` — исключение). Новую функциональность покрывай тестами
в соответствующем слое по образцу существующих.
