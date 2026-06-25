import sys
import argparse
from app.db.session import SessionLocal
from app.models.all_models import Aportantes

def main():
    parser = argparse.ArgumentParser(description="Edita el monto de una colaboración de forma segura")
    parser.add_argument("--id", type=int, required=True, help="ID del Aportante")
    parser.add_argument("--monto", type=float, required=True, help="Nuevo monto de la colaboración")
    parser.add_argument("--moneda", type=str, choices=["USD", "BS"], help="Nueva moneda (opcional)")
    
    args = parser.parse_args()
    
    db = SessionLocal()
    try:
        aportante = db.get(Aportantes, args.id)
        if not aportante:
            print(f"Error: Aportante con ID {args.id} no encontrado.")
            sys.exit(1)
            
        monto_antiguo = aportante.monto_reportado
        moneda_antigua = aportante.moneda
        
        aportante.monto_reportado = args.monto
        if args.moneda:
            aportante.moneda = args.moneda.upper()
            
        db.commit()
        print(f"Éxito: Se actualizó el aporte del ID {args.id}.")
        print(f"Monto: {monto_antiguo} {moneda_antigua} -> {aportante.monto_reportado} {aportante.moneda}")
    except Exception as e:
        db.rollback()
        print(f"Error al actualizar: {e}")
        sys.exit(1)
    finally:
        db.close()

if __name__ == "__main__":
    main()
