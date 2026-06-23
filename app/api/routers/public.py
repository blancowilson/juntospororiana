import logging
import random
from fastapi import APIRouter, Request, Form, Depends, BackgroundTasks
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import select, update, desc
from datetime import datetime, timezone

from app.db.session import get_db
from app.models.all_models import Campana, Rifas, Tickets, Aportantes
from app.schemas.public import DonacionIn, ReservaTicketIn
from app.services import whatsapp as wa
from app.services import crypto

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="templates")

# Limite de tiempo de validez de un captcha en segundos
CAPTCHA_TTL = 600  # 10 minutos

def _generar_captcha(request: Request) -> str:
    """Genera una pregunta matematica simple y guarda la respuesta en la sesion."""
    n1 = random.randint(2, 9)
    n2 = random.randint(1, 9)
    pregunta = f"{n1} + {n2}"
    respuesta = n1 + n2
    request.session["captcha_q"] = pregunta
    request.session["captcha_a"] = respuesta
    request.session["captcha_t"] = int(datetime.now(timezone.utc).timestamp())
    return pregunta

def _validar_captcha(request: Request, respuesta_usuario: str) -> tuple[bool, str]:
    """Valida la respuesta del captcha contra la almacenada en sesion.
    Retorna (es_valido, mensaje_error).
    """
    esperada = request.session.get("captcha_a")
    timestamp = request.session.get("captcha_t")
    if esperada is None or timestamp is None:
        return False, "Captcha expirado. Recarga la pagina."
    # Verificar expiracion
    ahora = int(datetime.now(timezone.utc).timestamp())
    if ahora - timestamp > CAPTCHA_TTL:
        return False, "Captcha expirado. Recarga la pagina."
    # Verificar respuesta
    try:
        usuario = int(str(respuesta_usuario).strip())
    except (ValueError, TypeError):
        return False, "Captcha invalido."
    if usuario != esperada:
        return False, "Captcha incorrecto. Intenta de nuevo."
    return True, ""

def _es_bot(website: str | None) -> bool:
    """Honeypot: si el campo oculto esta lleno, es un bot."""
    return bool(website and website.strip())

@router.get("/", response_class=HTMLResponse)
async def landing_page(request: Request, db: Session = Depends(get_db)):
    # 1. Obtener campaña activa
    campana = db.execute(select(Campana).where(Campana.activa == True)).scalar_one_or_none()
    if not campana:
        campana = Campana(meta_total=2750.00, recaudado_manual=0.00)

    # 2. Obtener rifa activa
    rifa = db.execute(select(Rifas).where(Rifas.estado == "Activa")).scalar_one_or_none()

    # 3. Obtener últimos 30 aportantes activos (donaciones o rifas con boletos reservados/pagados)
    from sqlalchemy import or_, exists
    stmt_aportantes = (
        select(Aportantes)
        .where(
            or_(
                Aportantes.tipo_aporte == "Donacion",
                exists().where(
                    (Tickets.aportante_id == Aportantes.id) & 
                    (Tickets.estado.in_(["Reservado", "Pagado"]))
                )
            )
        )
        .order_by(desc(Aportantes.fecha_aporte))
        .limit(30)
    )
    aportantes = db.execute(stmt_aportantes).scalars().all()

    # Decifrar nombres
    from app.services import crypto
    for ap in aportantes:
        try:
            ap.nombre_decrypted = crypto.descifrar(ap.nombre) or "Anónimo"
        except Exception as e:
            logger.error(f"Error descifrando nombre del aportante {ap.id}: {e}")
            ap.nombre_decrypted = "Anónimo"

    # Contar boletos disponibles
    from sqlalchemy import func
    boletos_disponibles = 0
    if rifa:
        boletos_disponibles = db.scalar(
            select(func.count(Tickets.id)).where(Tickets.rifa_id == rifa.id, Tickets.estado == "Disponible")
        ) or 0

    # Calcular total recaudado (Manual + Aportantes registrados en BD convertidos a USD si están en BS)
    tasa = 833.33
    if rifa and float(rifa.precio_ticket_usd) > 0:
        tasa = float(rifa.precio_ticket_bs) / float(rifa.precio_ticket_usd)

    # Solo sumar donaciones directas o compras de rifas que estén confirmadas ("Pagado")
    recaudado_usd = db.scalar(
        select(func.sum(Aportantes.monto_reportado))
        .where(
            Aportantes.moneda == "USD",
            or_(
                Aportantes.tipo_aporte == "Donacion",
                exists().where(
                    (Tickets.aportante_id == Aportantes.id) & 
                    (Tickets.estado == "Pagado")
                )
            )
        )
    ) or 0.0

    recaudado_bs = db.scalar(
        select(func.sum(Aportantes.monto_reportado))
        .where(
            Aportantes.moneda == "BS",
            or_(
                Aportantes.tipo_aporte == "Donacion",
                exists().where(
                    (Tickets.aportante_id == Aportantes.id) & 
                    (Tickets.estado == "Pagado")
                )
            )
        )
    ) or 0.0

    recaudado_aportes = float(recaudado_usd) + (float(recaudado_bs) / tasa)
    total_recaudado = float(campana.recaudado_manual) + recaudado_aportes

    # Generar captcha fresco para esta carga de pagina
    captcha_q = _generar_captcha(request)

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "campana": campana,
            "rifa": rifa,
            "aportantes": aportantes,
            "boletos_disponibles": boletos_disponibles,
            "total_recaudado": total_recaudado,
            "captcha_q": captcha_q
        }
    )

@router.post("/ticket/comprar-aleatorio", response_class=HTMLResponse)
async def comprar_tickets_aleatorios(
    request: Request,
    background_tasks: BackgroundTasks,
    cantidad: int = Form(...),
    nombre: str = Form(...),
    cedula: str = Form(...),
    telefono: str = Form(...),
    monto_reportado: float = Form(...),
    metodo_pago: str = Form(...),
    referencia: str = Form(...),
    banco_emisor: str = Form(...),
    captcha: str = Form(...),
    website: str = Form(None),  # honeypot - debe estar vacio
    db: Session = Depends(get_db)
):
    # Anti-bot: honeypot
    if _es_bot(website):
        return HTMLResponse('<div class="alert alert-danger">Acceso bloqueado.</div>', status_code=400)
    # Anti-bot: captcha
    valido, msg = _validar_captcha(request, captcha)
    if not valido:
        return HTMLResponse(f'<div class="alert alert-danger">{msg}</div>', status_code=400)
    # Consumir captcha (un solo uso)
    request.session.pop("captcha_a", None)
    request.session.pop("captcha_t", None)

    try:
        # Validación Pydantic
        reserva_data = ReservaTicketIn(
            nombre=nombre,
            cedula=cedula,
            telefono=telefono,
            monto_reportado=monto_reportado,
            metodo_pago=metodo_pago,
            referencia=referencia,
            banco_emisor=banco_emisor,
            cantidad=cantidad
        )
    except Exception as e:
        return HTMLResponse(
            f'<div class="alert alert-danger">Error de validación: {str(e)}</div>',
            status_code=400
        )

    rifa = db.execute(select(Rifas).where(Rifas.estado == "Activa")).scalar_one_or_none()
    if not rifa:
        return HTMLResponse('<div class="alert alert-danger">No hay rifa activa configurada.</div>', status_code=400)

    # 1. Obtener y bloquear tickets disponibles para evitar race conditions (Race Condition Control)
    # limit(cantidad) con with_for_update() asegura que otras transacciones concurrentes esperen o no agarren los mismos registros
    stmt = (
        select(Tickets)
        .where(Tickets.rifa_id == rifa.id, Tickets.estado == "Disponible")
        .limit(reserva_data.cantidad)
    )
    if db.bind.dialect.name != "sqlite":
        stmt = stmt.with_for_update()
    tickets_disponibles = db.execute(stmt).scalars().all()

    if len(tickets_disponibles) < reserva_data.cantidad:
        return HTMLResponse(
            f'<div class="alert alert-danger">Lo sentimos, no hay suficientes boletos disponibles. Solo quedan {len(tickets_disponibles)}.</div>',
            status_code=400
        )

    # 2. Registrar el Aportante (cifrando los datos sensibles)
    nombre_c = crypto.cifrar(reserva_data.nombre)
    cedula_c = crypto.cifrar(reserva_data.cedula)
    telefono_c = crypto.cifrar(reserva_data.telefono)
    referencia_c = crypto.cifrar(reserva_data.referencia)
    nuevo_aportante = Aportantes(
        nombre=nombre_c,
        cedula=cedula_c,
        telefono=telefono_c,
        monto_reportado=reserva_data.monto_reportado,
        moneda="USD" if reserva_data.metodo_pago in ["Zelle", "Binance", "PayPal", "Paypal"] else "BS",
        metodo_pago=reserva_data.metodo_pago,
        referencia=referencia_c,
        cedula_hash=crypto.hash_busqueda(reserva_data.cedula),
        telefono_hash=crypto.hash_busqueda(reserva_data.telefono),
        referencia_hash=crypto.hash_busqueda(reserva_data.referencia),
        tipo_aporte="Rifa"
    )
    db.add(nuevo_aportante)
    db.flush() # Obtener ID del aportante

    # 3. Asignar los tickets al aportante
    numeros_asignados = []
    precio_unitario = float(rifa.precio_ticket_usd) if nuevo_aportante.moneda == "USD" else float(rifa.precio_ticket_bs)

    for ticket in tickets_disponibles:
        ticket.estado = "Reservado"
        ticket.reservado_en = datetime.now(timezone.utc)
        ticket.aportante_id = nuevo_aportante.id
        ticket.referencia_pago = crypto.cifrar(reserva_data.referencia)
        ticket.referencia_pago_hash = crypto.hash_busqueda(reserva_data.referencia)
        ticket.monto_reportado = precio_unitario
        numeros_asignados.append(f"{ticket.numero:04d}") # Formatear ej: "0607"

    nuevo_aportante.boletos_iniciales = ", ".join(numeros_asignados)
    db.commit()

    # Notificacion WhatsApp: revision manual de la rifa (background para no bloquear
    # la respuesta HTTP; el envio se hace con delay humano + rate limit).
    if reserva_data.telefono:
        background_tasks.add_task(
            wa.notificar_recepcion_tickets,
            telefono=reserva_data.telefono,
            nombre=reserva_data.nombre,
            cantidad=reserva_data.cantidad,
            numeros=numeros_asignados,
            monto=float(reserva_data.monto_reportado),
            moneda=nuevo_aportante.moneda,
        )

    # Generar la respuesta HTML del modal exitoso
    numeros_html = "".join([f'<div class="col-6 col-sm-4 mb-2"><span class="badge bg-light text-dark border p-2 w-100 fs-5 fw-bold">{n}</span></div>' for n in numeros_asignados])
    
    html_response = f"""
    <div class="text-center p-4">
        <div class="mb-3">
            <span class="fs-1">🎉</span>
        </div>
        <h4 class="fw-bold text-success">¡Reporte de Compra Recibido!</h4>
        <p class="text-muted">Tus boletos han sido apartados correctamente en nuestro sistema.</p>
        
        <h5 class="mt-4 fw-bold text-dark">Números reservados:</h5>
        <div class="row justify-content-center my-3">
            {numeros_html}
        </div>

        <div class="alert alert-warning border-0 bg-warning-subtle text-dark-emphasis small rounded-3 p-3 my-3 text-start">
            <p class="mb-1 fw-bold text-dark"><i class="fa-solid fa-triangle-exclamation text-warning me-2"></i>Asignación Temporal</p>
            <p class="mb-0 small">Ten en cuenta que la asignación de estos números es <strong>temporal</strong>. Una vez que validemos la efectividad de tu pago, te enviaremos la confirmación definitiva con tus tickets oficiales directamente a tu número de WhatsApp registrado.</p>
        </div>

        <button type="button" class="btn btn-primary-grad px-5 py-2.5 fw-bold rounded-pill" onclick="window.location.reload()">Entendido / Finalizar</button>
    </div>
    """
    return HTMLResponse(html_response)

@router.post("/aportar/directo", response_class=HTMLResponse)
async def aportar_directo(
    request: Request,
    background_tasks: BackgroundTasks,
    nombre: str = Form(...),
    monto_reportado: float = Form(...),
    metodo_pago: str = Form(...),
    referencia: str = Form(...),
    telefono: str = Form(None),
    mensaje_apoyo: str = Form(None),
    captcha: str = Form(...),
    website: str = Form(None),  # honeypot - debe estar vacio
    db: Session = Depends(get_db)
):
    # Anti-bot: honeypot
    if _es_bot(website):
        return HTMLResponse('<div class="alert alert-danger">Acceso bloqueado.</div>', status_code=400)
    # Anti-bot: captcha
    valido, msg = _validar_captcha(request, captcha)
    if not valido:
        return HTMLResponse(f'<div class="alert alert-danger">{msg}</div>', status_code=400)
    # Consumir captcha (un solo uso)
    request.session.pop("captcha_a", None)
    request.session.pop("captcha_t", None)

    try:
        donacion_data = DonacionIn(
            nombre=nombre,
            mensaje_apoyo=mensaje_apoyo,
            monto_reportado=monto_reportado,
            metodo_pago=metodo_pago,
            referencia=referencia,
            telefono=telefono,
        )
    except Exception as e:
        return HTMLResponse(f'<div class="alert alert-danger">Error: {str(e)}</div>', status_code=400)

    nuevo_aportante = Aportantes(
        nombre=crypto.cifrar(donacion_data.nombre),
        telefono=crypto.cifrar(donacion_data.telefono),
        mensaje_apoyo=donacion_data.mensaje_apoyo,
        monto_reportado=donacion_data.monto_reportado,
        moneda="USD",
        metodo_pago=donacion_data.metodo_pago,
        referencia=crypto.cifrar(donacion_data.referencia),
        telefono_hash=crypto.hash_busqueda(donacion_data.telefono),
        referencia_hash=crypto.hash_busqueda(donacion_data.referencia),
        tipo_aporte="Donacion"
    )
    db.add(nuevo_aportante)
    db.commit()
    db.refresh(nuevo_aportante)

    # Notificacion WhatsApp (background: no bloquea la respuesta, delay humano + rate limit)
    if donacion_data.telefono:
        background_tasks.add_task(
            wa.notificar_donacion,
            telefono=donacion_data.telefono,
            nombre=donacion_data.nombre,
            monto=float(donacion_data.monto_reportado),
            moneda="USD",
            mensaje_apoyo=donacion_data.mensaje_apoyo,
        )

    # Obtener campaña activa para los límites de meta y recaudación manual
    campana = db.execute(select(Campana).where(Campana.activa == True)).scalar_one_or_none()
    if not campana:
        campana = Campana(meta_total=2750.00, recaudado_manual=0.00)

    # Recalcular recaudación total (donaciones directas + rifas pagadas)
    from sqlalchemy import func, or_, exists
    rifa = db.execute(select(Rifas).where(Rifas.estado == "Activa")).scalar_one_or_none()
    tasa = 833.33
    if rifa and float(rifa.precio_ticket_usd) > 0:
        tasa = float(rifa.precio_ticket_bs) / float(rifa.precio_ticket_usd)
        
    recaudado_usd = db.scalar(
        select(func.sum(Aportantes.monto_reportado))
        .where(
            Aportantes.moneda == "USD",
            or_(
                Aportantes.tipo_aporte == "Donacion",
                exists().where(
                    (Tickets.aportante_id == Aportantes.id) & 
                    (Tickets.estado == "Pagado")
                )
            )
        )
    ) or 0.0

    recaudado_bs = db.scalar(
        select(func.sum(Aportantes.monto_reportado))
        .where(
            Aportantes.moneda == "BS",
            or_(
                Aportantes.tipo_aporte == "Donacion",
                exists().where(
                    (Tickets.aportante_id == Aportantes.id) & 
                    (Tickets.estado == "Pagado")
                )
            )
        )
    ) or 0.0
    
    recaudado_aportes = float(recaudado_usd) + (float(recaudado_bs) / tasa)
    total_recaudado = float(campana.recaudado_manual) + recaudado_aportes
    
    porcentaje = (total_recaudado / float(campana.meta_total)) * 100
    if porcentaje > 100:
        porcentaje = 100

    # Computar iniciales y gradiente para el nuevo aportante (diseño idéntico a templates/index.html)
    nombre = donacion_data.nombre
    names = nombre.split()
    if len(names) > 1:
        initials = (names[0][0] + names[1][0]).upper()
    elif names:
        initials = names[0][0].upper()
    else:
        initials = "CS"

    char = nombre[0].upper() if nombre else 'A'
    if char in 'AEIOU':
        bg_grad = 'background: linear-gradient(135deg, #EC4899, #8B5CF6);'
    elif char in 'BCDFG':
        bg_grad = 'background: linear-gradient(135deg, #3B82F6, #06B6D4);'
    elif char in 'HJKLMN':
        bg_grad = 'background: linear-gradient(135deg, #F59E0B, #EF4444);'
    elif char in 'PQRST':
        bg_grad = 'background: linear-gradient(135deg, #10B981, #059669);'
    else:
        bg_grad = 'background: linear-gradient(135deg, #6366F1, #4F46E5);'

    fecha_str = datetime.now().strftime('%Y-%m-%d %H:%M')

    mensaje_html = ""
    if donacion_data.mensaje_apoyo:
        mensaje_html = f"""
        <div class="bubble-message">
            <p class="mb-0 fst-italic text-muted small">"{donacion_data.mensaje_apoyo}"</p>
        </div>
        """

    html_response = f"""
    <div class="p-3 mb-3 border-0 donor-card shadow-xs-soft">
        <div class="d-flex align-items-center gap-3">
            <div class="avatar-circle" style="{bg_grad}">
                {initials}
            </div>
            <div>
                <h6 class="mb-0 text-dark fw-bold">{donacion_data.nombre}</h6>
                <small class="text-muted" style="font-size: 0.75rem;">
                    <i class="fa-regular fa-clock me-1"></i>{fecha_str}
                </small>
            </div>
        </div>
        {mensaje_html}
    </div>
    
    <div id="pote-progress-container" hx-swap-oob="true">
        <div class="custom-progress mb-3">
            <div class="custom-progress-bar" role="progressbar" style="width: {porcentaje}%;" aria-valuenow="{porcentaje}" aria-valuemin="0" aria-valuemax="100">
                <div class="progress-heart">
                    <i class="fa-solid fa-heart"></i>
                </div>
            </div>
        </div>
        
        <div class="d-flex justify-content-between text-white fw-bold mb-3 px-1" style="font-size: 0.95rem;">
            <span>Recaudado: ${total_recaudado:.2f}</span>
            <span>Meta: ${float(campana.meta_total):.2f}</span>
        </div>

        <div class="badge bg-success-subtle text-success border border-success-subtle px-3 py-2 rounded-pill mt-2 fw-semibold">
            <i class="fa-solid fa-hands-holding-circle me-1 animate-pulse"></i> ¡{porcentaje:.1f}% de la meta alcanzada!
        </div>
    </div>
    """
    return HTMLResponse(html_response)
