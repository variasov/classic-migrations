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

import datetime
import hashlib
import os
import re
from collections import OrderedDict
from collections.abc import Iterable
from glob import glob
from graphlib import CycleError, TopologicalSorter
from logging import getLogger
from typing import Any

import sqlparse
from classic.migrations import exceptions, utils
from classic.migrations.backends.base import DatabaseBackend

logger = getLogger("classic.migrations")

HOOK_NAMES = ("pre-apply", "post-apply", "pre-rollback", "post-rollback")

DirectivesType = dict[str, str]


def _is_migration_file(path: str) -> bool:
    """
    Return True if the given path matches a migration file pattern
    """
    _, extension = os.path.splitext(path)
    return extension == ".sql"


def parse_metadata_from_sql_comments(s: str) -> tuple[DirectivesType, str]:
    directive_names = ["transactional", "depends", "comment"]
    comment_or_empty = re.compile(r"^(\s*|\s*--.*)$").match
    directive_pattern = re.compile(
        r"^\s*--\s*({})\s*:\s*(.*)$".format("|".join(map(re.escape, directive_names)))
    )

    lineending = re.search(r"\n|\r\n|\r", s + "\n").group(0)  # type: ignore
    lines = iter(s.split(lineending))
    directives: DirectivesType = {}
    sql = []
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

    def __init__(self, id, path, source_dir=None):
        self.id = id
        self.path = path
        self.source_dir = source_dir
        self.depends: set[str] = set()
        self.transactional = True
        self.comment = ""
        self.apply_statements: list[str] = []
        self.rollback_statements: list[str] = []
        self.content_hash = None
        self._loaded = False

    def __repr__(self):
        return f"<{self.__class__.__name__} {self.id!r} from {self.path}>"

    def load(self):
        if self._loaded:
            return

        directives, statements = read_sql(self.path)
        _, rollback_statements = read_sql(self._rollback_path())

        self.depends = {d for d in directives.get("depends", "").split() if d}
        transactional = directives.get("transactional", "true").lower()
        if transactional not in {"true", "false"}:
            raise exceptions.BadMigration(
                "Invalid transactional directive {!r} in {}".format(
                    directives.get("transactional"), self.path
                )
            )
        self.transactional = transactional == "true"
        self.comment = directives.get("comment", "")
        self.apply_statements = statements
        self.rollback_statements = rollback_statements

        body = "\n".join(statements)
        self.content_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()
        self._loaded = True

    def _rollback_path(self):
        base, ext = os.path.splitext(self.path)
        return base + ".rollback" + ext


class Hook:

    def __init__(self, name, path):
        self.name = name
        self.path = path
        self.transactional = True
        self.statements: list[str] = []
        self._loaded = False

    def load(self):
        if self._loaded:
            return
        directives, statements = read_sql(self.path)
        transactional = directives.get("transactional", "true").lower()
        self.transactional = transactional == "true"
        self.statements = statements
        self._loaded = True


class Migrations:

    def __init__(
        self,
        sources: str | list[str],
        driver: str,
        db_host: str | None = None,
        db_port: int | None = None,
        db_name: str | None = None,
        db_user: str | None = None,
        db_password: str | None = None,
        db_args: dict[str, Any] | None = None,
        migration_table: str | None = None,
    ):
        if not sources:
            raise ValueError("sources must not be empty")
        if not driver:
            raise ValueError("driver must not be empty")
        self.sources = sources
        self.driver = driver
        self.db_host = db_host
        self.db_port = db_port
        self.db_name = db_name
        self.db_user = db_user
        self.db_password = db_password
        self.db_args = db_args
        self.migration_table = migration_table or 'migrations'

    # ------------------------------------------------------------------
    # Source reading
    # ------------------------------------------------------------------
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
        return list(migrations.values())

    def _read_hooks(self) -> dict[str, list[Hook]]:
        hooks: dict[str, list[Hook]] = {name: [] for name in HOOK_NAMES}
        for directory in self._expand_sources():
            for name in HOOK_NAMES:
                path = os.path.join(directory, name + ".sql")
                if os.path.isfile(path):
                    hooks[name].append(Hook(name, path))
        return hooks

    # ------------------------------------------------------------------
    # Graph helpers
    # ------------------------------------------------------------------
    @staticmethod
    def _load_all(migrations):
        for m in migrations:
            m.load()

    def _topological(self, migrations: Iterable[Migration]) -> list[Migration]:
        ml = list(migrations)
        all_ids = {m.id for m in ml}
        by_id = {m.id: m for m in ml}
        for m in ml:
            for d in m.depends:
                if d not in all_ids:
                    raise exceptions.BadMigration(
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

    def _heads(self, migrations: Iterable[Migration]) -> set[Migration]:
        by_id = {m.id: m for m in migrations}
        result = set(migrations)
        for m in migrations:
            for d in m.depends:
                result.discard(by_id.get(d))
        return result

    def _ancestors(self, migration, population):
        by_id = {m.id: m for m in population}
        deps = set()
        to_process = {by_id[d] for d in migration.depends if d in by_id}
        while to_process:
            m = to_process.pop()
            deps.add(m)
            for d in m.depends:
                if d in by_id and by_id[d] not in deps:
                    to_process.add(by_id[d])
        return deps

    def _descendants(self, migration, population):
        population = set(population)
        descendants = {migration}
        descendant_ids = {migration.id}
        while True:
            found = False
            for m in population - descendants:
                if m.depends & descendant_ids:
                    descendants.add(m)
                    descendant_ids.add(m.id)
                    found = True
            if not found:
                break
        descendants.remove(migration)
        return descendants

    def _to_revision(self, migrations, revision, direction):
        targets = [m for m in migrations if revision in m.id]
        if not targets:
            raise ValueError(f"'{revision}' doesn't match any revisions.")
        if len(targets) > 1:
            raise ValueError(
                "'{}' matches multiple revisions: {}".format(
                    revision, ", ".join(m.id for m in targets)
                )
            )
        target = targets[0]
        if direction == "apply":
            selected = self._ancestors(target, migrations) | {target}
        else:
            selected = self._descendants(target, migrations) | {target}
        return [m for m in migrations if m in selected]

    def _filter(self, migrations, match=None, revision=None, direction="apply"):
        result = list(migrations)
        if match:
            search = re.compile(match).search
            result = [m for m in result if search(m.id)]
        if revision:
            result = self._to_revision(result, revision, direction)
        return result

    # ------------------------------------------------------------------
    # Backend helpers
    # ------------------------------------------------------------------
    def _get_backend(self):
        backend_class = DatabaseBackend.get_backend_class(self.driver)
        return backend_class(
            db_host=self.db_host,
            db_port=self.db_port,
            db_name=self.db_name,
            db_user=self.db_user,
            db_password=self.db_password,
            db_args=self.db_args,
            migration_table=self.migration_table,
        )

    @staticmethod
    def _applied_ids(backend):
        return {row[0]: row for row in backend.applied_migrations()}

    def _check_hashes(self, migrations, applied):
        changed = []
        for m in migrations:
            if m.id in applied:
                stored_hash = applied[m.id][1]
                if stored_hash is not None and stored_hash != m.content_hash:
                    changed.append(m.id)
        if changed:
            raise exceptions.MigrationHashMismatch(changed)

    def _select_apply(self, migrations, match, revision, all, applied):
        result = self._filter(migrations, match, revision, "apply")
        result = self._topological(result)
        if not all:
            result = [m for m in result if m.id not in applied]
        return result

    def _select_rollback(self, migrations, match, revision, applied):
        result = self._filter(migrations, match, revision, "rollback")
        result = [m for m in result if m.id in applied]
        result = list(reversed(self._topological(result)))
        return result

    def _last_applied(self, migrations, applied, n=1):
        ordered = [m for m in self._topological(migrations) if m.id in applied]
        return ordered[-n:] if ordered else []

    # ------------------------------------------------------------------
    # Execution helpers
    # ------------------------------------------------------------------
    def _apply_one(self, backend, migration, force=False):
        logger.info("Applying %s", migration.id)
        if migration.transactional:
            context = backend.transaction()
        else:
            context = backend.disable_transactions()
        with context:
            for stmt in migration.apply_statements:
                self._execute_statement(backend, stmt, migration, "apply", force)

    def _rollback_one(self, backend, migration, force=False):
        logger.info("Rolling back %s", migration.id)
        if not migration.rollback_statements:
            return
        if migration.transactional:
            context = backend.transaction()
        else:
            context = backend.disable_transactions()
        with context:
            for stmt in migration.rollback_statements:
                self._execute_statement(backend, stmt, migration, "rollback", force)

    @staticmethod
    def _execute_statement(backend, stmt, migration, direction, force):
        try:
            cursor = backend.cursor()
            try:
                logger.debug(" - executing %r", stmt)
                cursor.execute(stmt)
            finally:
                cursor.close()
        except backend.DatabaseError:
            if force:
                logger.exception("Ignored error %sing %s", direction, migration.id)
            else:
                raise

    def _run_hooks(self, backend, hooks):
        for hook in hooks:
            hook.load()
            if not hook.statements:
                continue
            if hook.transactional:
                context = backend.transaction()
            else:
                context = backend.disable_transactions()
            with context:
                for stmt in hook.statements:
                    cursor = backend.cursor()
                    try:
                        cursor.execute(stmt)
                    finally:
                        cursor.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def list(self):
        backend = self._get_backend()
        try:
            backend.ensure_migration_table()
            migrations = self._read_migrations()
            self._load_all(migrations)
            applied = self._applied_ids(backend)
            ordered = self._topological(migrations)
            return [
                ("A" if m.id in applied else "U", m.id, m.source_dir or "")
                for m in ordered
            ]
        finally:
            backend.close()

    def is_applied(self, migration_id):
        backend = self._get_backend()
        try:
            backend.ensure_migration_table()
            return backend.is_applied(migration_id)
        finally:
            backend.close()

    def apply(
        self,
        match=None,
        revision=None,
        all=False,
        force=False,
        one=False,
        check_hashes=True,
    ):
        backend = self._get_backend()
        try:
            with backend.lock():
                backend.ensure_migration_table()
                migrations = self._read_migrations()
                self._load_all(migrations)
                applied = self._applied_ids(backend)
                if check_hashes:
                    self._check_hashes(migrations, applied)
                to_apply = self._select_apply(migrations, match, revision, all, applied)
                if one:
                    if to_apply:
                        to_apply = to_apply[:1]
                    else:
                        to_apply = self._last_applied(migrations, applied)
                hooks = self._read_hooks()
                self._run_hooks(backend, hooks["pre-apply"])
                for m in to_apply:
                    self._apply_one(backend, m, force)
                    content_hash = m.content_hash if check_hashes else None
                    backend.mark_applied(m.id, content_hash, m.comment)
                self._run_hooks(backend, hooks["post-apply"])
        finally:
            backend.close()

    def develop(self, n=1, check_hashes=True):
        backend = self._get_backend()
        try:
            with backend.lock():
                backend.ensure_migration_table()
                migrations = self._read_migrations()
                self._load_all(migrations)
                applied = self._applied_ids(backend)
                if check_hashes:
                    self._check_hashes(migrations, applied)
                to_apply = self._select_apply(migrations, None, None, False, applied)
                hooks = self._read_hooks()
                if to_apply:
                    self._run_hooks(backend, hooks["pre-apply"])
                    for m in to_apply:
                        self._apply_one(backend, m, False)
                        content_hash = m.content_hash if check_hashes else None
                        backend.mark_applied(m.id, content_hash, m.comment)
                    self._run_hooks(backend, hooks["post-apply"])
                else:
                    to_reapply = self._last_applied(migrations, applied, n)
                    self._run_hooks(backend, hooks["pre-rollback"])
                    for m in reversed(to_reapply):
                        self._rollback_one(backend, m, False)
                        backend.unmark(m.id)
                    self._run_hooks(backend, hooks["post-rollback"])
                    self._run_hooks(backend, hooks["pre-apply"])
                    for m in to_reapply:
                        self._apply_one(backend, m, False)
                        content_hash = m.content_hash if check_hashes else None
                        backend.mark_applied(m.id, content_hash, m.comment)
                    self._run_hooks(backend, hooks["post-apply"])
        finally:
            backend.close()

    def rollback(self, match=None, revision=None, all=False, force=False):
        backend = self._get_backend()
        try:
            with backend.lock():
                backend.ensure_migration_table()
                migrations = self._read_migrations()
                self._load_all(migrations)
                applied = self._applied_ids(backend)
                to_rollback = self._select_rollback(
                    migrations, match, revision, applied
                )
                if not revision and not all and len(to_rollback) > 1:
                    to_rollback = to_rollback[:1]
                hooks = self._read_hooks()
                self._run_hooks(backend, hooks["pre-rollback"])
                for m in to_rollback:
                    self._rollback_one(backend, m, force)
                    backend.unmark(m.id)
                self._run_hooks(backend, hooks["post-rollback"])
        finally:
            backend.close()

    def reapply(
        self,
        match=None,
        revision=None,
        force=False,
        check_hashes=True,
    ):
        backend = self._get_backend()
        try:
            with backend.lock():
                backend.ensure_migration_table()
                migrations = self._read_migrations()
                self._load_all(migrations)
                applied = self._applied_ids(backend)
                if check_hashes:
                    self._check_hashes(migrations, applied)
                to_rollback = self._select_rollback(
                    migrations, match, revision, applied
                )
                hooks = self._read_hooks()
                self._run_hooks(backend, hooks["pre-rollback"])
                for m in to_rollback:
                    self._rollback_one(backend, m, force)
                    backend.unmark(m.id)
                self._run_hooks(backend, hooks["post-rollback"])
                to_apply = list(reversed(to_rollback))
                self._run_hooks(backend, hooks["pre-apply"])
                for m in to_apply:
                    self._apply_one(backend, m, force)
                    content_hash = m.content_hash if check_hashes else None
                    backend.mark_applied(m.id, content_hash, m.comment)
                self._run_hooks(backend, hooks["post-apply"])
        finally:
            backend.close()

    def mark(self, match=None, revision=None, all=False):
        backend = self._get_backend()
        try:
            with backend.lock():
                backend.ensure_migration_table()
                migrations = self._read_migrations()
                self._load_all(migrations)
                applied = self._applied_ids(backend)
                to_mark = self._select_apply(migrations, match, revision, all, applied)
                for m in to_mark:
                    backend.mark_applied(m.id, m.content_hash, m.comment)
        finally:
            backend.close()

    def unmark(self, match=None, revision=None):
        backend = self._get_backend()
        try:
            with backend.lock():
                backend.ensure_migration_table()
                migrations = self._read_migrations()
                self._load_all(migrations)
                applied = self._applied_ids(backend)
                to_unmark = self._select_rollback(
                    migrations, match, revision, applied
                )
                for m in to_unmark:
                    backend.unmark(m.id)
        finally:
            backend.close()

    def new(self, message=""):
        sources = self._sources()
        if not sources:
            raise ValueError("Please specify a migrations directory")
        directory = sources[0]
        if not os.path.isdir(directory):
            raise ValueError(f"Migrations directory does not exist: {directory}")

        migrations = self._read_migrations()
        self._load_all(migrations)
        heads = sorted(m.id for m in self._heads(migrations))
        depends_str = "  ".join(heads)

        content = f"-- {message}\n-- depends: {depends_str}\n\n"
        filename = make_filename(directory, message, ".sql")
        with open(filename, "w", encoding="UTF-8") as f:
            f.write(content)
        return filename


def make_filename(directory, message, extension):
    lines = (line.strip() for line in message.split("\n"))
    lines = (line for line in lines if line)
    message = next(lines, None)

    if message:
        slug = "-" + utils.slugify(message)
    else:
        slug = ""

    datestr = datetime.datetime.now(datetime.UTC).date().strftime("%Y%m%d")
    number = "01"
    rand = utils.get_random_string(5)

    for p in glob(os.path.join(directory, f"{datestr}_*")):
        n = os.path.basename(p)[len(datestr) + 1 :].split("_")[0]
        try:
            if number <= n:
                number = str(int(n) + 1).zfill(2)
        except ValueError:
            continue

    return os.path.join(
        directory,
        f"{datestr}_{number}_{rand}{slug}{extension}",
    )
