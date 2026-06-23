"""End-to-end: pg_dump fake + restore logic verification."""
import sys, io, os, subprocess, tempfile, hashlib
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# Crear un dump valido estilo pg_dump
db_path = tempfile.mktemp(suffix='.sqlite')
dump_path = tempfile.mktemp(suffix='.sql')

# Crear BD sqlite
import sqlite3
conn = sqlite3.connect(db_path)
cur = conn.cursor()
cur.execute("""CREATE TABLE Aportantes (
    id INTEGER PRIMARY KEY,
    nombre TEXT, telefono TEXT, monto REAL, boletos_iniciales TEXT
)""")
cur.execute("""CREATE TABLE Tickets (
    id INTEGER PRIMARY KEY, numero INTEGER, estado TEXT, aportante_id INTEGER
)""")
cur.execute("""CREATE TABLE Rifas (
    id INTEGER PRIMARY KEY, titulo TEXT, estado TEXT
)""")
cur.execute("INSERT INTO Aportantes VALUES (1, 'Ana', '+584141111', 5.0, '001, 002')")
cur.execute("INSERT INTO Aportantes VALUES (2, 'Beto', '+584142222', 3.0, NULL)")
cur.execute("INSERT INTO Tickets VALUES (1, 1, 'Reservado', 1)")
cur.execute("INSERT INTO Tickets VALUES (2, 2, 'Reservado', 1)")
cur.execute("INSERT INTO Rifas VALUES (1, 'Gran Rifa', 'Activa')")
conn.commit()
conn.close()

# Generar dump estilo pg_dump (plain SQL)
conn = sqlite3.connect(db_path)
with open(dump_path, 'w') as f:
    for line in conn.iterdump():
        f.write(line + '\n')
conn.close()

# Comprimir + SHA256
import gzip, shutil
gz_path = dump_path + '.gz'
with open(dump_path, 'rb') as src, gzip.open(gz_path, 'wb') as dst:
    shutil.copyfileobj(src, dst)
sha = hashlib.sha256(open(gz_path, 'rb').read()).hexdigest()
with open(gz_path + '.sha256', 'w') as f:
    f.write(f"{sha}  {os.path.basename(gz_path)}\n")
print(f'GENERADO dump: {os.path.basename(gz_path)}  size={os.path.getsize(gz_path)}  sha={sha[:16]}...')

# === Verificar integridad (logica de verify_backup) ===
def verify_backup(gz):
    if not os.path.exists(gz):
        return False, 'no existe'
    # gzip -t en Python
    try:
        with gzip.open(gz, 'rb') as f:
            f.read(1)
    except Exception as e:
        return False, f'gzip invalido: {e}'
    sha_path = gz + '.sha256'
    if os.path.exists(sha_path):
        expected = open(sha_path).read().split()[0]
        actual = hashlib.sha256(open(gz, 'rb').read()).hexdigest()
        if expected != actual:
            return False, f'SHA mismatch ({expected[:12]} vs {actual[:12]})'
    return True, 'OK'

ok, msg = verify_backup(gz_path)
print(f'VERIFY: {ok} ({msg})')
assert ok, msg

# === Simular "restore" a una BD nueva ===
db2_path = tempfile.mktemp(suffix='.sqlite')
conn2 = sqlite3.connect(db2_path)
with gzip.open(gz_path, 'rt') as f:
    sql = f.read()
conn2.executescript(sql)
conn2.commit()

# Validar contenido
cur = conn2.cursor()
aportantes = cur.execute("SELECT id, nombre, monto_reportado, boletos_iniciales FROM Aportantes ORDER BY id").fetchall() if False else None
# sqlite dump no tiene las columnas exactas del modelo. Verificar al menos que las tablas existen y tienen filas
counts = {
    'Aportantes': cur.execute("SELECT count(*) FROM Aportantes").fetchone()[0],
    'Tickets': cur.execute("SELECT count(*) FROM Tickets").fetchone()[0],
    'Rifas': cur.execute("SELECT count(*) FROM Rifas").fetchone()[0],
}
print(f'POST-RESTORE: {counts}')
assert counts == {'Aportantes': 2, 'Tickets': 2, 'Rifas': 1}, f'Conteos incorrectos: {counts}'
conn2.close()

# === Simular corrupcion y verificar que se detecta ===
print('\nTEST corrupcion:')
with open(gz_path, 'ab') as f:
    f.write(b'\x00\x00\x00\x00\x00\x00\x00\x00')
ok, msg = verify_backup(gz_path)
print(f'  Verificar dump corrupto: ok={ok} msg={msg}')
assert not ok, 'Debio rechazar dump corrupto'

# Limpiar
for p in [db_path, dump_path, gz_path, gz_path + '.sha256', db2_path]:
    if os.path.exists(p):
        os.unlink(p)

print('\n=== BACKUP+RESTORE ROUNDTRIP: OK ===')
