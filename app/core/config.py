from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    # Database Config (PostgreSQL)
    DB_SERVER: str = "127.0.0.1"
    DB_PORT: int = 5432
    DB_USER: str = "postgres"
    DB_PASSWORD: str = "postgres"
    DB_NAME: str = "JuntosPorOriana"

    # Admin Auth
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "admin"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def database_url(self) -> str:
        # Cadena de conexión para PostgreSQL usando psycopg2
        return f"postgresql+psycopg2://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_SERVER}:{self.DB_PORT}/{self.DB_NAME}"

settings = Settings()
