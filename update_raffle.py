from app.db.session import SessionLocal
from app.models.all_models import Rifas

def update_active_raffle():
    db = SessionLocal()
    try:
        # Buscar la rifa activa
        rifa = db.query(Rifas).filter(Rifas.estado == "Activa").first()
        if rifa:
            print(f"Rifa activa encontrada: '{rifa.titulo}' (ID: {rifa.id})")
            print(f"Valores actuales:")
            print(f"  - Premio: {rifa.premio}")
            print(f"  - Precio Bs: {rifa.precio_ticket_bs}")
            print(f"  - Precio USD: {rifa.precio_ticket_usd}")
            print(f"  - Lotería: {rifa.loteria_referencia}")
            
            # Actualizar valores
            rifa.premio = "Xiaomi Redmi 15c 8gb/256 Gb"
            rifa.precio_ticket_bs = 800.00
            rifa.precio_ticket_usd = 1.00
            rifa.loteria_referencia = "Triple Caracas"
            
            db.commit()
            print("\nValores actualizados exitosamente:")
            print(f"  - Premio: {rifa.premio}")
            print(f"  - Precio Bs: {rifa.precio_ticket_bs}")
            print(f"  - Precio USD: {rifa.precio_ticket_usd}")
            print(f"  - Lotería: {rifa.loteria_referencia}")
        else:
            print("No se encontró ninguna rifa activa en la base de datos.")
    except Exception as e:
        db.rollback()
        print(f"Error al actualizar la rifa: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    update_active_raffle()
