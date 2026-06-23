import io
import logging
import pandas as pd
import secrets
from dataclasses import dataclass
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File, Request, BackgroundTasks, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from sqlalchemy.orm import Session
from sqlalchemy import select, update, func
from datetime import datetime, timezone

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
            numeros = [f"{t.numero:03d}" for t in tickets_pagados]
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


@router.get("/reasignar/aportante/{aportante_id}/form", response_class=HTMLResponse)
async def reasignar_aportante_form(aportante_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        aportante = db.get(Aportantes, aportante_id)
        if not aportante:
            raise HTTPException(status_code=404, detail="Aportante no encontrado")
            
        nombre_decrypted = crypto.descifrar(aportante.nombre) or "Anónimo"
        
        rifa = db.execute(select(Rifas).where(Rifas.estado == "Activa")).scalar_one_or_none()
        if not rifa:
            raise HTTPException(status_code=400, detail="No hay una rifa activa")
            
        precio_unitario = float(rifa.precio_ticket_usd) if aportante.moneda == "USD" else float(rifa.precio_ticket_bs)
        
        # Calcular recomendación de cantidad
        monto = float(aportante.monto_reportado) if aportante.monto_reportado else 0.0
        recomendado = int(monto / precio_unitario) if precio_unitario > 0 else 1
        if recomendado < 1:
            recomendado = 1
            
        # Parsear y verificar boletos iniciales
        original_tickets_status = []
        all_original_available = True
        original_numeros = []
        if aportante.boletos_iniciales:
            original_numeros = [int(n.strip()) for n in aportante.boletos_iniciales.split(",") if n.strip().isdigit()]
            if original_numeros:
                stmt = select(Tickets).where(Tickets.rifa_id == rifa.id, Tickets.numero.in_(original_numeros))
                tickets = db.execute(stmt).scalars().all()
                tickets_by_num = {t.numero: t for t in tickets}
                for num in original_numeros:
                    t = tickets_by_num.get(num)
                    disponible = (t.estado == "Disponible" or t.aportante_id == aportante.id) if t else False
                    if not disponible:
                        all_original_available = False
                    original_tickets_status.append({
                        "numero": num,
                        "disponible": disponible,
                        "estado": t.estado if t else "No existe"
                    })
        else:
            all_original_available = False

        # Total de boletos libres (para informar al admin)
        libres_count = db.scalar(
            select(func.count(Tickets.id))
            .where(Tickets.rifa_id == rifa.id, Tickets.estado == "Disponible")
        ) or 0

        # Candidatos para selección manual: priorizamos los originales libres
        # y, si faltan para llegar a 30, completamos con los primeros libres
        # (ordenados por número, NO al azar, para que la lista sea estable).
        MANUAL_PICKER_LIMIT = 30
        manual_candidates = []
        seen_nums = set()
        if original_numeros:
            libres_originales_nums = [
                item["numero"] for item in original_tickets_status if item["disponible"]
            ]
            if libres_originales_nums:
                stmt = (
                    select(Tickets)
                    .where(
                        Tickets.rifa_id == rifa.id,
                        Tickets.numero.in_(libres_originales_nums),
                        Tickets.estado == "Disponible",
                    )
                    .order_by(Tickets.numero)
                    .limit(MANUAL_PICKER_LIMIT)
                )
                rows = db.execute(stmt).scalars().all()
                for t in rows:
                    manual_candidates.append({"numero": t.numero, "origen": "original"})
                    seen_nums.add(t.numero)
        # Completar con libres adicionales si todavía no llegamos al límite
        if len(manual_candidates) < MANUAL_PICKER_LIMIT:
            restantes = MANUAL_PICKER_LIMIT - len(manual_candidates)
            stmt = (
                select(Tickets.numero)
                .where(
                    Tickets.rifa_id == rifa.id,
                    Tickets.estado == "Disponible",
                    Tickets.numero.notin_(seen_nums) if seen_nums else True,
                )
                .order_by(Tickets.numero)
                .limit(restantes)
            )
            rows = db.execute(stmt).scalars().all()
            for num in rows:
                manual_candidates.append({"numero": num, "origen": "extra"})
                seen_nums.add(num)
            
        return templates.TemplateResponse(
            "_modal_reasignar.html",
            {
                "request": request,
                "aportante": aportante,
                "nombre_decrypted": nombre_decrypted,
                "precio_unitario": precio_unitario,
                "recomendado": recomendado,
                "original_tickets_status": original_tickets_status,
                "all_original_available": all_original_available,
                "libres_count": libres_count,
                "manual_candidates": manual_candidates,
            }
        )
    except Exception as e:
        logger.exception(f"Error cargando formulario de reasignación: {e}")
        return HTMLResponse(f'<div class="alert alert-danger">Error: {str(e)}</div>', status_code=500)


@router.post("/reasignar/aportante/{aportante_id}", response_class=HTMLResponse)
async def reasignar_aportante(
    aportante_id: int,
    request: Request,
    background_tasks: BackgroundTasks,
    mode: str = Form(...),
    cantidad: int = Form(...),
    numeros_manual: Optional[str] = Form(None),
    notificar_wa: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """
    Reasigna boletos a un aportante. Soporta 4 modos:
      - "original_only_free": solo asigna los originales que estén Disponible.
        La cantidad final puede ser menor a la solicitada.
      - "mixed": toma los originales libres y completa con al azar
        hasta llegar a la cantidad solicitada.
      - "random": asigna todos al azar (ignora los originales).
      - "manual": asigna EXACTAMENTE la lista enviada en numeros_manual (CSV).

    Siempre:
      - Libera los tickets previos del aportante (estado -> Disponible).
      - Marca los nuevos como "Reservado" con referencia/monto de la reasignación.
      - Actualiza boletos_iniciales con la nueva lista.
      - Si notificar_wa == "on" y el aportante tiene teléfono, envía un
        mensaje de reasignación por WhatsApp (background, con rate limit).
    """
    usuario = "admin"
    try:
        aportante = db.get(Aportantes, aportante_id)
        if not aportante:
            return HTMLResponse('<div class="alert alert-danger">Aportante no encontrado</div>', status_code=404)
            
        rifa = db.execute(select(Rifas).where(Rifas.estado == "Activa")).scalar_one_or_none()
        if not rifa:
            return HTMLResponse('<div class="alert alert-danger">No hay una rifa activa</div>', status_code=400)
            
        precio_unitario = float(rifa.precio_ticket_usd) if aportante.moneda == "USD" else float(rifa.precio_ticket_bs)
        
        # Validar cantidad
        if cantidad < 1:
            return HTMLResponse('<div class="alert alert-danger">La cantidad de boletos debe ser al menos 1</div>', status_code=400)

        # Validar mode
        modos_validos = {"original_only_free", "mixed", "random", "manual"}
        if mode not in modos_validos:
            return HTMLResponse(
                f'<div class="alert alert-danger">Modo inválido: {mode}. Use: {", ".join(sorted(modos_validos))}.</div>',
                status_code=400,
            )

        # Parsear originales (para modos que los usan)
        original_numeros = []
        if aportante.boletos_iniciales:
            original_numeros = [int(n.strip()) for n in aportante.boletos_iniciales.split(",") if n.strip().isdigit()]

        # 1) Liberar boletos actuales de este aportante antes de asignar nuevos
        stmt_old = select(Tickets).where(Tickets.aportante_id == aportante.id)
        old_tickets = db.execute(stmt_old).scalars().all()
        for ot in old_tickets:
            ot.estado = "Disponible"
            ot.reservado_en = None
            ot.aportante_id = None
            ot.referencia_pago = None
            ot.referencia_pago_hash = None
            ot.monto_reportado = 0.0
        db.flush()
        
        tickets_to_assign = []
        detalle_log = {
            "mode": mode,
            "cantidad_solicitada": cantidad,
            "originales": len(original_numeros),
        }

        # 2) Determinar los tickets a asignar según el modo
        if mode == "original_only_free":
            if not original_numeros:
                return HTMLResponse(
                    '<div class="alert alert-danger">No hay boletos originales registrados.</div>',
                    status_code=400,
                )
            stmt = (
                select(Tickets)
                .where(
                    Tickets.rifa_id == rifa.id,
                    Tickets.numero.in_(original_numeros),
                    Tickets.estado == "Disponible",
                )
                .order_by(Tickets.numero)
            )
            if db.bind.dialect.name != "sqlite":
                stmt = stmt.with_for_update()
            libres = db.execute(stmt).scalars().all()
            # Solo tomamos hasta `cantidad` (cap superior; si hay menos, los tomamos todos)
            tickets_to_assign = list(libres[:cantidad])
            detalle_log["libres_originales"] = len(libres)
            detalle_log["asignados"] = len(tickets_to_assign)

        elif mode == "mixed":
            # 1) Originales libres
            stmt = (
                select(Tickets)
                .where(
                    Tickets.rifa_id == rifa.id,
                    Tickets.numero.in_(original_numeros) if original_numeros else False,
                    Tickets.estado == "Disponible",
                )
                .order_by(Tickets.numero)
            )
            if db.bind.dialect.name != "sqlite":
                stmt = stmt.with_for_update()
            libres_originales = list(db.execute(stmt).scalars().all())
            tickets_to_assign.extend(libres_originales)

            # 2) Completar con al azar (excluyendo los que ya tomamos)
            faltan = cantidad - len(tickets_to_assign)
            if faltan > 0:
                nums_ya_tomados = [t.numero for t in tickets_to_assign]
                stmt = (
                    select(Tickets)
                    .where(
                        Tickets.rifa_id == rifa.id,
                        Tickets.estado == "Disponible",
                        Tickets.numero.notin_(nums_ya_tomados) if nums_ya_tomados else True,
                    )
                    .order_by(func.random())
                    .limit(faltan)
                )
                if db.bind.dialect.name != "sqlite":
                    stmt = stmt.with_for_update()
                random_extra = list(db.execute(stmt).scalars().all())
                tickets_to_assign.extend(random_extra)

            detalle_log["libres_originales"] = len(libres_originales)
            detalle_log["random_extra"] = len(tickets_to_assign) - len(libres_originales)
            detalle_log["asignados"] = len(tickets_to_assign)

        elif mode == "random":
            stmt = (
                select(Tickets)
                .where(Tickets.rifa_id == rifa.id, Tickets.estado == "Disponible")
                .order_by(func.random())
                .limit(cantidad)
            )
            if db.bind.dialect.name != "sqlite":
                stmt = stmt.with_for_update()
            tickets_to_assign = list(db.execute(stmt).scalars().all())
            detalle_log["asignados"] = len(tickets_to_assign)

        elif mode == "manual":
            if not numeros_manual:
                return HTMLResponse(
                    '<div class="alert alert-danger">Debes marcar al menos un número en modo manual.</div>',
                    status_code=400,
                )
            # Parsear CSV -> set de enteros
            try:
                nums_pedidos = []
                vistos = set()
                for chunk in numeros_manual.split(","):
                    chunk = chunk.strip()
                    if not chunk:
                        continue
                    n = int(chunk)
                    if n in vistos:
                        continue
                    vistos.add(n)
                    nums_pedidos.append(n)
            except ValueError:
                return HTMLResponse(
                    '<div class="alert alert-danger">La lista de números manuales contiene valores no numéricos.</div>',
                    status_code=400,
                )
            if not nums_pedidos:
                return HTMLResponse(
                    '<div class="alert alert-danger">Debes marcar al menos un número en modo manual.</div>',
                    status_code=400,
                )
            # Rango válido
            fuera_de_rango = [n for n in nums_pedidos if n < 0 or n > rifa.total_numeros - 1]
            if fuera_de_rango:
                return HTMLResponse(
                    f'<div class="alert alert-danger">Números fuera de rango (0-{rifa.total_numeros-1}): {", ".join(str(n) for n in fuera_de_rango)}.</div>',
                    status_code=400,
                )
            stmt = (
                select(Tickets)
                .where(
                    Tickets.rifa_id == rifa.id,
                    Tickets.numero.in_(nums_pedidos),
                )
            )
            if db.bind.dialect.name != "sqlite":
                stmt = stmt.with_for_update()
            encontrados = list(db.execute(stmt).scalars().all())
            encontrados_por_num = {t.numero: t for t in encontrados}
            # Detectar números que no existen
            faltantes = [n for n in nums_pedidos if n not in encontrados_por_num]
            if faltantes:
                return HTMLResponse(
                    f'<div class="alert alert-danger">Estos números no existen en la rifa: {", ".join(str(n) for n in faltantes)}.</div>',
                    status_code=400,
                )
            # Detectar números que no están disponibles
            ocupados = [t for t in encontrados if t.estado != "Disponible"]
            if ocupados:
                nums_oc = ", ".join(f"{t.numero:03d}" for t in ocupados)
                return HTMLResponse(
                    f'<div class="alert alert-danger">Estos números ya no están disponibles: {nums_oc}. Cierra y vuelve a abrir el modal para refrescar la lista.</div>',
                    status_code=400,
                )
            tickets_to_assign = encontrados
            detalle_log["solicitados"] = len(nums_pedidos)
            detalle_log["asignados"] = len(tickets_to_assign)

        # 3) Verificación final común: tenemos suficientes boletos?
        # - En "original_only_free" permitimos menos (es la semántica del modo)
        # - En los demás, la cantidad debe coincidir
        if mode != "original_only_free" and len(tickets_to_assign) < cantidad:
            return HTMLResponse(
                f'<div class="alert alert-danger">No hay suficientes boletos disponibles. Se necesitaban {cantidad} y se consiguieron {len(tickets_to_assign)}.</div>',
                status_code=400,
            )
        if not tickets_to_assign:
            return HTMLResponse(
                '<div class="alert alert-danger">No fue posible asignar ningún boleto con los criterios elegidos.</div>',
                status_code=400,
            )

        # 4) Marcar como "Reservado"
        ahora = datetime.now(timezone.utc)
        for t in tickets_to_assign:
            t.estado = "Reservado"
            t.reservado_en = ahora
            t.aportante_id = aportante.id
            t.referencia_pago = aportante.referencia
            t.referencia_pago_hash = aportante.referencia_hash
            t.monto_reportado = precio_unitario

        # 5) Actualizar boletos_iniciales con la nueva lista
        nuevos_nums_str = ", ".join([f"{t.numero:03d}" for t in tickets_to_assign])
        aportante.boletos_iniciales = nuevos_nums_str

        # 6) Audit
        detalle_log["boletos"] = [t.numero for t in tickets_to_assign]
        detalle_log["notificar_wa"] = bool(notificar_wa)
        audit(
            db, request, usuario, "REASSIGN_APORTANTE", "Aportante", aportante_id,
            f"boletos_asignados={len(tickets_to_assign)} " + " ".join(f"{k}={v}" for k, v in detalle_log.items()),
        )
        db.commit()

        # 7) Notificación WhatsApp en background (si se pidió y hay teléfono)
        wa_enviado_ok = None
        if notificar_wa == "on":
            telefono = crypto.descifrar(aportante.telefono) if aportante.telefono else None
            nombre = crypto.descifrar(aportante.nombre) or "amigo/a"
            if telefono:
                numeros_str = [f"{t.numero:03d}" for t in tickets_to_assign]
                # Capturamos el resultado en una lista mutable para que la lambda pueda escribir
                wa_result: list[bool] = [False]
                def _tarea_wa(tel=telefono, nom=nombre, cant=len(tickets_to_assign), nums=numeros_str, sink=wa_result):
                    try:
                        sink[0] = wa.notificar_reasignacion(tel, nom, cant, nums)
                    except Exception as e:
                        logger.error(f"Error enviando WA de reasignación: {e}")
                        sink[0] = False
                background_tasks.add_task(_tarea_wa)
                wa_enviado_ok = "scheduled"

        msg = '<div class="alert alert-success border-0 small mb-0"><i class="fa-solid fa-circle-check me-1"></i> Boletos reasignados con éxito.'
        if wa_enviado_ok == "scheduled":
            msg += ' Notificación WhatsApp en cola.'
        msg += ' Recargando...</div><script>setTimeout(() => { window.location.reload(); }, 1500);</script>'
        return HTMLResponse(msg)
    except Exception as e:
        db.rollback()
        logger.exception(f"Error procesando reasignación: {e}")
        return HTMLResponse(f'<div class="alert alert-danger">Error: {str(e)}</div>', status_code=500)


@router.get("/whatsapp/aportante/{aportante_id}/mensaje", response_class=HTMLResponse)
async def whatsapp_mensaje_form(aportante_id: int, request: Request, db: Session = Depends(get_db)):
    try:
        aportante = db.get(Aportantes, aportante_id)
        if not aportante:
            raise HTTPException(status_code=404, detail="Aportante no encontrado")
            
        nombre_decrypted = crypto.descifrar(aportante.nombre) or "Anónimo"
        telefono_decrypted = crypto.descifrar(aportante.telefono) or ""
        
        # Generar un mensaje por defecto simpático y descriptivo
        tickets_nums = ", ".join([f"{t.numero:03d}" for t in aportante.tickets])
        if tickets_nums:
            mensaje_default = (
                f"Hola {nombre_decrypted}, un saludo de parte de Juntos por Oriana. 💛\n\n"
                f"Te escribimos para confirmar que tus boletos para la Gran Rifa Solidaria son: {tickets_nums}.\n\n"
                f"¡Muchísimas gracias por tu generoso apoyo! Cada aporte cuenta mucho para la salud de Oriana. 🙏"
            )
            mensaje_reasignacion = (
                f"Hola {nombre_decrypted}, un saludo de parte de Juntos por Oriana. 💛\n\n"
                f"Te escribimos para informarte que hemos reasignado los números de tus boletos para la Gran Rifa Solidaria. "
                f"Debido a un inconveniente en el sistema, los números anteriores no se estaban asignando de forma aleatoria como corresponde.\n\n"
                f"Para garantizar la total transparencia del sorteo, tus nuevos números asignados al azar son: {tickets_nums}.\n\n"
                f"Agradecemos enormemente tu comprensión y tu valioso apoyo para la salud de Oriana. 🙏"
            )
        else:
            mensaje_default = (
                f"Hola {nombre_decrypted}, un saludo de parte de Juntos por Oriana. 💛\n\n"
                f"Nos comunicamos contigo sobre tu reporte de pago para la Rifa. ¡Muchas gracias por tu intención de colaborar! 🙏"
            )
            mensaje_reasignacion = (
                f"Hola {nombre_decrypted}, un saludo de parte de Juntos por Oriana. 💛\n\n"
                f"Nos comunicamos contigo para informarte que debido a una reasignación en el sistema, procesaremos nuevamente tu reporte de pago. "
                f"¡Muchas gracias por tu paciencia y por tu intención de colaborar! 🙏"
            )
            
        return templates.TemplateResponse(
            "_modal_whatsapp.html",
            {
                "request": request,
                "aportante": aportante,
                "nombre_decrypted": nombre_decrypted,
                "telefono_decrypted": telefono_decrypted,
                "mensaje_default": mensaje_default,
                "mensaje_reasignacion": mensaje_reasignacion
            }
        )
    except Exception as e:
        logger.exception(f"Error cargando formulario de mensaje de WhatsApp: {e}")
        return HTMLResponse(f'<div class="alert alert-danger">Error: {str(e)}</div>', status_code=500)


@router.post("/whatsapp/aportante/{aportante_id}/enviar", response_class=HTMLResponse)
async def whatsapp_enviar_mensaje(
    aportante_id: int,
    request: Request,
    mensaje: str = Form(...),
    db: Session = Depends(get_db)
):
    usuario = "admin"
    try:
        aportante = db.get(Aportantes, aportante_id)
        if not aportante:
            return HTMLResponse('<div class="alert alert-danger">Aportante no encontrado</div>', status_code=404)
            
        telefono_decrypted = crypto.descifrar(aportante.telefono)
        if not telefono_decrypted:
            return HTMLResponse('<div class="alert alert-danger">Este aportante no posee teléfono registrado</div>', status_code=400)
            
        # Llamar al servicio de whatsapp para enviar el texto
        success = wa.enviar_texto(telefono_decrypted, mensaje)
        
        if success:
            audit(db, request, usuario, "SEND_CUSTOM_WA", "Aportante", aportante_id, f"telefono={telefono_decrypted[:8]}...")
            db.commit()
            return HTMLResponse(
                '<div class="alert alert-success border-0 small mb-0"><i class="fa-solid fa-circle-check me-1"></i> Mensaje enviado con éxito.</div>'
                '<script>setTimeout(() => { bootstrap.Modal.getInstance(document.getElementById("whatsappModal")).hide(); }, 2000);</script>'
            )
        else:
            import urllib.parse
            # Normalizar el telefono para el enlace (quitar +, espacios, guiones)
            cleaned_phone = "".join([c for c in telefono_decrypted if c.isdigit()])
            if len(cleaned_phone) == 10 and cleaned_phone.startswith("4"):
                cleaned_phone = "58" + cleaned_phone
            elif len(cleaned_phone) == 11 and cleaned_phone.startswith("04"):
                cleaned_phone = "58" + cleaned_phone[1:]
                
            mensaje_encoded = urllib.parse.quote(mensaje)
            wa_link = f"https://api.whatsapp.com/send?phone={cleaned_phone}&text={mensaje_encoded}"
            
            return HTMLResponse(
                f'<div class="alert alert-warning border-0 small mb-3">'
                f'  <i class="fa-solid fa-triangle-exclamation me-1"></i> No se pudo enviar automáticamente por OpenWA.'
                f'</div>'
                f'<a href="{wa_link}" target="_blank" class="btn btn-sm btn-success w-100 fw-semibold rounded-3 mb-0" '
                f'   onclick="setTimeout(() => {{ bootstrap.Modal.getInstance(document.getElementById(\'whatsappModal\')).hide(); }}, 1000);">'
                f'  <i class="fa-solid fa-arrow-up-right-from-square me-1"></i> Enviar por WhatsApp Web (Manual)'
                f'</a>'
            )
    except Exception as e:
        logger.exception(f"Error enviando mensaje de WhatsApp: {e}")
        return HTMLResponse(f'<div class="alert alert-danger">Error: {str(e)}</div>', status_code=500)
