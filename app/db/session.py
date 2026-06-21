from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
from app.core.config import settings

# Motor de base de datos
engine = create_engine(
    settings.database_url,
    pool_pre_ping=True, # Evita conexiones colgadas
    echo=False
)

# Session Local
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base para los modelos
Base = declarative_base()

# Dependencia para inyección en FastAPI
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
