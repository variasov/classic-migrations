# Copyright 2015 Oliver Cope
# Copyright 2026 Sergey Variasov
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Command-line interface for the migrations tool."""

import argparse
import csv
import logging
import sys
import traceback
from typing import Any

from classic.migrations.backends.base import Backend
from classic.migrations.exceptions import (
    BadConnectionURI,
    BadMigration,
    InvalidArgument,
    MigrationConflict,
    MigrationLockError,
    MigrationPending,
    MissingSource,
    NoMigration,
)
from classic.migrations.migrations import MigrationsCollection
from classic.migrations.migrator import Migrator
from classic.migrations.settings import Settings

verbosity_levels = {
    0: logging.WARNING,
    1: logging.INFO,
    2: logging.DEBUG,
}

min_verbosity = min(verbosity_levels)
max_verbosity = max(verbosity_levels)


_LIBRARY_ERRORS = (
    BadConnectionURI,
    BadMigration,
    MigrationConflict,
    NoMigration,
    MigrationLockError,
    MigrationPending,
    MissingSource,
)


class _ConfigError(Exception):
    """Ошибка конфигурации (переменные окружения); используется только в CLI."""


def _is_driver_error(error: BaseException) -> bool:
    """Вернуть True, если ``error`` — ошибка одного из зарегистрированных драйверов."""
    for impl in Backend.implementations.values():
        driver = impl.driver
        error_type = getattr(driver, "Error", driver.DatabaseError)
        if isinstance(error, error_type):
            return True
    return False


class CLI:
    """Класс, запускающий миграции при запуске из CLI."""

    _collection: MigrationsCollection | None
    _migrator: Migrator | None

    def __init__(
        self,
        collection: MigrationsCollection | None = None,
        migrator: Migrator | None = None,
    ) -> None:
        """Инициализировать CLI.

        Если ``collection`` или ``migrator`` не переданы, они лениво
        создаются при первом обращении из настроек окружения
        (:class:`Settings`), поэтому ``--help`` работает без настроек.
        """
        self._collection = collection
        self._migrator = migrator

    @property
    def collection(self) -> MigrationsCollection:
        """Вернуть коллекцию миграций, создав её при первом обращении."""
        if self._collection is None:
            self._collection = self._make_collection()
        return self._collection

    @property
    def migrator(self) -> Migrator:
        """Вернуть мигратор; при первом обращении он создаётся из настроек."""
        if self._migrator is None:
            self._migrator = self._make_migrator()
        return self._migrator

    def build_parser(self) -> argparse.ArgumentParser:
        """Собрать и вернуть парсер аргументов командной строки."""
        parser = argparse.ArgumentParser(prog="migrations")
        common = argparse.ArgumentParser(add_help=False)
        common.add_argument(
            "-v",
            dest="verbosity",
            action="count",
            default=min_verbosity,
            help="Verbose output. Use multiple times to increase level of verbosity",
        )
        subparsers = parser.add_subparsers(dest="command", required=False)
        p = subparsers.add_parser("apply", parents=[common], help="Apply migrations")
        p.add_argument(
            "migration_name",
            nargs="?",
            default=None,
            help="Apply migrations up to and including this one",
        )
        p.add_argument(
            "--fake",
            action="store_true",
            help="Only create history records, without running the migrations",
        )
        p.add_argument(
            "--plan",
            action="store_true",
            help="Do not apply migrations, only list those that could be applied",
        )
        p.set_defaults(func=self._apply)

        p = subparsers.add_parser(
            "list",
            parents=[common],
            help="List all available and applied migrations",
        )
        p.add_argument(
            "--history",
            action="store_true",
            help="Show only applied and pending migrations",
        )
        p.set_defaults(func=self._list)

        p = subparsers.add_parser(
            "rollback",
            parents=[common],
            help="Rollback migrations",
        )
        p.add_argument(
            "migration_name",
            nargs="?",
            default=None,
            help="Rollback migrations up to and including this one",
        )
        p.add_argument(
            "--fake",
            action="store_true",
            help="Only create history records, without running the migrations",
        )
        p.add_argument(
            "--plan",
            action="store_true",
            help=(
                "Do not rollback migrations, only list those that could be rolled back"
            ),
        )
        p.set_defaults(func=self._rollback)
        return parser

    def _make_collection(self) -> MigrationsCollection:
        """Создать коллекцию миграций из каталогов-источников из настроек."""
        settings = Settings()
        sources = settings.sources_list
        if not sources:
            raise _ConfigError("SOURCES is not set")
        return MigrationsCollection(sources=sources)

    def _make_migrator(self) -> Migrator:
        """Создать мигратор на основе настроек окружения."""
        settings = Settings()
        if not settings.DATABASE_DRIVER:
            raise _ConfigError("DATABASE_DRIVER is not set")
        try:
            port = settings.DATABASE_PORT
        except ValueError as e:
            raise _ConfigError(f"DATABASE_PORT must be an integer ({e})") from e
        message = "MIGRATIONS_LOCK_TIMEOUT must be a non-negative number"
        try:
            lock_timeout = settings.MIGRATIONS_LOCK_TIMEOUT
        except ValueError as e:
            raise _ConfigError(f"{message} ({e})") from e
        if lock_timeout is not None and not lock_timeout >= 0:
            raise _ConfigError(f"{message} (got {lock_timeout:g})")
        return Migrator(
            driver=settings.DATABASE_DRIVER,
            db_host=settings.DATABASE_HOST,
            db_port=port,
            db_name=settings.DATABASE_NAME,
            db_user=settings.DATABASE_USER,
            db_pass=settings.DATABASE_PASSWORD,
            lock_timeout=lock_timeout,
            migration_table=settings.MIGRATIONS_TABLE,
            migration_schema=settings.MIGRATIONS_SCHEMA,
            versions_schema=settings.OLD_MIGRATIONS_SCHEMA,
        )

    def _write_csv(self, header: tuple[str, ...], rows: list[tuple[Any, ...]]) -> None:
        """Записать в stdout CSV-таблицу: заголовок ``header``, строки ``rows``."""
        writer = csv.writer(sys.stdout)
        writer.writerow(header)
        writer.writerows(rows)

    def _configure_logging(self, verbosity: int) -> None:
        """Настроить корневой логгер по выбранному уровню ``verbosity``."""
        logging.basicConfig(
            format="%(levelname)s %(name)s: %(message)s",
            level=verbosity_levels[verbosity],
            force=True,
        )

    def _print_migrations(self, migrations: list[Any]) -> None:
        """Вывести миграции в виде CSV-таблицы: идентификатор и каталог-источник."""
        ids = [m.id for m in migrations]
        sources = [getattr(m, "source_dir", "") or "" for m in migrations]
        self._write_csv(("ID", "SOURCE"), list(zip(ids, sources, strict=False)))

    def _apply(self, args: argparse.Namespace) -> int:
        """Применить ожидающие миграции до ``args.migration_name`` включительно."""
        collection = self.collection
        migrator = self.migrator
        with migrator:
            history = migrator.history()
            hooks, migrations = collection.to_apply(
                history,
                target=args.migration_name,
                fake=args.fake,
            )
            if args.plan:
                self._print_migrations(migrations)
            else:
                migrator.apply(migrations, hooks, fake=args.fake)
        return 0

    def _list(self, args: argparse.Namespace) -> int:
        """Вывести список миграций и их статус.

        A — применена, P — зависла (PENDING: предыдущий запуск упал посередине),
        U — не применена. При ``--history`` выводятся только A и P, то есть
        миграции, которые затронули базу.
        """
        collection = self.collection
        migrator = self.migrator
        with migrator:
            history = migrator.history()

        statuses = MigrationsCollection.statuses(history)
        migrations = collection.list()

        def letter(migration_id: str) -> str:
            status = statuses.get(migration_id)
            if status == "APPLIED":
                return "A"
            if status == "PENDING":
                return "P"
            return "U"

        rows = [(letter(m.id), m.id, m.source_dir or "") for m in migrations]
        if args.history:
            rows = [row for row in rows if row[0] in ("A", "P")]

        status, ids, sources = zip(*rows, strict=False) if rows else ((), (), ())

        if sys.stdout.isatty():
            colors = {"A": "\033[92m", "P": "\033[93m"}
            red = "\033[91m"
            status = [f"{colors.get(s, red)}{s}\033[0m" for s in status]

        self._write_csv(
            ("STATUS", "ID", "SOURCE"),
            list(zip(status, ids, sources, strict=False)),
        )
        return 0

    def _rollback(self, args: argparse.Namespace) -> int:
        """Откатить применённые миграции до ``args.migration_name`` включительно."""
        collection = self.collection
        migrator = self.migrator
        with migrator:
            history = migrator.history()
            hooks, migrations = collection.to_rollback(
                history,
                target=args.migration_name,
                fake=args.fake,
            )
            if args.plan:
                self._print_migrations(migrations)
            else:
                migrator.rollback(migrations, hooks, fake=args.fake)
        return 0

    def __call__(self, argv: list[str] | None = None) -> int:
        """Разобрать ``argv``, настроить логирование и выполнить команду.

        Возвращает код завершения: ``0`` — успех, ``1`` — ошибка или команда
        не указана, ``2`` — неверные аргументы (:exc:`InvalidArgument` и ошибки
        парсера; сообщение печатается через ``parser.error``).

        Ошибки конфигурации, исключения библиотеки и ошибки драйверов БД
        печатаются в stderr одной строкой ``error: ...`` (для ошибок драйвера
        — ``error: <ИмяКласса>: <сообщение>``) и дают код ``1``; при ``-vv``
        перед ней печатается полный traceback. Прочие исключения (ошибки в
        коде) не перехватываются.
        """
        parser = self.build_parser()
        args = parser.parse_args(argv)

        if not getattr(args, "func", None):
            parser.print_usage(sys.stderr)
            return 1

        verbosity = min(max_verbosity, max(min_verbosity, args.verbosity))
        self._configure_logging(verbosity)

        try:
            return args.func(args)
        except InvalidArgument as e:
            parser.error(e.args[0])
        except Exception as e:
            is_driver_error = _is_driver_error(e)
            if not (is_driver_error or isinstance(e, (_ConfigError, *_LIBRARY_ERRORS))):
                raise
            if verbosity >= max_verbosity:
                traceback.print_exc()
            message = str(e) or type(e).__name__
            if is_driver_error:
                message = f"{type(e).__name__}: {message}"
            sys.stderr.write(f"error: {message}\n")
            return 1


def main(argv: list[str] | None = None) -> int:
    """Разобрать ``argv`` и выполнить выбранную подкоманду."""
    cli = CLI()
    return cli(argv)
