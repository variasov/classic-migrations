# Задачи: приведение кода и тестов к обновлённой spec.md

## Принятые решения по неоднозначностям spec (согласовано с пользователем)

1. Противоречие схем истории (spec.md:22-25 против spec.md:59) разрешено в
   пользу раздела «История миграций»: столбцы `id` (PK, нужен для порядка
   событий), `migration_id`, `created_at`, `status`. Столбца `comment` нет;
   строка события в логе — `(migration_id, created_at, status)`. Директива
   `-- comment:` удаляется из кода и тестов.
2. `target`/`migration_name` — точное совпадение с названием миграции;
   не найдено — исключение `NoMigration` (частичный матч подстрокой
   убирается вместе с `_to_revision`).
3. Семантика «до target включительно»:
   - `to_apply` — неприменённые миграции в топологическом порядке до target
     включительно (префикс порядка); target применён — пустой список;
   - `to_rollback` — применённые миграции с конца топологического порядка
     до target включительно, в обратном порядке (суффикс порядка);
     target не применён — пустой список.
4. rollback-файл `<name>.rollback.sql` имеет собственную директиву
   `transactional` (true/false/None, как у основной); другие директивы в
   rollback-файле — ошибка `BadMigration`.
5. Откат нетранзакционной миграции симметричен apply: событие `PENDING`
   перед выполнением SQL, `ROLLED_BACK` — после успеха.
6. fake-режим apply/rollback: одна финальная запись (`APPLIED`/`ROLLED_BACK`)
   без `PENDING`, без SQL миграций и без хуков (в spec не оговорено).

## Задачи

- [ ] 1. Исключение `NoMigration`
  - Добавить класс в `sources/classic/migrations/exceptions.py`, экспортировать
    в `sources/classic/migrations/__init__.py` (`__all__`).
  - Использовать для: неизвестного имени в `depends`; ненайденного target
    в `to_apply`/`to_rollback`.

- [ ] 2. Директивы миграций (`sources/classic/migrations/migrations.py`)
  - `depends`: имена через запятую, пробелы обрезаются — сейчас сплит по
    пробелам (migrations.py:107).
  - Неизвестная зависимость → `NoMigration` (сейчас `BadMigration`,
    migrations.py:214).
  - `transactional`: тип `bool | None`; нет директивы — `None`; `"true"`/`"false"`
    → True/False; иное значение — `BadMigration` (migrations.py:108-115).
    Дефолт `True` (migrations.py:91) убрать.
  - Удалить директиву `comment`: из `directive_names` (migrations.py:44) и
    атрибут `Migration.comment` (migrations.py:92, 116).
  - rollback-файл: читать его директивы; допустима только `transactional`
    (три состояния), другие директивы — `BadMigration`; хранить отдельно,
    напр. `Migration.rollback_transactional` (migrations.py:105, 121-123).

- [ ] 3. Таблица истории и события (бэкенды)
  - Схема: `id` PK, `migration_id`, `created_at`, `status`; статусы
    `'PENDING' | 'APPLIED' | 'ROLLED_BACK'` заглавными буквами. Константы
    статусов (напр. в `backends/base.py`).
  - `SQLiteBackend`: `_create_migration_table`/`_copy_versions`/SELECT'ы —
    `applied_at` → `created_at`, убрать `comment`, заглавные статусы
    (sqlite3.py:43-92).
  - Заменить `mark_applied`/`unmark` на один метод записи события
    `mark(migration_id, status)`; убрать параметры `comment`/`applied_at`
    (base.py:166-178, sqlite3.py:78-92) — `created_at` генерирует бэкенд.
  - `_migration_history` возвращает кортежи `(migration_id, created_at, status)`.
  - Удалить неиспользуемые методы: `Backend.applied_migrations` (base.py:196),
    `Backend.is_applied` (base.py:215), абстрактный `_applied_migrations`
    (base.py:160) и все его реализации (sqlite3.py:60, fake.py:177,
    core/psycopg.py, core/postgresql.py, core/mysql.py, contrib/*).
  - Механически привести непроверяемые бэкенды (`core/psycopg.py`,
    `core/postgresql.py`, `core/mysql.py`, `contrib/*.py`) к новой схеме и
    методам (SQL не проверяется, драйверы не подключаются).

- [ ] 4. Поддержка Transactional DDL
  - `ClassVar transactional_ddl: bool` в `Backend` (base.py);
    `SQLiteBackend` — `True`.

- [ ] 5. `Migrator`: транзакции и события (`sources/classic/migrations/migrator.py`)
  - Эффективная транзакционность: `migration.transactional`, если задан,
    иначе `backend.transactional_ddl` (для apply — `transactional`,
    для rollback — `rollback_transactional` из задачи 2).
  - apply transactional: statements + событие `APPLIED` в одной транзакции
    (сейчас история пишется после коммита, migrator.py:112-116, 147-154).
  - apply нетранзакционный: вне транзакции; событие `PENDING` → statements →
    событие `APPLIED` после успеха.
  - rollback: симметрично — транзакционный: statements + `ROLLED_BACK` в одной
    транзакции; нетранзакционный: `PENDING` → statements → `ROLLED_BACK`
    (migrator.py:120-142, 156-165).
  - fake=True: только одна финальная запись `APPLIED`/`ROLLED_BACK`.
  - Хуки: выполнять без обёртки в транзакцию; убрать использование
    `Hook.transactional` (migrator.py:174-188).
  - Обновить docstrings: строка истории `(migration_id, created_at, status)`
    (migrator.py:91).

- [ ] 6. `MigrationsCollection`: target (`sources/classic/migrations/migrations.py`)
  - `to_apply`: точный target (задача 1 п.2); неприменённые в топологическом
    порядке до target включительно (migrations.py:299-318).
  - `to_rollback`: применённые с конца топологического порядка до target
    включительно, обратный порядок (migrations.py:320-342).
  - Удалить ставшие неиспользуемыми `_ancestors`, `_descendants`,
    `_to_revision` (migrations.py:227-270).
  - `_applied_ids`: строки `(migration_id, created_at, status)`, статус
    `'APPLIED'` (migrations.py:275-286).

- [ ] 7. CLI (`sources/classic/migrations/cli.py`)
  - `cmd_list`: работает с новым форматом строки истории через
    `_applied_ids` (cli.py:152); вывод A/U без изменений.

- [ ] 8. FakeBackend (`tests/backends/fake.py`)
  - События `(migration_id, created_at, status)`; метод `mark(migration_id,
    status)`; `applied_list` — по последнему событию `'APPLIED'`.
  - Удалить `_applied_migrations` (fake.py:177-185).
  - Добавить журнал операций (`begin`/`execute`/`mark`/`commit`) для проверки
    порядка «SQL и событие в одной транзакции» в тестах Migrator.

- [ ] 9. Тесты
  - `tests/test_migrations.py`: depends через запятую; `NoMigration`
    (зависимость и target); `transactional` None/true/false, некорректное
    значение; rollback-файл со своей `transactional` и запретом других
    директив; семантика target префикс/суффикс; удалить comment-тесты
    (test_migrations.py:259-273).
  - `tests/test_migrator.py`: формат событий; порядок `PENDING` → SQL →
    `APPLIED` для нетранзакционных; `APPLIED` внутри транзакции для
    транзакционных (через журнал операций FakeBackend); rollback
    симметрично; fake; использовать `Migrator(driver='fake')` без патча
    `get_implementation` (test_migrator.py:18-25).
  - `tests/test_integration.py`: сценарии со статусами
    `PENDING`/`APPLIED`/`ROLLED_BACK`, нетранзакционные миграции.
  - `tests/backends/test_sqlite3.py`: новая схема (`created_at`, без
    `comment`), заглавные статусы, `PENDING`; тесты `_applied_migrations`/
    `is_applied` переписать на `migration_history()` (test_sqlite3.py:34-100,
    243-245); `_copy_versions`.
  - `tests/test_cli.py`: строки истории 3-элементные, статусы заглавные
    (test_cli.py:204, 226).
  - `tests/conftest.py`: удалить неиспользуемую фикстуру `db_path`
    (conftest.py:22-24).

- [ ] 10. Проверка
  - `uv run ruff check sources/ tests/`
  - `uv run pyright`
  - `uv run pytest`
