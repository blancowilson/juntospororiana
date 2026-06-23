import io
import logging
import pandas as pd
import secrets
from dataclasses import dataclass
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session
from sqlalchemy import select, update, func
from datetime import datetime

from app.db.session import get_db
from app.core.config import settings
from app.models.all_models import Tickets, LotesConciliacion, Campana, Aportantes, Rifas, AuditLog
from app.services import whatsapp as wa
from app.services import crypto
from app.services import openwa_admin as wa_admin

logger = logging.getLogger(__name__)

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


# =========================================================
# Helpers de seguridad / privacidad
# =========================================================

def audit(
    db: Session,
    request: Request,
    usuario: str,
    accion: str,
    recurso_tipo: Optional[str] = None,
    recurso_id: Optional[str] = None,
    detalle: Optional[str] = None,
) -> None:
    """Registra una accion en AuditLog. NO hace commit (lo hace el caller)."""
    try:
        ip = request.client.host if request.client else None
        entry = AuditLog(
            usuario=usuario,
            ip=ip,
            accion=accion,
            recurso_tipo=recurso_tipo,
            recurso_id=str(recurso_id) if recurso_id is not None else None,
            detalle=detalle,
        )
        db.add(entry)
    except Exception as e:
        logger.error(f"No se pudo escribir en AuditLog: {e}")


@dataclass
class AporteMasked:
    """
    Wrapper que descifra y enmascara los campos sensibles de un Aportante
    para mostrarlos en el panel admin sin exponer la PII completa.
    Todas las properties estan protegidas con try/except para que un fallo
    de cifrado (clave incorrecta, etc.) NUNCA tumbe el panel admin.
    """
    id: int
    _ap: Aportantes
    _reveal: bool = False  # si True muestra datos completos (solo para "ver detalle")

    def _safe_descifrar(self, campo: str) -> str:
        try:
            v = getattr(self._ap, campo, None)
            return crypto.descifrar(v) or ""
        except Exception as e:
            logger.error(f"Error descifrando {campo} del aportante {self.id}: {e}")
            return ""

    @property
    def nombre(self) -> str:
        v = self._safe_descifrar("nombre")
        return v if self._reveal else crypto.enmascarar_nombre(v)

    @property
    def cedula(self) -> str:
        v = self._safe_descifrar("cedula")
        if not v:
            return ""
        return v if self._reveal else crypto.enmascarar_cedula(v)

    @property
    def telefono(self) -> str:
        v = self._safe_descifrar("telefono")
        if not v:
            return ""
        return v if self._reveal else crypto.enmascarar_telefono(v)

    @property
    def referencia(self) -> str:
        v = self._safe_descifrar("referencia")
        if not v:
            return ""
        return v if self._reveal else crypto.enmascarar_referencia(v)

    @property
    def mensaje_apoyo(self) -> str:
        try:
            return self._ap.mensaje_apoyo or ""
        except Exception:
            return ""

    @property
    def monto_reportado(self) -> float:
        try:
            return float(self._ap.monto_reportado)
        except Exception:
            return 0.0

    @property
    def moneda(self) -> str:
        try:
            return self._ap.moneda or ""
        except Exception:
            return ""

    @property
    def metodo_pago(self) -> str:
        try:
            return self._ap.metodo_pago or ""
        except Exception:
            return ""

    @property
    def tipo_aporte(self) -> str:
        try:
            return self._ap.tipo_aporte or ""
        except Exception:
            return ""

    @property
    def fecha_aporte(self):
        try:
            return self._ap.fecha_aporte
        except Exception:
            return None

    @property
    def tickets(self):
        try:
            return self._ap.tickets
        except Exception:
            return []

    @property
    def boletos_iniciales(self) -> str:
        try:
            return self._ap.boletos_iniciales or ""
        except Exception:
            return ""

    @property
    def has_full_data(self) -> bool:
        """True si tiene cedula/telefono (para mostrar la columna)."""
        try:
            return bool(self._ap.cedula) or bool(self._ap.telefono)
        except Exception:
            return False


router = APIRouter(prefix="/admin", dependencies=[Depends(verify_admin)])
templates = Jinja2Templates(directory="templates")


# =========================================================
# Vistas principales
# =========================================================

@router.get("/", response_class=HTMLResponse)
async def admin_panel_view(request: Request, db: Session = Depends(get_db)):
    usuario = "admin"
    try:
        # 1. Obtener estadísticas
        total_reservados = db.scalar(select(func.count(Tickets.id)).where(Tickets.estado == "Reservado")) or 0
        total_pagados = db.scalar(select(func.count(Tickets.id)).where(Tickets.estado == "Pagado")) or 0

        recaudacion_rifa = db.scalar(
            select(func.sum(Rifas.precio_ticket_usd))
            .select_from(Tickets)
            .join(Rifas, Tickets.rifa_id == Rifas.id)
            .where(Tickets.estado == "Pagado")
        ) or 0.0

        total_donaciones = db.scalar(
            select(func.sum(Aportantes.monto_reportado))
            .where(Aportantes.tipo_aporte == "Donacion")
        ) or 0.0

        campana = db.execute(select(Campana).where(Campana.activa == True)).scalar_one_or_none()
        meta_total = float(campana.meta_total) if campana else 2750.00
        recaudado_manual = float(campana.recaudado_manual) if campana else 0.00

        # 2. Lista de aportantes (envuelta para mostrar enmascarada)
        try:
            aportes_raw = db.execute(
                select(Aportantes).order_by(Aportantes.fecha_aporte.desc())
            ).scalars().all()
            aportes = [AporteMasked(id=ap.id, _ap=ap) for ap in aportes_raw]
        except Exception as e:
            logger.error(f"Error cargando aportantes: {e}")
            aportes = []

        # 3. Tickets reservados con su aportante
        try:
            tickets_reservados = db.execute(
                select(Tickets)
                .where(Tickets.estado == "Reservado")
                .order_by(Tickets.numero)
            ).scalars().all()
        except Exception as e:
            logger.error(f"Error cargando tickets reservados: {e}")
            tickets_reservados = []

        # Estado de WhatsApp (no rompe la pagina si OpenWA no responde)
        try:
            wa_status = wa_admin.estado_sesion()
        except Exception as e:
            logger.error(f"Error consultando OpenWA: {e}")
            wa_status = {"configurado": False, "conectado": False, "error": str(e)}
        wa_enabled = settings.OPENWA_ENABLED and bool(settings.OPENWA_API_KEY) and bool(settings.OPENWA_SESSION_ID)

        # Audit (no debe tumbar el panel si falla)
        try:
            audit(db, request, usuario, "VIEW_PANEL", "Admin", None, f"aportes={len(aportes)} tickets_reservados={len(tickets_reservados)}")
            db.commit()
        except Exception as e:
            logger.error(f"Error en audit: {e}")
            db.rollback()

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
                "tickets_reservados": tickets_reservados,
                "wa_status": wa_status,
                "wa_enabled": wa_enabled,
            }
        )
    except Exception as e:
        logger.exception("Error en admin_panel_view:")
        # En vez de 500, devolvemos un panel minimo con el error visible
        return HTMLResponse(
            f"""
            <html><head><title>Error en panel admin</title>
            <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.2/dist/css/bootstrap.min.css" rel="stylesheet">
            </head><body class="p-5">
            <div class="container" style="max-width: 800px;">
                <h1 class="text-danger"><i class="fa-solid fa-triangle-exclamation"></i> Error cargando el panel</h1>
                <p class="lead">El servidor respondio con un error. Detalle:</p>
                <pre class="bg-light p-3 border rounded">{str(e)}</pre>
                <h3 class="mt-4">Posibles causas y soluciones:</h3>
                <ol>
                    <li><strong>Cifrado no configurado:</strong> ejecuta en el servidor
                        <code>python scripts/migrate_encrypt_data.py</code></li>
                    <li><strong>Esquema desactualizado:</strong> reinicia la app para que
                        <code>create_all</code> anada las nuevas columnas.</li>
                    <li><strong>BD inalcanzable:</strong> revisa que PostgreSQL este arriba
                        y que las credenciales en <code>.env</code> sean correctas.</li>
                </ol>
                <p>Para ver el traceback completo revisa los logs:
                    <code>journalctl -u juntospororiana -n 100 --no-pager</code></p>
                <a href="/health" class="btn btn-primary">Ver estado del servidor (/health)</a>
            </div></body></html>
            """,
            status_code=500,
        )


@router.get("/dashboard")
async def dashboard(request: Request, db: Session = Depends(get_db)):
    usuario = "admin"
    total_reservados = db.scalar(select(func.count(Tickets.id)).where(Tickets.estado == "Reservado")) or 0
    total_pagados = db.scalar(select(func.count(Tickets.id)).where(Tickets.estado == "Pagado")) or 0
    recaudacion_rifa = db.scalar(
        select(func.sum(Rifas.precio_ticket_usd))
        .select_from(Tickets)
        .join(Rifas, Tickets.rifa_id == Rifas.id)
        .where(Tickets.estado == "Pagado")
    ) or 0.0

    audit(db, request, usuario, "VIEW_DASHBOARD", "Admin", None, None)
    db.commit()
    return {
        "dashboard": {
            "tickets_reservados": total_reservados,
            "tickets_pagados": total_pagados,
            "recaudacion_rifa_usd": float(recaudacion_rifa)
        }
    }


# =========================================================
# Endpoints para la gestion de la sesion de WhatsApp
# (accesibles solo desde /admin, auth HTTPBasic)
# =========================================================

@router.get("/whatsapp", response_class=HTMLResponse)
async def whatsapp_panel(request: Request, db: Session = Depends(get_db)):
    """Pagina dedicada para gestionar la sesion de WhatsApp."""
    if not settings.OPENWA_ENABLED:
        return HTMLResponse(
            '<div class="alert alert-warning">OpenWA deshabilitado en la configuracion '
            '(OPENWA_ENABLED=false).</div>',
            status_code=200,
        )
    if not settings.OPENWA_API_KEY or not settings.OPENWA_SESSION_ID:
        return HTMLResponse(
            '<div class="alert alert-danger">Faltan variables: OPENWA_API_KEY o '
            'OPENWA_SESSION_ID en el archivo .env. Revisa openwa/README.md.</div>',
            status_code=200,
        )

    usuario = "admin"
    audit(db, request, usuario, "VIEW_WHATSAPP_PANEL", "OpenWA", None, None)
    db.commit()

    estado = wa_admin.estado_sesion()
    return templates.TemplateResponse(
        "admin_whatsapp.html",
        {"request": request, "estado": estado, "settings": settings},
    )


@router.get("/whatsapp/status")
async def whatsapp_status(request: Request, db: Session = Depends(get_db)):
    """JSON con el estado actual de la sesion. Usado por el JS del panel."""
    estado = wa_admin.estado_sesion()
    return JSONResponse(estado)


@router.get("/whatsapp/status-html", response_class=HTMLResponse)
async def whatsapp_status_html(request: Request, db: Session = Depends(get_db)):
    """Devuelve solo el fragmento _wa_status.html para refresco via fetch."""
    estado = wa_admin.estado_sesion()
    return templates.TemplateResponse("_wa_status.html", {"request": request, "estado": estado})


@router.get("/whatsapp/qr")
async def whatsapp_qr(request: Request, db: Session = Depends(get_db)):
    """Devuelve el QR actual (base64 PNG + codigo) si lo hay."""
    qr = wa_admin.obtener_qr()
    if qr is None:
        return JSONResponse({"qr": None, "mensaje": "No hay QR disponible. La sesion ya esta vinculada o aun no se inicio."}, status_code=404)
    return JSONResponse(qr)


@router.post("/whatsapp/start")
async def whatsapp_start(request: Request, db: Session = Depends(get_db)):
    usuario = "admin"
    res = wa_admin.iniciar_sesion()
    audit(db, request, usuario, "WHATSAPP_START", "OpenWA", settings.OPENWA_SESSION_ID, str(res))
    db.commit()
    return JSONResponse(res)


@router.post("/whatsapp/restart")
async def whatsapp_restart(request: Request, db: Session = Depends(get_db)):
    usuario = "admin"
    res = wa_admin.reiniciar_sesion()
    audit(db, request, usuario, "WHATSAPP_RESTART", "OpenWA", settings.OPENWA_SESSION_ID, str(res))
    db.commit()
    return JSONResponse(res)


@router.post("/whatsapp/logout")
async def whatsapp_logout(request: Request, db: Session = Depends(get_db)):
    usuario = "admin"
    res = wa_admin.cerrar_sesion()
    audit(db, request, usuario, "WHATSAPP_LOGOUT", "OpenWA", settings.OPENWA_SESSION_ID, str(res))
    db.commit()
    return JSONResponse(res)


# =========================================================
# Conciliacion, reset, etc. (con audit)
# =========================================================

@router.post("/conciliar/upload")
async def upload_conciliacion(
    request: Request,
    file: UploadFile = File(...),
    db: Session = Depends(get_db)
):
    usuario = "admin"
    try:
        content = await file.read()
        if file.filename.endswith(".csv"):
            df = pd.read_csv(io.BytesIO(content))
        elif file.filename.endswith(".xlsx") or file.filename.endswith(".xls"):
            df = pd.read_excel(io.BytesIO(content))
        else:
            raise HTTPException(status_code=400, detail="Formato de archivo no soportado. Usa CSV o Excel.")

        if "Referencia" not in df.columns or "Monto" not in df.columns:
            raise HTTPException(status_code=400, detail="El archivo debe contener las columnas 'Referencia' y 'Monto'")

        df["ReferenciaLimpia"] = df["Referencia"].astype(str).str.strip().str.lstrip("0")
        # Hash de las referencias del archivo para compararlos contra la BD
        df["ReferenciaHash"] = df["ReferenciaLimpia"].apply(
            lambda r: crypto.hash_busqueda(r) if r and r != "nan" else None
        )

        tickets_pendientes = db.execute(
            select(Tickets).where(Tickets.estado == "Reservado", Tickets.referencia_pago_hash.isnot(None))
        ).scalars().all()

        aprobados = 0
        tickets_a_actualizar = []
        for ticket in tickets_pendientes:
            if not ticket.referencia_pago_hash or not ticket.monto_reportado:
                continue
            monto_ticket = float(ticket.monto_reportado)
            match = df[
                (df["ReferenciaHash"] == ticket.referencia_pago_hash) &
                (abs(pd.to_numeric(df["Monto"], errors='coerce') - monto_ticket) < 1.0)
            ]
            if not match.empty:
                tickets_a_actualizar.append(ticket.id)
                aprobados += 1

        if tickets_a_actualizar:
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
            audit(db, request, usuario, "CONCILIAR", "LotesConciliacion", lote.id if lote.id else None,
                  f"archivo={file.filename} procesados={len(df)} aprobados={aprobados}")
            db.commit()
        else:
            audit(db, request, usuario, "CONCILIAR_SIN_MATCHES", None, None,
                  f"archivo={file.filename} procesados={len(df)}")
            db.commit()

        return {
            "filename": file.filename,
            "procesados": len(df),
            "aprobados": aprobados
        }
    except Exception as e:
        db.rollback()
        if not isinstance(e, HTTPException):
            raise HTTPException(status_code=400, detail=f"Error procesando el archivo: {str(e)}")
        raise e


@router.post("/reset-db")
async def reset_database(request: Request, db: Session = Depends(get_db)):
    usuario = "admin"
    audit(db, request, usuario, "RESET_DB", "DB", None, "reset manual")
    try:
        db.execute(
            update(Tickets)
            .values(
                estado="Disponible",
                aportante_id=None,
                reservado_en=None,
                referencia_pago=None,
                referencia_pago_hash=None,
                monto_reportado=None
            )
        )
        db.query(Aportantes).delete()
        db.query(LotesConciliacion).delete()
        campana = db.query(Campana).filter(Campana.activa == True).first()
        if campana:
            campana.recaudado_manual = 0.00
        db.commit()
        return {"status": "success", "message": "Base de datos y rifa reiniciadas con exito."}
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al reiniciar base de datos: {str(e)}"
        )


@router.post("/reversar/todos")
async def reversar_todos_reservados(request: Request, db: Session = Depends(get_db)):
    usuario = "admin"
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
                referencia_pago_hash=None,
                monto_reportado=None
            )
        )
        audit(db, request, usuario, "REVERSE_ALL", "Tickets", None, f"liberados={len(vencidos_ids)}")
        db.commit()
        return {"status": "success", "message": f"Se liberaron {len(vencidos_ids)} tickets reservados."}
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al liberar tickets: {str(e)}"
        )


@router.post("/reversar/referencia/{referencia}")
async def reversar_por_referencia(referencia: str, request: Request, db: Session = Depends(get_db)):
    """
    Libera los tickets reservados cuya referencia coincida.
    La comparacion se hace por HMAC (no por texto plano) para no romper
    la proteccion de datos cifrados.
    """
    usuario = "admin"
    ref_hash = crypto.hash_busqueda(referencia)
    if not ref_hash:
        raise HTTPException(status_code=400, detail="Referencia invalida")
    try:
        stmt = select(Tickets).where(
            Tickets.estado == "Reservado",
            Tickets.referencia_pago_hash == ref_hash
        )
        reservados = db.execute(stmt).scalars().all()
        if not reservados:
            raise HTTPException(status_code=404, detail=f"No se encontraron tickets reservados con la referencia proporcionada")
        vencidos_ids = [t.id for t in reservados]
        db.execute(
            update(Tickets)
            .where(Tickets.id.in_(vencidos_ids))
            .values(
                estado="Disponible",
                reservado_en=None,
                aportante_id=None,
                referencia_pago=None,
                referencia_pago_hash=None,
                monto_reportado=None
            )
        )
        audit(db, request, usuario, "REVERSE_REFERENCIA", "Tickets", None, f"liberados={len(vencidos_ids)}")
        db.commit()
        return {"status": "success", "message": f"Se liberaron {len(vencidos_ids)} tickets para la referencia."}
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error al liberar tickets por referencia: {str(e)}"
        )


@router.post("/confirmar/aportante/{aportante_id}", response_class=HTMLResponse)
async def confirmar_aportante(aportante_id: int, request: Request, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    """Confirma tickets y notifica por WhatsApp (decifrando el telefono)."""
    usuario = "admin"
    try:
        aportante = db.get(Aportantes, aportante_id)
        if not aportante:
            return HTMLResponse(
                '<span class="badge badge-danger-custom">Aportante no encontrado</span>',
                status_code=404,
            )

        tickets_pagados = db.execute(
            select(Tickets)
            .where(Tickets.aportante_id == aportante_id, Tickets.estado == "Reservado")
        ).scalars().all()

        if not tickets_pagados:
            return HTMLResponse(
                '<span class="badge badge-warning-custom"><i class="fa-solid fa-circle-info me-1"></i>Sin pendientes</span>'
            )

        rifa_id = tickets_pagados[0].rifa_id
        rifa = db.get(Rifas, rifa_id)

        db.execute(
            update(Tickets)
            .where(Tickets.aportante_id == aportante_id, Tickets.estado == "Reservado")
            .values(estado="Pagado")
        )

        # Descifrar PII solo en el momento del envio (no se guarda en ningun log)
        telefono = crypto.descifrar(aportante.telefono)
        nombre = crypto.descifrar(aportante.nombre)

        if telefono and rifa and nombre:
            numeros = [f"{t.numero:04d}" for t in tickets_pagados]
            # Background: el envio respeta delay humano y rate limit
            background_tasks.add_task(
                wa.notificar_confirmacion_tickets,
                telefono=telefono,
                nombre=nombre,
                cantidad=len(tickets_pagados),
                numeros=numeros,
                rifa_titulo=rifa.titulo,
            )

        audit(db, request, usuario, "CONFIRM", "Aportante", aportante_id,
              f"tickets={len(tickets_pagados)} rifa={rifa.titulo if rifa else '-'}")
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
async def reversar_aportante(aportante_id: int, request: Request, db: Session = Depends(get_db)):
    usuario = "admin"
    try:
        db.execute(
            update(Tickets)
            .where(Tickets.aportante_id == aportante_id)
            .values(
                estado="Disponible",
                reservado_en=None,
                aportante_id=None,
                referencia_pago=None,
                referencia_pago_hash=None,
                monto_reportado=None
            )
        )
        audit(db, request, usuario, "REVERSE_APORTANTE", "Aportante", aportante_id, None)
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


@router.post("/reasignar/aportante/{aportante_id}", response_class=HTMLResponse)
async def reasignar_aportante(aportante_id: int, request: Request, db: Session = Depends(get_db)):
    usuario = "admin"
    try:
        aportante = db.get(Aportantes, aportante_id)
        if not aportante:
            raise HTTPException(status_code=404, detail="Aportante no encontrado")
            
        if not aportante.boletos_iniciales:
            raise HTTPException(status_code=400, detail="No hay registro de boletos iniciales para este aportante")
            
        # Parsear boletos
        numeros = [int(n.strip()) for n in aportante.boletos_iniciales.split(",") if n.strip().isdigit()]
        if not numeros:
            raise HTTPException(status_code=400, detail="El formato de boletos iniciales es inválido")
            
        # Buscar el estado de esos tickets en la rifa activa
        rifa = db.execute(select(Rifas).where(Rifas.estado == "Activa")).scalar_one_or_none()
        if not rifa:
            raise HTTPException(status_code=400, detail="No hay una rifa activa")
            
        stmt = select(Tickets).where(Tickets.rifa_id == rifa.id, Tickets.numero.in_(numeros))
        tickets = db.execute(stmt).scalars().all()
        
        # Verificar disponibilidad
        ocupados = [t.numero for t in tickets if t.estado != "Disponible"]
        if ocupados:
            ocupados_str = ", ".join([f"{n:03d}" for n in ocupados])
            return HTMLResponse(
                f'<span class="text-danger small fw-bold"><i class="fa-solid fa-triangle-exclamation"></i> Ocupados: {ocupados_str}</span>'
            )
            
        # Reasignar como "Reservado"
        precio_unitario = float(rifa.precio_ticket_usd) if aportante.moneda == "USD" else float(rifa.precio_ticket_bs)
        for t in tickets:
            t.estado = "Reservado"
            t.reservado_en = datetime.now(timezone.utc)
            t.aportante_id = aportante.id
            t.referencia_pago = aportante.referencia
            t.referencia_pago_hash = aportante.referencia_hash
            t.monto_reportado = precio_unitario
            
        audit(db, request, usuario, "REASSIGN_APORTANTE", "Aportante", aportante_id, f"boletos={len(tickets)}")
        db.commit()
        
        return HTMLResponse(
            '<script>alert("Boletos reasignados con éxito como Reservados."); window.location.reload();</script>'
        )
    except Exception as e:
        db.rollback()
        return HTMLResponse(
            f'<span class="text-danger small">Error: {str(e)}</span>',
            status_code=500
        )
