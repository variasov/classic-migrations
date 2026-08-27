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

import os
import re
from collections import OrderedDict
from collections.abc import Iterable
from glob import glob
from graphlib import CycleError, TopologicalSorter
from logging import getLogger

import sqlparse
from classic.migrations import exceptions

logger = getLogger("classic.migrations")

HOOK_NAMES = ("pre-apply", "post-apply", "pre-rollback", "post-rollback")

DirectivesType = dict[str, str]


def _is_migration_file(path: str) -> bool:
    _, extension = os.path.splitext(path)
    return extension == ".sql"


def parse_metadata_from_sql_comments(s: str) -> tuple[DirectivesType, str]:
    directive_names = ["transactional", "depends"]
    comment_or_empty = re.compile(r"^(\s*|\s*--.*)$").match
    directive_pattern = re.compile(
        r"^\s*--\s*({})\s*:\s*(.*)$".format("|".join(map(re.escape, directive_names)))
    )

    lineending = re.search(r"\n|\r\n|\r", s + "\n").group(0)  # type: ignore
    lines = iter(s.split(lineending))
    directives: DirectivesType = {}
    sql: list[str] = []
    for line in lines:
        match = directive_pattern.match(line)
        if match:
            k, v = match.groups()
            if k in directives:
                directives[k] += f" {v}"
            else:
                directives[k] = v
        elif comment_or_empty(line):
            continue
        else:
            sql.append(line)
            break
    sql.extend(lines)
    return directives, lineending.join(sql)


def read_sql(path: str) -> tuple[DirectivesType, list[str]]:
    directives: DirectivesType = {}
    statements = []
    if os.path.exists(path):
        with open(path, "r", encoding="UTF-8") as f:
            statements = sqlparse.split(f.read())
            if statements:
                directives, sql = parse_metadata_from_sql_comments(statements[0])
                statements[0] = sql
    statements = [s for s in statements if s.strip()]
    return directives, statements


class Migration:

    def __init__(self, id: str, path: str, source_dir: str | None = None) -> None:
        self.id = id
        self.path = path
        self.source_dir = source_dir
        self.depends: set[str] = set()
        self.transactional: bool | None = None
        self.rollback_transactional: bool | None = None
        self.apply_statements: list[str] = []
        self.rollback_statements: list[str] = []
        self._loaded = False

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__} {self.id!r} from {self.path}>"

    def load(self) -> None:
        if self._loaded:
            return

        directives, statements = read_sql(self.path)
        rb_directives, rollback_statements = read_sql(self._rollback_path())

        self.depends = {d.strip() for d in directives.get("depends", "").split(",") if d.strip()}
        transactional_raw = directives.get("transactional")
        if transactional_raw is None:
            self.transactional = None
        elif transactional_raw.lower() in {"true", "false"}:
            self.transactional = transactional_raw.lower() == "true"
        else:
            raise exceptions.BadMigration(
                f"Invalid transactional directive {transactional_raw!r} in {self.path}"
            )

        self._load_rollback_directives(rb_directives)

        self.apply_statements = statements
        self.rollback_statements = rollback_statements
        self._loaded = True

    def _load_rollback_directives(self, rb_directives: DirectivesType) -> None:
        for k in rb_directives:
            if k != "transactional":
                raise exceptions.BadMigration(
                    f"Invalid directive {k!r} in rollback file {self._rollback_path()}"
                )
        transactional_raw = rb_directives.get("transactional")
        if transactional_raw is None:
            self.rollback_transactional = None
        elif transactional_raw.lower() in {"true", "false"}:
            self.rollback_transactional = transactional_raw.lower() == "true"
        else:
            raise exceptions.BadMigration(
                f"Invalid transactional directive {transactional_raw!r} in rollback file {self._rollback_path()}"
            )

    def _rollback_path(self) -> str:
        base, ext = os.path.splitext(self.path)
        return base + ".rollback" + ext


class Hook:

    def __init__(self, name: str, path: str) -> None:
        self.name = name
        self.path = path
        self.statements: list[str] = []
        self._loaded = False

    def load(self) -> None:
        if self._loaded:
            return
        _directives, statements = read_sql(self.path)
        self.statements = statements
        self._loaded = True


class MigrationsCollection:
    """Collection of migrations read from one or more source directories."""

    def __init__(self, sources: str | list[str]):
        if not sources:
            raise ValueError("sources must not be empty")
        self.sources = sources
        self._migrations: list[Migration] | None = None
        self._hooks: dict[str, list[Hook]] | None = None

    def _sources(self) -> list[str]:
        if isinstance(self.sources, str):
            return [self.sources]
        return list(self.sources)

    def _expand_sources(self) -> Iterable[str]:
        for source in self._sources():
            for directory in glob(source):
                if os.path.isdir(directory):
                    yield directory

    def _read_migrations(self) -> list[Migration]:
        migrations: OrderedDict[str, Migration] = OrderedDict()
        for directory in self._expand_sources():
            for filename in sorted(os.listdir(directory)):
                if not _is_migration_file(filename):
                    continue
                if filename.endswith(".rollback.sql"):
                    continue
                basename = os.path.splitext(filename)[0]
                if basename in HOOK_NAMES:
                    continue
                if basename in migrations:
                    raise exceptions.MigrationConflict(basename)
                path = os.path.join(directory, filename)
                migrations[basename] = Migration(basename, path, source_dir=directory)
        migrations_list = list(migrations.values())
        for m in migrations.values():
            m.load()
        return migrations_list

    def _read_hooks(self) -> dict[str, list[Hook]]:
        hooks: dict[str, list[Hook]] = {name: [] for name in HOOK_NAMES}
        for directory in self._expand_sources():
            for name in HOOK_NAMES:
                path = os.path.join(directory, name + ".sql")
                if os.path.isfile(path):
                    hooks[name].append(Hook(name, path))
        return hooks

    def _topological(self, migrations: Iterable[Migration]) -> list[Migration]:
        ml = list(migrations)
        all_ids = {m.id for m in ml}
        by_id = {m.id: m for m in ml}
        for m in ml:
            for d in m.depends:
                if d not in all_ids:
                    raise exceptions.NoMigration(
                        f"Could not resolve dependency {d!r} in {m.path}"
                    )
        dependency_graph = {m: {by_id[d] for d in m.depends} for m in ml}
        try:
            return list(TopologicalSorter(dependency_graph).static_order())
        except CycleError as e:
            raise exceptions.BadMigration(
                "Circular dependencies among these migrations {}".format(
                    ", ".join(m.id for m in e.args[1])
                )
            )

    @staticmethod
    def applied_ids(history: Iterable[tuple[str, ...]]) -> set[str]:
        latest: dict[str, str] = {}
        for row in history:
            latest[row[0]] = row[2]
        return {migration_id for migration_id, status in latest.items() if status == "APPLIED"}

    def list(self) -> list[Migration]:
        if self._migrations is None:
            migrations = self._read_migrations()
            self._migrations = self._topological(migrations)
        return self._migrations

    def _get_hooks(self) -> dict[str, list[Hook]]:
        if self._hooks is None:
            self._hooks = self._read_hooks()
        return self._hooks

    def to_apply(
        self,
        history: Iterable[tuple[str, ...]],
        target: str | None = None,
    ) -> tuple[dict[str, list[Hook]], list[Migration]]:
        migrations = self.list()
        applied = self.applied_ids(history)
        result = [m for m in migrations if m.id not in applied]
        if target is not None:
            target_ids = {m.id for m in migrations}
            if target not in target_ids:
                raise exceptions.NoMigration(f"Migration {target!r} not found")
            target_idx = next((i for i, m in enumerate(migrations) if m.id == target), -1)
            keep_ids = {m.id for m in migrations[: target_idx + 1]}
            result = [m for m in result if m.id in keep_ids]
        hooks = self._get_hooks()
        return hooks, result

    def to_rollback(
        self,
        history: Iterable[tuple[str, ...]],
        target: str | None = None,
    ) -> tuple[dict[str, list[Hook]], list[Migration]]:
        migrations = self.list()
        applied = self.applied_ids(history)
        reversed_migrations = list(reversed(migrations))
        result = [m for m in reversed_migrations if m.id in applied]
        if target is not None:
            target_ids = {m.id for m in migrations}
            if target not in target_ids:
                raise exceptions.NoMigration(f"Migration {target!r} not found")
            target_idx = next((i for i, m in enumerate(migrations) if m.id == target), -1)
            if target not in {m.id for m in result}:
                result = []
            else:
                descendants: set[str] = {target}
                for m in migrations[target_idx:]:
                    descendants.add(m.id)
                result = [m for m in result if m.id in descendants]
        hooks = self._get_hooks()
        return hooks, result
