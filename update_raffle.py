"""Actualiza la campana y rifa activas con los valores actuales del proyecto."""
from datetime import datetime, timezone
from app.db.session import SessionLocal
from app.models.all_models import Campana, Rifas

# Valores objetivo
META_TOTAL = 2750.00
FECHA_SORTEO = datetime(2026, 7, 22, 19, 0, 0, tzinfo=timezone.utc)
PREMIO = "Xiaomi Redmi 15c 8gb/256 Gb"
PRECIO_TICKET_BS = 500.00
PRECIO_TICKET_USD = 0.60
LOTERIA = "Triple Caracas"
TITULO = "Gran Rifa Solidaria por Oriana"


def update_campana_y_rifa():
    """Actualiza los valores de la campana activa y la rifa activa."""
    db = SessionLocal()
    try:
        # 1. Campana
        campana = db.query(Campana).filter(Campana.activa == True).first()
        if campana:
            print(f"Campana actual: meta={campana.meta_total}, recaudado={campana.recaudado_manual}")
            campana.meta_total = META_TOTAL
            print(f"  -> nueva meta: {campana.meta_total}")
        else:
            print("Creando campana por defecto...")
            campana = Campana(meta_total=META_TOTAL, recaudado_manual=0.00, activa=True)
            db.add(campana)

        # 2. Rifa
        rifa = db.query(Rifas).filter(Rifas.estado == "Activa").first()
        if rifa:
            print(f"\nRifa activa encontrada: '{rifa.titulo}' (ID: {rifa.id})")
            print(f"Valores actuales:")
            print(f"  - Premio: {rifa.premio}")
            print(f"  - Precio Bs: {rifa.precio_ticket_bs}")
            print(f"  - Precio USD: {rifa.precio_ticket_usd}")
            print(f"  - Loteria: {rifa.loteria_referencia}")
            print(f"  - Fecha sorteo: {rifa.fecha_sorteo}")

            rifa.titulo = TITULO
            rifa.premio = PREMIO
            rifa.precio_ticket_bs = PRECIO_TICKET_BS
            rifa.precio_ticket_usd = PRECIO_TICKET_USD
            rifa.loteria_referencia = LOTERIA
            rifa.fecha_sorteo = FECHA_SORTEO

            print(f"\nValores actualizados:")
            print(f"  - Premio: {rifa.premio}")
            print(f"  - Precio Bs: {rifa.precio_ticket_bs}")
            print(f"  - Precio USD: {rifa.precio_ticket_usd}")
            print(f"  - Loteria: {rifa.loteria_referencia}")
            print(f"  - Fecha sorteo: {rifa.fecha_sorteo}")
        else:
            print("Creando rifa por defecto...")
            rifa = Rifas(
                titulo=TITULO,
                premio=PREMIO,
                precio_ticket_bs=PRECIO_TICKET_BS,
                precio_ticket_usd=PRECIO_TICKET_USD,
                total_numeros=1000,
                loteria_referencia=LOTERIA,
                fecha_sorteo=FECHA_SORTEO,
                estado="Activa"
            )
            db.add(rifa)

        db.commit()
        print("\n[OK] Cambios guardados en la base de datos.")
    except Exception as e:
        db.rollback()
        print(f"\n[ERROR] {e}")
    finally:
        db.close()


if __name__ == "__main__":
    update_campana_y_rifa()
