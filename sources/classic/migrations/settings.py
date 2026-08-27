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


def _read_env_file(path: str) -> dict[str, str]:
    values: dict[str, str] = {}
    if not os.path.exists(path):
        return values
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    return values


class Settings:
    """Reads configuration from environment variables and a ``.env`` file.

    Environment variables take precedence over values from the ``.env`` file.
    """

    def __init__(self, env_file: str = ".env") -> None:
        self._environ = dict(os.environ)
        for key, value in _read_env_file(env_file).items():
            self._environ.setdefault(key, value)

    @property
    def SOURCES(self) -> str:
        return self._environ.get("SOURCES", "")

    @property
    def DATABASE_DRIVER(self) -> str:
        return self._environ.get("DATABASE_DRIVER", "")

    @property
    def DATABASE_USER(self) -> str | None:
        value = self._environ.get("DATABASE_USER")
        if not value:
            return None
        if self.DATABASE_USER_DOMAIN:
            return f"{self.DATABASE_USER_DOMAIN}\\{value}"
        return value

    @property
    def DATABASE_USER_DOMAIN(self) -> str | None:
        value = self._environ.get("DATABASE_USER_DOMAIN")
        return value if value else None

    @property
    def DATABASE_PASSWORD(self) -> str | None:
        value = self._environ.get("DATABASE_PASSWORD")
        return value if value else None

    @property
    def DATABASE_HOST(self) -> str | None:
        value = self._environ.get("DATABASE_HOST")
        return value if value else None

    @property
    def DATABASE_PORT(self) -> int | None:
        value = self._environ.get("DATABASE_PORT")
        if not value:
            return None
        return int(value)

    @property
    def DATABASE_NAME(self) -> str | None:
        value = self._environ.get("DATABASE_NAME")
        return value if value else None

    @property
    def MIGRATIONS_TABLE(self) -> str:
        return self._environ.get("MIGRATIONS_TABLE", "migrations")

    @property
    def MIGRATIONS_SCHEMA(self) -> str | None:
        value = self._environ.get("MIGRATIONS_SCHEMA")
        return value if value else None

    @property
    def OLD_VERSIONS_SCHEMA(self) -> str | None:
        value = self._environ.get("OLD_VERSIONS_SCHEMA")
        return value if value else None

    @property
    def sources_list(self) -> list[str]:
        return [s for s in self.SOURCES.split(":") if s]
