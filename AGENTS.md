# AGENTS.md

Инструкции для агентов, работающих с этим репозиторием.

## Проект

`classic-migrations` — библиотека SQL-миграций (форк yoyo-migrations). Идёт
переписывание на новую major-версию **2.0.0**: только SQL-миграции, единый
класс `Migrations`, одна служебная таблица истории, pre/post-хуки и сверка
хешей применённых миграций.

## Источник правды

Спецификация лежит в `spec/` и является главным источником требований:

- `spec/spec-2.0.0.md` — полная спецификация новой версии;
- `spec/implementation-notes.md` — рабочие заметки: что реализовано,
  отклонения от спецификации и принятые решения по сигнатурам.

Прежде чем менять поведение, сверяйся со спецификацией. Любое расхождение
между кодом и спекой — баг или осознанное отклонение (фиксируется в
`implementation-notes.md`).

## Ограничения

- **Нельзя ничего коммитить в git.** Только рабочие изменения в дереве; никаких
  `git commit`/`git push`. Не создавай коммиты даже по просьбе.
- **Работает только backend SQLite.** Остальные СУБД (`postgresql`, `mysql`,
  `pymssql`, `odbc`, `oracle`, `redshift`, `snowflake`) будут добавлены позже.
  Не подключай и не полагайся на их драйверы; их SQL-запросы не проверены.

## Команды

Зависимости и окружение — через `uv` (`uv.lock` в корне), dev-группа
`pytest`. Запуск тестов:

```powershell
uv run pytest
```

Отдельный тест:

```powershell
uv run pytest tests/test_migrations.py::TestApply::test_apply_calls_backend_sequence
```

Линтер — **ruff**, тайпчекер — **pyright**. Конфигурация в `pyproject.toml`
(ruff) и `pyrightconfig.json`. Запуск:

```powershell
uv run ruff check sources/ tests/
uv run pyright
```

Тесты тоже должны проходить линтер и тайпчекинг без ошибок.

Пакет ставится из `sources/` (`setup.py`, `package_dir={'': 'sources'}`),
точка входа `migrations = classic.migrations.cli:main`.

## Структура

```
spec/                                  # спецификация (см. выше)
sources/classic/migrations/
    __init__.py                        # публичный API: Migrations, исключения, __version__
    migrations.py                      # класс Migrations + чтение/парсинг SQL + сверка хешей
    cli.py                             # argparse + main() (отдельно от Migrations)
    settings.py                        # Settings (pydantic-settings, .env)
    exceptions.py                      # BadMigration, MigrationConflict, MigrationHashMismatch, BadConnectionURI
    utils.py                           # slugify, get_random_string, unidecode
    backends/
        base.py                        # DatabaseBackend: оркестрация, БЕЗ SQL-запросов
        core/sqlite3.py                # SQLiteBackend — единственный работающий бэкенд
        core/postgresql.py             # не проверены
        core/mysql.py                  # не проверены
        contrib/*.py                   # pymssql/odbc/oracle/redshift/snowflake — не проверены
tests/
    conftest.py                        # фикстуры source (tmp_path) и db_path (tmp_path)
    test_cli.py                        # тесты CLI: парсинг аргументов + cmd_* с Mock(Migrations)
    test_migrations.py                 # тесты Migrations с Mock(DatabaseBackend)
    backends/
        test_sqlite3.py                # тесты SQLiteBackend с реальной SQLite БД
```

## Ключевые архитектурные правила

1. **Публичный API** — в `__init__.py`: `Migrations`, `BadMigration`,
   `MigrationConflict`, `MigrationHashMismatch`, `__version__`. Больше ничего
   публичного (`read_migrations`, `get_backend` и т.п. удалены).

2. **Бэкенды — отдельные классы, по файлу на СУБД.** `Migrations` выбирает
   бэкенд по имени драйвера через реестр
   `DatabaseBackend.implementations` (ключ — `driver.__name__` в `__init_subclass__`;
   сейчас только `sqlite3`) и класс-метод `DatabaseBackend.get_backend_class`.
   Базовый `DatabaseBackend` **не содержит
   SQL** — только управление соединением/транзакциями и абстрактные методы
   запросов. Каждый бэкенд пишет SQL в своём нативном `paramstyle`.

3. **Одна таблица истории** `{schema}.{migration_table}`:
   `migration_id` (PK), `content_hash`, `applied_at`, `comment`. Старая
   таблица `versions` при первом запуске переносится (`_copy_versions`) и не
   удаляется; `history`/`lock` игнорируются.

4. **Сверка хешей.** `content_hash` = sha256 тела миграции после парсинга
   (без блока директив `-- depends:/-- transactional:/-- comment:`). При
   `apply`/`develop`/`reapply` сверяются все миграции источника, уже
   присутствующие в истории; `NULL` в `content_hash` = сверка отключена.

5. **Хуки** — зарезервированные имена `pre-apply.sql`, `post-apply.sql`,
   `pre-rollback.sql`, `post-rollback.sql` в каталоге миграций. Не являются
   миграциями, не попадают в историю, не участвуют в сверке.

6. **Batch-режим всегда.** Интерактивные вопросы, `BATCH_MODE`, `--batch`,
   `break-lock` удалены. Блокировка — нативная, сессионная (SQLite: удержание
   write-транзакции `BEGIN IMMEDIATE` в `SQLiteBackend.lock`).

## Конвенции кода

- Python ≥ 3.10, современный синтаксис (`str | None`, аннотации типов).
- Существующий код частично унаследован от yoyo-migrations: попадаются
  `str.format(...)` вместо f-строк и строковые докстрины. Новый код пиши в том
  же стиле, что и окружающий, но f-строки допустимы.
- Комментарии в коде — только где действительно нужны (не добавляй лишних).
- Миграционные SQL-файлы в тестах создаются вспомогательной функцией
  `write_file` (в `tests/test_migrations.py`).

## Структура тестов

Тесты разделены по слоям:

- **`tests/test_cli.py`** — только парсинг аргументов CLI и вызовы `cmd_*`.
  `Migrations` подменяется `Mock`, проверяется что вызываются правильные
  методы с правильными параметрами (`assert_called_once_with(...)`).
  Настоящие миграции и БД не используются.

- **`tests/test_migrations.py`** — тесты класса `Migrations`.
  `DatabaseBackend.get_backend_class` патчится, возвращая `Mock`-бэкенд.
  Проверяется последовательность вызовов (`lock` → `ensure_migration_table` →
  `mark_applied`/`unmark` → `close`), фильтрация (`match`, `revision`, `all`,
  `one`), сверка хешей, разрешение зависимостей.

- **`tests/backends/test_sqlite3.py`** — тесты `SQLiteBackend` с реальной
  SQLite БД (через `tmp_path`). Проверяется создание таблицы, `mark_applied` /
  `unmark` / `is_applied`, порядок выборки, `_copy_versions`, лок и
  транзакции. Для каждого будущего бэкенда должен быть аналогичный файл
  в `tests/backends/`.

Фикстуры `source` и `db_path` — в `tests/conftest.py`.

## Проверка изменений

После правок обязательно:

```powershell
uv run ruff check sources/ tests/
uv run pyright
uv run pytest
```

Все тесты должны проходить, ruff и pyright — без ошибок. Новую
функциональность покрывай тестами в соответствующем слое по образцу существующих.
