"""End-to-end test of the 4 reassign modes + edge cases."""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import os, tempfile
# Use a file-based SQLite so the schema persists across multiple connections/sessions.
_db_fd, _db_path = tempfile.mkstemp(suffix='.sqlite')
os.close(_db_fd)
os.environ['DB_SERVER'] = f'sqlite:///{_db_path}'
os.environ['ADMIN_USERNAME'] = 'admin'
os.environ['ADMIN_PASSWORD'] = 'admin'
os.environ['FERNET_KEY'] = 'CcGFR7mSHe6b6QMsrh_wKAKu5O7bc9P7InTjPNXWamk='
os.environ['SEARCH_HMAC_KEY'] = 'a'*64
os.environ['OPENWA_ENABLED'] = 'false'

from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy import text as sql_text

from app.db.session import Base, engine, SessionLocal
from app.models.all_models import Campana, Rifas, Tickets, Aportantes, AuditLog
from app.services import crypto

Base.metadata.create_all(engine)

# === Seed ===
db = SessionLocal()
campana = Campana(meta_total=2750, activa=True)
rifa = Rifas(
    titulo='Gran Rifa Solidaria', premio='Xiaomi Redmi 15c',
    precio_ticket_bs=500, precio_ticket_usd=0.6, total_numeros=1000,
    loteria_referencia='Triple Caracas',
    fecha_sorteo=datetime(2026,7,22,0,0,0), estado='Activa',
)
db.add_all([campana, rifa]); db.flush()
db.add_all([Tickets(rifa_id=rifa.id, numero=i, estado='Disponible') for i in range(1000)])
nombre_c = crypto.cifrar('Juan Perez Test')
telefono_c = crypto.cifrar('+584141234567')
cedula_c = crypto.cifrar('V12345678')
referencia_c = crypto.cifrar('REF123456')
ap = Aportantes(
    nombre=nombre_c, cedula=cedula_c, telefono=telefono_c,
    monto_reportado=3.0, moneda='USD', metodo_pago='Zelle',
    referencia=referencia_c,
    cedula_hash=crypto.hash_busqueda('V12345678'),
    telefono_hash=crypto.hash_busqueda('+584141234567'),
    referencia_hash=crypto.hash_busqueda('REF123456'),
    tipo_aporte='Rifa', boletos_iniciales='010, 020, 030',
)
db.add(ap); db.commit()
APORTANTE_ID = ap.id
RIFA_ID = rifa.id
db.close()
print('SETUP OK: aportante_id=', APORTANTE_ID, 'rifa_id=', RIFA_ID)


def reset_tickets(reserved_nums=set(), occupied_by_other_nums=set()):
    db = SessionLocal()
    try:
        # Liberar TODOS los tickets
        db.execute(sql_text(
            "UPDATE Tickets SET estado='Disponible', aportante_id=NULL, "
            "reservado_en=NULL, referencia_pago=NULL, referencia_pago_hash=NULL, monto_reportado=NULL"
        ))
        # Asignar reservados al aportante
        for n in reserved_nums:
            t = db.query(Tickets).filter(Tickets.rifa_id == RIFA_ID, Tickets.numero == n).first()
            if t:
                t.estado = 'Reservado'
                t.aportante_id = APORTANTE_ID
                t.reservado_en = datetime.now(timezone.utc)
        # Asignar ocupados a otro aportante
        if occupied_by_other_nums:
            otro_nombre = crypto.cifrar('Otro Colaborador')
            otro_tel = crypto.cifrar('+584129998888')
            otro_ref = crypto.cifrar('REFOTRO999')
            otro = Aportantes(
                nombre=otro_nombre, telefono=otro_tel,
                monto_reportado=1.0, moneda='USD', metodo_pago='Zelle',
                referencia=otro_ref,
                telefono_hash=crypto.hash_busqueda('+584129998888'),
                referencia_hash=crypto.hash_busqueda('REFOTRO999'),
                tipo_aporte='Rifa',
            )
            db.add(otro); db.flush()
            for n in occupied_by_other_nums:
                t = db.query(Tickets).filter(Tickets.rifa_id == RIFA_ID, Tickets.numero == n).first()
                if t:
                    t.estado = 'Reservado'
                    t.aportante_id = otro.id
                    t.reservado_en = datetime.now(timezone.utc)
        db.commit()
    finally:
        db.close()


def tickets_del_aportante():
    db = SessionLocal()
    try:
        return [t.numero for t in db.query(Tickets).filter(Tickets.aportante_id == APORTANTE_ID).order_by(Tickets.numero).all()]
    finally:
        db.close()


def audit_reasignaciones():
    db = SessionLocal()
    try:
        return db.query(AuditLog).filter(AuditLog.accion == 'REASSIGN_APORTANTE').count()
    finally:
        db.close()


def boletos_iniciales():
    db = SessionLocal()
    try:
        return db.get(Aportantes, APORTANTE_ID).boletos_iniciales
    finally:
        db.close()


def libres_count():
    db = SessionLocal()
    try:
        return db.query(Tickets).filter(Tickets.rifa_id == RIFA_ID, Tickets.estado == 'Disponible').count()
    finally:
        db.close()


# === App + client ===
from app.api.routers.admin import router as admin_router
from app.api.routers.public import router as public_router
from fastapi import FastAPI
app = FastAPI()
app.include_router(public_router)
app.include_router(admin_router)
client = TestClient(app)

# Capturar la tarea WA en background
wa_calls = []
def fake_notificar_reasignacion(telefono, nombre, cantidad, numeros):
    wa_calls.append({'telefono': telefono, 'nombre': nombre, 'cantidad': cantidad, 'numeros': numeros})
    return True

import app.api.routers.admin as adm_mod
adm_mod.wa.notificar_reasignacion = fake_notificar_reasignacion

AUTH = ('admin', 'admin')

# === Test 1: original_only_free (todos libres) ===
reset_tickets()
r = client.post(f'/admin/reasignar/aportante/{APORTANTE_ID}',
                data={'mode': 'original_only_free', 'cantidad': '3', 'notificar_wa': 'on'},
                auth=AUTH)
print('\nTEST1 original_only_free todos libres status:', r.status_code)
print('  body preview:', r.text[:200])
nums = tickets_del_aportante()
print('  tickets del aportante:', nums)
assert r.status_code == 200, f'expected 200, got {r.status_code}: {r.text}'
assert nums == [10, 20, 30], f'expected [10,20,30], got {nums}'
assert boletos_iniciales() == '010, 020, 030'
print('  boletos_iniciales:', boletos_iniciales())
print('  audit count:', audit_reasignaciones())
print('  wa_calls:', wa_calls[-1] if wa_calls else None)
assert wa_calls and wa_calls[-1]['cantidad'] == 3

# === Test 2: mixed (010 libre, 020 ocupado por otro, 030 libre; cantidad=3) ===
reset_tickets(reserved_nums=set(), occupied_by_other_nums={20})
r = client.post(f'/admin/reasignar/aportante/{APORTANTE_ID}',
                data={'mode': 'mixed', 'cantidad': '3', 'notificar_wa': 'on'},
                auth=AUTH)
print('\nTEST2 mixed status:', r.status_code)
nums = tickets_del_aportante()
print('  tickets del aportante (esperaba 010,030 + 1 al azar):', nums)
assert r.status_code == 200, f'expected 200, got {r.status_code}: {r.text}'
assert 10 in nums and 30 in nums, 'Debio mantener 010 y 030 libres'
assert len(nums) == 3, f'Debio tener 3 tickets, obtuvo {nums}'
assert 20 not in nums, '020 esta ocupado por otro, no debe estar'
print('  boletos_iniciales:', boletos_iniciales())
print('  audit count:', audit_reasignaciones())
print('  wa_calls:', wa_calls[-1] if wa_calls else None)

# === Test 3: random (ignora originales, asigna 3 al azar) ===
reset_tickets(reserved_nums=set(), occupied_by_other_nums={20})
r = client.post(f'/admin/reasignar/aportante/{APORTANTE_ID}',
                data={'mode': 'random', 'cantidad': '3', 'notificar_wa': 'on'},
                auth=AUTH)
print('\nTEST3 random status:', r.status_code)
nums = tickets_del_aportante()
print('  tickets del aportante:', nums)
assert r.status_code == 200
assert len(nums) == 3
print('  boletos_iniciales:', boletos_iniciales())
print('  audit count:', audit_reasignaciones())
print('  wa_calls:', wa_calls[-1] if wa_calls else None)

# === Test 4: manual con numeros especificos ===
reset_tickets(reserved_nums=set(), occupied_by_other_nums={20})  # 010,030 libres
r = client.post(f'/admin/reasignar/aportante/{APORTANTE_ID}',
                data={'mode': 'manual', 'cantidad': '2', 'numeros_manual': '10,30', 'notificar_wa': 'on'},
                auth=AUTH)
print('\nTEST4 manual status:', r.status_code)
nums = tickets_del_aportante()
print('  tickets del aportante (esperaba [10,30]):', nums)
assert r.status_code == 200, f'expected 200, got {r.status_code}: {r.text}'
assert nums == [10, 30], f'Esperaba [10,30], obtuve {nums}'
print('  boletos_iniciales:', boletos_iniciales())
print('  audit count:', audit_reasignaciones())
print('  wa_calls:', wa_calls[-1] if wa_calls else None)

# === Test 5: manual con numero fuera de rango ===
reset_tickets()
r = client.post(f'/admin/reasignar/aportante/{APORTANTE_ID}',
                data={'mode': 'manual', 'cantidad': '1', 'numeros_manual': '9999', 'notificar_wa': 'on'},
                auth=AUTH)
print('\nTEST5 manual fuera de rango status:', r.status_code)
print('  body:', r.text[:200])
assert r.status_code == 400, f'expected 400, got {r.status_code}'

# === Test 6: manual con numero ocupado por otro ===
reset_tickets(reserved_nums=set(), occupied_by_other_nums={10})
r = client.post(f'/admin/reasignar/aportante/{APORTANTE_ID}',
                data={'mode': 'manual', 'cantidad': '1', 'numeros_manual': '10', 'notificar_wa': 'on'},
                auth=AUTH)
print('\nTEST6 manual ocupado status:', r.status_code)
print('  body:', r.text[:300])
assert r.status_code == 400, f'expected 400, got {r.status_code}'

# === Test 7: original_only_free con TODOS los originales ocupados ===
reset_tickets(reserved_nums=set(), occupied_by_other_nums={10, 20, 30})
r = client.post(f'/admin/reasignar/aportante/{APORTANTE_ID}',
                data={'mode': 'original_only_free', 'cantidad': '3', 'notificar_wa': 'on'},
                auth=AUTH)
print('\nTEST7 original todos ocupados status:', r.status_code)
nums = tickets_del_aportante()
print('  tickets del aportante (esperaba 0):', nums)
assert r.status_code == 400, f'expected 400 (no hay libres), got {r.status_code}'
assert nums == [], f'Esperaba [] (no hay libres), obtuve {nums}'
print('  body:', r.text[:200])

# === Test 8: mode invalido ===
reset_tickets()
r = client.post(f'/admin/reasignar/aportante/{APORTANTE_ID}',
                data={'mode': 'hack', 'cantidad': '1', 'notificar_wa': 'on'},
                auth=AUTH)
print('\nTEST8 mode invalido status:', r.status_code)
assert r.status_code == 400
print('  body:', r.text[:150])

# === Test 9: GET form ===
# Antes de TEST9 el boletos_iniciales se actualizo en TESTs previos. Lo
# reseteamos manualmente para que el form muestre los tres numeros.
db = SessionLocal()
ap_for_form = db.get(Aportantes, APORTANTE_ID)
ap_for_form.boletos_iniciales = '010, 020, 030'
db.commit()
db.close()
reset_tickets(reserved_nums=set(), occupied_by_other_nums={20})  # 010 libre, 020 ocupado, 030 libre
r = client.get(f'/admin/reasignar/aportante/{APORTANTE_ID}/form', auth=AUTH)
print('\nTEST9 GET form status:', r.status_code, 'bytes:', len(r.text))
content_checks = {
    'mode-mixed': 'mode-mixed' in r.text,
    'mode-original': 'mode-original' in r.text,
    'mode-random': 'mode-random' in r.text,
    'mode-manual': 'mode-manual' in r.text,
    '010 visible': '010' in r.text,
    '020 visible': '020' in r.text,
    '030 visible': '030' in r.text,
    'manual-picker': 'manual-picker' in r.text,
    'notificar-wa': 'notificar-wa' in r.text,
    'libres count text': 'Disponibles en la rifa' in r.text,
    'libres count value': str(libres_count()) in r.text,
    'mixto recomendado': 'Mixto (Recomendado)' in r.text,
    'seleccion manual': 'Selecci' in r.text,
}
for k, v in content_checks.items():
    print(f'  {k}: {v}')

# Debug: buscar donde aparece cada numero
import re
print('\n  matches 010:', re.findall(r'010[^0-9]', r.text)[:3])
print('  matches 020:', re.findall(r'020[^0-9]', r.text)[:3])
print('  matches 030:', re.findall(r'030[^0-9]', r.text)[:3])
print('  all bytes around 020:', [m.start() for m in re.finditer(r'020', r.text)])

assert all(content_checks.values()), f'Missing: {[k for k,v in content_checks.items() if not v]}'

# === Test 10: GET form cuando NO hay boletos_iniciales ===
db = SessionLocal()
ap_test = db.get(Aportantes, APORTANTE_ID)
ap_test.boletos_iniciales = None
db.commit()
db.close()
r = client.get(f'/admin/reasignar/aportante/{APORTANTE_ID}/form', auth=AUTH)
print('\nTEST10 GET sin boletos_iniciales status:', r.status_code, 'bytes:', len(r.text))
assert 'Este aportante no tiene boletos originales' in r.text
assert 'mode-original' in r.text and 'disabled' in r.text  # el radio de original_only_free debe estar disabled
print('  OK: mensaje informativo + radio original disabled')

print('\n=== TODOS LOS TESTS PASARON ===')
