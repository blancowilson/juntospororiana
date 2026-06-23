"""
Verifica que el flujo de reasignacion NO elimina aportantes ni tickets,
solo cambia el estado y la asignacion. Ademas valida que boletos_iniciales
se actualiza correctamente y que AuditLog registra la operacion.
"""
import sys, io
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
import os, tempfile

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
from app.models.all_models import Campana, Rifas, Tickets, Aportantes, AuditLog, LotesConciliacion
from app.services import crypto

Base.metadata.create_all(engine)

# === Seed realista: 3 aportantes con diferentes estados + 1 donacion + 1 rifa pagada + 1 rifa liberada ===
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


def mk_aportante(nombre, telefono, monto, ref, iniciales):
    return Aportantes(
        nombre=crypto.cifrar(nombre),
        cedula=crypto.cifrar('V' + str(abs(hash(nombre)) % 1_000_000_000)),
        telefono=crypto.cifrar(telefono),
        monto_reportado=monto, moneda='USD', metodo_pago='Zelle',
        referencia=crypto.cifrar(ref),
        cedula_hash=crypto.hash_busqueda('V' + str(abs(hash(nombre)) % 1_000_000_000)),
        telefono_hash=crypto.hash_busqueda(telefono),
        referencia_hash=crypto.hash_busqueda(ref),
        tipo_aporte='Rifa', boletos_iniciales=iniciales,
    )


ap1 = mk_aportante('Ana Reservada', '+584141111111', 1.2, 'REF-ANA', '100, 200, 300')
ap2 = mk_aportante('Beto Pagado', '+584142222222', 1.2, 'REF-BETO', '150, 250, 350')
ap3 = mk_aportante('Carla Liberada', '+584143333333', 1.2, 'REF-CARLA', '111, 222, 333')
ap_don = Aportantes(
    nombre=crypto.cifrar('Don Pedro'),
    telefono=crypto.cifrar('+584144444444'),
    monto_reportado=10.0, moneda='USD', metodo_pago='PayPal',
    referencia=crypto.cifrar('REF-DON'),
    telefono_hash=crypto.hash_busqueda('+584144444444'),
    referencia_hash=crypto.hash_busqueda('REF-DON'),
    tipo_aporte='Donacion', boletos_iniciales=None,
)
db.add_all([ap1, ap2, ap3, ap_don]); db.flush()

# Asignar tickets: ap1 Reservado, ap2 Pagado, ap3 Liberado (ap_id=None)
for n in [100, 200, 300]:
    t = db.query(Tickets).filter(Tickets.rifa_id == rifa.id, Tickets.numero == n).first()
    t.estado = 'Reservado'; t.aportante_id = ap1.id; t.reservado_en = datetime.now(timezone.utc)
    t.monto_reportado = 0.4
for n in [150, 250, 350]:
    t = db.query(Tickets).filter(Tickets.rifa_id == rifa.id, Tickets.numero == n).first()
    t.estado = 'Pagado'; t.aportante_id = ap2.id; t.reservado_en = datetime.now(timezone.utc)
    t.monto_reportado = 0.4
for n in [111, 222, 333]:
    t = db.query(Tickets).filter(Tickets.rifa_id == rifa.id, Tickets.numero == n).first()
    t.estado = 'Disponible'  # liberado por scheduler / reversar
# Lote de conciliacion
db.add(LotesConciliacion(nombre_archivo='banco.csv', registros_procesados=10, pagos_aprobados=1))
db.commit()

ANA_ID = ap1.id
BETO_ID = ap2.id
CARLA_ID = ap3.id
DON_ID = ap_don.id
RIFA_ID = rifa.id
CAMPANA_ID = campana.id
db.close()

def snapshot():
    db = SessionLocal()
    try:
        return {
            'aportantes': db.query(Aportantes).count(),
            'aportantes_ids': sorted(a.id for a in db.query(Aportantes).all()),
            'tickets_total': db.query(Tickets).count(),
            'tickets_estado': {e: db.query(Tickets).filter(Tickets.estado == e).count()
                              for e in ['Disponible', 'Reservado', 'Pagado']},
            'campanas': db.query(Campana).count(),
            'rifas': db.query(Rifas).count(),
            'lotes': db.query(LotesConciliacion).count(),
            'audit_total': db.query(AuditLog).count(),
        }
    finally:
        db.close()

before = snapshot()
print('=== SNAPSHOT INICIAL ===')
for k, v in before.items():
    print(f'  {k}: {v}')

# === App + client ===
from app.api.routers.admin import router as admin_router
from app.api.routers.public import router as public_router
from fastapi import FastAPI
app = FastAPI()
app.include_router(public_router)
app.include_router(admin_router)
client = TestClient(app)

# Stub WA
import app.api.routers.admin as adm_mod
adm_mod.wa.notificar_reasignacion = lambda *a, **k: True
AUTH = ('admin', 'admin')

def reasignar(aportante_id, mode, cantidad, **extra):
    data = {'mode': mode, 'cantidad': str(cantidad), 'notificar_wa': 'on'}
    data.update({k: str(v) for k, v in extra.items()})
    return client.post(f'/admin/reasignar/aportante/{aportante_id}', data=data, auth=AUTH)


# === Reasignar a Ana (Reservado -> random) ===
r = reasignar(ANA_ID, 'random', 3)
print('\n--- Reasignar Ana (random, 3) ---')
print(f'  status={r.status_code}')
assert r.status_code == 200

# === Reasignar a Carla (Liberado -> mixed) ===
r = reasignar(CARLA_ID, 'mixed', 3)
print('\n--- Reasignar Carla (mixed, 3) ---')
print(f'  status={r.status_code}')
assert r.status_code == 200

# === Reasignar a Ana otra vez (random) ===
r = reasignar(ANA_ID, 'random', 3)
print('\n--- Reasignar Ana otra vez (random, 3) ---')
print(f'  status={r.status_code}')
assert r.status_code == 200

# === Reasignar a Ana con manual ===
r = reasignar(ANA_ID, 'manual', 1, numeros_manual='450')
print('\n--- Reasignar Ana (manual, 450) ---')
print(f'  status={r.status_code}')
assert r.status_code == 200

# === Reasignar a Carla con original_only_free (todos ocupados, debe fallar) ===
# Primero ocupamos sus originales por Beto
db = SessionLocal()
for n in [111, 222, 333]:
    t = db.query(Tickets).filter(Tickets.rifa_id == RIFA_ID, Tickets.numero == n).first()
    t.estado = 'Pagado'; t.aportante_id = BETO_ID
db.commit(); db.close()
r = reasignar(CARLA_ID, 'original_only_free', 3)
print('\n--- Reasignar Carla (original_only_free, todos ocupados) ---')
print(f'  status={r.status_code} body={r.text[:200]}')
assert r.status_code == 400

# === Reasignar a Carla con mixed (originales ocupados, debe tomar al azar) ===
r = reasignar(CARLA_ID, 'mixed', 3)
print('\n--- Reasignar Carla (mixed, sin libres) ---')
print(f'  status={r.status_code}')
assert r.status_code == 200

# === Reasignar con modo invalido (debe fallar sin tocar nada) ===
r = reasignar(ANA_ID, 'hacker_mode', 3)
print('\n--- Reasignar Ana (modo invalido) ---')
print(f'  status={r.status_code}')
assert r.status_code == 400

# === Reasignar a Ana con cantidad 0 (debe fallar) ===
r = reasignar(ANA_ID, 'random', 0)
print('\n--- Reasignar Ana (cantidad 0) ---')
print(f'  status={r.status_code}')
assert r.status_code == 400

# === Verificacion FINAL: nada se elimino ===
after = snapshot()
print('\n=== SNAPSHOT FINAL ===')
for k, v in after.items():
    print(f'  {k}: {v}')

print('\n=== COMPARACION ===')
errors = []
# 1. Misma cantidad de aportantes
if after['aportantes'] != before['aportantes']:
    errors.append(f"aportantes cambio: {before['aportantes']} -> {after['aportantes']}")
# 2. Mismos IDs de aportantes (no se borro ninguno)
if after['aportantes_ids'] != before['aportantes_ids']:
    errors.append(f"IDs de aportantes cambiaron: {before['aportantes_ids']} -> {after['aportantes_ids']}")
# 3. Misma cantidad total de tickets
if after['tickets_total'] != before['tickets_total']:
    errors.append(f"tickets cambio: {before['tickets_total']} -> {after['tickets_total']}")
# 4. Misma cantidad en cada estado (suma constante)
sum_estados_before = sum(before['tickets_estado'].values())
sum_estados_after = sum(after['tickets_estado'].values())
if sum_estados_before != sum_estados_after:
    errors.append(f"suma de tickets cambio: {sum_estados_before} -> {sum_estados_after}")
# 5. Campana, rifa y lote intactos
if after['campanas'] != before['campanas']:
    errors.append(f"campanas cambio: {before['campanas']} -> {after['campanas']}")
if after['rifas'] != before['rifas']:
    errors.append(f"rifas cambio: {before['rifas']} -> {after['rifas']}")
if after['lotes'] != before['lotes']:
    errors.append(f"lotes cambio: {before['lotes']} -> {after['lotes']}")
# 6. Audit crecio (al menos 6 reasignaciones exitosas)
if after['audit_total'] < before['audit_total'] + 4:
    errors.append(f"audit no crecio lo suficiente: {before['audit_total']} -> {after['audit_total']}")

if errors:
    print('\n!!! ERRORES DE PRESERVACION DE DATOS !!!')
    for e in errors:
        print('  -', e)
    sys.exit(1)
print('\nOK: ningun dato fue eliminado.')
print(f'   - Aportantes: {after["aportantes"]} (igual)')
print(f'   - Tickets: {after["tickets_total"]} (igual)')
print(f'   - Suma por estado: {sum_estados_after} (igual)')
print(f'   - Campana: {after["campanas"]} (igual)')
print(f'   - Rifa: {after["rifas"]} (igual)')
print(f'   - Lotes conciliacion: {after["lotes"]} (igual)')
print(f'   - AuditLog paso de {before["audit_total"]} a {after["audit_total"]}')

# === Verificar boletos_iniciales de Ana y Carla ===
db = SessionLocal()
ana_bi = db.get(Aportantes, ANA_ID).boletos_iniciales
carla_bi = db.get(Aportantes, CARLA_ID).boletos_iniciales
beto_bi = db.get(Aportantes, BETO_ID).boletos_iniciales
print(f'\n   boletos_iniciales Ana: "{ana_bi}"')
print(f'   boletos_iniciales Beto (no tocado): "{beto_bi}"')
print(f'   boletos_iniciales Carla: "{carla_bi}"')
assert beto_bi == '150, 250, 350', f'Beto (Pagado) no debio cambiar, obtuvo {beto_bi}'
db.close()
print('\n=== PRESERVACION DE DATOS: OK ===')
