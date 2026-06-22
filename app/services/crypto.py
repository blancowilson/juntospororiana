"""
Cifrado de datos personales (PII) para proteger a los aportantes.

Dos mecanismos:

1) Fernet (cifrado simetrico AES-128-CBC + HMAC-SHA256)
   - Para guardar nombre, cedula, telefono, referencia, etc.
   - Solo se puede descifrar con FERNET_KEY (en .env, NUNCA en el repo)
   - Si un atacante obtiene un dump de la BD, ve texto cifrado inutilizable

2) HMAC-SHA256 determinista (busqueda)
   - Para buscar por cedula/telefono sin descifrar
   - El mismo input siempre produce el mismo hash
   - No se puede invertir (one-way), pero permite igualdad exacta
   - Se guarda en columnas *_hash aparte (cedula_hash, telefono_hash)

Mascarillas para mostrar en UI sin revelar todo:
   - enmascarar_cedula("V-12345678") -> "V-****5678"
   - enmascarar_telefono("584141234567") -> "+58 414 ***-4567"
   - enmascarar_referencia("1234567890") -> "REF ****7890"
"""
import hashlib
import hmac
import logging
import re
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from app.core.config import settings

logger = logging.getLogger(__name__)

# Prefijo que usamos para identificar valores cifrados en la BD.
# Asi el script de migracion sabe si tiene que cifrar o no.
_ENC_PREFIX = "enc:v1:"

# Cache lazy de las instancias Fernet (se crean al primer uso)
_fernet: Optional[Fernet] = None
_fernet_warned = False
_fernet_unavailable = False  # True si la clave no esta configurada


def _get_fernet() -> Optional[Fernet]:
    """
    Devuelve la instancia Fernet, o None si no hay clave configurada.
    Si la clave existe pero es invalida, lanza RuntimeError.
    """
    global _fernet, _fernet_warned, _fernet_unavailable
    if _fernet_unavailable:
        return None
    if _fernet is None:
        if not settings.FERNET_KEY:
            if not _fernet_warned:
                logger.warning(
                    "FERNET_KEY no configurada. Los datos se mostraran en PLANO. "
                    "Ejecuta: python scripts/migrate_encrypt_data.py"
                )
                _fernet_warned = True
            _fernet_unavailable = True
            return None
        try:
            _fernet = Fernet(settings.FERNET_KEY.encode())
        except Exception as e:
            raise RuntimeError(
                f"FERNET_KEY invalida: {e}. "
                f"Genera una nueva con: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            ) from e
    return _fernet


def _get_hmac_key() -> Optional[bytes]:
    if not settings.SEARCH_HMAC_KEY:
        return None
    return settings.SEARCH_HMAC_KEY.encode()


# =========================================================
# Cifrado / Descifrado (Fernet)
# =========================================================

def cifrar(valor: Optional[str]) -> Optional[str]:
    """
    Cifra un valor (PII) y devuelve una cadena lista para guardar en BD.
    Si el valor ya esta cifrado (empieza por enc:v1:), lo devuelve tal cual.
    Si es None o vacio, devuelve None.
    Si no hay FERNET_KEY configurada, devuelve el valor en plano (modo
    pre-migracion; el script de migracion se encargara de cifrar todo
    cuando se ejecute).
    """
    if valor is None:
        return None
    v = str(valor).strip()
    if not v:
        return None
    if v.startswith(_ENC_PREFIX):
        return v  # idempotente
    f = _get_fernet()
    if f is None:
        return v  # sin clave -> guardamos en plano (temporal)
    token = f.encrypt(v.encode("utf-8")).decode("ascii")
    return _ENC_PREFIX + token


def descifrar(valor: Optional[str]) -> Optional[str]:
    """
    Descifra un valor. Si no esta cifrado (legacy) lo devuelve tal cual.
    Si no hay FERNET_KEY configurada, devuelve el valor en plano.
    Si el token es invalido (clave cambiada), devuelve None y loguea error.
    """
    if valor is None:
        return None
    v = str(valor)
    if not v.startswith(_ENC_PREFIX):
        # Legacy: dato en plano (durante la ventana de migracion)
        _warn_plano()
        return v
    f = _get_fernet()
    if f is None:
        # No tenemos clave pero los datos SI estan cifrados: avisar
        logger.error(
            "Datos cifrados en BD pero FERNET_KEY no esta configurada. "
            "Restaurala o ejecuta: python scripts/migrate_encrypt_data.py"
        )
        return None
    try:
        token = v[len(_ENC_PREFIX):].encode("ascii")
        return f.decrypt(token).decode("utf-8")
    except InvalidToken:
        logger.error(
            "Token Fernet invalido. Probable FERNET_KEY incorrecta o cambiada. "
            "Los datos no se pueden descifrar con esta clave."
        )
        return None
    except Exception as e:
        logger.error(f"Error descifrando: {e}")
        return None


_plain_warned = False
def _warn_plano():
    global _plain_warned
    if not _plain_warned:
        logger.warning(
            "Detectados datos en plano en la BD. "
            "Ejecuta el script de migracion: python scripts/migrate_encrypt_data.py"
        )
        _plain_warned = True


# =========================================================
# Hash determinista (busquedas)
# =========================================================

def hash_busqueda(valor: Optional[str]) -> Optional[str]:
    """
    HMAC-SHA256 determinista del valor (en hex).
    Util para guardar en columnas *_hash y buscar por igualdad
    sin descifrar.
    Si no hay SEARCH_HMAC_KEY configurada, devuelve None.
    """
    if valor is None:
        return None
    v = str(valor).strip().lower()
    if not v:
        return None
    key = _get_hmac_key()
    if key is None:
        return None
    return hmac.new(
        key,
        v.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def estado() -> dict:
    """
    Devuelve el estado del subsistema de cifrado. Util para /health.
    """
    return {
        "fernet_configured": bool(settings.FERNET_KEY),
        "fernet_valid": _get_fernet() is not None,
        "search_hmac_configured": bool(settings.SEARCH_HMAC_KEY),
        "encryption_ready": bool(settings.FERNET_KEY) and bool(settings.SEARCH_HMAC_KEY),
        "recommendacion": (
            None if (settings.FERNET_KEY and settings.SEARCH_HMAC_KEY)
            else "Ejecuta: python scripts/migrate_encrypt_data.py"
        ),
    }


# =========================================================
# Mascarillas para UI
# =========================================================

def enmascarar_cedula(cedula: Optional[str]) -> str:
    if not cedula:
        return ""
    limpio = re.sub(r"[^0-9A-Za-z]", "", cedula)
    if len(limpio) <= 4:
        return "*" * len(limpio)
    # Mantener prefijo si es V- o E-
    prefijo = ""
    s = cedula.strip()
    if s[:2].upper() in ("V-", "E-"):
        prefijo = s[:2]
        limpio = limpio[1:]  # quitar la letra
    return f"{prefijo}****{limpio[-4:]}"


def enmascarar_telefono(telefono: Optional[str]) -> str:
    if not telefono:
        return ""
    digitos = re.sub(r"\D", "", str(telefono))
    if len(digitos) < 4:
        return "****"
    # Prefijo de pais si esta
    prefijo = ""
    resto = digitos
    if digitos.startswith("58") and len(digitos) >= 12:
        prefijo = "+58 "
        resto = digitos[2:]
    elif digitos.startswith("0") and len(digitos) >= 11:
        prefijo = ""
        resto = digitos[1:]
    # resto: 3 digitos operador + 7 digitos numero
    if len(resto) >= 10:
        op = resto[:3]
        medio = "*" * 3
        fin = resto[-4:]
        return f"{prefijo}{op} {medio}-{fin}"
    return f"***{digitos[-4:]}"


def enmascarar_referencia(ref: Optional[str]) -> str:
    if not ref:
        return ""
    s = str(ref).strip()
    if len(s) <= 4:
        return "****"
    return f"****{s[-4:]}"


def enmascarar_nombre(nombre: Optional[str]) -> str:
    """
    Devuelve solo el primer nombre + inicial del apellido.
    'Juan Carlos Perez' -> 'Juan C.'
    'Maria' -> 'Maria'
    """
    if not nombre:
        return ""
    partes = [p for p in re.split(r"\s+", str(nombre).strip()) if p]
    if not partes:
        return ""
    if len(partes) == 1:
        return partes[0]
    return f"{partes[0]} {partes[-1][0].upper()}."
