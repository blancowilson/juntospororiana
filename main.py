import logging
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from starlette.middleware.sessions import SessionMiddleware
from sqlalchemy import text
from apscheduler.schedulers.background import BackgroundScheduler

from app.core.scheduler import liberar_reservas_vencidas
from app.api.routers.public import router as public_router
from app.api.routers.admin import router as admin_router
from app.db.session import engine, Base
from app.core.config import settings

# Configuración de logging para el scheduler
logging.basicConfig(level=logging.INFO)

# 1. Crear las tablas en la BD (para MVP, aunque en pro se usa Alembic)
Base.metadata.create_all(bind=engine)

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
    """Filtro Jinja para formatear fechas en espanol.
    Formatos: 'completo' (22 de julio de 2026), 'corto' (22 de julio 2026),
              'con_dia' (miercoles 22 de julio de 2026), 'solo_dia' (miercoles 22 de julio)
    """
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
    scheduler = BackgroundScheduler()
    scheduler.add_job(liberar_reservas_vencidas, 'interval', minutes=1)
    scheduler.start()
    logging.info("Scheduler de liberación de tickets iniciado.")

    yield

    # Teardown
    scheduler.shutdown()
    logging.info("Scheduler detenido.")

app = FastAPI(title="Juntos por Oriana", lifespan=lifespan)

# Middleware de sesiones (para almacenar respuestas de captcha)
app.add_middleware(SessionMiddleware, secret_key=settings.SECRET_KEY, max_age=3600)

# Filtros Jinja personalizados
from app.api.routers.public import templates as _public_templates
from app.api.routers.admin import templates as _admin_templates
for _tpl in (_public_templates, _admin_templates):
    _tpl.env.filters["fecha_es"] = fecha_es

# Montar archivos estáticos
app.mount("/static", StaticFiles(directory="static"), name="static")

# Incluir los routers
app.include_router(public_router)
app.include_router(admin_router)

# Endpoint de health check para el sistema de monitoreo
@app.get("/health")
def health_check():
    """Verifica el estado de la aplicacion y la conexion a la BD."""
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"status": "ok", "database": "ok"}
    except Exception as e:
        return JSONResponse(
            status_code=503,
            content={"status": "error", "database": "down", "detail": str(e)}
        )

if __name__ == "__main__":
    import uvicorn
    # Inicia con: uvicorn main:app --host 0.0.0.0 --port 8000
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
