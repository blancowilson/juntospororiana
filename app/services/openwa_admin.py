"""
Cliente admin para el servidor OpenWA: gestion de la sesion de WhatsApp
desde el panel de administracion (QR, estado, iniciar, reiniciar, logout).
"""
import logging
from typing import Optional

import requests
from requests.exceptions import RequestException

from app.core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 3  # segundos (corto para no colgar el panel admin)


def _url(path: str) -> str:
    base = settings.OPENWA_BASE_URL.rstrip("/")
    return f"{base}{path}"


def _headers() -> dict:
    return {
        "X-API-Key": settings.OPENWA_API_KEY,
        "Content-Type": "application/json",
    }


def _check_config() -> Optional[dict]:
    """Devuelve un dict con error si falta config, None si todo OK."""
    if not settings.OPENWA_ENABLED:
        return {"error": "OpenWA deshabilitado (OPENWA_ENABLED=false)"}
    if not settings.OPENWA_API_KEY:
        return {"error": "Falta OPENWA_API_KEY en .env"}
    if not settings.OPENWA_SESSION_ID:
        return {"error": "Falta OPENWA_SESSION_ID en .env"}
    return None


def _resolver_sesion_id() -> Optional[str]:
    """
    Devuelve un SESSION_ID valido para usar con OpenWA.

    Estrategia:
    1. Si el OPENWA_SESSION_ID configurado existe -> usarlo.
    2. Si no, buscar una sesion con name == "juntospororiana" (o == OPENWA_SESSION_ID)
       y devolver su id. Asi si el id cambia (porque se reinicio), seguimos funcionando.
    3. Si no hay ninguna, devolver None.
    """
    sid_config = settings.OPENWA_SESSION_ID

    # 1. Probar el id configurado
    try:
        r = requests.get(_url(f"/sessions/{sid_config}"), headers=_headers(), timeout=DEFAULT_TIMEOUT)
        if r.status_code == 200:
            return sid_config
    except RequestException:
        pass

    # 2. Buscar por nombre
    try:
        r = requests.get(_url("/sessions"), headers=_headers(), timeout=DEFAULT_TIMEOUT)
        if r.status_code != 200:
            return None
        sesiones = r.json()
    except RequestException:
        return None

    # Prioridad: match exacto por nombre o por id
    for s in sesiones:
        if s.get("id") == sid_config:
            return sid_config
    for s in sesiones:
        if s.get("name") == sid_config or s.get("name") == "juntospororiana":
            return s.get("id")
    # Como ultimo recurso, la primera sesion
    if sesiones:
        return sesiones[0].get("id")
    return None


def _get_sesion_raw() -> Optional[dict]:
    """Devuelve la sesion cruda desde OpenWA, o None si no existe / falla."""
    sid = _resolver_sesion_id()
    if sid is None:
        return None
    try:
        r = requests.get(_url(f"/sessions/{sid}"), headers=_headers(), timeout=DEFAULT_TIMEOUT)
        if r.status_code == 200:
            data = r.json()
            data["_resolved_id"] = sid
            return data
        if r.status_code == 404:
            return None
        logger.warning(f"OpenWA devolvio {r.status_code} al pedir sesion: {r.text[:200]}")
        return None
    except RequestException as e:
        logger.warning(f"No se pudo obtener sesion de OpenWA: {e}")
        return None


def estado_sesion() -> dict:
    """
    Devuelve un dict con el estado actual de la sesion.
    """
    err = _check_config()
    if err:
        return {"configurado": False, "conectado": False, "error": err["error"]}

    data = _get_sesion_raw()
    if data is None:
        return {
            "configurado": False,
            "conectado": False,
            "status": "NOT_FOUND",
            "error": (
                f"No hay una sesion '{settings.OPENWA_SESSION_ID}' en OpenWA. "
                "Pulsa 'Iniciar sesion' para crearla."
            ),
        }

    status = (data.get("status") or "").upper()
    return {
        "configurado": True,
        "conectado": status == "CONNECTED",
        "status": status,
        "phone": data.get("phoneNumber"),
        "push_name": data.get("pushName"),
        "platform": data.get("platform"),
        "session_id": data.get("_resolved_id"),
    }


def obtener_qr() -> Optional[dict]:
    """
    Devuelve el QR actual normalizado para el frontend:
      {"image": "data:image/png;base64,...", "qrCode": "<string>", "status": "..."}
    Devuelve None si no hay QR.
    """
    err = _check_config()
    if err:
        return None

    sid = _resolver_sesion_id()
    if sid is None:
        return None
    try:
        r = requests.get(
            _url(f"/sessions/{sid}/qr"),
            headers=_headers(),
            timeout=DEFAULT_TIMEOUT,
        )
        if r.status_code != 200:
            return None
        data = r.json()
        qr_data_url = data.get("qrCode") or data.get("image") or ""
        if qr_data_url and not qr_data_url.startswith("data:"):
            qr_data_url = "data:image/png;base64," + qr_data_url
        return {
            "image": qr_data_url,
            "qrCode": qr_data_url,
            "status": data.get("status"),
        }
    except RequestException as e:
        logger.warning(f"No se pudo obtener QR: {e}")
        return None


# Estados que indican que la sesion YA esta en marcha.
_ESTADOS_ACTIVOS = {"INITIALIZING", "AUTHENTICATING", "AUTHENTICATED", "CONNECTED"}


def iniciar_sesion() -> dict:
    """
    Crea la sesion (si no existe) y la inicia.
    """
    err = _check_config()
    if err:
        return {"ok": False, "mensaje": err["error"]}

    sid_config = settings.OPENWA_SESSION_ID

    # 1. Consultar estado actual (resiliente: usa cualquier sesion existente con ese nombre)
    actual = _get_sesion_raw()
    if actual is not None:
        status_actual = (actual.get("status") or "").upper()
        if status_actual in _ESTADOS_ACTIVOS:
            return {
                "ok": True,
                "mensaje": f"La sesion ya esta activa (estado: {status_actual}). Recarga para ver el QR si esta en qr_ready.",
                "status": status_actual,
                "already_active": True,
            }
        sid = actual.get("_resolved_id") or sid_config
        return _start_existing(sid)

    # 2. No existe: crearla
    try:
        cr = requests.post(
            _url("/sessions"),
            headers=_headers(),
            json={"name": sid_config},
            timeout=DEFAULT_TIMEOUT,
        )
    except RequestException as e:
        return {"ok": False, "mensaje": f"Error de conexion con OpenWA: {e}"}

    if cr.status_code not in (200, 201):
        return {
            "ok": False,
            "mensaje": f"No se pudo crear la sesion: {cr.status_code} {cr.text[:200]}",
        }
    nueva = cr.json()
    nuevo_id = nueva.get("id")
    if not nuevo_id:
        return {"ok": False, "mensaje": "OpenWA devolvio una respuesta sin id."}

    return _start_existing(nuevo_id)


def _start_existing(sid: str) -> dict:
    """Llama a /start sobre la sesion indicada."""
    try:
        sr = requests.post(
            _url(f"/sessions/{sid}/start"),
            headers=_headers(),
            timeout=DEFAULT_TIMEOUT,
        )
    except RequestException as e:
        return {"ok": False, "mensaje": f"Error de conexion con OpenWA: {e}"}

    if sr.status_code in (200, 202):
        return {
            "ok": True,
            "mensaje": "Sesion iniciada. Recarga esta pagina en unos segundos para ver el QR.",
            "session_id": sid,
        }
    # OpenWA devuelve 400 si la sesion ya esta en marcha (race condition). Lo tratamos como exito idempotente.
    if sr.status_code == 400 and "already" in sr.text.lower():
        return {
            "ok": True,
            "mensaje": "La sesion ya estaba iniciada.",
            "already_active": True,
            "session_id": sid,
        }
    return {
        "ok": False,
        "mensaje": f"No se pudo iniciar: {sr.status_code} {sr.text[:200]}",
    }


def reiniciar_sesion() -> dict:
    """
    'Reinicia' la sesion borrando la actual y creando una nueva.
    Como OpenWA no expone un endpoint /restart, lo simulamos.
    """
    err = _check_config()
    if err:
        return {"ok": False, "mensaje": err["error"]}

    sid_config = settings.OPENWA_SESSION_ID
    sid_actual = _resolver_sesion_id()

    # 1. Borrar la sesion actual (si existe)
    if sid_actual:
        try:
            dr = requests.delete(
                _url(f"/sessions/{sid_actual}"),
                headers=_headers(),
                timeout=DEFAULT_TIMEOUT,
            )
            if dr.status_code not in (200, 204, 404):
                return {
                    "ok": False,
                    "mensaje": f"No se pudo borrar la sesion anterior: {dr.status_code} {dr.text[:200]}",
                }
        except RequestException as e:
            return {"ok": False, "mensaje": f"Error de conexion con OpenWA: {e}"}

    # 2. Crear nueva sesion con el mismo nombre
    try:
        cr = requests.post(
            _url("/sessions"),
            headers=_headers(),
            json={"name": sid_config},
            timeout=DEFAULT_TIMEOUT,
        )
    except RequestException as e:
        return {"ok": False, "mensaje": f"Error de conexion con OpenWA: {e}"}

    if cr.status_code not in (200, 201):
        return {
            "ok": False,
            "mensaje": f"No se pudo crear la nueva sesion: {cr.status_code} {cr.text[:200]}",
        }
    nuevo = cr.json()
    nuevo_id = nuevo.get("id")
    if not nuevo_id:
        return {"ok": False, "mensaje": "OpenWA devolvio una respuesta sin id."}

    # 3. Iniciarla
    res = _start_existing(nuevo_id)
    if res.get("ok"):
        res["mensaje"] = (
            "Sesion reiniciada. Espera 15-25s y recarga para ver el nuevo QR. "
            f"Nuevo session_id: {nuevo_id}"
        )
        res["nuevo_session_id"] = nuevo_id
    return res


def cerrar_sesion() -> dict:
    """
    Cierra la sesion. Como OpenWA no expone /logout, paramos y borramos.
    """
    err = _check_config()
    if err:
        return {"ok": False, "mensaje": err["error"]}

    sid = _resolver_sesion_id()
    if not sid:
        return {"ok": True, "mensaje": "No habia sesion activa que cerrar."}

    msgs = []

    # 1. Stop
    try:
        sr = requests.post(
            _url(f"/sessions/{sid}/stop"),
            headers=_headers(),
            timeout=DEFAULT_TIMEOUT,
        )
        if sr.status_code in (200, 202, 404):
            msgs.append("Sesion detenida")
        else:
            msgs.append(f"Stop respondio {sr.status_code}")
    except RequestException as e:
        msgs.append(f"No se pudo detener: {e}")

    # 2. Delete
    try:
        dr = requests.delete(
            _url(f"/sessions/{sid}"),
            headers=_headers(),
            timeout=DEFAULT_TIMEOUT,
        )
        if dr.status_code in (200, 204, 404):
            msgs.append("Sesion eliminada de OpenWA")
        else:
            msgs.append(f"Delete respondio {dr.status_code}")
    except RequestException as e:
        msgs.append(f"No se pudo borrar: {e}")

    return {
        "ok": True,
        "mensaje": " | ".join(msgs) + ". Recuerda desvincular tambien desde tu WhatsApp (Dispositivos vinculados).",
    }
