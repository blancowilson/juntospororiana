import sys
from datetime import datetime, timedelta, timezone
from app.db.session import SessionLocal, engine, Base
from app.models.all_models import Campana, Rifas, Tickets

# Valores por defecto de la campaña y rifa
META_TOTAL = 2750.00
FECHA_SORTEO = datetime(2026, 7, 22, 19, 0, 0, tzinfo=timezone.utc)  # 22 de Julio 2026, 7pm UTC
PREMIO = "Xiaomi Redmi 15c 8gb/256 Gb"
PRECIO_TICKET_BS = 425.00
PRECIO_TICKET_USD = 0.50
LOTERIA = "Triple Caracas"
TITULO = "Gran Rifa Solidaria por Oriana"

def seed_database(reset: bool = False):
    if reset:
        print("Eliminando tablas existentes (Reset)...")
        Base.metadata.drop_all(bind=engine)

    # 1. Asegurar que las tablas existan
    print("Creando tablas...")
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        # 2. Configurar Campaña Global
        campana_activa = db.query(Campana).filter(Campana.activa == True).first()
        if not campana_activa:
            print("Configurando campaña de recaudación global...")
            campana_activa = Campana(
                meta_total=META_TOTAL,
                recaudado_manual=0.00,
                activa=True
            )
            db.add(campana_activa)
            db.flush()
        else:
            print("Campaña activa ya configurada.")

        # 3. Configurar la Rifa Activa
        rifa_activa = db.query(Rifas).filter(Rifas.estado == "Activa").first()
        if not rifa_activa:
            print("Configurando nueva Rifa Activa...")
            rifa_activa = Rifas(
                titulo=TITULO,
                premio=PREMIO,
                precio_ticket_bs=PRECIO_TICKET_BS,
                precio_ticket_usd=PRECIO_TICKET_USD,
                total_numeros=1000,
                loteria_referencia=LOTERIA,
                fecha_sorteo=FECHA_SORTEO,
                estado="Activa"
            )
            db.add(rifa_activa)
            db.flush()  # Para obtener el id de la rifa

            # 4. Generar el inventario de 1000 tickets (000 a 999)
            print("Generando 1000 tickets (000 al 999)... Esto puede tomar unos segundos.")
            tickets = []
            for i in range(1000):
                ticket = Tickets(
                    rifa_id=rifa_activa.id,
                    numero=i,
                    estado="Disponible"
                )
                tickets.append(ticket)

            # Insertar en lotes (bulk save) para optimizar rendimiento
            db.bulk_save_objects(tickets)
            db.commit()
            print("¡Rifa y 1000 tickets generados con éxito!")
        else:
            print(f"Rifa activa existente: '{rifa_activa.titulo}'. No se generaron tickets duplicados.")

    except Exception as e:
        db.rollback()
        print(f"Error al configurar la base de datos: {e}")
        sys.exit(1)
    finally:
        db.close()

if __name__ == "__main__":
    reset_db = "--reset" in sys.argv
    seed_database(reset=reset_db)
