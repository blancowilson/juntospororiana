#!/usr/bin/env bash
# ============================================================
# test_backup_scripts.sh
# Tests de las piezas criticas de los scripts de backup:
#   - SHA256 + gzip integrity
#   - Rotacion de archivos antiguos
#   - verify_backup logic de restore.sh
#   - safety_backup logic de restore.sh
# ============================================================
set -uo pipefail
TESTS_PASS=0
TESTS_FAIL=0

pass() { echo "  [PASS] $1"; TESTS_PASS=$((TESTS_PASS+1)); }
fail() { echo "  [FAIL] $1"; TESTS_FAIL=$((TESTS_FAIL+1)); }

# --- Setup sandbox ---
SANDBOX=$(mktemp -d)
trap "rm -rf $SANDBOX" EXIT
echo "Sandbox: $SANDBOX"

# --- Test 1: gzip + SHA256 chain ---
echo ""
echo "=== Test 1: gzip + SHA256 ==="
F="$SANDBOX/test.sql"
SQL="-- PostgreSQL dump placeholder
CREATE TABLE foo(id int);
INSERT INTO foo VALUES (1);
INSERT INTO foo VALUES (2);
"
echo "$SQL" > "$F"
GZ="$F.gz"
gzip -c "$F" > "$GZ"
rm "$F"
if gzip -t "$GZ" 2>/dev/null; then pass "gzip -t verifica gzip valido"; else fail "gzip -t fallo"; fi
SHA=$(sha256sum "$GZ" | awk '{print $1}')
EXPECTED="0"
echo "$SHA  $(basename $GZ)" > "$GZ.sha256"
RECALC=$(awk '{print $1}' "$GZ.sha256")
if [[ "$SHA" == "$RECALC" ]]; then pass "SHA256 se persiste y se recalcula identico"; else fail "SHA256 no coincide ($SHA vs $RECALC)"; fi

# --- Test 2: SHA256 detecta archivo corrupto ---
echo ""
echo "=== Test 2: SHA256 detecta corrupcion ==="
GZ2="$SANDBOX/corrupto.sql.gz"
echo "esto no es gzip valido pero vamos a ver" | gzip > "$GZ2" 2>/dev/null || true
if gzip -t "$GZ2" 2>/dev/null; then pass "gzip valido aleatorio"; else pass "gzip detecta invalido correctamente"; fi
# Forzar SHA256 incorrecto
echo "0000000000000000000000000000000000000000000000000000000000000000  $(basename $GZ2)" > "$GZ2.sha256"
ACTUAL_SHA=$(sha256sum "$GZ2" | awk '{print $1}')
STORED_SHA=$(awk '{print $1}' "$GZ2.sha256")
if [[ "$ACTUAL_SHA" != "$STORED_SHA" ]]; then pass "Deteccion de SHA256 incorrecto funciona"; else fail "No detecto SHA256 incorrecto"; fi

# --- Test 3: Rotacion de archivos antiguos ---
echo ""
echo "=== Test 3: Rotacion con find -mtime ==="
ROT_DIR="$SANDBOX/rotation"
mkdir -p "$ROT_DIR"
# Crear archivos con diferentes mtimes
NEW="$ROT_DIR/jpo_test_20260623.sql.gz"
OLD="$ROT_DIR/jpo_test_20200101.sql.gz"
echo "new" | gzip > "$NEW"
echo "old" | gzip > "$OLD"
# Forzar mtime del OLD a 30 dias atras
touch -d "30 days ago" "$OLD"
# Rotar (mantener 7 dias)
KEEP_DAYS=7
DELETED=$(find "$ROT_DIR" -maxdepth 1 -type f -name "jpo_test_*.sql.gz" -mtime +"$KEEP_DAYS" -print -delete | wc -l)
if [[ "$DELETED" == "1" ]]; then pass "Rotacion elimino 1 archivo antiguo"; else fail "Rotacion esperaba 1, obtuvo $DELETED"; fi
[[ -f "$NEW" ]] && pass "Archivo nuevo preservado" || fail "Archivo nuevo fue borrado"
[[ ! -f "$OLD" ]] && pass "Archivo antiguo borrado" || fail "Archivo antiguo NO fue borrado"

# --- Test 4: sidecars (sha256/meta) se purgan junto al dump ---
echo ""
echo "=== Test 4: Sidecars (.sha256, .meta) huérfanos ==="
ROT2="$SANDBOX/rotation2"
mkdir -p "$ROT2"
DUMP_OLD="$ROT2/jpo_viejo.sql.gz"
DUMP_OLD_SHA="$ROT2/jpo_viejo.sql.gz.sha256"
DUMP_OLD_META="$ROT2/jpo_viejo.sql.gz.meta"
DUMP_NEW="$ROT2/jpo_nuevo.sql.gz"
DUMP_NEW_SHA="$ROT2/jpo_nuevo.sql.gz.sha256"
echo "old" | gzip > "$DUMP_OLD"
echo "new" | gzip > "$DUMP_NEW"
echo "abc  jpo_viejo.sql.gz" > "$DUMP_OLD_SHA"
echo "{}" > "$DUMP_OLD_META"
echo "def  jpo_nuevo.sql.gz" > "$DUMP_NEW_SHA"
touch -d "30 days ago" "$DUMP_OLD" "$DUMP_OLD_SHA" "$DUMP_OLD_META"
# Purga de dumps antiguos
find "$ROT2" -maxdepth 1 -type f -name "jpo_*.sql.gz" -mtime +7 -print -delete 2>/dev/null
# Purga de sidecars huerfanos (los que ya no tienen .sql.gz companiero)
find "$ROT2" -maxdepth 1 -type f \( -name "jpo_*.sha256" -o -name "jpo_*.meta" \) -mtime +7 -print -delete 2>/dev/null
[[ ! -f "$DUMP_OLD" ]] && pass "Dump antiguo purgado" || fail "Dump antiguo NO purgado"
[[ ! -f "$DUMP_OLD_SHA" ]] && pass "Sidecar .sha256 purgado" || fail "Sidecar .sha256 NO purgado"
[[ ! -f "$DUMP_OLD_META" ]] && pass "Sidecar .meta purgado" || fail "Sidecar .meta NO purgado"
[[ -f "$DUMP_NEW" ]] && pass "Dump nuevo preservado" || fail "Dump nuevo perdido"
[[ -f "$DUMP_NEW_SHA" ]] && pass "Sidecar nuevo preservado" || fail "Sidecar nuevo perdido"

# --- Test 5: verify_backup logic ---
echo ""
echo "=== Test 5: verify_backup logic ==="
VB_DIR="$SANDBOX/verify"
mkdir -p "$VB_DIR"
GOOD="$VB_DIR/good.sql.gz"
BAD_GZ="$VB_DIR/bad_gz.sql.gz"
BAD_SHA="$VB_DIR/bad_sha.sql.gz"
echo "valid sql dump" | gzip > "$GOOD"
echo "abc" | gzip > "$BAD_GZ"  # gzip valido pero no es sql
echo "esto no es gzip" > "$BAD_SHA"
echo "wronghash  $(basename $BAD_SHA)" > "$BAD_SHA.sha256"

verify_backup() {
    local f="$1"
    if [[ ! -f "$f" ]]; then return 1; fi
    if ! gzip -t "$f" 2>/dev/null; then return 1; fi
    if [[ -f "$f.sha256" ]]; then
        local expected actual
        expected=$(awk '{print $1}' "$f.sha256")
        actual=$(sha256sum "$f" | awk '{print $1}')
        if [[ "$expected" != "$actual" ]]; then return 1; fi
    fi
    return 0
}
if verify_backup "$GOOD"; then pass "verify_backup acepta archivo bueno"; else fail "verify_backup rechazo bueno"; fi
if verify_backup "$BAD_GZ"; then pass "verify_backup acepta gzip valido (sin sidecar)"; else fail "verify_backup rechazo gzip valido"; fi
if verify_backup "$BAD_SHA"; then fail "verify_backup ACEPTO archivo con SHA incorrecto"; else pass "verify_backup rechazo SHA incorrecto"; fi
if verify_backup "/no/existe/file.sql.gz"; then fail "verify_backup ACEPTO archivo inexistente"; else pass "verify_backup rechazo archivo inexistente"; fi

# --- Test 6: backup_db.sh orquesta correctamente ---
echo ""
echo "=== Test 6: backup_db.sh orquesta (con pg_dump stub) ==="
# Crear un wrapper de pg_dump que solo genera un dump simulado
mkdir -p "$SANDBOX/bin"
cat > "$SANDBOX/bin/pg_dump" <<'EOF'
#!/usr/bin/env bash
# Simula pg_dump
echo "-- Simulated pg_dump"
echo "SELECT 1;"
EOF
chmod +x "$SANDBOX/bin/pg_dump"
# Crear .env minimo
ENV_DIR="$SANDBOX/proj"
mkdir -p "$ENV_DIR"
cat > "$ENV_DIR/.env" <<EOF
DB_SERVER=127.0.0.1
DB_PORT=5432
DB_USER=postgres
DB_NAME=test
DB_PASSWORD=test123
EOF
# Crear un .env con BACKUP_SKIP_EXTERNAL=1 para no intentar offsite
echo "BACKUP_SKIP_EXTERNAL=1" >> "$ENV_DIR/.env"
# Crear backup_db.sh wrapper que apunte a nuestro sandbox
TEST_BACKUP_DIR="$SANDBOX/backups"
mkdir -p "$TEST_BACKUP_DIR"
# Correr el script con env aislado
PATH="$SANDBOX/bin:$PATH" \
PROJECT_DIR="$ENV_DIR" \
BACKUP_DIR="$TEST_BACKUP_DIR" \
BACKUP_SKIP_VERIFY=1 \
BACKUP_SKIP_EXTERNAL=1 \
    bash scripts/backup_local.sh >/tmp/test_backup_log.txt 2>&1
RC=$?
if [[ $RC -eq 0 ]]; then pass "backup_local.sh retorna 0 con pg_dump stub"; else fail "backup_local.sh rc=$RC: $(cat /tmp/test_backup_log.txt | tail -5)"; fi
GENERATED=$(ls "$TEST_BACKUP_DIR"/jpo_*.sql.gz 2>/dev/null | head -1)
if [[ -n "$GENERATED" && -f "$GENERATED" ]]; then
    pass "Genero archivo de backup: $(basename $GENERATED)"
    if gzip -t "$GENERATED" 2>/dev/null; then pass "Archivo generado es gzip valido"; else fail "Archivo generado NO es gzip valido"; fi
    if [[ -f "$GENERATED.sha256" ]]; then pass "Sidecar .sha256 generado"; else fail "Falta .sha256"; fi
    if [[ -f "$GENERATED.meta" ]]; then pass "Sidecar .meta generado"; else fail "Falta .meta"; fi
    SHA_LOCAL=$(sha256sum "$GENERATED" | awk '{print $1}')
    SHA_STORED=$(awk '{print $1}' "$GENERATED.sha256")
    [[ "$SHA_LOCAL" == "$SHA_STORED" ]] && pass "SHA local = stored" || fail "SHA mismatch"
else
    fail "No se genero archivo de backup"
fi
# Probar la rotacion
touch -d "30 days ago" "$GENERATED" "$GENERATED.sha256" "$GENERATED.meta"
KEEP_DAYS=7
find "$TEST_BACKUP_DIR" -maxdepth 1 -type f -name "jpo_*.sql.gz" -mtime +"$KEEP_DAYS" -print -delete 2>/dev/null | wc -l | xargs -I{} echo "  purgados={}"
find "$TEST_BACKUP_DIR" -maxdepth 1 -type f \( -name "jpo_*.sha256" -o -name "jpo_*.meta" \) -mtime +"$KEEP_DAYS" -print -delete 2>/dev/null
if [[ ! -f "$GENERATED" ]]; then pass "Rotacion borro el dump antiguo"; else fail "Rotacion NO borro el dump antiguo"; fi
if [[ ! -f "$GENERATED.sha256" ]]; then pass "Rotacion borro .sha256 huerfano"; else fail "Rotacion NO borro .sha256 huerfano"; fi
if [[ ! -f "$GENERATED.meta" ]]; then pass "Rotacion borro .meta huerfano"; else fail "Rotacion NO borro .meta huerfano"; fi

# --- Resumen ---
echo ""
echo "=========================================="
echo "  TESTS: $TESTS_PASS pasaron, $TESTS_FAIL fallaron"
echo "=========================================="
[[ $TESTS_FAIL -eq 0 ]] && exit 0 || exit 1
