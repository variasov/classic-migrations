# Copyright 2022 Oliver Cope
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

import pathlib
import logging
import sys

logger = logging.getLogger("classic.migrations")


def install_argparsers(global_parser, subparsers):
    parser = subparsers.add_parser(
        "init", parents=[global_parser], help="Initialize a new project"
    )
    parser.set_defaults(func=init)
    parser.add_argument("sources", nargs=1, help="Directory for migration scripts")
    parser.add_argument(
        "-d",
        "--database",
        help="Database, eg 'sqlite:///path/to/sqlite.db' "
        "or 'postgresql://user@host/db'",
    )


def init(args, config) -> int:
    migrations_path = pathlib.Path(args.sources[0]).resolve()
    if not migrations_path.exists():
        migrations_path.mkdir(exist_ok=True, parents=True)
    print(f"Created migrations directory {migrations_path}")
    return 0
