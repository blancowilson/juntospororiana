from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import select, update, desc
from datetime import datetime, timezone

from app.db.session import get_db
from app.models.all_models import Campana, Rifas, Tickets, Aportantes
from app.schemas.public import DonacionIn, ReservaTicketIn

router = APIRouter()
templates = Jinja2Templates(directory="templates")

@router.get("/", response_class=HTMLResponse)
async def landing_page(request: Request, db: Session = Depends(get_db)):
    # 1. Obtener campaña activa
    campana = db.execute(select(Campana).where(Campana.activa == True)).scalar_one_or_none()
    if not campana:
        campana = Campana(meta_total=2600.00, recaudado_manual=0.00)
    
    # 2. Obtener rifa activa
    rifa = db.execute(select(Rifas).where(Rifas.estado == "Activa")).scalar_one_or_none()
    
    # 3. Obtener últimos 30 aportantes
    aportantes = db.execute(
        select(Aportantes).order_by(desc(Aportantes.fecha_aporte)).limit(30)
    ).scalars().all()

    # Contar boletos disponibles
    from sqlalchemy import func
    boletos_disponibles = 0
    if rifa:
        boletos_disponibles = db.scalar(
            select(func.count(Tickets.id)).where(Tickets.rifa_id == rifa.id, Tickets.estado == "Disponible")
        ) or 0

    # Calcular total recaudado (Manual + Aportantes registrados en BD)
    recaudado_aportes = db.scalar(select(func.sum(Aportantes.monto_reportado))) or 0.0
    total_recaudado = float(campana.recaudado_manual) + float(recaudado_aportes)

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "campana": campana,
            "rifa": rifa,
            "aportantes": aportantes,
            "boletos_disponibles": boletos_disponibles,
            "total_recaudado": total_recaudado
        }
    )

@router.post("/ticket/comprar-aleatorio", response_class=HTMLResponse)
async def comprar_tickets_aleatorios(
    cantidad: int = Form(...),
    nombre: str = Form(...),
    cedula: str = Form(...),
    telefono: str = Form(...),
    monto_reportado: float = Form(...),
    metodo_pago: str = Form(...),
    referencia: str = Form(...),
    banco_emisor: str = Form(...),
    db: Session = Depends(get_db)
):
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
        .with_for_update()
    )
    tickets_disponibles = db.execute(stmt).scalars().all()

    if len(tickets_disponibles) < reserva_data.cantidad:
        return HTMLResponse(
            f'<div class="alert alert-danger">Lo sentimos, no hay suficientes boletos disponibles. Solo quedan {len(tickets_disponibles)}.</div>',
            status_code=400
        )

    # 2. Registrar el Aportante
    nuevo_aportante = Aportantes(
        nombre=reserva_data.nombre,
        cedula=reserva_data.cedula,
        telefono=reserva_data.telefono,
        monto_reportado=reserva_data.monto_reportado,
        moneda="USD" if reserva_data.metodo_pago in ["Zelle", "Binance"] else "BS",
        metodo_pago=reserva_data.metodo_pago,
        referencia=reserva_data.referencia,
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
        ticket.referencia_pago = reserva_data.referencia
        ticket.monto_reportado = precio_unitario
        numeros_asignados.append(f"{ticket.numero:04d}") # Formatear ej: "0607"

    db.commit()

    # Generar la respuesta HTML del modal exitoso
    numeros_html = "".join([f'<div class="col-6 col-sm-4 mb-2"><span class="badge bg-light text-dark border p-2 w-100 fs-5 fw-bold">{n}</span></div>' for n in numeros_asignados])
    
    html_response = f"""
    <div class="text-center p-4">
        <div class="mb-3">
            <span class="fs-1">🎉</span>
        </div>
        <h4 class="fw-bold text-success">¡Compra exitosa!</h4>
        <p class="text-muted">Tu pago ha sido registrado y tus boletos están reservados correctamente.</p>
        
        <h5 class="mt-4 fw-bold">Números asignados:</h5>
        <div class="row justify-content-center my-3">
            {numeros_html}
        </div>

        <button type="button" class="btn btn-success px-4 mt-3 fw-bold rounded-pill" onclick="window.location.reload()">Finalizar</button>
    </div>
    """
    return HTMLResponse(html_response)

@router.post("/aportar/directo", response_class=HTMLResponse)
async def aportar_directo(
    nombre: str = Form(...),
    monto_reportado: float = Form(...),
    metodo_pago: str = Form(...),
    referencia: str = Form(...),
    mensaje_apoyo: str = Form(None),
    db: Session = Depends(get_db)
):
    try:
        donacion_data = DonacionIn(
            nombre=nombre,
            mensaje_apoyo=mensaje_apoyo,
            monto_reportado=monto_reportado,
            metodo_pago=metodo_pago,
            referencia=referencia
        )
    except Exception as e:
        return HTMLResponse(f'<div class="alert alert-danger">Error: {str(e)}</div>', status_code=400)

    nuevo_aportante = Aportantes(
        nombre=donacion_data.nombre,
        mensaje_apoyo=donacion_data.mensaje_apoyo,
        monto_reportado=donacion_data.monto_reportado,
        moneda="USD",
        metodo_pago=donacion_data.metodo_pago,
        referencia=donacion_data.referencia,
        tipo_aporte="Donacion"
    )
    db.add(nuevo_aportante)
    db.commit()

    # Obtener campaña activa para los límites de meta y recaudación manual
    campana = db.execute(select(Campana).where(Campana.activa == True)).scalar_one_or_none()
    if not campana:
        campana = Campana(meta_total=2600.00, recaudado_manual=0.00)

    # Recalcular recaudación total
    from sqlalchemy import func
    recaudado_aportes = db.scalar(select(func.sum(Aportantes.monto_reportado))) or 0.0
    total_recaudado = float(campana.recaudado_manual) + float(recaudado_aportes)
    
    porcentaje = (total_recaudado / float(campana.meta_total)) * 100
    if porcentaje > 100:
        porcentaje = 100

    mensaje_html = ""
    if donacion_data.mensaje_apoyo:
        mensaje_html = f'<p class="mb-0 fst-italic text-muted">"{donacion_data.mensaje_apoyo}"</p>'

    html_response = f"""
    <div class="p-3 mb-2 border-bottom">
        <h6 class="mb-1 text-primary">{donacion_data.nombre} ha donado!</h6>
        {mensaje_html}
    </div>
    
    <div id="pote-progress-container" hx-swap-oob="true">
        <div class="progress mb-3" style="height: 30px; border-radius: 15px; background-color: rgba(255,255,255,0.25);">
            <div class="progress-bar bg-success progress-bar-striped progress-bar-animated rounded-pill fw-bold" role="progressbar" style="width: {porcentaje}%;" aria-valuenow="{porcentaje}" aria-valuemin="0" aria-valuemax="100">
                {porcentaje:.1f}%
            </div>
        </div>
        
        <div class="d-flex justify-content-between text-white fw-bold mb-4">
            <span>Recaudado: ${total_recaudado:.2f}</span>
            <span>Meta: ${float(campana.meta_total):.2f}</span>
        </div>
    </div>
    """
    return HTMLResponse(html_response)
