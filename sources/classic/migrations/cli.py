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
from typing import Any

from classic.migrations.exceptions import InvalidArgument
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


def build_parser() -> argparse.ArgumentParser:
    """Build and return the argument parser for the CLI."""
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
    p.set_defaults(func=cmd_apply)

    p = subparsers.add_parser(
        "list",
        parents=[common],
        help="List all available and applied migrations",
    )
    p.add_argument(
        "--history",
        action="store_true",
        help="Show only applied migrations",
    )
    p.set_defaults(func=cmd_list)

    p = subparsers.add_parser("rollback", parents=[common], help="Rollback migrations")
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
        help="Do not rollback migrations, only list those that could be rolled back",
    )
    p.set_defaults(func=cmd_rollback)

    return parser


def _make_collection() -> MigrationsCollection:
    settings = Settings()
    return MigrationsCollection(sources=settings.sources_list)


def _make_migrator() -> Migrator:
    settings = Settings()
    return Migrator(
        driver=settings.DATABASE_DRIVER,
        db_host=settings.DATABASE_HOST,
        db_port=settings.DATABASE_PORT,
        db_name=settings.DATABASE_NAME,
        db_user=settings.DATABASE_USER,
        db_pass=settings.DATABASE_PASSWORD,
        migration_table=settings.MIGRATIONS_TABLE,
        migration_schema=settings.MIGRATIONS_SCHEMA,
        versions_schema=settings.OLD_MIGRATIONS_SCHEMA,
    )


def _write_csv(header: tuple[str, ...], rows: list[tuple[Any, ...]]) -> None:
    writer = csv.writer(sys.stdout)
    writer.writerow(header)
    writer.writerows(rows)


def _configure_logging(verbosity: int) -> None:
    """Configure the root logger with the level selected by ``verbosity``."""
    logging.basicConfig(
        format="%(levelname)s %(name)s: %(message)s",
        level=verbosity_levels[verbosity],
        force=True,
    )


def _print_migrations(migrations: list[Any]) -> None:
    ids = [m.id for m in migrations]
    sources = [getattr(m, "source_dir", "") or "" for m in migrations]
    _write_csv(("ID", "SOURCE"), list(zip(ids, sources, strict=False)))


def cmd_apply(args: argparse.Namespace) -> int:
    """Apply pending migrations up to and including ``args.migration_name``."""
    collection = _make_collection()
    migrator = _make_migrator()
    with migrator:
        history = migrator.history()
        hooks, migrations = collection.to_apply(history, target=args.migration_name)
        if args.plan:
            _print_migrations(migrations)
        else:
            migrator.apply(migrations, hooks, fake=args.fake)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """List migrations and their applied/unapplied status."""
    collection = _make_collection()
    migrator = _make_migrator()
    with migrator:
        history = migrator.history()

    applied = MigrationsCollection.applied_ids(history)
    migrations = collection.list()

    if args.history:
        rows = [("A", m.id, m.source_dir or "") for m in migrations if m.id in applied]
    else:
        rows = [
            ("A" if m.id in applied else "U", m.id, m.source_dir or "")
            for m in migrations
        ]

    status, ids, sources = zip(*rows, strict=False) if rows else ((), (), ())

    if sys.stdout.isatty():
        status = [
            f"\033[92m{s}\033[0m" if s == "A" else f"\033[91m{s}\033[0m" for s in status
        ]

    _write_csv(
        ("STATUS", "ID", "SOURCE"),
        list(zip(status, ids, sources, strict=False)),
    )
    return 0


def cmd_rollback(args: argparse.Namespace) -> int:
    """Rollback applied migrations down to and including ``args.migration_name``."""
    collection = _make_collection()
    migrator = _make_migrator()
    with migrator:
        history = migrator.history()
        hooks, migrations = collection.to_rollback(history, target=args.migration_name)
        if args.plan:
            _print_migrations(migrations)
        else:
            migrator.rollback(migrations, hooks, fake=args.fake)
    return 0


def main(argv: list[str] | None = None) -> int:
    """Parse ``argv`` and dispatch to the requested subcommand."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "func", None):
        parser.print_usage(sys.stderr)
        return 1

    verbosity = min(max_verbosity, max(min_verbosity, args.verbosity))
    _configure_logging(verbosity)

    try:
        return args.func(args)
    except InvalidArgument as e:
        parser.error(e.args[0])
