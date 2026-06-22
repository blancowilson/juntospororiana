"""
Cliente HTTP para el servidor OpenWA (WhatsApp API Gateway).
https://www.open-wa.org/

Pensado para fallar en silencio: si OpenWA no esta disponible o
la sesion no esta conectada, NO rompemos el flujo principal de
la aplicacion. Se registra el error y se sigue.
"""
import logging
import re
from typing import Optional

import requests
from requests.exceptions import RequestException

from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 8  # segundos


def _normalizar_telefono(telefono: str, codigo_pais: str = "58") -> str:
    """
    Convierte un numero en formato local (4141234567) a formato
    E.164 que usa WhatsApp (584141234567) y luego al chatId de
    OpenWA / WhatsApp Web: 584141234567@c.us

    Acepta:
        04141234567   -> 584141234567@c.us
        4141234567    -> 584141234567@c.us
        584141234567  -> 584141234567@c.us
        +584141234567 -> 584141234567@c.us
    """
    if not telefono:
        return ""

    t = re.sub(r"[^\d+]", "", str(telefono).strip())

    # Quitar el "+" inicial si lo tiene
    if t.startswith("+"):
        t = t[1:]

    # Si empieza con 0 (formato local venezolano 0414...), quitarlo
    if t.startswith("0"):
        t = t[1:]

    # Si ya viene con codigo de pais (58 para VE) o cualquier otro,
    # lo dejamos igual. Si no, se lo anteponemos.
    if not t.startswith(codigo_pais):
        # Puede que sea un numero local de 10 digitos (ej. 4141234567)
        if len(t) == 10:
            t = codigo_pais + t
        elif len(t) < 10:
            # Numero demasiado corto, probablemente invalido
            logger.warning(f"Telefono parece invalido (muy corto): {telefono}")

    return f"{t}@c.us"


def _get_url(path: str) -> str:
    base = settings.OPENWA_BASE_URL.rstrip("/")
    return f"{base}{path}"


def _headers() -> dict:
    return {
        "X-API-Key": settings.OPENWA_API_KEY,
        "Content-Type": "application/json",
    }


def openwa_disponible() -> bool:
    """Chequeo rapido: True si OpenWA responde y esta autenticado."""
    if not settings.OPENWA_ENABLED:
        return False
    if not settings.OPENWA_API_KEY or not settings.OPENWA_SESSION_ID:
        return False
    try:
        r = requests.get(
            _get_url(f"/sessions/{settings.OPENWA_SESSION_ID}"),
            headers=_headers(),
            timeout=3,
        )
        return r.status_code == 200
    except RequestException:
        return False


def enviar_texto(telefono: str, mensaje: str, codigo_pais: str = "58") -> bool:
    """
    Envia un mensaje de texto via OpenWA.
    Retorna True si se envio, False si fallo (no lanza excepcion).
    """
    if not settings.OPENWA_ENABLED:
        logger.info("OpenWA deshabilitado por configuracion. No se envia mensaje.")
        return False

    if not settings.OPENWA_API_KEY:
        logger.warning("OPENWA_API_KEY no configurada. No se envia mensaje.")
        return False

    if not settings.OPENWA_SESSION_ID:
        logger.warning("OPENWA_SESSION_ID no configurada. No se envia mensaje.")
        return False

    chat_id = _normalizar_telefono(telefono, codigo_pais=codigo_pais)
    if not chat_id or chat_id == "@c.us":
        logger.warning(f"Telefono invalido, no se envia WhatsApp: {telefono!r}")
        return False

    try:
        r = requests.post(
            _get_url(f"/sessions/{settings.OPENWA_SESSION_ID}/messages/send-text"),
            headers=_headers(),
            json={"chatId": chat_id, "text": mensaje},
            timeout=DEFAULT_TIMEOUT,
        )
        if r.status_code in (200, 201):
            logger.info(f"WhatsApp enviado a {chat_id} ({len(mensaje)} chars)")
            return True

        # 400/404 pueden ser numeros que no tienen WhatsApp, no es un error fatal
        logger.warning(
            f"OpenWA respondio {r.status_code} al enviar a {chat_id}: "
            f"{r.text[:200]}"
        )
        return False

    except RequestException as e:
        # OpenWA caido, timeout, red, etc. NO rompemos el flujo principal.
        logger.error(f"No se pudo conectar con OpenWA para enviar a {chat_id}: {e}")
        return False


# =========================================================
# Mensajes pre-armados para Juntos por Oriana
# =========================================================

def notificar_donacion(
    telefono: Optional[str],
    nombre: str,
    monto: float,
    moneda: str,
    mensaje_apoyo: Optional[str] = None,
) -> bool:
    """
    Envia un agradecimiento por una donacion directa.
    Si telefono viene vacio, no se envia.
    """
    if not telefono:
        return False

    simbolo = "$" if moneda == "USD" else "Bs. "
    texto = (
        f"¡Hola {nombre}! 💛\n\n"
        f"Desde *Juntos por Oriana* queremos agradecerte de todo corazón "
        f"por tu aporte de {simbolo}{monto:.2f}.\n\n"
    )
    if mensaje_apoyo:
        texto += (
            f"Tu mensaje de apoyo significa muchísimo para nosotros "
            f"y para Oriana. ❤️\n\n"
        )
    texto += (
        f"Cada colaboración nos acerca más a la meta. "
        f"¡Eres parte de esta cadena de amor!\n\n"
        f"— Equipo de Juntos por Oriana"
    )
    return enviar_texto(telefono, texto)


def notificar_recepcion_tickets(
    telefono: Optional[str],
    nombre: str,
    cantidad: int,
    numeros: list[str],
    monto: float,
    moneda: str,
) -> bool:
    """
    Envia el mensaje de "estamos revisando manualmente" tras la
    reserva de tickets de una rifa.
    """
    if not telefono:
        return False

    simbolo = "$" if moneda == "USD" else "Bs. "
    lista = ", ".join(numeros) if numeros else "(pendiente de asignar)"

    texto = (
        f"¡Hola {nombre}! 🎟️\n\n"
        f"Recibimos tu reporte de compra en *Juntos por Oriana* "
        f"por {cantidad} ticket(s) por un total de {simbolo}{monto:.2f}.\n\n"
        f"*Números reservados:* {lista}\n\n"
        f"⚠️ *Importante:* estamos realizando la *revisión manual* "
        f"de tu pago. Una vez confirmemos tu aporte, te enviaremos "
        f"un nuevo mensaje con la confirmación definitiva y tus "
        f"tickets oficiales. 🙏\n\n"
        f"Gracias por tu paciencia y por sumarte a esta causa."
    )
    return enviar_texto(telefono, texto)


def notificar_confirmacion_tickets(
    telefono: Optional[str],
    nombre: str,
    cantidad: int,
    numeros: list[str],
    rifa_titulo: str,
) -> bool:
    """
    Envia la confirmacion definitiva cuando el admin marca los
    tickets como Pagado.
    """
    if not telefono:
        return False

    lista = ", ".join(numeros) if numeros else ""

    texto = (
        f"¡{nombre}, tenemos buenas noticias! 🎉✅\n\n"
        f"Confirmamos tu pago para la rifa *{rifa_titulo}*.\n\n"
        f"*Tus tickets oficiales ({cantidad}):*\n{lista}\n\n"
        f"¡Mucha suerte! El sorteo se realizará en la fecha indicada. 🍀\n\n"
        f"— Equipo de Juntos por Oriana"
    )
    return enviar_texto(telefono, texto)
