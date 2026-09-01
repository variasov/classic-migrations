# AGENTS.md

Инструкции для агентов, работающих с этим репозиторием.

## Проект

`classic-migrations` — библиотека SQL-миграций (форк yoyo-migrations).
Версия **2.0.0**: только SQL-миграции, разделение на `MigrationsCollection`
(чтение источников) и `Migrator` (выполнение через `Backend`), append-only
таблица истории, pre/post-хуки.

## Ограничения

- **Нельзя ничего коммитить в git.** Только рабочие изменения в дереве; никаких
  `git commit`/`git push`. Не создавай коммиты даже по просьбе.
- **Нельзя ставить новые пакеты.** Если пакет нужен — спроси у пользователя.

## Команды

Зависимости и окружение — через `uv` (`uv.lock` в корне), группа зависимостей
`dev` (содержит pytest, pytest-cov, ruff, pyright, драйверы СУБД).

Проект собирается из `sources/` (корневой `pyproject.toml`,
`package-dir = {"" = "sources"}`), точка входа
`migrations = classic.migrations.cli:main`.

Проверка изменений выполняется командами:

```powershell
uv run ruff check sources/ tests/
uv run pyright
uv run pytest
```

Настройки ruff — в `[tool.ruff]` (`pyproject.toml`); pyright — в
`[tool.pyright]` и `[tool.pyright.executionEnvironments]`; покрытие —
плагин pytest-cov, настройки в `[tool.pytest.ini_options]` (запускается
сам `pytest`, см. `addopts`).

## Структура

```
sources/classic/migrations/
    __init__.py                        # публичный API: MigrationsCollection, Migrator, исключения, __version__
    migrations.py                      # MigrationsCollection + Migration/Hook + чтение/парсинг SQL
    migrator.py                        # Migrator: lock/history/apply/rollback, БЕЗ SQL
    cli.py                             # argparse + main(): list/apply/rollback
    settings.py                        # Settings (чтение из os.environ и .env)
    exceptions.py                      # исключения: BadMigration, MigrationConflict, BadConnectionURI,
                                       # InvalidArgument, MigrationLockError, NoMigration
    backends/
        __init__.py                    # регистрация бэкендов (опциональный импорт)
        base.py                        # Backend: оркестрация, БЕЗ SQL-запросов
        sqlite3.py                     # SQLiteBackend
        psycopg.py                     # PsycopgBackend
        pymysql.py                     # PyMySQLBackend
        pymssql.py                     # PyMSSQLBackend
        oracle.py                      # OracleBackend
tests/
    conftest.py                        # фикстура source (tmp_path) + get_credentials
    test_cli.py                        # тесты CLI: парсинг + cmd_* с Mock(Migrator)/Mock(MigrationsCollection)
    test_migrations.py                 # тесты MigrationsCollection без бэкенда
    test_migrator.py                   # тесты Migrator с FakeBackend
    test_integration.py                # публичный API на реальной SQLite в RAM
    backends/
        fake.py                        # FakeBackend (тестовый, регистрируется как driver 'fake')
        test_sqlite3.py                # тесты SQLiteBackend с реальной SQLite БД в RAM
        test_psycopg.py                # тесты PsycopgBackend на реальном PostgreSQL (креды PG_DATABASE_*)
        test_mysql.py                  # тесты PyMySQLBackend (креды MYSQL_DATABASE_*)
        test_oracle.py                 # тесты OracleBackend (креды ORACLE_DATABASE_*)
        test_pymssql.py                # тесты PyMSSQLBackend (креды MS_SQL_DATABASE_*)
```

## Ключевые архитектурные правила

1. **Разделение слоёв.** `MigrationsCollection` не имеет доступа к БД:
   принимает историю (лог событий) и возвращает миграции/хуки. `Migrator`
   не содержит SQL — работает через `Backend`. CLI собирает их вместе
   внутри `with migrator:` (вход в контекст берёт advisory-lock):
   `history()` → `to_apply`/`to_rollback` → `apply`/`rollback`.

2. **Бэкенды — отдельные классы, по файлу на СУБД.** `Migrator` выбирает
   бэкенд по имени драйвера через реестр
   `Backend.implementations` (ключ — `driver.__name__` в `__init_subclass__`)
   и класс-метод `Backend.get_implementation`. Базовый `Backend`
   **не содержит SQL** — только управление соединением/транзакциями и
   абстрактные методы запросов. Каждый бэкенд пишет SQL в своём нативном
   `paramstyle`. Доступные драйверы: `sqlite3`, `psycopg`, `pymysql`,
   `pymssql`, `oracledb` (+ тестовый `fake`).

3. **Одна таблица истории** `{migration_table}` — append-only лог событий:
   `id` (PK), `migration_id`, `created_at`, `status`
   (`PENDING` | `APPLIED` | `ROLLED_BACK`). Каждое apply/rollback дописывает
   событие; актуальный статус миграции — её последнее событие. Старая таблица
   `versions` при первом запуске переносится (`_copy_versions`) как события
   `APPLIED` и не удаляется. Сверка хешей не выполняется (удалена в 2.0.0).

4. **Хуки** — зарезервированные имена `pre-apply.sql`, `post-apply.sql`,
   `pre-rollback.sql`, `post-rollback.sql` в каталоге миграций. Не являются
   миграциями, не попадают в историю. Читаются `MigrationsCollection`,
   выполняются `Migrator` (`pre-apply`/`post-apply` в `apply`,
   `pre-rollback`/`post-rollback` в `rollback`).

5. **FakeBackend** (`tests/backends/fake.py`) — тестовый инструмент для
   `Migrator`: хранит лог событий в памяти, предоставляет `applied_list`,
   `events` и другие методы проверки состояния. Регистрируется через
   `driver=_FakeDriver` (имя `fake`); в тестах `Migrator(driver='fake')`
   выбирает его.

## Конвенции кода

- Python ≥ 3.10, современный синтаксис (`str | None`, аннотации типов).
- Миграционные SQL-файлы в тестах создаются вспомогательной функцией
  `write_file` (в `tests/test_migrations.py`, `test_migrator.py`,
  `test_integration.py` и др.).

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
  (хуки → statements → события истории → хуки), fake-режим, лок,
  история, жизненный цикл.

- **`tests/test_integration.py`** — сценарий публичного API на реальной SQLite в RAM.

- **`tests/backends/test_*.py`** — тесты каждого бэкенда на реальной БД.
  `test_sqlite3.py` работает на SQLite в RAM (`:memory:`); остальные
  (`test_psycopg.py`, `test_mysql.py`, `test_oracle.py`, `test_pymssql.py`)
  пропускаются (`pytest.skip`) при отсутствии реальной СУБД — креды берутся
  из `tests/.env` через `get_credentials` с префиксами `PG_`, `MYSQL_`,
  `ORACLE_`, `MS_SQL_`. Проверяется append-only события вместо удаления,
  вычисление текущего статуса, порядок, `_copy_versions`, лок и транзакции.

Фикстура `source` и `get_credentials` — в `tests/conftest.py`.

## Проверка изменений

После правок обязательно выполнить команды из раздела «Команды». Все тесты
должны проходить, ruff и pyright — без ошибок. Новую функциональность
покрывай тестами в соответствующем слое по образцу существующих.
