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

    # --- OpenWA (WhatsApp API Gateway) ---
    # Apunta al servidor OpenWA que corre en el mismo VPS (puerto 2785)
    OPENWA_BASE_URL: str = "http://127.0.0.1:2785/api"
    # API key generada por OpenWA en el primer arranque (ver openwa/data/.api-key)
    OPENWA_API_KEY: str = ""
    # ID de la sesion de WhatsApp (se obtiene con el script openwa/init-session.sh)
    OPENWA_SESSION_ID: str = ""
    # Si False, no se envian mensajes (util para desarrollo sin OpenWA)
    OPENWA_ENABLED: bool = True
    # Codigo de pais por defecto para normalizar telefonos (Venezuela = 58)
    OPENWA_DEFAULT_COUNTRY_CODE: str = "58"

    # --- Anti-ban: delays y rate limits para envios WhatsApp ---
    # Delay min/max entre envios a destinatarios distintos (segundos).
    # El delay real se sortea entre estos valores, ajustado por horario (mas lento de noche).
    WA_MIN_DELAY_SEC: int = 25
    WA_MAX_DELAY_SEC: int = 90
    # Multiplicador del delay durante la "noche" (22:00 - 08:00 hora Venezuela).
    # 1.0 = igual de rapido, 3.0 = tres veces mas lento.
    WA_NIGHT_MULTIPLIER: float = 3.0
    # Maximo de mensajes salientes por hora y por dia.
    # Comunidad recomienda <30/hora y <200/dia para numeros con <3 semanas.
    WA_MAX_PER_HOUR: int = 20
    WA_MAX_PER_DAY: int = 80
    # Si True, NO envia si se alcanza el limite (se hace log y se descarta el mensaje).
    # Si False, encola y espera. Recomendado True para que la app no se cuelgue.
    WA_BLOCK_ON_LIMIT: bool = True
    # Timezone de la campana para detectar "noche" (default America/Caracas)
    WA_TIMEZONE: str = "America/Caracas"

    # --- Cifrado de datos personales (PII) ---
    # Generar con:
    #   FERNET_KEY=$(python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())")
    #   SEARCH_HMAC_KEY=$(python -c "import secrets; print(secrets.token_hex(32))")
    # Si se dejan vacias, el script de migracion las genera y las escribe en .env
    FERNET_KEY: str = ""
    SEARCH_HMAC_KEY: str = ""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def database_url(self) -> str:
        # Soporte para SQLite en pruebas locales o PostgreSQL por defecto
        if self.DB_SERVER.startswith("sqlite"):
            return self.DB_SERVER
        return f"postgresql+psycopg2://{self.DB_USER}:{self.DB_PASSWORD}@{self.DB_SERVER}:{self.DB_PORT}/{self.DB_NAME}"

settings = Settings()
