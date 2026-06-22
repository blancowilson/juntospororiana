import io
import pandas as pd
import secrets
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session
from sqlalchemy import select, update, func
from datetime import datetime

from app.db.session import get_db
from app.core.config import settings
from app.models.all_models import Tickets, LotesConciliacion, Campana, Aportantes, Rifas

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
templates = Jinja2Templates(directory="templates")

@router.get("/", response_class=HTMLResponse)
async def admin_panel_view(request: Request, db: Session = Depends(get_db)):
    # 1. Obtener estadísticas
    total_reservados = db.scalar(select(func.count(Tickets.id)).where(Tickets.estado == "Reservado")) or 0
    total_pagados = db.scalar(select(func.count(Tickets.id)).where(Tickets.estado == "Pagado")) or 0
    
    # Recaudación de rifa (Solo tickets pagados)
    recaudacion_rifa = db.scalar(
        select(func.sum(Rifas.precio_ticket_usd))
        .join(Rifas, Tickets.rifa_id == Rifas.id)
        .where(Tickets.estado == "Pagado")
    ) or 0.0

    # Donaciones directas
    total_donaciones = db.scalar(
        select(func.sum(Aportantes.monto_reportado))
        .where(Aportantes.tipo_aporte == "Donacion")
    ) or 0.0

    # Campaña activa
    campana = db.execute(select(Campana).where(Campana.activa == True)).scalar_one_or_none()
    meta_total = float(campana.meta_total) if campana else 2750.00
    recaudado_manual = float(campana.recaudado_manual) if campana else 0.00
    
    # 2. Obtener lista de aportantes ordenada por fecha
    aportes = db.execute(
        select(Aportantes).order_by(Aportantes.fecha_aporte.desc())
    ).scalars().all()

    # 3. Obtener lista de tickets reservados con su aportante
    tickets_reservados = db.execute(
        select(Tickets)
        .where(Tickets.estado == "Reservado")
        .order_by(Tickets.numero)
    ).scalars().all()

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "stats": {
                "tickets_reservados": total_reservados,
                "tickets_pagados": total_pagados,
                "recaudacion_rifa_usd": float(recaudacion_rifa),
                "total_donaciones_usd": float(total_donaciones),
                "total_recaudado_usd": float(recaudacion_rifa) + float(total_donaciones) + recaudado_manual,
                "meta_total": meta_total
            },
            "aportes": aportes,
            "tickets_reservados": tickets_reservados
        }
    )

@router.get("/dashboard")
async def dashboard(db: Session = Depends(get_db)):
    # Totales de tickets
    total_reservados = db.scalar(select(func.count(Tickets.id)).where(Tickets.estado == "Reservado")) or 0
    total_pagados = db.scalar(select(func.count(Tickets.id)).where(Tickets.estado == "Pagado")) or 0
    
    # Recaudación de rifa (Solo tickets pagados, convertida/sumada en USD)
    from app.models.all_models import Rifas
    recaudacion_rifa = db.scalar(
        select(func.sum(Rifas.precio_ticket_usd))
        .join(Rifas, Tickets.rifa_id == Rifas.id)
        .where(Tickets.estado == "Pagado")
    ) or 0.0
    
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

@router.post("/reset-db")
async def reset_database(db: Session = Depends(get_db)):
    """
    Reinicia completamente la base de datos de la rifa y campaña para pruebas locales:
    - Libera y limpia todos los 1000 tickets (estado='Disponible').
    - Elimina todos los registros de aportantes (donaciones y compras de rifa).
    - Elimina los lotes de conciliación registrados.
    - Reinicia la recaudación manual de la campaña activa a 0.00.
    """
    from app.models.all_models import Campana, Aportantes
    try:
        # 1. Resetear todos los tickets
        db.execute(
            update(Tickets)
            .values(
                estado="Disponible",
                aportante_id=None,
                reservado_en=None,
                referencia_pago=None,
                monto_reportado=None
            )
        )
        
        # 2. Eliminar aportantes
        db.query(Aportantes).delete()
        
        # 3. Eliminar lotes de conciliación
        db.query(LotesConciliacion).delete()
        
        # 4. Resetear recaudación de campaña
        campana = db.query(Campana).filter(Campana.activa == True).first()
        if campana:
            campana.recaudado_manual = 0.00
            
        db.commit()
        return {"status": "success", "message": "Base de datos y rifa reiniciadas con éxito."}
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al reiniciar base de datos: {str(e)}"
        )

@router.post("/reversar/todos")
async def reversar_todos_reservados(db: Session = Depends(get_db)):
    """
    Libera inmediatamente todos los tickets que se encuentren en estado 'Reservado' 
    (no confirmados), volviéndolos a poner 'Disponibles'.
    """
    try:
        stmt = select(Tickets).where(Tickets.estado == "Reservado")
        reservados = db.execute(stmt).scalars().all()
        
        if not reservados:
            return {"status": "success", "message": "No hay tickets reservados para liberar."}
            
        vencidos_ids = [t.id for t in reservados]
        
        db.execute(
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
        db.commit()
        return {"status": "success", "message": f"Se liberaron {len(vencidos_ids)} tickets reservados."}
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al liberar tickets: {str(e)}"
        )

@router.post("/reversar/referencia/{referencia}")
async def reversar_por_referencia(referencia: str, db: Session = Depends(get_db)):
    """
    Libera inmediatamente los tickets en estado 'Reservado' asociados a una referencia 
    de pago específica (por ejemplo, si el pago de esa referencia fue rechazado o falso).
    """
    try:
        stmt = select(Tickets).where(Tickets.estado == "Reservado", Tickets.referencia_pago == referencia)
        reservados = db.execute(stmt).scalars().all()
        
        if not reservados:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No se encontraron tickets reservados con la referencia {referencia}"
            )
            
        vencidos_ids = [t.id for t in reservados]
        
        db.execute(
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
        db.commit()
        return {"status": "success", "message": f"Se liberaron {len(vencidos_ids)} tickets para la referencia {referencia}."}
    except Exception as e:
        if isinstance(e, HTTPException):
            raise e
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al liberar tickets por referencia: {str(e)}"
        )

@router.post("/confirmar/aportante/{aportante_id}", response_class=HTMLResponse)
async def confirmar_aportante(aportante_id: int, db: Session = Depends(get_db)):
    """
    Confirma manualmente todos los tickets asociados a un aportante 
    (cambia el estado de 'Reservado' a 'Pagado').
    """
    try:
        db.execute(
            update(Tickets)
            .where(Tickets.aportante_id == aportante_id, Tickets.estado == "Reservado")
            .values(estado="Pagado")
        )
        db.commit()
        return HTMLResponse(
            '<span class="badge badge-success-custom"><i class="fa-solid fa-circle-check me-1"></i>Pagado</span>'
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<span class="badge badge-danger-custom">Error: {str(e)}</span>',
            status_code=500
        )

@router.post("/reversar/aportante/{aportante_id}", response_class=HTMLResponse)
async def reversar_aportante(aportante_id: int, db: Session = Depends(get_db)):
    """
    Libera manualmente todos los tickets asociados a un aportante
    (vuelven a estar 'Disponibles').
    """
    try:
        db.execute(
            update(Tickets)
            .where(Tickets.aportante_id == aportante_id)
            .values(
                estado="Disponible",
                reservado_en=None,
                aportante_id=None,
                referencia_pago=None,
                monto_reportado=None
            )
        )
        db.commit()
        return HTMLResponse(
            '<span class="badge badge-danger-custom"><i class="fa-solid fa-circle-xmark me-1"></i>Liberado</span>'
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<span class="badge badge-danger-custom">Error: {str(e)}</span>',
            status_code=500
        )
