import sys
from datetime import datetime, timedelta, timezone
from app.db.session import SessionLocal, engine, Base
from app.models.all_models import Campana, Rifas, Tickets

def seed_database():
    # 1. Asegurar que las tablas existan
    print("Creando tablas si no existen...")
    Base.metadata.create_all(bind=engine)
    
    db = SessionLocal()
    try:
        # 2. Configurar Campaña Global
        campana_activa = db.query(Campana).filter(Campana.activa == True).first()
        if not campana_activa:
            print("Configurando campaña de recaudación global...")
            campana_activa = Campana(
                meta_total=2600.00,
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
            fecha_sorteo = datetime.now(timezone.utc) + timedelta(days=30)  # Sorteo en 30 días
            rifa_activa = Rifas(
                titulo="Gran Rifa Solidaria por Oriana",
                premio="Combo Tecnológico (Teléfono inteligente + Audífonos inalámbricos)",
                precio_ticket_bs=180.00,  # Precio de referencia en Bs
                precio_ticket_usd=5.00,   # Precio en USD
                total_numeros=1000,
                loteria_referencia="Triple Caracas (Sorteo 7:00 PM)",
                fecha_sorteo=fecha_sorteo,
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
    seed_database()
