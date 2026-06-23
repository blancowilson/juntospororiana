import logging
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import text
from apscheduler.schedulers.background import BackgroundScheduler

# Configuracion de logging ANTES de cualquier import problematico
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("juntospororiana")

# Configuracion de la app (carga .env)
from app.core.config import settings

# =========================================================
# Diagnostico de arranque (no falla la app si algo esta mal)
# =========================================================
try:
    from app.services import crypto as _crypto
    _crypto_status = _crypto.estado()
    if not _crypto_status["encryption_ready"]:
        logger.warning(
            "============================================================\n"
            "  CIFRADO DE PII NO CONFIGURADO\n"
            "  FERNET_KEY o SEARCH_HMAC_KEY vacias en .env\n"
            "  Los datos se guardaran y mostraran EN PLANO.\n"
            "  Ejecuta: python scripts/migrate_encrypt_data.py\n"
            "============================================================"
        )
    else:
        logger.info("Cifrado de PII activo (Fernet + HMAC).")
except Exception as _e:
    logger.error(f"No se pudo cargar el modulo crypto (no fatal): {_e}")

# =========================================================
# Importar modelos y routers ANTES de create_all para que
# Base.metadata conozca las tablas
# =========================================================
try:
    # Importar explicitamente el paquete de modelos para registrar todas las clases en Base.metadata
    from app.db.session import engine, Base
    import app.models.all_models  # noqa: F401 - registra Campana, Rifas, Aportantes, Tickets, AuditLog
    Base.metadata.create_all(bind=engine)
    
    # Agregar columna boletos_iniciales de forma transaccional si no existe
    with engine.begin() as conn:
        try:
            conn.execute(text('ALTER TABLE "Aportantes" ADD COLUMN boletos_iniciales VARCHAR(500)'))
            logger.info("Añadida columna boletos_iniciales a Aportantes.")
        except Exception:
            pass  # Ignorar si ya existe o no se soporta en dialectos locales

    logger.info("Esquema de BD verificado/creado.")
except Exception as _e:
    logger.exception(f"ERROR CRITICO creando esquema de BD: {_e}")
    logger.error(
        "La aplicacion NO podra operar hasta que la BD este disponible.\n"
        "Revisa: DB_SERVER, DB_USER, DB_PASSWORD, DB_NAME en .env\n"
        "y que PostgreSQL este corriendo."
    )
    # Aun asi dejamos que la app arranque para que /health reporte el problema

# =========================================================
# Resto de imports (los routers pueden fallar si la BD no responde,
# pero los protegemos individualmente)
# =========================================================
try:
    from app.core.scheduler import liberar_reservas_vencidas
    from app.api.routers.public import router as public_router
    from app.api.routers.admin import router as admin_router
    _routers_ok = True
    logger.info("Routers cargados correctamente.")
except Exception as _e:
    logger.exception(f"ERROR cargando routers: {_e}")
    _routers_ok = False
    public_router = None
    admin_router = None


# Meses en espanol para filtros Jinja (no depende del locale del sistema)
_MESES_ES = [
    "", "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"
]
_MESES_ES_ABREV = [
    "", "ene", "feb", "mar", "abr", "may", "jun",
    "jul", "ago", "sep", "oct", "nov", "dic"
]
_DIAS_ES = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]


def fecha_es(value: datetime, formato: str = "completo") -> str:
    """Filtro Jinja para formatear fechas en espanol."""
    if value is None:
        return ""
    if formato == "completo":
        return f"{value.day} de {_MESES_ES[value.month]} de {value.year}"
    elif formato == "corto":
        return f"{value.day} de {_MESES_ES[value.month]} {value.year}"
    elif formato == "con_dia":
        return f"{_DIAS_ES[value.weekday()]} {value.day} de {_MESES_ES[value.month]} de {value.year}"
    elif formato == "solo_dia":
        return f"{_DIAS_ES[value.weekday()]} {value.day} de {_MESES_ES[value.month]}"
    elif formato == "hora":
        return value.strftime("%H:%M")
    return value.strftime("%Y-%m-%d %H:%M:%S")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup Background Scheduler
    try:
        scheduler = BackgroundScheduler()
        scheduler.add_job(liberar_reservas_vencidas, 'interval', minutes=1)
        scheduler.start()
        logger.info("Scheduler de liberacion de tickets iniciado.")
    except Exception as e:
        logger.exception(f"Error iniciando scheduler (no fatal): {e}")

    yield

    try:
        scheduler.shutdown()
        logger.info("Scheduler detenido.")
    except Exception:
        pass


app = FastAPI(title="Juntos por Oriana", lifespan=lifespan)

# Middleware de sesiones (para almacenar respuestas de captcha)
try:
    app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY, max_age=3600)
except Exception as e:
    logger.exception(f"Error anadiendo SessionMiddleware: {e}")

# Filtros Jinja personalizados (solo si los routers cargaron)
if _routers_ok:
    try:
        from app.api.routers.public import templates as _public_templates
        from app.api.routers.admin import templates as _admin_templates
        for _tpl in (_public_templates, _admin_templates):
            _tpl.env.filters["fecha_es"] = fecha_es
    except Exception as e:
        logger.exception(f"Error registrando filtros Jinja: {e}")

# Montar archivos estaticos
try:
    app.mount("/static", StaticFiles(directory="static"), name="static")
except Exception as e:
    logger.exception(f"Error montando /static: {e}")

# Incluir los routers (solo si se cargaron)
if _routers_ok and public_router is not None:
    app.include_router(public_router)
if _routers_ok and admin_router is not None:
    app.include_router(admin_router)


# =========================================================
# /health robusto - SIEMPRE responde
# =========================================================
@app.get("/health")
def health_check():
    """Verifica estado de la app, BD, cifrado y OpenWA. Siempre responde."""
    import importlib
    out = {
        "status": "ok",
        "app": "Juntos por Oriana",
        "routers_ok": _routers_ok,
        "database": "unknown",
        "encryption": {},
        "whatsapp": {},
    }

    # BD
    try:
        from app.db.session import engine as _engine
        with _engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        out["database"] = "ok"
    except Exception as e:
        out["database"] = "down"
        out["database_error"] = str(e)[:300]
        out["status"] = "degraded"

    # Cifrado
    try:
        from app.services import crypto as _crypto
        out["encryption"] = _crypto.estado()
    except Exception as e:
        out["encryption"] = {"error": str(e)[:200]}

    # OpenWA
    out["whatsapp"] = {"enabled": getattr(settings, "OPENWA_ENABLED", False)}
    if out["whatsapp"]["enabled"]:
        try:
            from app.services import openwa_admin as _wa
            wa_s = _wa.estado_sesion()
            out["whatsapp"].update({
                "configurado": wa_s.get("configurado", False),
                "conectado": wa_s.get("conectado", False),
                "status": wa_s.get("status"),
                "phone": wa_s.get("phone"),
                "error": wa_s.get("error"),
            })
        except Exception as e:
            out["whatsapp"]["error"] = str(e)[:200]
    return JSONResponse(
        status_code=200 if out["status"] == "ok" else 503,
        content=out,
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
