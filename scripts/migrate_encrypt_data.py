"""
Migracion: cifra los datos sensibles de aportantes y tickets que ya
esten en la BD en texto plano, y rellena las columnas *_hash.

Uso:
    python scripts/migrate_encrypt_data.py

El script es IDEMPOTENTE: si lo corres varias veces no rompe nada.
Ademas, si las claves FERNET_KEY / SEARCH_HMAC_KEY no estan en .env,
las genera y las agrega (backup previo a .env.bak).
"""
import os
import re
import sys
from pathlib import Path

# Path raiz del proyecto (un nivel arriba de /scripts)
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# Cargar .env manualmente (pydantic-settings tambien lo haria, pero aqui
# queremos ver el estado antes de importar la app)
ENV_PATH = ROOT / ".env"
ENV_BAK = ROOT / ".env.bak"


def _read_env() -> str:
    if ENV_PATH.exists():
        return ENV_PATH.read_text(encoding="utf-8")
    return ""


def _write_env(content: str) -> None:
    ENV_PATH.write_text(content, encoding="utf-8")


def ensure_keys() -> None:
    """
    Si FERNET_KEY o SEARCH_HMAC_KEY no existen (o estan vacias) en .env,
    las genera y las escribe. Hace backup previo a .env.bak.
    """
    from cryptography.fernet import Fernet
    import secrets

    content = _read_env()
    if ENV_PATH.exists():
        ENV_BAK.write_text(content, encoding="utf-8")
        print(f"[OK] Backup de .env -> {ENV_BAK}")

    fernet_present = "FERNET_KEY=" in content and not re.search(r"^FERNET_KEY=\s*$", content, re.M)
    hmac_present = "SEARCH_HMAC_KEY=" in content and not re.search(r"^SEARCH_HMAC_KEY=\s*$", content, re.M)

    if not fernet_present:
        new_key = Fernet.generate_key().decode()
        if content and not content.endswith("\n"):
            content += "\n"
        content += f"FERNET_KEY={new_key}\n"
        print("[OK] Generada FERNET_KEY (Fernet AES-128-CBC + HMAC-SHA256)")
    else:
        print("[..] FERNET_KEY ya existe, no se regenera")

    if not hmac_present:
        new_key = secrets.token_hex(32)
        if content and not content.endswith("\n"):
            content += "\n"
        content += f"SEARCH_HMAC_KEY={new_key}\n"
        print("[OK] Generada SEARCH_HMAC_KEY (HMAC-SHA256 hex)")
    else:
        print("[..] SEARCH_HMAC_KEY ya existe, no se regenera")

    _write_env(content)


def _is_encrypted(val: str | None) -> bool:
    if not val:
        return True  # nada que cifrar
    return str(val).startswith("enc:v1:")


def migrate() -> None:
    # Importar DESPUES de asegurar las claves (pydantic-settings las lee)
    from sqlalchemy import select
    from app.db.session import SessionLocal, engine, Base
    from app.models.all_models import Aportantes, Tickets
    from app.services import crypto

    print()
    print("=== Migrando datos a cifrado ===")
    print(f"BD: {engine.url}")

    # Crear tablas / columnas nuevas si no existen
    Base.metadata.create_all(bind=engine)
    print("[OK] Esquema verificado (columnas hash y AuditLog creadas si faltaban)")

    with SessionLocal() as db:
        # Aportantes
        aportantes = db.execute(select(Aportantes)).scalars().all()
        ap_total = len(aportantes)
        ap_cifrados = 0
        ap_hashes = 0
        for a in aportantes:
            dirty = False
            for campo in ("nombre", "cedula", "telefono", "referencia"):
                val = getattr(a, campo)
                if val and not _is_encrypted(val):
                    setattr(a, campo, crypto.cifrar(val))
                    ap_cifrados += 1
                    dirty = True
            # Rellenar *_hash si estan vacios
            for campo, src in (("cedula_hash", "cedula"), ("telefono_hash", "telefono"), ("referencia_hash", "referencia")):
                if not getattr(a, campo):
                    h = crypto.hash_busqueda(getattr(a, src))
                    if h:
                        setattr(a, campo, h)
                        ap_hashes += 1
                        dirty = True
            if dirty:
                pass  # al hacer commit al final aplica todos los cambios

        # Tickets
        tickets = db.execute(select(Tickets)).scalars().all()
        tk_total = len(tickets)
        tk_cifrados = 0
        tk_hashes = 0
        for t in tickets:
            dirty = False
            if t.referencia_pago and not _is_encrypted(t.referencia_pago):
                t.referencia_pago = crypto.cifrar(t.referencia_pago)
                tk_cifrados += 1
                dirty = True
            if not t.referencia_pago_hash and t.referencia_pago:
                # Descifrar el valor (recien cifrado o ya cifrado) para hashear
                raw = crypto.descifrar(t.referencia_pago) if t.referencia_pago else None
                h = crypto.hash_busqueda(raw)
                if h:
                    t.referencia_pago_hash = h
                    tk_hashes += 1
                    dirty = True

        db.commit()

        print()
        print("=== Resultado ===")
        print(f"Aportantes revisados: {ap_total}")
        print(f"  Campos cifrados:    {ap_cifrados}")
        print(f"  Hashes generados:   {ap_hashes}")
        print(f"Tickets revisados:    {tk_total}")
        print(f"  Referencias cifradas: {tk_cifrados}")
        print(f"  Hashes generados:   {tk_hashes}")
        print()
        print("Migracion completada.")


if __name__ == "__main__":
    print("=== Juntos por Oriana - Migracion a cifrado de PII ===\n")
    ensure_keys()
    # Recargar config (pydantic-settings ya leyo .env en import time;
    # para que tome las claves nuevas que acabamos de escribir,
    # reimportamos el modulo de settings).
    import importlib
    from app.core import config as cfg_mod
    importlib.reload(cfg_mod)
    from app.services import crypto
    importlib.reload(crypto)
    migrate()
