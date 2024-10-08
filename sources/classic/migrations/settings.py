from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    DB_DRIVER: str = ''
    DB_USER: str = ''
    DB_PASSWORD: str = ''
    DB_HOST: str = ''
    DB_PORT: str = ''
    DB_NAME: str = ''
    VERSION_TABLE: str = ''

    SOURCE: str = ''
    BATCH_MODE: bool = False
    VERBOSITY: int = 0
    EDITOR: str = ''
    POST_CREATE_COMMAND: str = ''
    PREFIX: str = ''

    @property
    def DATABASE(self) -> str:
        return (
            f'{self.DB_DRIVER}://{self.DB_USER}'
            f"{':' if self.DB_PASSWORD else ''}{self.DB_PASSWORD}"
            f'@{self.DB_HOST}'
            f"{':' if self.DB_PORT else ''}{self.DB_PORT}"
            f'/{self.DB_NAME}'
        )

    @property
    def sources_list(self) -> list[str]:
        return self.SOURCE.split()

    class Config:
        env_file = '.env'
        env_file_encoding = 'utf-8'
