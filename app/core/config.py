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
    SECRET_KEY: str = "super_secret_session_key_for_local_testing"

    # Secret key para sesiones (firmar cookies de captcha)
    SECRET_KEY: str = "CHANGE_ME_IN_PRODUCTION_use_openssl_rand_hex_32"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def database_url(self) -> str:
        # Soporte para SQLite en pruebas locales o PostgreSQL por defecto
        if self.DB_SERVER.startswith("sqlite"):
            return self.DB_SERVER
        return f"postgresql+psycopg2://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_SERVER}:{self.DB_PORT}/{self.DB_NAME}"

settings = Settings()
