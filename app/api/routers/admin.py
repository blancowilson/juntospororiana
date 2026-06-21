import io
import pandas as pd
import secrets
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session
from sqlalchemy import select, update, func
from datetime import datetime

from app.db.session import get_db
from app.core.config import settings
from app.models.all_models import Tickets, LotesConciliacion

security = HTTPBasic()

def verify_admin(credentials: HTTPBasicCredentials = Depends(security)):
    correct_username = secrets.compare_digest(credentials.username, settings.ADMIN_USERNAME)
    correct_password = secrets.compare_digest(credentials.password, settings.ADMIN_PASSWORD)
    if not (correct_username and correct_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Credenciales inválidas",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username

router = APIRouter(prefix="/admin", dependencies=[Depends(verify_admin)])

@router.get("/dashboard")
async def dashboard(db: Session = Depends(get_db)):
    # Totales de tickets
    total_reservados = db.scalar(select(func.count(Tickets.id)).where(Tickets.estado == "Reservado")) or 0
    total_pagados = db.scalar(select(func.count(Tickets.id)).where(Tickets.estado == "Pagado")) or 0
    
    # Recaudación de rifa (Solo tickets pagados)
    recaudacion_rifa = db.scalar(select(func.sum(Tickets.monto_reportado)).where(Tickets.estado == "Pagado")) or 0.0
    
    return {
        "dashboard": {
            "tickets_reservados": total_reservados,
            "tickets_pagados": total_pagados,
            "recaudacion_rifa_usd": float(recaudacion_rifa)
        }
    }

@router.post("/conciliar/upload")
async def upload_conciliacion(file: UploadFile = File(...), db: Session = Depends(get_db)):
    try:
        content = await file.read()
        if file.filename.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content))
        elif file.filename.endswith(".xlsx") or file.filename.endswith(".xls"):
            df = pd.read_excel(io.BytesIO(content))
        else:
            raise HTTPException(status_code=400, detail="Formato de archivo no soportado. Usa CSV o Excel.")
            
        # Validar columnas
        if "Referencia" not in df.columns or "Monto" not in df.columns:
            raise HTTPException(status_code=400, detail="El archivo debe contener las columnas 'Referencia' y 'Monto'")

        # Limpiar Referencias del DataFrame
        df["ReferenciaLimpia"] = df["Referencia"].astype(str).str.strip().str.lstrip("0")
        
        # Obtener tickets reservados con referencia
        tickets_pendientes = db.execute(
            select(Tickets).where(Tickets.estado == "Reservado", Tickets.referencia_pago.isnot(None))
        ).scalars().all()
        
        aprobados = 0
        tickets_a_actualizar = []

        for ticket in tickets_pendientes:
            if not ticket.referencia_pago or not ticket.monto_reportado:
                continue
                
            # Limpiar referencia del ticket
            ticket_ref_limpia = str(ticket.referencia_pago).strip().lstrip("0")
            monto_ticket = float(ticket.monto_reportado)
            
            # Buscar coincidencia: misma referencia limpia y diferencia de monto menor a 1.0
            match = df[
                (df["ReferenciaLimpia"] == ticket_ref_limpia) & 
                (abs(pd.to_numeric(df["Monto"], errors='coerce') - monto_ticket) < 1.0)
            ]
            
            if not match.empty:
                tickets_a_actualizar.append(ticket.id)
                aprobados += 1

        if tickets_a_actualizar:
            # Actualización transaccional
            try:
                db.execute(
                    update(Tickets)
                    .where(Tickets.id.in_(tickets_a_actualizar))
                    .values(estado="Pagado")
                )
                
                lote = LotesConciliacion(
                    nombre_archivo=file.filename,
                    registros_procesados=len(df),
                    pagos_aprobados=aprobados
                )
                db.add(lote)
                db.commit()
            except Exception as e:
                db.rollback()
                raise HTTPException(status_code=500, detail=f"Error durante la actualización de la BD: {str(e)}")

        return {
            "filename": file.filename,
            "procesados": len(df),
            "aprobados": aprobados
        }

    except Exception as e:
        if not isinstance(e, HTTPException):
            raise HTTPException(status_code=400, detail=f"Error procesando el archivo: {str(e)}")
        raise e
