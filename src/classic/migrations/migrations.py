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

"""Classes for reading migration files and computing apply/rollback plans."""

from __future__ import annotations

import heapq
import re
from collections import OrderedDict
from glob import glob
from graphlib import CycleError, TopologicalSorter
from logging import getLogger
from pathlib import Path
from typing import TYPE_CHECKING

import sqlparse

from classic.migrations import exceptions

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = getLogger("classic.migrations")

HOOK_NAMES = ("pre-apply", "post-apply", "pre-rollback", "post-rollback")

DirectivesType = dict[str, str]


def parse_metadata_from_sql_comments(s: str) -> tuple[DirectivesType, str]:
    """Extract migration directives from the leading comment block of ``s``."""
    directive_names = ["transactional", "depends"]
    comment_or_empty = re.compile(r"^(\s*|\s*--.*)$").match
    directive_pattern = re.compile(
        r"^\s*--\s*({})\s*:\s*(.*)$".format(
            "|".join(map(re.escape, directive_names)),
        ),
    )

    match = re.search(r"\n|\r\n|\r", s + "\n")
    lineending = "\n" if match is None else match.group(0)
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
    """Read ``path``, returning its directives and SQL statements."""
    directives: DirectivesType = {}
    statements: list[str] = []
    sql_path = Path(path)
    if sql_path.exists():
        with sql_path.open(encoding="UTF-8") as f:
            statements = sqlparse.split(f.read())
            if statements:
                directives, sql = parse_metadata_from_sql_comments(statements[0])
                statements[0] = sql
    statements = [s for s in statements if s.strip()]
    return directives, statements


class Migration:
    """A single migration file with its directives and SQL statements."""

    def __init__(self, id: str, path: str, source_dir: str | None = None) -> None:
        """Initialize a migration with its id and SQL file path."""
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
        """Return a developer-friendly representation."""
        return f"<{self.__class__.__name__} {self.id!r} from {self.path}>"

    def load(self) -> None:
        """Load the SQL and directives for this migration (idempotent)."""
        if self._loaded:
            return

        directives, statements = read_sql(self.path)
        rb_directives, rollback_statements = read_sql(self.rollback_path())

        self.depends = {
            d.strip() for d in directives.get("depends", "").split(",") if d.strip()
        }
        transactional_raw = directives.get("transactional")
        if transactional_raw is None:
            self.transactional = None
        elif transactional_raw.lower() in ("true", "false"):
            self.transactional = transactional_raw.lower() == "true"
        else:
            raise exceptions.BadMigration(
                f"Invalid transactional directive {transactional_raw!r} in {self.path}",
            )

        self._load_rollback_directives(rb_directives)

        self.apply_statements = statements
        self.rollback_statements = rollback_statements
        self._loaded = True
        logger.debug(
            "Loaded %s: %d apply statement(s), %d rollback statement(s)",
            self.id,
            len(self.apply_statements),
            len(self.rollback_statements),
        )
        if not self.apply_statements:
            logger.warning("Migration %s has no SQL statements", self.id)

    def _load_rollback_directives(self, rb_directives: DirectivesType) -> None:
        for k in rb_directives:
            if k != "transactional":
                raise exceptions.BadMigration(
                    f"Invalid directive {k!r} in rollback file {self.rollback_path()}",
                )
        transactional_raw = rb_directives.get("transactional")
        if transactional_raw is None:
            self.rollback_transactional = None
        elif transactional_raw.lower() in {"true", "false"}:
            self.rollback_transactional = transactional_raw.lower() == "true"
        else:
            raise exceptions.BadMigration(
                f"Invalid transactional directive {transactional_raw!r} "
                f"in rollback file {self.rollback_path()}",
            )

    def rollback_path(self) -> str:
        """Return the path of the rollback file paired with this migration."""
        path = Path(self.path)
        return str(path.with_name(path.name.replace(".sql", ".rollback.sql")))


class Hook:
    """A hook SQL file executed before or after apply/rollback."""

    def __init__(self, name: str, path: str) -> None:
        """Initialize a hook with its name and SQL file path."""
        self.name = name
        self.path = path
        self.statements: list[str] = []
        self._loaded = False

    def load(self) -> None:
        """Load this hook's SQL statements (idempotent)."""
        if self._loaded:
            return
        _directives, statements = read_sql(self.path)
        self.statements = statements
        self._loaded = True


class MigrationsCollection:
    """Collection of migrations read from one or more source directories."""

    def __init__(self, sources: str | list[str]) -> None:
        """Initialize a collection for the given source directories."""
        if not sources:
            raise ValueError("sources must not be empty")
        self.sources = sources
        self._migrations: list[Migration] | None = None
        self._hooks: dict[str, list[Hook]] | None = None

    def _source_list(self) -> list[str]:
        if isinstance(self.sources, str):
            return [self.sources]
        return list(self.sources)

    def _expand_sources(self) -> Iterable[str]:
        for source in self._source_list():
            for directory in glob(source):  # noqa: PTH207
                if Path(directory).is_dir():
                    yield directory

    def _read_migrations(self) -> list[Migration]:
        migrations: OrderedDict[str, Migration] = OrderedDict()
        for directory in self._expand_sources():
            logger.debug("Reading migrations from source %s", directory)
            for filename in sorted(Path(directory).iterdir()):
                if filename.is_dir():
                    continue
                if filename.suffix != ".sql" or filename.name.endswith(".rollback.sql"):
                    continue
                basename = filename.stem
                if basename in HOOK_NAMES:
                    continue
                if basename in migrations:
                    raise exceptions.MigrationConflict(basename)
                migrations[basename] = Migration(
                    basename,
                    str(filename),
                    source_dir=str(directory),
                )
        migrations_list = list(migrations.values())
        for m in migrations.values():
            m.load()
        return migrations_list

    def _read_hooks(self) -> dict[str, list[Hook]]:
        hooks: dict[str, list[Hook]] = {name: [] for name in HOOK_NAMES}
        for directory in self._expand_sources():
            for name in HOOK_NAMES:
                path = Path(directory) / f"{name}.sql"
                if path.is_file():
                    hooks[name].append(Hook(name, str(path)))
        return hooks

    def _topological(self, migrations: Iterable[Migration]) -> list[Migration]:
        ml = list(migrations)
        all_ids = {m.id for m in ml}
        by_id = {m.id: m for m in ml}
        for m in ml:
            for d in m.depends:
                if d not in all_ids:
                    raise exceptions.NoMigration(
                        f"Could not resolve dependency {d!r} in {m.path}",
                    )
        sorter = TopologicalSorter(
            {m.id: set(m.depends) for m in ml},
        )
        try:
            sorter.prepare()
        except CycleError as e:
            raise exceptions.BadMigration(
                "Circular dependencies among these migrations {}".format(
                    ", ".join(e.args[1]),
                ),
            ) from e
        # Among the migrations ready to run, always take the smallest id,
        # so the order is deterministic.
        ready = list(sorter.get_ready())
        heapq.heapify(ready)
        result: list[Migration] = []
        while ready:
            migration_id = heapq.heappop(ready)
            result.append(by_id[migration_id])
            sorter.done(migration_id)
            for new_id in sorter.get_ready():
                heapq.heappush(ready, new_id)
        return result

    @staticmethod
    def applied_ids(history: Iterable[tuple[str, ...]]) -> set[str]:
        """Return the ids with an ``APPLIED`` latest status in ``history``."""
        latest: dict[str, str] = {}
        for row in history:
            latest[row[0]] = row[2]
        return {
            migration_id
            for migration_id, status in latest.items()
            if status == "APPLIED"
        }

    def list(self) -> list[Migration]:
        """Return all migrations in a deterministic topological order.

        A migration always goes after the ones it depends on. Among the
        migrations that are ready at the same time the one with the smallest
        id (plain string comparison) goes first, so without dependencies the
        order is just the sorted ids.
        """
        if self._migrations is None:
            migrations = self._read_migrations()
            self._migrations = self._topological(migrations)
        return self._migrations

    def _get_hooks(self) -> dict[str, list[Hook]]:
        if self._hooks is None:
            self._hooks = self._read_hooks()
        return self._hooks

    def _check_target(self, target: str) -> None:
        if target not in {m.id for m in self.list()}:
            raise exceptions.NoMigration(f"Migration {target!r} not found")

    def _dependencies_closure(self, target: str) -> set[str]:
        """Return ``target`` and all its transitive dependencies."""
        by_id = {m.id: m for m in self.list()}
        closure: set[str] = set()
        stack = [target]
        while stack:
            migration_id = stack.pop()
            if migration_id in closure:
                continue
            closure.add(migration_id)
            stack.extend(by_id[migration_id].depends)
        return closure

    def _dependents_closure(self, target: str) -> set[str]:
        """Return ``target`` and all migrations transitively depending on it."""
        closure = {target}
        # list() is topological, so dependents always come after dependencies.
        for m in self.list():
            if m.id not in closure and m.depends & closure:
                closure.add(m.id)
        return closure

    def to_apply(
        self,
        history: Iterable[tuple[str, ...]],
        target: str | None = None,
    ) -> tuple[dict[str, list[Hook]], list[Migration]]:
        """Return hooks and the migrations that should be applied.

        Without ``target`` these are all not yet applied migrations. With
        ``target`` these are the target and all its transitive dependencies
        that are not yet applied; unrelated migrations are left alone. If the
        target is already applied, only its unapplied dependencies are
        returned. The order is the common topological order of ``list()``.
        Unknown ``target`` raises ``NoMigration``.
        """
        migrations = self.list()
        applied = self.applied_ids(history)
        keep: set[str] | None = None
        if target is not None:
            self._check_target(target)
            keep = self._dependencies_closure(target)
        result = [
            m
            for m in migrations
            if m.id not in applied and (keep is None or m.id in keep)
        ]
        return self._get_hooks(), result

    def to_rollback(
        self,
        history: Iterable[tuple[str, ...]],
        target: str | None = None,
    ) -> tuple[dict[str, list[Hook]], list[Migration]]:
        """Return hooks and the migrations that should be rolled back.

        Without ``target`` these are all applied migrations. With ``target``
        these are the currently applied ones among the target and all
        migrations transitively depending on it; unrelated migrations are left
        alone. If the target itself is not applied, its applied dependents are
        still returned. The order is the reverse of the topological order of
        ``list()``. Unknown ``target`` raises ``NoMigration``.
        """
        migrations = self.list()
        applied = self.applied_ids(history)
        keep: set[str] | None = None
        if target is not None:
            self._check_target(target)
            keep = self._dependents_closure(target)
        result = [
            m
            for m in reversed(migrations)
            if m.id in applied and (keep is None or m.id in keep)
        ]
        return self._get_hooks(), result
