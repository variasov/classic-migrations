import argparse
import logging
import os
import re
import sys

import tabulate
from classic.migrations import Migrations

verbosity_levels = {
    0: logging.ERROR,
    1: logging.WARNING,
    2: logging.INFO,
    3: logging.DEBUG,
}

min_verbosity = min(verbosity_levels)
max_verbosity = max(verbosity_levels)


class InvalidArgument(Exception):
    pass


def configure_logging(level):
    logging.basicConfig(level=verbosity_levels[level])


def build_parser():
    parser = argparse.ArgumentParser(prog="migrations")

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "-d",
        "--database",
        default=None,
        help="Database, eg 'sqlite:///path/to/sqlite.db' "
        "or 'postgresql://user@host/db'",
    )
    common.add_argument(
        "--migration-table",
        dest="migration_table",
        default=None,
        help="Name of table to use for storing migration metadata",
    )
    common.add_argument(
        "--schema",
        dest="schema",
        default=None,
        help="Schema for the migration history table",
    )
    common.add_argument(
        "-v",
        dest="verbosity",
        action="count",
        default=min_verbosity,
        help="Verbose output. Use multiple times to increase level of verbosity",
    )
    common.add_argument(
        "sources", nargs="*", help="Source directory of migration scripts"
    )

    subparsers = parser.add_subparsers(dest="command", required=False)

    def add_match(p):
        p.add_argument(
            "-m",
            "--match",
            help="Select migrations matching PATTERN (regular expression)",
            metavar="PATTERN",
        )

    def add_revision(p):
        p.add_argument(
            "-r",
            "--revision",
            help="Apply/rollback migration with id REVISION and all its dependencies",
            metavar="REVISION",
        )

    def add_all_flag(p):
        p.add_argument(
            "-a",
            "--all",
            dest="all",
            action="store_true",
            help="Select all migrations, regardless of whether "
            "they have been previously applied",
        )

    def add_force_flag(p):
        p.add_argument(
            "-f",
            "--force",
            dest="force",
            action="store_true",
            help="Force apply/rollback of steps even if previous steps have failed",
        )

    def add_skip_hash_check(p):
        p.add_argument(
            "--skip-hash-check",
            dest="skip_hash_check",
            action="store_true",
            help="Skip verification that applied migrations have not changed",
        )

    p = subparsers.add_parser("apply", parents=[common], help="Apply migrations")
    add_match(p)
    add_revision(p)
    add_all_flag(p)
    add_force_flag(p)
    add_skip_hash_check(p)
    p.add_argument(
        "-1",
        "--one",
        action="store_true",
        help="Apply a single migration. "
        "If there are no unapplied migrations, reapply the last migration",
    )
    p.set_defaults(func=cmd_apply)

    p = subparsers.add_parser(
        "develop",
        parents=[common],
        help="Apply migrations. "
        "If there are no unapplied migrations, reapply the last migration",
    )
    add_skip_hash_check(p)
    p.add_argument("-n", type=int, default=1, help="Act on the last N migrations")
    p.set_defaults(func=cmd_develop)

    p = subparsers.add_parser(
        "list",
        parents=[common],
        help="List all available and applied migrations",
    )
    add_match(p)
    p.set_defaults(func=cmd_list)

    p = subparsers.add_parser("rollback", parents=[common], help="Rollback migrations")
    add_match(p)
    add_revision(p)
    add_all_flag(p)
    add_force_flag(p)
    p.set_defaults(func=cmd_rollback)

    p = subparsers.add_parser(
        "reapply", parents=[common], help="Rollback then reapply migrations"
    )
    add_match(p)
    add_revision(p)
    add_force_flag(p)
    add_skip_hash_check(p)
    p.set_defaults(func=cmd_reapply)

    p = subparsers.add_parser(
        "mark",
        parents=[common],
        help="Mark migrations as applied, without running them",
    )
    add_match(p)
    add_revision(p)
    add_all_flag(p)
    p.set_defaults(func=cmd_mark)

    p = subparsers.add_parser(
        "unmark",
        parents=[common],
        help="Unmark applied migrations, without rolling them back",
    )
    add_match(p)
    add_revision(p)
    p.set_defaults(func=cmd_unmark)

    p = subparsers.add_parser("new", parents=[common], help="Create a new migration")
    p.add_argument("-m", "--message", default="", help="Message")
    p.set_defaults(func=cmd_new)

    p = subparsers.add_parser(
        "init", parents=[common], help="Initialize a new project"
    )
    p.set_defaults(func=cmd_init)

    return parser


def _make_migrations(args):
    return Migrations(
        source=args.sources or None,
        database=args.database,
        migration_table=args.migration_table,
        schema=args.schema,
    )


def cmd_apply(args):
    m = _make_migrations(args)
    m.apply(
        match=args.match,
        revision=args.revision,
        all=args.all,
        force=args.force,
        one=args.one,
        check_hashes=not args.skip_hash_check,
    )
    return 0


def cmd_develop(args):
    m = _make_migrations(args)
    m.develop(n=args.n, check_hashes=not args.skip_hash_check)
    return 0


def cmd_list(args):
    m = _make_migrations(args)
    rows = m.list()
    if args.match:
        search = re.compile(args.match).search
        rows = [r for r in rows if search(r[1])]

    status, ids, sources = zip(*rows) if rows else ((), (), ())

    if sys.stdout.isatty():
        status = [
            f"\033[92m{s}\033[0m" if s == "A"
            else f"\033[91m{s}\033[0m"
            for s in status
        ]

    print(
        tabulate.tabulate(
            zip(status, ids, sources), headers=("STATUS", "ID", "SOURCE")
        )
    )
    return 0


def cmd_rollback(args):
    m = _make_migrations(args)
    m.rollback(
        match=args.match,
        revision=args.revision,
        all=args.all,
        force=args.force,
    )
    return 0


def cmd_reapply(args):
    m = _make_migrations(args)
    m.reapply(
        match=args.match,
        revision=args.revision,
        force=args.force,
        check_hashes=not args.skip_hash_check,
    )
    return 0


def cmd_mark(args):
    m = _make_migrations(args)
    m.mark(match=args.match, revision=args.revision, all=args.all)
    return 0


def cmd_unmark(args):
    m = _make_migrations(args)
    m.unmark(match=args.match, revision=args.revision)
    return 0


def cmd_new(args):
    m = _make_migrations(args)
    filename = m.new(message=args.message)
    print("Created file", filename)
    return 0


def cmd_init(args):
    if not args.sources:
        raise InvalidArgument("Please specify a migrations directory")
    path = os.path.abspath(args.sources[0])
    os.makedirs(path, exist_ok=True)
    print(f"Created migrations directory {path}")
    return 0


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)

    if not getattr(args, "func", None):
        parser.print_usage(sys.stderr)
        return 1

    verbosity = min(max_verbosity, max(min_verbosity, args.verbosity))
    configure_logging(verbosity)

    try:
        return args.func(args)
    except InvalidArgument as e:
        parser.error(e.args[0])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
