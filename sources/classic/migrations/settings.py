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

    MIGRATION_TABLE: str = ""
    MIGRATIONS_SCHEMA: str = ""
    VERSION_TABLE: str = ""

    SOURCE: str = ""

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
        return self.SOURCE.split()

    @property
    def migration_table(self) -> str:
        return self.MIGRATION_TABLE or self.VERSION_TABLE or "migrations"

    @property
    def migrations_schema(self) -> str | None:
        return self.MIGRATIONS_SCHEMA or None
