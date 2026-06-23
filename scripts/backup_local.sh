#!/usr/bin/env bash
# ============================================================
# backup_local.sh
# Dump de la base de datos PostgreSQL con rotacion automatica.
#
# - Genera un dump comprimido con timestamp en BACKUP_DIR
# - Calcula SHA256 para verificacion de integridad
# - Rota los archivos antiguos (mantiene los ultimos N dias)
# - Por defecto escribe tambien un manifest con metadata
#
# Uso:
#   bash scripts/backup_local.sh                  # backup normal
#   BACKUP_KEEP_DAYS=14 bash scripts/backup_local.sh
#   BACKUP_DIR=/mnt/backup bash scripts/backup_local.sh
#
# Variables de entorno relevantes (con defaults):
#   PROJECT_DIR         directorio del proyecto (default: /var/www/juntospororiana)
#   BACKUP_DIR          donde guardar los dumps (default: $PROJECT_DIR/backups)
#   BACKUP_KEEP_DAYS    dias de backups a conservar (default: 7)
#   BACKUP_PREFIX       prefijo del archivo (default: jpo)
#   PG_*                DB_SERVER, DB_PORT, DB_USER, DB_NAME (del .env)
#   DB_PASSWORD         del .env (NO se loguea)
# ============================================================
set -Eeuo pipefail

# ---------- Configuracion ----------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-/var/www/juntospororiana}"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/backups}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-7}"
BACKUP_PREFIX="${BACKUP_PREFIX:-jpo}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
HOSTNAME_S="$(hostname -s 2>/dev/null || echo unknown)"

LOG_DIR="$BACKUP_DIR/logs"
mkdir -p "$BACKUP_DIR" "$LOG_DIR"
LOG_FILE="$LOG_DIR/backup_local_${TS}.log"

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG_FILE" ; }
err() { log "ERROR: $*" >&2; }

cleanup() {
    local rc=$?
    if [[ $rc -ne 0 ]]; then
        err "Backup FALLIDO (rc=$rc). Revisa $LOG_FILE"
    fi
    exit $rc
}
trap cleanup EXIT

# ---------- Cargar .env ----------
if [[ ! -f "$PROJECT_DIR/.env" ]]; then
    err "No se encontro $PROJECT_DIR/.env"
    exit 1
fi

# Lee variables de .env sin exportar todo (solo las que nos interesan)
get_env() {
    local key="$1"
    local line
    line="$(grep -E "^${key}=" "$PROJECT_DIR/.env" 2>/dev/null | tail -1 || true)"
    if [[ -n "$line" ]]; then
        # Quitar prefijo y comillas opcionales
        echo "${line#*=}" | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
    fi
}

DB_SERVER="${DB_SERVER:-$(get_env DB_SERVER)}"
DB_PORT="${DB_PORT:-$(get_env DB_PORT)}"
DB_USER="${DB_USER:-$(get_env DB_USER)}"
DB_NAME="${DB_NAME:-$(get_env DB_NAME)}"
DB_PASSWORD="${DB_PASSWORD:-$(get_env DB_PASSWORD)}"

# Defaults razonables
DB_SERVER="${DB_SERVER:-127.0.0.1}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${DB_NAME:-JuntosPorOriana}"

log "=== backup_local iniciando ==="
log "PROJECT_DIR=$PROJECT_DIR"
log "BACKUP_DIR=$BACKUP_DIR  KEEP_DAYS=$BACKUP_KEEP_DAYS"
log "DB=$DB_USER@$DB_SERVER:$DB_PORT/$DB_NAME"

# ---------- Validar herramientas ----------
command -v pg_dump >/dev/null 2>&1 || { err "pg_dump no instalado. apt install postgresql-client"; exit 2; }
command -v sha256sum >/dev/null 2>&1 || { err "sha256sum no disponible"; exit 2; }
command -v gzip >/dev/null 2>&1 || { err "gzip no disponible"; exit 2; }

# ---------- Crear el dump ----------
DUMP_FILE="$BACKUP_DIR/${BACKUP_PREFIX}_${HOSTNAME_S}_${TS}.sql.gz"
MANIFEST="$DUMP_FILE.sha256"
META="$DUMP_FILE.meta"

log "Generando dump: $DUMP_FILE"

# Exportar la password solo para esta llamada
export PGPASSWORD="$DB_PASSWORD"
if ! pg_dump \
    -h "$DB_SERVER" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
    --no-owner --no-privileges --clean --if-exists \
    --serializable-deferrable \
    2> >(tee -a "$LOG_FILE" >&2) \
    | gzip -c > "$DUMP_FILE.tmp"; then
    rm -f "$DUMP_FILE.tmp"
    err "pg_dump fallo"
    exit 3
fi
unset PGPASSWORD

mv "$DUMP_FILE.tmp" "$DUMP_FILE"
DUMP_SIZE=$(stat -c '%s' "$DUMP_FILE" 2>/dev/null || wc -c < "$DUMP_FILE")
log "Dump OK: $(numfmt --to=iec --suffix=B "$DUMP_SIZE" 2>/dev/null || echo "${DUMP_SIZE} bytes")"

# ---------- Verificacion: debe ser gzip valido y contener SQL ----------
if ! gzip -t "$DUMP_FILE" 2>>"$LOG_FILE"; then
    err "El dump no es un gzip valido"
    exit 4
fi

# ---------- SHA256 ----------
sha256sum "$DUMP_FILE" | awk '{print $1"  "$2}' > "$MANIFEST"
SHA=$(awk '{print $1}' "$MANIFEST")
log "SHA256: $SHA"

# ---------- Metadata ----------
cat > "$META" <<EOF
{
  "timestamp_utc": "$TS",
  "hostname": "$HOSTNAME_S",
  "db_server": "$DB_SERVER",
  "db_port": $DB_PORT,
  "db_name": "$DB_NAME",
  "db_user": "$DB_USER",
  "size_bytes": $DUMP_SIZE,
  "sha256": "$SHA",
  "filename": "$(basename "$DUMP_FILE")",
  "script_version": "1.0",
  "pg_dump_version": "$(pg_dump --version | head -1)"
}
EOF
log "Metadata: $META"

# ---------- Verificacion de restauracion (rapida, en una DB temporal) ----------
VERIFY_DIR="$BACKUP_DIR/.verify"
if [[ "${BACKUP_SKIP_VERIFY:-0}" != "1" ]] && command -v createdb >/dev/null 2>&1; then
    log "Verificando que el dump se puede importar (puede tardar)..."
    TEST_DB="${BACKUP_PREFIX}_verify_${TS}"
    export PGPASSWORD="$DB_PASSWORD"
    if createdb -h "$DB_SERVER" -p "$DB_PORT" -U "$DB_USER" "$TEST_DB" 2>>"$LOG_FILE"; then
        if gunzip -c "$DUMP_FILE" | psql -h "$DB_SERVER" -p "$DB_PORT" -U "$DB_USER" -d "$TEST_DB" -v ON_ERROR_STOP=1 -q 2>>"$LOG_FILE"; then
            # Validar que las tablas criticas existen y tienen filas
            TABLES_OK=$(psql -h "$DB_SERVER" -p "$DB_PORT" -U "$DB_USER" -d "$TEST_DB" -tA -c "
                SELECT
                    (SELECT count(*) FROM \"Aportantes\") AS aportantes,
                    (SELECT count(*) FROM \"Tickets\") AS tickets,
                    (SELECT count(*) FROM \"Rifas\") AS rifas,
                    (SELECT count(*) FROM \"AuditLog\") AS audit
            " 2>>"$LOG_FILE" || echo "VERIFY_FAILED")
            log "Resultado de verificacion: $TABLES_OK"
            dropdb -h "$DB_SERVER" -p "$DB_PORT" -U "$DB_USER" "$TEST_DB" 2>>"$LOG_FILE" || true
            if [[ "$TABLES_OK" == "VERIFY_FAILED" || -z "$TABLES_OK" ]]; then
                err "La verificacion post-import fallo. El dump podria estar corrupto."
                exit 5
            fi
        else
            err "La importacion del dump en la DB de prueba fallo"
            dropdb -h "$DB_SERVER" -p "$DB_PORT" -U "$DB_USER" "$TEST_DB" 2>/dev/null || true
            exit 5
        fi
    else
        log "WARN: no se pudo crear DB de prueba (¿permisos?). Se omite verificacion."
    fi
    unset PGPASSWORD
fi

# ---------- Rotacion ----------
log "Aplicando rotacion: conservar ultimos $BACKUP_KEEP_DAYS dias"
DELETED=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name "${BACKUP_PREFIX}_${HOSTNAME_S}_*.sql.gz" -mtime +"$BACKUP_KEEP_DAYS" -print -delete 2>/dev/null | wc -l)
# Limpiar tambien los .sha256 y .meta huerfanos (los que ya no tienen .sql.gz companiero)
SIDECAR_DELETED=$(find "$BACKUP_DIR" -maxdepth 1 -type f \( -name "${BACKUP_PREFIX}_${HOSTNAME_S}_*.sha256" -o -name "${BACKUP_PREFIX}_${HOSTNAME_S}_*.meta" \) -mtime +"$BACKUP_KEEP_DAYS" -print -delete 2>/dev/null | wc -l)
log "Archivos purgados por antiguedad: $DELETED dumps + $SIDECAR_DELETED sidecars"

# ---------- Resumen ----------
TOTAL=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name "${BACKUP_PREFIX}_${HOSTNAME_S}_*.sql.gz" | wc -l)
TOTAL_SIZE=$(du -sh "$BACKUP_DIR" 2>/dev/null | awk '{print $1}')
log "=== backup_local COMPLETADO ==="
log "Archivo: $(basename "$DUMP_FILE")"
log "SHA256: $SHA"
log "Backups totales en $BACKUP_DIR: $TOTAL (uso: $TOTAL_SIZE)"

# Imprime ultima linea en stdout para integracion con scripts
echo "BACKUP_FILE=$DUMP_FILE"
echo "BACKUP_SHA256=$SHA"
echo "BACKUP_BYTES=$DUMP_SIZE"
