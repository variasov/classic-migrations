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

"""Application configuration read from environment variables and a ``.env`` file."""

import os
from pathlib import Path


def _read_env_file(path: str | Path) -> dict[str, str]:
    """Parse a ``.env`` file into a mapping, ignoring comments and blank lines."""
    values: dict[str, str] = {}
    env_path = Path(path)
    if not env_path.exists():
        return values
    with env_path.open(encoding="utf-8") as f:
        for raw_line in f:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


class Settings:
    """Read configuration from environment variables and a ``.env`` file.

    Environment variables take precedence over values from the ``.env`` file.
    """

    def __init__(self, env_file: str | Path = ".env") -> None:
        """Load environment variables, giving precedence to ``os.environ``."""
        self._environ = dict(os.environ)
        for key, value in _read_env_file(env_file).items():
            self._environ.setdefault(key, value)

    @property
    def SOURCES(self) -> str:
        """Return the colon-separated list of migration source directories."""
        return self._environ.get("SOURCES", "")

    @property
    def DATABASE_DRIVER(self) -> str:
        """Return the name of the database driver module."""
        return self._environ.get("DATABASE_DRIVER", "")

    @property
    def DATABASE_USER(self) -> str | None:
        """Return the database user, prefixed with its domain if one is set."""
        value = self._environ.get("DATABASE_USER")
        if not value:
            return None
        if self.DATABASE_USER_DOMAIN:
            return f"{self.DATABASE_USER_DOMAIN}\\{value}"
        return value

    @property
    def DATABASE_USER_DOMAIN(self) -> str | None:
        """Return the optional domain for the database user."""
        value = self._environ.get("DATABASE_USER_DOMAIN")
        return value or None

    @property
    def DATABASE_PASSWORD(self) -> str | None:
        """Return the database password."""
        value = self._environ.get("DATABASE_PASSWORD")
        return value or None

    @property
    def DATABASE_HOST(self) -> str | None:
        """Return the database host."""
        value = self._environ.get("DATABASE_HOST")
        return value or None

    @property
    def DATABASE_PORT(self) -> int | None:
        """Return the database port as an integer, if supplied."""
        value = self._environ.get("DATABASE_PORT")
        if not value:
            return None
        return int(value)

    @property
    def DATABASE_NAME(self) -> str | None:
        """Return the database name."""
        value = self._environ.get("DATABASE_NAME")
        return value or None

    @property
    def MIGRATIONS_TABLE(self) -> str:
        """Return the migration history table name (default ``migrations``)."""
        return self._environ.get("MIGRATIONS_TABLE", "migrations")

    @property
    def MIGRATIONS_SCHEMA(self) -> str | None:
        """Return the schema for the migration history table, if supplied."""
        value = self._environ.get("MIGRATIONS_SCHEMA")
        return value or None

    @property
    def OLD_MIGRATIONS_SCHEMA(self) -> str | None:
        """Return the schema of the legacy ``versions`` table, if supplied."""
        value = self._environ.get("OLD_MIGRATIONS_SCHEMA")
        return value or None

    @property
    def sources_list(self) -> list[str]:
        """Return the migration source directories as a list."""
        return [s for s in self.SOURCES.split(":") if s]
