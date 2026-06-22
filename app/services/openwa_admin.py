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


def estado_sesion() -> dict:
    """
    Devuelve un dict con el estado actual de la sesion:
    {
        "configurado": bool,
        "conectado": bool,
        "status": "CONNECTED" | "DISCONNECTED" | "SCAN_QR" | ...,
        "phone": "58414..." | None,
        "push_name": "...",
        "error": "..." (solo si algo falla)
    }
    """
    err = _check_config()
    if err:
        return {"configurado": False, "conectado": False, "error": err["error"]}

    sid = settings.OPENWA_SESSION_ID
    try:
        r = requests.get(
            _url(f"/sessions/{sid}"),
            headers=_headers(),
            timeout=DEFAULT_TIMEOUT,
        )
        if r.status_code == 200:
            data = r.json()
            status = (data.get("status") or "").upper()
            return {
                "configurado": True,
                "conectado": status == "CONNECTED",
                "status": status,
                "phone": data.get("phoneNumber"),
                "push_name": data.get("pushName"),
                "platform": data.get("platform"),
            }
        if r.status_code == 404:
            return {
                "configurado": False,
                "conectado": False,
                "status": "NOT_FOUND",
                "error": f"La sesion {sid} no existe en OpenWA. Usa 'Iniciar sesion' para crearla.",
            }
        return {
            "configurado": True,
            "conectado": False,
            "status": "ERROR",
            "error": f"OpenWA respondio {r.status_code}: {r.text[:200]}",
        }
    except RequestException as e:
        return {
            "configurado": True,
            "conectado": False,
            "status": "OFFLINE",
            "error": f"No se pudo contactar OpenWA ({settings.OPENWA_BASE_URL}): {e}",
        }


def obtener_qr() -> Optional[dict]:
    """
    Devuelve el QR actual (PNG base64 + codigo) si la sesion lo esta mostrando.
    None si no hay QR (ya vinculada o no iniciada).
    """
    err = _check_config()
    if err:
        return None

    sid = settings.OPENWA_SESSION_ID
    try:
        r = requests.get(
            _url(f"/sessions/{sid}/qr"),
            headers=_headers(),
            timeout=DEFAULT_TIMEOUT,
        )
        if r.status_code == 200:
            return r.json()
        return None
    except RequestException as e:
        logger.warning(f"No se pudo obtener QR: {e}")
        return None


def iniciar_sesion() -> dict:
    """
    Crea la sesion (si no existe) y la inicia.
    Retorna dict con 'ok' y 'mensaje'.
    """
    err = _check_config()
    if err:
        return {"ok": False, "mensaje": err["error"]}

    sid = settings.OPENWA_SESSION_ID
    try:
        # 1. Verificar si la sesion ya existe
        r = requests.get(_url("/sessions"), headers=_headers(), timeout=DEFAULT_TIMEOUT)
        sesiones = r.json() if r.status_code == 200 else []
        existe = any(s.get("id") == sid for s in sesiones)

        if not existe:
            # Crear sesion nueva
            cr = requests.post(
                _url("/sessions"),
                headers=_headers(),
                json={"name": settings.OPENWA_SESSION_ID},
                timeout=DEFAULT_TIMEOUT,
            )
            if cr.status_code not in (200, 201):
                return {
                    "ok": False,
                    "mensaje": f"No se pudo crear la sesion: {cr.status_code} {cr.text[:200]}",
                }
            nueva = cr.json()
            nuevo_id = nueva.get("id")
            if nuevo_id and nuevo_id != sid:
                return {
                    "ok": False,
                    "mensaje": f"OpenWA genero un id distinto ({nuevo_id}). "
                               f"Actualiza OPENWA_SESSION_ID en .env y reinicia FastAPI.",
                }

        # 2. Iniciar la sesion
        sr = requests.post(
            _url(f"/sessions/{sid}/start"),
            headers=_headers(),
            timeout=DEFAULT_TIMEOUT,
        )
        if sr.status_code in (200, 202):
            return {"ok": True, "mensaje": "Sesion iniciada. Recarga la pagina para ver el QR."}
        return {
            "ok": False,
            "mensaje": f"No se pudo iniciar: {sr.status_code} {sr.text[:200]}",
        }
    except RequestException as e:
        return {"ok": False, "mensaje": f"Error de conexion con OpenWA: {e}"}


def reiniciar_sesion() -> dict:
    """Reinicia el contenedor / la sesion."""
    err = _check_config()
    if err:
        return {"ok": False, "mensaje": err["error"]}
    sid = settings.OPENWA_SESSION_ID
    try:
        r = requests.post(
            _url(f"/sessions/{sid}/restart"),
            headers=_headers(),
            timeout=DEFAULT_TIMEOUT,
        )
        if r.status_code in (200, 202):
            return {"ok": True, "mensaje": "Sesion reiniciada. Refresca para ver el nuevo QR."}
        return {
            "ok": False,
            "mensaje": f"No se pudo reiniciar: {r.status_code} {r.text[:200]}",
        }
    except RequestException as e:
        return {"ok": False, "mensaje": f"Error de conexion con OpenWA: {e}"}


def cerrar_sesion() -> dict:
    """Cierra la sesion de WhatsApp (logout)."""
    err = _check_config()
    if err:
        return {"ok": False, "mensaje": err["error"]}
    sid = settings.OPENWA_SESSION_ID
    try:
        r = requests.post(
            _url(f"/sessions/{sid}/logout"),
            headers=_headers(),
            timeout=DEFAULT_TIMEOUT,
        )
        if r.status_code in (200, 202):
            return {"ok": True, "mensaje": "Sesion cerrada en WhatsApp."}
        return {
            "ok": False,
            "mensaje": f"No se pudo cerrar: {r.status_code} {r.text[:200]}",
        }
    except RequestException as e:
        return {"ok": False, "mensaje": f"Error de conexion con OpenWA: {e}"}
