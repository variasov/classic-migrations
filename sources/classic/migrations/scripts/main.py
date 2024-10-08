# Copyright 2015 Oliver Cope
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


from getpass import getpass
import argparse
import configparser
import logging
import os
import sys
import typing as t

from classic.migrations import connections
from classic.migrations import default_migration_table
from classic.migrations import logger
from classic.migrations import utils
from classic.migrations.settings import Settings

settings = Settings()

verbosity_levels = {
    0: logging.ERROR,
    1: logging.WARN,
    2: logging.INFO,
    3: logging.DEBUG,
}

min_verbosity = min(verbosity_levels)
max_verbosity = max(verbosity_levels)

LEGACY_CONFIG_FILENAME = ".yoyo-migrate"


class InvalidArgument(Exception):
    pass


def parse_args(
    argv=None,
) -> t.Tuple[argparse.ArgumentParser, argparse.Namespace]:
    """
    Parse the config file and command line args.

    :return: tuple of ``(parsed config file, argument parser, parsed arguments)``
    """
    #: List of arguments whose defaults should be read from the config file
    config_args = {
        "batch_mode": "getboolean",
        "sources": "get",
        "database": "get",
        "verbosity": "getint",
        "migration_table": "get",
    }

    globalparser, argparser, subparsers = make_argparser()

    # Initial parse to extract --config and any global arguments
    global_args, _ = globalparser.parse_known_args(argv)

    # Now parse for real, starting from the top
    args = argparser.parse_args(argv)
    # Update the args namespace with the global args.
    # This ensures that global args (eg '-v) are recognized regardless
    # of whether they were placed before or after the subparser command.
    # If we do not do this then the sub_parser copy of the argument takes
    # precedence, and overwrites any global args set before the command name.
    args.__dict__.update(globalparser.parse_known_args(argv)[0].__dict__)

    return argparser, args


def make_argparser():
    """
    Return a top-level ArgumentParser parser object,
    plus a list of sub_parsers
    """
    global_parser = argparse.ArgumentParser(add_help=False)
    global_parser.add_argument(
        "--config", "-c", default=None, help="Path to config file"
    )
    global_parser.add_argument(
        "-v",
        dest="verbosity",
        action="count",
        default=min_verbosity,
        help="Verbose output. Use multiple times to increase level of verbosity",
    )
    global_parser.add_argument(
        "-b",
        "--batch",
        dest="batch_mode",
        action="store_true",
        default=(not sys.stdout.isatty()),
        help="Run in batch mode" ". Turns off all user prompts",
    )

    # global_parser.add_argument(
    #     "-s",
    #     "--source",
    #     dest="sources",
    #     help="Source directory of migration scripts",
    # )

    global_parser.add_argument(
        "--no-config-file",
        "--no-cache",
        dest="use_config_file",
        action="store_false",
        default=True,
        help="Don't look for a migrations.ini config file",
    )
    argparser = argparse.ArgumentParser(prog="migrations", parents=[global_parser])

    subparsers = argparser.add_subparsers(help="Commands help")

    from . import migrate
    from . import newmigration
    from . import init

    init.install_argparsers(global_parser, subparsers)
    migrate.install_argparsers(global_parser, subparsers)
    newmigration.install_argparsers(global_parser, subparsers)

    return global_parser, argparser, subparsers


def configure_logging(level):
    """
    Configure the python logging module with the requested loglevel
    """
    logging.basicConfig(level=verbosity_levels[level])


def prompt_save_config(config, path):
    # Offer to save the current configuration for future runs
    # Don't cache anything in batch mode (because we can't prompt to find the
    # user's preference).

    # if utils.confirm(
    #     "Save migration configuration to {}?\n"
    #     "This is saved in plain text and "
    #     "contains your database password.\n\n"
    #     "Answering 'y' means you do not have to specify "
    #     "the migration source or database connection "
    #     "for future runs".format(path)
    # ):
    #     save_config(config, path)
    ...

def upgrade_legacy_config(args, config, sources):

    for dir in reversed(sources):
        path = os.path.join(dir, LEGACY_CONFIG_FILENAME)
        if not os.path.isfile(path):
            continue

        legacy_config = read_config(path)

        def transfer_setting(oldname, newname, transform=None, section="DEFAULT"):
            try:
                config.get(section, newname)
            except configparser.NoOptionError:
                try:
                    value = legacy_config.get(section, oldname)
                except configparser.NoOptionError:
                    pass
                else:
                    if transform:
                        value = transform(value)
                    config.set(section, newname, value)

        transfer_setting("dburi", "database")
        transfer_setting(
            "migration_table",
            "migration_table",
            lambda v: (default_migration_table if v == "None" else v),
        )

        config_path = args.config or CONFIG_FILENAME
        if not args.batch_mode:
            if utils.confirm(
                "Move legacy configuration in {!r} to {!r}?".format(path, config_path)
            ):
                save_config(config, config_path)
                try:
                    if utils.confirm(
                        "Delete legacy configuration file {!r}".format(path)
                    ):
                        os.unlink(path)
                except OSError:
                    logger.warn(
                        "Could not remove %r. Manually remove this file "
                        "to avoid future warnings",
                        path,
                    )
                return True
        else:
            logger.warn(
                "Found legacy configuration in %r. Run "
                "yoyo in interactive mode to update your "
                "configuration files",
                path,
            )

            try:
                args.database = args.database or legacy_config.get("DEFAULT", "dburi")
            except configparser.NoOptionError:
                pass
            try:
                if not vars(args).get("migration_table"):
                    args.migration_table = legacy_config.get(
                        "DEFAULT", "migration_table"
                    )
            except configparser.NoOptionError:
                pass

        return False



def get_backend(args, settings:Settings):


    dburi = getattr(args, 'database', settings.DATABASE)
    dburi = dburi or settings.DATABASE
    if dburi is None:
        raise InvalidArgument("Please specify a database uri")
    migration_table = default_migration_table
    try:
        migration_table = args.migration_table
    except AttributeError:
        pass

    if len(migration_table)==0:
        migration_table = (
            settings.VERSION_TABLE
            if len(settings.VERSION_TABLE) == 0
            else default_migration_table
        )

    try:
        if args.prompt_password:
            password = getpass("Password for %s: " % dburi)
            parsed = connections.parse_uri(dburi)
            dburi = parsed._replace(password=password).uri
    except AttributeError:
        pass

    return connections.get_backend(dburi, migration_table)


def main(argv=None):

    argparser, args = parse_args(argv)
    if getattr(args, "func", None) is None:
        argparser.print_usage(sys.stderr)
        argparser.exit(1)

    args.batch_mode = settings.BATCH_MODE
    verbosity = args.verbosity
    verbosity = min(max_verbosity, max(min_verbosity, verbosity))
    configure_logging(verbosity)

    try:
        if vars(args).get("func"):
            exitcode = args.func(args, settings) #config
    except InvalidArgument as e:
        argparser.error(e.args[0])

    return exitcode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
