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

import difflib
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

DIRECTIVE_NAMES = ("transactional", "depends")
# Minimum difflib ratio for a header word to be taken for a misspelt directive.
# 0.8 catches ``depend``, ``dependss``, ``depends_on``, ``transactonal``,
# ``transactionnal``, ``transaction`` but not ``TODO``, ``Author``, ``Note``.
_TYPO_CUTOFF = 0.8

# Directive name -> values of every header line carrying it, in file order.
DirectivesType = dict[str, list[str]]

_HEADER_DIRECTIVE = re.compile(r"^\s*--\s*([\w-]+)\s*:\s*(.*?)\s*$")
_COMMENT_OR_EMPTY = re.compile(r"^(\s*|\s*--.*)$")


def parse_metadata_from_sql_comments(
    s: str,
    path: str = "<sql>",
    *,
    check_typos: bool = True,
    first_line: int = 1,
) -> tuple[DirectivesType, str]:
    """Extract migration directives from the leading comment block of ``s``.

    Directive names are case-insensitive. Only the header (everything before
    the first SQL line) is scanned. A ``-- word: value`` line whose word is
    not a directive is an ordinary comment, unless ``check_typos`` is set and
    the word closely resembles a directive name (``-- depend: a``): then
    ``BadMigration`` is raised, mentioning ``path`` and the line number
    (``first_line`` is the file line number of the first line of ``s``).
    """
    match = re.search(r"\n|\r\n|\r", s + "\n")
    lineending = "\n" if match is None else match.group(0)
    lines = iter(s.split(lineending))
    directives: DirectivesType = {}
    sql: list[str] = []
    for lineno, line in enumerate(lines, start=first_line):
        match = _HEADER_DIRECTIVE.match(line)
        if match:
            word, value = match.groups()
            name = word.lower()
            if name in DIRECTIVE_NAMES:
                directives.setdefault(name, []).append(value)
            elif check_typos:
                close = difflib.get_close_matches(
                    name,
                    DIRECTIVE_NAMES,
                    n=1,
                    cutoff=_TYPO_CUTOFF,
                )
                if close:
                    raise exceptions.BadMigration(
                        f"Unknown directive {word!r} in {path}:{lineno}, "
                        f"did you mean {close[0]!r}?",
                    )
        elif _COMMENT_OR_EMPTY.match(line):
            continue
        else:
            sql.append(line)
            break
    sql.extend(lines)
    return directives, lineending.join(sql)


def read_sql(
    path: str,
    *,
    check_typos: bool = True,
) -> tuple[DirectivesType, list[str]]:
    """Read ``path``, returning its directives and SQL statements."""
    directives: DirectivesType = {}
    statements: list[str] = []
    sql_path = Path(path)
    if sql_path.exists():
        with sql_path.open(encoding="UTF-8") as f:
            text = f.read()
        # sqlparse strips leading whitespace; keep line numbers true to the file.
        skipped = text[: len(text) - len(text.lstrip())]
        statements = sqlparse.split(text)
        if statements:
            directives, sql = parse_metadata_from_sql_comments(
                statements[0],
                path,
                check_typos=check_typos,
                first_line=1 + len(re.findall(r"\r\n|\n|\r", skipped)),
            )
            statements[0] = sql
    statements = [s for s in statements if s.strip()]
    return directives, statements


def _parse_transactional(
    directives: DirectivesType,
    path: str,
    what: str,
) -> bool | None:
    """Return the value of the ``transactional`` directive, if present once."""
    values = directives.get("transactional")
    if values is None:
        return None
    if len(values) > 1:
        raise exceptions.BadMigration(
            f"Duplicate transactional directive in {what}{path}",
        )
    value = values[0].lower()
    if value not in ("true", "false"):
        raise exceptions.BadMigration(
            f"Invalid transactional directive {values[0]!r} in {what}{path}",
        )
    return value == "true"


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

        # Ids are file names without ".sql", so they never contain commas or
        # whitespace: both separate ids, and several lines are merged.
        self.depends = {
            d
            for value in directives.get("depends", [])
            for d in re.split(r"[,\s]+", value)
            if d
        }
        self.transactional = _parse_transactional(directives, self.path, "")

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
        self.rollback_transactional = _parse_transactional(
            rb_directives,
            self.rollback_path(),
            "rollback file ",
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
        # Directives are meaningless in hooks: ignore them, typos included.
        _directives, statements = read_sql(self.path, check_typos=False)
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
    def statuses(history: Iterable[tuple[str, ...]]) -> dict[str, str]:
        """Return the latest status of every migration in ``history``."""
        latest: dict[str, str] = {}
        for row in history:
            latest[row[0]] = row[2]
        return latest

    @staticmethod
    def applied_ids(history: Iterable[tuple[str, ...]]) -> set[str]:
        """Return the ids with an ``APPLIED`` latest status in ``history``."""
        return {
            migration_id
            for migration_id, status in MigrationsCollection.statuses(history).items()
            if status == "APPLIED"
        }

    @staticmethod
    def pending_ids(history: Iterable[tuple[str, ...]]) -> set[str]:
        """Return the ids with a ``PENDING`` latest status in ``history``.

        Such a migration was started but never finished: its SQL may be
        partially executed.
        """
        return {
            migration_id
            for migration_id, status in MigrationsCollection.statuses(history).items()
            if status == "PENDING"
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

    def _check_no_pending(self, pending: set[str]) -> None:
        """Raise ``MigrationPending`` if ``pending`` is not empty.

        The message tells how to mark each migration. A migration whose file
        is gone cannot be marked until the file is restored.
        """
        if not pending:
            return
        known = {m.id for m in self.list()}

        def advice(i: str) -> str:
            commands = (
                f'"migrations apply --fake {i}" or "migrations rollback --fake {i}"'
            )
            if i in known:
                return commands
            return f"its file is missing: restore it, then run {commands}"

        ids = sorted(pending)
        if len(ids) == 1:
            message = (
                f"Migration {ids[0]!r} is PENDING: a previous run failed midway. "
                f"Fix the database manually, then mark it with {advice(ids[0])}."
            )
        else:
            listed = ", ".join(repr(i) for i in ids)
            per_id = "; ".join(f"for {i!r}: {advice(i)}" for i in ids)
            message = (
                f"Migrations {listed} are PENDING: a previous run failed midway. "
                f"Fix the database manually, then mark each of them ({per_id})."
            )
        raise exceptions.MigrationPending(message)

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
        *,
        fake: bool = False,
    ) -> tuple[dict[str, list[Hook]], list[Migration]]:
        """Return hooks and the migrations that should be applied.

        Without ``target`` these are all not yet applied migrations. With
        ``target`` these are the target and all its transitive dependencies
        that are not yet applied; unrelated migrations are left alone. If the
        target is already applied, only its unapplied dependencies are
        returned. The order is the common topological order of ``list()``.
        Unknown ``target`` raises ``NoMigration`` (checked first).

        A ``PENDING`` migration (a previous run failed midway) blocks the
        whole run with ``MigrationPending``, even if it is unrelated to
        ``target`` or unknown to the collection: the database state is
        uncertain. With ``fake=True`` nothing is blocked and ``PENDING``
        migrations count as not applied, so they get marked ``APPLIED``.
        """
        migrations = self.list()
        history = list(history)
        if target is not None:
            self._check_target(target)
        if not fake:
            self._check_no_pending(self.pending_ids(history))
        applied = self.applied_ids(history)
        keep: set[str] | None = None
        if target is not None:
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
        *,
        fake: bool = False,
    ) -> tuple[dict[str, list[Hook]], list[Migration]]:
        """Return hooks and the migrations that should be rolled back.

        Without ``target`` these are all applied migrations. With ``target``
        these are the currently applied ones among the target and all
        migrations transitively depending on it; unrelated migrations are left
        alone. If the target itself is not applied, its applied dependents are
        still returned. The order is the reverse of the topological order of
        ``list()``. Unknown ``target`` raises ``NoMigration`` (checked first).

        A ``PENDING`` migration blocks the whole run with ``MigrationPending``
        (see ``to_apply``). With ``fake=True`` nothing is blocked and
        ``PENDING`` migrations are rolled back like applied ones, so they get
        marked ``ROLLED_BACK``.
        """
        migrations = self.list()
        history = list(history)
        if target is not None:
            self._check_target(target)
        if not fake:
            self._check_no_pending(self.pending_ids(history))
        rollbackable = self.applied_ids(history)
        if fake:
            rollbackable |= self.pending_ids(history)
        keep: set[str] | None = None
        if target is not None:
            keep = self._dependents_closure(target)
        result = [
            m
            for m in reversed(migrations)
            if m.id in rollbackable and (keep is None or m.id in keep)
        ]
        return self._get_hooks(), result
