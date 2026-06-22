"""
Diagnostico del entorno de produccion para Juntos por Oriana.
Ejecutar en el servidor con:
    python scripts/diagnose.py

Imprime una lista de chequeo con OK / ERROR para los puntos
mas comunes que dejan la app fuera de servicio.
"""
import os
import sys
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

VERDE = "\033[92m"
ROJO = "\033[91m"
AMARILLO = "\033[93m"
AZUL = "\033[94m"
RESET = "\033[0m"


def _ok(msg): print(f"  {VERDE}[OK]{RESET}  {msg}")
def _err(msg): print(f"  {ROJO}[ERROR]{RESET} {msg}")
def _warn(msg): print(f"  {AMARILLO}[WARN]{RESET} {msg}")
def _info(msg): print(f"  {AZUL}[..]{RESET}  {msg}")


def check_python():
    print(f"\n{AZUL}== Python =={RESET}")
    v = sys.version_info
    _info(f"Version: {v.major}.{v.minor}.{v.micro}")
    if v.major < 3 or (v.major == 3 and v.minor < 10):
        _warn(f"Recomendado Python 3.10+. Encontrado {v.major}.{v.minor}")
    else:
        _ok(f"Python {v.major}.{v.minor} OK")


def check_venv():
    print(f"\n{AZUL}== Entorno virtual =={RESET}")
    if hasattr(sys, "real_prefix") or (hasattr(sys, "base_prefix") and sys.base_prefix != sys.prefix):
        _ok(f"venv activo: {sys.prefix}")
    else:
        _warn("NO estas dentro de un venv. Activalo: source .venv/bin/activate")


def check_dependencies():
    print(f"\n{AZUL}== Dependencias criticas =={RESET}")
    requeridas = {
        "fastapi": "FastAPI",
        "uvicorn": "Uvicorn",
        "sqlalchemy": "SQLAlchemy",
        "pydantic": "Pydantic",
        "pydantic_settings": "Pydantic Settings",
        "jinja2": "Jinja2",
        "apscheduler": "APScheduler",
        "gunicorn": "Gunicorn",
        "itsdangerous": "itsdangerous",
        "requests": "Requests",
        "cryptography": "Cryptography (cifrado PII)",
        "psycopg2": "psycopg2 (PostgreSQL)",
    }
    for modulo, nombre in requeridas.items():
        try:
            __import__(modulo)
            _ok(f"{nombre}")
        except ImportError:
            _err(f"{nombre} NO INSTALADO. Ejecuta: pip install -r requirements.txt")


def check_env_file():
    print(f"\n{AZUL}== Archivo .env =={RESET}")
    env_path = ROOT / ".env"
    if not env_path.exists():
        _err(f"No existe {env_path}. Crealo con: cp .env.example .env")
        return False
    _ok(f"Existe: {env_path}")
    contenido = env_path.read_text(encoding="utf-8")
    required = [
        ("DB_SERVER", "PostgreSQL host"),
        ("DB_USER", "PostgreSQL user"),
        ("DB_PASSWORD", "PostgreSQL password"),
        ("DB_NAME", "PostgreSQL database"),
        ("SECRET_KEY", "Session secret"),
        ("ADMIN_USERNAME", "Admin user"),
        ("ADMIN_PASSWORD", "Admin password"),
    ]
    for var, desc in required:
        if f"{var}=" in contenido and not _is_default(contenido, var):
            _ok(f"{var} ({desc})")
        else:
            _warn(f"{var} ({desc}) falta o tiene valor por defecto")
    # Verificar cifrado
    if "FERNET_KEY=" in contenido and not _is_default(contenido, "FERNET_KEY"):
        _ok("FERNET_KEY configurada")
    else:
        _warn("FERNET_KEY no configurada. Ejecuta: python scripts/migrate_encrypt_data.py")
    if "SEARCH_HMAC_KEY=" in contenido and not _is_default(contenido, "SEARCH_HMAC_KEY"):
        _ok("SEARCH_HMAC_KEY configurada")
    else:
        _warn("SEARCH_HMAC_KEY no configurada. Ejecuta: python scripts/migrate_encrypt_data.py")
    # Verificar OpenWA
    if "OPENWA_API_KEY=" in contenido and not _is_default(contenido, "OPENWA_API_KEY"):
        _ok("OPENWA_API_KEY configurada")
    else:
        _warn("OPENWA_API_KEY no configurada (WhatsApp no funcionara, pero el resto si)")
    if "OPENWA_SESSION_ID=" in contenido and not _is_default(contenido, "OPENWA_SESSION_ID"):
        _ok("OPENWA_SESSION_ID configurada")
    else:
        _warn("OPENWA_SESSION_ID no configurada")
    return True


def _is_default(contenido, var):
    lineas = [l for l in contenido.splitlines() if l.startswith(f"{var}=")]
    if not lineas:
        return True
    val = lineas[0].split("=", 1)[1].strip()
    defaults = {"", "tu_contraseña_aqui", "contraseña_admin_aqui",
                "admin", "postgres", "owa_k1_pega_aqui_la_api_key_de_openwa",
                "sess_pega_aqui_el_id_de_sesion", "genera_una_clave_aleatoria_de_64_caracteres_aqui"}
    return val in defaults


def check_db_connection():
    print(f"\n{AZUL}== Conexion a la base de datos =={RESET}")
    try:
        # Recargar settings con el .env actual
        from app.core.config import Settings
        from app.core import config as cfg_mod
        importlib.reload(cfg_mod)
        s = cfg_mod.settings
        from sqlalchemy import create_engine, text
        eng = create_engine(s.database_url, connect_args={"connect_timeout": 5} if "sqlite" not in s.database_url else {})
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        _ok(f"Conexion exitosa: {s.database_url.split('@')[-1] if '@' in s.database_url else s.database_url}")
        return True
    except Exception as e:
        _err(f"No se pudo conectar: {e}")
        _info("Revisa DB_SERVER, DB_PORT, DB_USER, DB_PASSWORD, DB_NAME en .env")
        _info("Asegurate de que PostgreSQL este corriendo: sudo systemctl status postgresql")
        return False
    finally:
        import importlib


def check_app_imports():
    print(f"\n{AZUL}== Importacion de la aplicacion =={RESET}")
    try:
        import main
        _ok("main.py se importa sin errores")
        rutas = sum(1 for r in main.app.routes if hasattr(r, "path"))
        _info(f"Rutas registradas: {rutas}")
        return True
    except Exception as e:
        import traceback
        _err(f"main.py NO se puede importar: {e}")
        print()
        print(traceback.format_exc())
        return False


def check_service():
    print(f"\n{AZUL}== Servicio systemd =={RESET}")
    try:
        out = subprocess.run(
            ["systemctl", "is-active", "juntospororiana"],
            capture_output=True, text=True, timeout=5
        )
        if "active" in out.stdout:
            _ok("Servicio juntospororiana: ACTIVE")
        else:
            _err(f"Servicio juntospororiana: {out.stdout.strip() or 'inactive/failed'}")
            _info("Reactivar: sudo systemctl restart juntospororiana")
            _info("Ver logs:    sudo journalctl -u juntospororiana -n 50 --no-pager")
    except FileNotFoundError:
        _warn("systemctl no disponible (no estas en un sistema systemd?)")
    except subprocess.TimeoutExpired:
        _warn("systemctl timeout")
    except Exception as e:
        _warn(f"No se pudo consultar systemctl: {e}")


def check_docker():
    print(f"\n{AZUL}== Docker / OpenWA =={RESET}")
    try:
        out = subprocess.run(
            ["docker", "ps", "--filter", "name=openwa", "--format", "{{.Names}}: {{.Status}}"],
            capture_output=True, text=True, timeout=5
        )
        if out.returncode == 0 and out.stdout.strip():
            _ok(f"OpenWA: {out.stdout.strip()}")
        else:
            _warn("OpenWA no esta corriendo. La pagina funciona pero sin WhatsApp.")
            _info("Arrancar: cd /var/www/juntospororiana/openwa && docker compose up -d")
    except FileNotFoundError:
        _warn("docker no instalado o no en PATH")
    except subprocess.TimeoutExpired:
        _warn("docker timeout")
    except Exception as e:
        _warn(f"No se pudo consultar docker: {e}")


def main():
    print(f"{AZUL}========================================{RESET}")
    print(f"{AZUL}  Diagnostico - Juntos por Oriana{RESET}")
    print(f"{AZUL}========================================{RESET}")
    check_python()
    check_venv()
    check_dependencies()
    check_env_file()
    check_db_connection()
    check_app_imports()
    check_service()
    check_docker()
    print()
    print(f"{AZUL}========================================{RESET}")
    print(f"{AZUL}  Fin del diagnostico{RESET}")
    print(f"{AZUL}========================================{RESET}")


if __name__ == "__main__":
    main()
