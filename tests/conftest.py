import pytest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from unittest.mock import MagicMock

# 1. Configurar la base de datos de pruebas en memoria antes de cargar nada del backend
from app.core.config import settings
settings.DB_SERVER = "sqlite:///:memory:"
settings.OPENWA_ENABLED = False  # Deshabilitar WhatsApp real en pruebas

from app.db.session import Base
from app.models.all_models import Campana, Rifas, Tickets
from app.api.routers.public import get_db
from main import app

# 2. Configuración del motor y sesión en memoria para pruebas
test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False}
)
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)

@pytest.fixture(scope="function")
def db_session():
    # Crear tablas
    Base.metadata.create_all(bind=test_engine)
    db = TestingSessionLocal()
    try:
        # Seeding inicial para las pruebas
        # 1. Campaña activa
        campana = Campana(meta_total=2750.00, recaudado_manual=0.00, activa=True)
        db.add(campana)
        
        # 2. Rifa activa
        rifa = Rifas(
            id=1,
            titulo="Gran Rifa Solidaria por Oriana",
            premio="Xiaomi Redmi 15c 8gb/256 Gb",
            precio_ticket_bs=500.00,
            precio_ticket_usd=0.60,
            total_numeros=100,  # 100 boletos para simplificar
            loteria_referencia="Triple Caracas",
            fecha_sorteo=datetime(2026, 7, 22, 19, 0, 0, tzinfo=timezone.utc),
            estado="Activa"
        )
        db.add(rifa)
        db.flush()
        
        # 3. 100 Tickets (000 al 099)
        for i in range(100):
            t = Tickets(
                rifa_id=rifa.id,
                numero=i,
                estado="Disponible"
            )
            db.add(t)
            
        db.commit()
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=test_engine)

@pytest.fixture(scope="function", autouse=True)
def override_db(db_session):
    def _get_db_override():
        try:
            yield db_session
        finally:
            pass
            
    app.dependency_overrides[get_db] = _get_db_override
    yield
    app.dependency_overrides.pop(get_db, None)

@pytest.fixture(scope="function", autouse=True)
def mock_whatsapp(monkeypatch):
    mock_wa = MagicMock()
    mock_wa.enviar_texto.return_value = True
    mock_wa.notificar_recepcion_tickets.return_value = True
    mock_wa.notificar_confirmacion_tickets.return_value = True
    mock_wa.notificar_reasignacion.return_value = True

    # Parchear el modulo importado en los routers
    import app.api.routers.public as public_router
    import app.api.routers.admin as admin_router

    monkeypatch.setattr(public_router, "wa", mock_wa)
    monkeypatch.setattr(admin_router, "wa", mock_wa)

    return mock_wa
