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

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    SOURCES: str
    DATABASE_DRIVER: str
    DATABASE_USER_: str | None = Field(alias="DATABASE_USER", default=None)
    DATABASE_USER_DOMAIN: str | None = None
    DATABASE_PASSWORD: str | None = None
    DATABASE_HOST: str | None = None
    DATABASE_PORT: int | None = None
    DATABASE_NAME: str | None = None
    MIGRATIONS_TABLE: str = "migrations"

    @property
    def DATABASE_USER(self) -> str | None:
        if self.DATABASE_USER_DOMAIN:
            return f"{self.DATABASE_USER_DOMAIN}\\{self.DATABASE_USER_ or ''}"
        return self.DATABASE_USER_

    @property
    def sources_list(self) -> list[str]:
        return [s for s in self.SOURCES.split(":") if s]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="allow",
    )
