"""
Cliente HTTP para el servidor OpenWA (WhatsApp API Gateway).
https://www.open-wa.org/

Pensado para fallar en silencio: si OpenWA no esta disponible o
la sesion no esta conectada, NO rompemos el flujo principal de
la aplicacion. Se registra el error y se sigue.

Anti-ban (jun-2026):
- Variantes de mensaje con spintax para que cada envio sea unico
  (WhatsApp detecta batches identicos estructuralmente, no por texto)
- Delay aleatorio entre envios (simula tipeo humano)
- Rate limits por hora/dia configurables (conservadores por default)
- "Noche" venezolana: delays 3x entre 22:00 y 08:00 hora local
- Cooldown por destinatario (no molestar al mismo numero muy seguido)
"""
import logging
import random
import re
import time
from collections import defaultdict, deque
from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import requests
from requests.exceptions import RequestException

from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 8  # segundos


# ==========================================================
# Normalizacion de telefono
# ==========================================================

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
    if t.startswith("+"):
        t = t[1:]
    if t.startswith("0"):
        t = t[1:]
    if not t.startswith(codigo_pais):
        if len(t) == 10:
            t = codigo_pais + t
        elif len(t) < 10:
            logger.warning(f"Telefono parece invalido (muy corto): {telefono}")
    return f"{t}@c.us"


# ==========================================================
# Variantes de mensaje (spintax)
# ==========================================================
# Cada lista se sortea al construir el mensaje. Asi, de 4 variantes
# por tipo + 3-4 saludos + 3-4 cierres, salen >50 combinaciones
# unicas que NO caen en el detector de "template identico" de Meta.

_SALUDOS_DONACION = [
    "¡Hola {nombre}! 💛",
    "¡{nombre}, un abrazo enorme! 🤗",
    "¡{nombre}, gracias por estar! 😊",
    "Hola {nombre} 💛",
    "¡{nombre}, qué'acte de amor! ✨",
]
_MEDIO_DONACION = [
    "Cada granito de arena nos acerca más a la meta. ¡Eres parte de esta cadena de amor!",
    "Tu apoyo nos da fuerzas para seguir adelante con esta lucha por Oriana. 💪",
    "Gente como tú hace posible que Oriana tenga una oportunidad. Mil gracias.",
    "Tu ayuda llega en el momento justo. Gracias por sumarte a esta causa. 🙏",
    "Con personas como tú, esto se hace posible. ¡Gracias por creer en Oriana!",
]
_CIERRE_DONACION = [
    "— Equipo de Juntos por Oriana",
    "Con cariño,\n— Juntos por Oriana 💛",
    "Un abrazo enorme,\n— Familia de Oriana",
    "— Juntos por Oriana 🙏",
]
# Bloque opcional cuando el donante dejo un mensaje de apoyo
_MENSAJE_APOYO_INTROS = [
    "Tu mensaje de apoyo significa muchísimo para nosotros:\n\n> {msg}\n\n",
    "Leímos tu mensaje y nos llegó al corazón:\n\n> {msg}\n\n",
    "Esto fue lo que nos escribiste y nos llenó de fuerzas:\n\n> {msg}\n\n",
    "",
    "",
]  # un ~40% de las veces se omite para no repetir el patron

_VARIANTES_DONACION = [
    # Variante 1: calido + mencion a Oriana
    (
        "{saludo}\n\n"
        "Desde *Juntos por Oriana* queremos agradecerte de todo corazón "
        "por tu aporte de {simbolo}{monto:.2f}.\n\n"
        "{apoyo}{medio}\n\n"
        "{cierre}"
    ),
    # Variante 2: breve, al grano
    (
        "{saludo}\n\n"
        "Tu generoso aporte de {simbolo}{monto:.2f} significa muchísimo "
        "para Oriana y para todos nosotros. {apoyo_corto}\n\n"
        "{medio}\n\n"
        "{cierre}"
    ),
    # Variante 3: personal, en nombre de la familia
    (
        "{saludo}\n\n"
        "{apoyo}"
        "Tu ayuda de {simbolo}{monto:.2f} nos llena de esperanza. "
        "Oriana y toda la familia te agradecemos de corazón.\n\n"
        "{medio}\n\n"
        "{cierre}"
    ),
    # Variante 4: motivador
    (
        "{saludo}\n\n"
        "Gracias a tu aporte de {simbolo}{monto:.2f} estamos un paso más "
        "cerca de la meta para Oriana. {apoyo_corto}\n\n"
        "{medio}\n\n"
        "{cierre}"
    ),
]


_SALUDOS_TICKETS = [
    "¡Hola {nombre}! 🎟️",
    "¡{nombre}, gracias por sumarte! 🎟️",
    "¡{nombre}, qué bueno tenerte! 🎟️",
    "Hola {nombre} 🎟️",
]
_INTROS_TICKETS = [
    "Recibimos tu reporte de compra en *Juntos por Oriana* por {cantidad} ticket(s) por un total de {simbolo}{monto:.2f}.",
    "¡Ya registramos tu compra! Son {cantidad} ticket(s) por {simbolo}{monto:.2f} en la rifa de *Juntos por Oriana*.",
    "Anotamos tu compra de {cantidad} ticket(s) por {simbolo}{monto:.2f} para la rifa de *Juntos por Oriana*.",
]
_REVISIONES_TICKETS = [
    (
        "⚠️ *Importante:* estamos realizando la *revisión manual* de tu pago. "
        "Una vez confirmemos tu aporte, te enviaremos un nuevo mensaje con la "
        "confirmación definitiva y tus tickets oficiales. 🙏"
    ),
    (
        "Ahora estamos confirmando tu pago manualmente. En cuanto esté listo, "
        "te avisamos con la confirmación final y tus tickets. 🙌"
    ),
    (
        "Nuestro equipo está validando tu pago. Apenas esté confirmado, te "
        "escribimos de nuevo con tus tickets oficiales. ⏳"
    ),
]
_CIERRES_TICKETS = [
    "Gracias por tu paciencia y por sumarte a esta causa.",
    "¡Gracias por tu paciencia!",
    "Mil gracias por sumarte a esta cadena de amor. 💛",
    "Gracias por creer en Oriana. 💛",
]

_VARIANTES_RECEPCION = [
    # 1: informativo
    (
        "{saludo}\n\n"
        "{intro}\n\n"
        "*Números reservados:* {numeros}\n\n"
        "{revision}\n\n"
        "{cierre}"
    ),
    # 2: con entusiasmo
    (
        "{saludo}\n\n"
        "{intro} {apoyo_corto}\n\n"
        "*Tus números:* {numeros}\n\n"
        "{revision}\n\n"
        "{cierre}"
    ),
    # 3: breve
    (
        "{saludo}\n\n"
        "{intro}\n"
        "*Números:* {numeros}\n\n"
        "{revision}\n\n"
        "{cierre}"
    ),
]


_SALUDOS_CONFIRMACION = [
    "¡{nombre}, buenas noticias! 🎉✅",
    "¡{nombre}, ya está! 🎉",
    "¡{nombre}, confirmado! ✅",
    "¡{nombre}, lo logramos! 🎉",
]
_INTROS_CONFIRMACION = [
    "Confirmamos tu pago para la rifa *{rifa}*.",
    "¡Listo! Tu pago para la rifa *{rifa}* fue confirmado.",
    "Ya confirmamos tu pago. Estos son tus tickets oficiales para la rifa *{rifa}*.",
    "Tu pago entró. Aquí tienes tus tickets para *{rifa}*.",
]
_SUERTES_CONFIRMACION = [
    "¡Mucha suerte! El sorteo se realizará en la fecha indicada. 🍀",
    "¡Te deseamos toda la suerte del mundo! El sorteo se acerca. 🍀",
    "¡Que la suerte te acompañe! Sorteo en la fecha indicada. ✨",
    "¡Toda la suerte para ti! 🤞",
]
_CIERRES_CONFIRMACION = [
    "— Equipo de Juntos por Oriana",
    "— Juntos por Oriana 💛",
    "Con cariño,\n— Familia de Oriana",
    "Un abrazo,\n— Equipo de Juntos por Oriana",
]

_VARIANTES_CONFIRMACION = [
    # 1: entusiasta
    (
        "{saludo}\n\n"
        "{intro}\n\n"
        "*Tus tickets oficiales ({cantidad}):* {numeros}\n\n"
        "{suerte}\n\n"
        "{cierre}"
    ),
    # 2: mas sobrio
    (
        "{saludo}\n\n"
        "{intro}\n"
        "*Tickets ({cantidad}):* {numeros}\n\n"
        "{suerte}\n\n"
        "{cierre}"
    ),
    # 3: familiar
    (
        "{saludo}\n\n"
        "{intro}\n\n"
        "Estos son tus {cantidad} ticket(s) oficiales:\n{numeros}\n\n"
        "{suerte}\n\n"
        "{cierre}"
    ),
]


def _elegir_variante(variantes: list, **kwargs) -> str:
    """Elige una variante random y la llena con kwargs + spintax."""
    patron = random.choice(variantes)
    return patron.format(**kwargs)


def _armar_mensaje_donacion(nombre: str, simbolo: str, monto: float, mensaje_apoyo: Optional[str]) -> str:
    """Construye un mensaje de agradecimiento por donacion, unico cada vez."""
    saludo = random.choice(_SALUDOS_DONACION).format(nombre=nombre)
    medio = random.choice(_MEDIO_DONACION)
    cierre = random.choice(_CIERRE_DONACION)

    if mensaje_apoyo:
        apoyo = random.choice(_MENSAJE_APOYO_INTROS).format(msg=mensaje_apoyo)
        apoyo_corto = f"Leímos tu mensaje: «{mensaje_apoyo[:80]}{'...' if len(mensaje_apoyo) > 80 else ''}»"
    else:
        apoyo = ""
        apoyo_corto = ""

    return _elegir_variante(
        _VARIANTES_DONACION,
        saludo=saludo, simbolo=simbolo, monto=monto,
        apoyo=apoyo, apoyo_corto=apoyo_corto, medio=medio, cierre=cierre,
    )


def _armar_mensaje_recepcion_tickets(
    nombre: str, cantidad: int, numeros: list[str], simbolo: str, monto: float
) -> str:
    saludo = random.choice(_SALUDOS_TICKETS).format(nombre=nombre)
    intro = random.choice(_INTROS_TICKETS).format(
        cantidad=cantidad, simbolo=simbolo, monto=monto
    )
    revision = random.choice(_REVISIONES_TICKETS)
    cierre = random.choice(_CIERRES_TICKETS)
    lista = ", ".join(numeros) if numeros else "(pendiente)"
    apoyo_corto = ""

    return _elegir_variante(
        _VARIANTES_RECEPCION,
        saludo=saludo, intro=intro, numeros=lista, revision=revision, cierre=cierre,
        apoyo_corto=apoyo_corto,
    )


def _armar_mensaje_confirmacion_tickets(
    nombre: str, cantidad: int, numeros: list[str], rifa_titulo: str
) -> str:
    saludo = random.choice(_SALUDOS_CONFIRMACION).format(nombre=nombre)
    intro = random.choice(_INTROS_CONFIRMACION).format(rifa=rifa_titulo)
    suerte = random.choice(_SUERTES_CONFIRMACION)
    cierre = random.choice(_CIERRES_CONFIRMACION)
    lista = ", ".join(numeros) if numeros else ""

    return _elegir_variante(
        _VARIANTES_CONFIRMACION,
        saludo=saludo, intro=intro, cantidad=cantidad, numeros=lista,
        suerte=suerte, cierre=cierre,
    )


# ==========================================================
# Rate limit + delay humano
# ==========================================================

# Ventanas deslizantes en memoria. Persisten mientras el proceso vive.
# Si el server reinicia, se resetean (es OK: en un restart "se gana" un margen).
_envios_por_hora: deque = deque()  # timestamps (epoch float) de envios en la ultima hora
_envios_por_dia: deque = deque()   # timestamps en las ultimas 24h
_ultimo_envio_por_destinatario: dict[str, float] = defaultdict(float)


def _es_noche() -> bool:
    """True si estamos en horario 'noche' (22:00 - 08:00) hora VE."""
    try:
        tz = ZoneInfo(settings.WA_TIMEZONE)
    except Exception:
        tz = ZoneInfo("America/Caracas")
    h = datetime.now(tz).hour
    return h >= 22 or h < 8


def _purgar_ventanas(ahora: float) -> None:
    """Limpia timestamps fuera de las ventanas de 1h / 24h."""
    while _envios_por_hora and _envios_por_hora[0] < ahora - 3600:
        _envios_por_hora.popleft()
    while _envios_por_dia and _envios_por_dia[0] < ahora - 86400:
        _envios_por_dia.popleft()


def _rate_limit_ok(ahora: float) -> tuple[bool, str]:
    """Devuelve (ok, motivo). Si no ok, motivo explica por que."""
    _purgar_ventanas(ahora)
    if len(_envios_por_hora) >= settings.WA_MAX_PER_HOUR:
        return False, f"hour_cap_reached ({len(_envios_por_hora)}/{settings.WA_MAX_PER_HOUR})"
    if len(_envios_por_dia) >= settings.WA_MAX_PER_DAY:
        return False, f"day_cap_reached ({len(_envios_por_dia)}/{settings.WA_MAX_PER_DAY})"
    return True, ""


def _registrar_envio(ahora: float) -> None:
    _envios_por_hora.append(ahora)
    _envios_por_dia.append(ahora)


def _delay_humano(ahora: float, destinatario: str) -> float:
    """
    Calcula cuanto tiempo dormir antes del proximo envio.
    Combina: base aleatoria (entre min/max) + multiplicador nocturno
    + cooldown por destinatario (si ya le escribimos hace poco, espera mas).
    """
    base = random.uniform(settings.WA_MIN_DELAY_SEC, settings.WA_MAX_DELAY_SEC)

    if _es_noche():
        base *= settings.WA_NIGHT_MULTIPLIER

    # Cooldown: si el mismo destinatario recibio algo en los ultimos 5 min,
    # forzamos al menos 5 min de espera adicional.
    ultimo = _ultimo_envio_por_destinatario.get(destinatario, 0.0)
    desde_ultimo = ahora - ultimo
    if 0 < desde_ultimo < 300:  # mismo destinatario en ventana de 5 min
        falta = 300 - desde_ultimo
        if base < falta:
            base = falta + random.uniform(0, 30)

    return base


# ==========================================================
# Cliente HTTP + envio con comportamiento humano
# ==========================================================

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
    Envia un mensaje de texto via OpenWA con comportamiento humano:
    - delay aleatorio antes del envio
    - respeta rate limits por hora/dia
    - registra el envio para cooldown del destinatario
    - no rompe el flujo principal si falla
    """
    if not settings.OPENWA_ENABLED:
        logger.info("OpenWA deshabilitado por configuracion. No se envia mensaje.")
        return False
    if not settings.OPENWA_API_KEY:
        logger.warning("OPENWA_API_KEY no configurada. No se envia WhatsApp.")
        return False
    if not settings.OPENWA_SESSION_ID:
        logger.warning("OPENWA_SESSION_ID no configurada. No se envia WhatsApp.")
        return False

    chat_id = _normalizar_telefono(telefono, codigo_pais=codigo_pais)
    if not chat_id or chat_id == "@c.us":
        logger.warning(f"Telefono invalido, no se envia WhatsApp: {telefono!r}")
        return False

    ahora = time.time()

    # Rate limit
    ok, motivo = _rate_limit_ok(ahora)
    if not ok:
        if settings.WA_BLOCK_ON_LIMIT:
            logger.warning(
                f"WA: rate limit alcanzado ({motivo}), se descarta envio a {chat_id} "
                f"({len(mensaje)} chars). Ajustar WA_MAX_PER_HOUR/DAY si es legitimo."
            )
            return False
        else:
            # Encolar: esperar hasta que se libere la ventana horaria
            espera = 3600 - (ahora - _envios_por_hora[0])
            logger.info(f"WA: rate limit ({motivo}), esperando {espera:.0f}s...")
            time.sleep(max(1.0, espera))
            ahora = time.time()

    # Delay humano
    delay = _delay_humano(ahora, chat_id)
    logger.info(
        f"WA: esperando {delay:.1f}s antes de enviar a {chat_id} "
        f"({len(mensaje)} chars) [noche={_es_noche()}]"
    )
    time.sleep(delay)
    ahora = time.time()

    # Envio real
    try:
        r = requests.post(
            _get_url(f"/sessions/{settings.OPENWA_SESSION_ID}/messages/send-text"),
            headers=_headers(),
            json={"chatId": chat_id, "text": mensaje},
            timeout=DEFAULT_TIMEOUT,
        )
        if r.status_code in (200, 201):
            _registrar_envio(ahora)
            _ultimo_envio_por_destinatario[chat_id] = ahora
            logger.info(
                f"WA enviado a {chat_id} ({len(mensaje)} chars) "
                f"[ventana: {len(_envios_por_hora)}/h, {len(_envios_por_dia)}/d]"
            )
            return True

        logger.warning(f"OpenWA respondio {r.status_code} al enviar a {chat_id}: {r.text[:200]}")
        return False
    except RequestException as e:
        logger.error(f"No se pudo conectar con OpenWA para enviar a {chat_id}: {e}")
        return False


# ==========================================================
# Mensajes pre-armados para Juntos por Oriana
# ==========================================================

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
    Delay humano + rate limit se aplican internamente.
    """
    if not telefono:
        return False

    simbolo = "$" if moneda == "USD" else "Bs. "
    texto = _armar_mensaje_donacion(nombre, simbolo, monto, mensaje_apoyo)
    return enviar_texto(telefono, texto)


def notificar_recepcion_tickets(
    telefono: Optional[str],
    nombre: str,
    cantidad: int,
    numeros: list[str],
    monto: float,
    moneda: str,
) -> bool:
    """Envia el mensaje de "estamos revisando manualmente" tras una reserva."""
    if not telefono:
        return False

    simbolo = "$" if moneda == "USD" else "Bs. "
    texto = _armar_mensaje_recepcion_tickets(nombre, cantidad, numeros, simbolo, monto)
    return enviar_texto(telefono, texto)


def notificar_confirmacion_tickets(
    telefono: Optional[str],
    nombre: str,
    cantidad: int,
    numeros: list[str],
    rifa_titulo: str,
) -> bool:
    """Envia la confirmacion definitiva cuando el admin marca tickets como Pagado."""
    if not telefono:
        return False

    texto = _armar_mensaje_confirmacion_tickets(nombre, cantidad, numeros, rifa_titulo)
    return enviar_texto(telefono, texto)
