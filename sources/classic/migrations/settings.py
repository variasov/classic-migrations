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

from urllib.parse import quote

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="allow",
    )

    DATABASE_DRIVER: str = ""
    DATABASE_USER_: str = Field(alias="DATABASE_USER", default="")
    DATABASE_USER_DOMAIN: str = ""
    DATABASE_PASSWORD: str = ""
    DATABASE_HOST: str = ""
    DATABASE_PORT: str = ""
    DATABASE_NAME: str = ""

    MIGRATIONS_TABLE: str = ""
    MIGRATIONS_SCHEMA: str = ""

    SOURCES: str = ""

    @property
    def DATABASE_USER(self) -> str:
        if self.DATABASE_USER_DOMAIN:
            return f"{self.DATABASE_USER_DOMAIN}\\{self.DATABASE_USER_}"
        return self.DATABASE_USER_

    @property
    def DATABASE(self) -> str:
        return (
            f"{self.DATABASE_DRIVER}://{self.DATABASE_USER}"
            f"{':' if self.DATABASE_PASSWORD else ''}"
            f"{quote(self.DATABASE_PASSWORD)}"
            f"@{self.DATABASE_HOST}"
            f"{':' if self.DATABASE_PORT else ''}{self.DATABASE_PORT}"
            f"/{self.DATABASE_NAME}"
        )

    @property
    def sources_list(self) -> list[str]:
        return [s for s in self.SOURCES.split(":") if s]

    @property
    def migration_table(self) -> str:
        return self.MIGRATIONS_TABLE or "migrations"

    @property
    def migrations_schema(self) -> str | None:
        return self.MIGRATIONS_SCHEMA or None
