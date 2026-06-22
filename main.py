import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import JSONResponse
from sqlalchemy import text
from apscheduler.schedulers.background import BackgroundScheduler

from app.core.scheduler import liberar_reservas_vencidas
from app.api.routers.public import router as public_router
from app.api.routers.admin import router as admin_router
from app.db.session import engine, Base

# Configuración de logging para el scheduler
logging.basicConfig(level=logging.INFO)

# 1. Crear las tablas en la BD (para MVP, aunque en pro se usa Alembic)
Base.metadata.create_all(bind=engine)

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
