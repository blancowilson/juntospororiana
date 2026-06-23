import logging
from datetime import datetime, timedelta, timezone
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import select, update

from app.db.session import SessionLocal
from app.models.all_models import Tickets

logger = logging.getLogger(__name__)

def liberar_reservas_vencidas():
    db = SessionLocal()
    try:
        limite = datetime.now(timezone.utc) - timedelta(hours=24)
        
        # Buscar tickets vencidos
        stmt = select(Tickets).where(Tickets.estado == "Reservado", Tickets.reservado_en <= limite)
        vencidos = db.execute(stmt).scalars().all()
        
        if vencidos:
            vencidos_ids = [t.id for t in vencidos]
            
            # Liberar los tickets
            update_stmt = (
                update(Tickets)
                .where(Tickets.id.in_(vencidos_ids))
                .values(
                    estado="Disponible",
                    reservado_en=None,
                    aportante_id=None,
                    referencia_pago=None,
                    monto_reportado=None
                )
            )
            db.execute(update_stmt)
            db.commit()
            logger.info(f"Se liberaron {len(vencidos_ids)} tickets vencidos.")
            
    except Exception as e:
        db.rollback()
        logger.error(f"Error al liberar reservas vencidas: {str(e)}")
    finally:
        db.close()
