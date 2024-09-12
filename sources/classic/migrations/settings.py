from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    DB_DRIVER: str = Field(default="")
    DB_USER: str = Field(default="")
    DB_PASSWORD: str = Field(default="")
    DB_HOST: str = Field(default="")
    DB_PORT: str = Field(default="")
    DB_NAME: str = Field(default="")
    VERSION_TABLE: str = ''

    SOURCE: str = Field(default="")
    BATCH_MODE: str = Field(default="")
    VERBOSITY: int = 0
    EDITOR: str = Field(default="")
    POST_CREATE_COMMAND: str = Field(default="")
    PREFIX: str = Field(default="")

    @property
    def DATABASE(self) -> str:
        return (
            f"{self.DB_DRIVER}://{self.DB_USER}"
            f"{':' if self.DB_PASSWORD else ''}{self.DB_PASSWORD}"
            f"@{self.DB_HOST}"
            f"{':' if self.DB_PORT else ''}{self.DB_PORT}"
            f"/{self.DB_NAME}"
        )

    @property
    def sources_list(self) -> list[str]:
        return self.SOURCE.split()

    class Config:
        env_file = '.env'
        env_file_encoding = 'utf-8'
