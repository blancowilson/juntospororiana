#!/usr/bin/env bash
# ============================================================
# backup_db.sh
# Orquestador: ejecuta backup_local y, si tuvo exito, backup_external.
#
# Uso:
#   bash scripts/backup_db.sh
#   BACKUP_SKIP_EXTERNAL=1 bash scripts/backup_db.sh  # solo local
#
# Variables (todas opcionales, toman defaults razonables):
#   PROJECT_DIR, BACKUP_DIR, BACKUP_KEEP_DAYS
#   BACKUP_SKIP_EXTERNAL=1  -> no sincroniza al remoto
#   BACKUP_SKIP_VERIFY=1     -> no verifica el dump en una DB temporal
# ============================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-/var/www/juntospororiana}"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/backups}"

LOG_DIR="$BACKUP_DIR/logs"
mkdir -p "$LOG_DIR"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
LOG_FILE="$LOG_DIR/backup_orchestrator_${TS}.log"

log() { printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" | tee -a "$LOG_FILE" ; }
err() { log "ERROR: $*" >&2; }

log "=== backup_db orquestador iniciando ==="

# ---------- 1. Backup local ----------
log "[1/2] Ejecutando backup_local.sh..."
set +e
LOCAL_OUTPUT=$(bash "$SCRIPT_DIR/backup_local.sh" 2>&1 | tee -a "$LOG_FILE")
LOCAL_RC=$?
set -e
log "backup_local rc=$LOCAL_RC"
if [[ $LOCAL_RC -ne 0 ]]; then
    err "backup_local fallo, NO se ejecuta backup externo"
    exit 1
fi

# Extraer el archivo generado del output
BACKUP_FILE=$(echo "$LOCAL_OUTPUT" | grep -E '^BACKUP_FILE=' | tail -1 | cut -d= -f2-)
BACKUP_SHA=$(echo "$LOCAL_OUTPUT" | grep -E '^BACKUP_SHA256=' | tail -1 | cut -d= -f2-)
log "Backup local exitoso: $BACKUP_FILE  sha=$BACKUP_SHA"

# ---------- 2. Backup externo (offsite) ----------
if [[ "${BACKUP_SKIP_EXTERNAL:-0}" == "1" ]]; then
    log "[2/2] backup externo omitido por BACKUP_SKIP_EXTERNAL=1"
else
    log "[2/2] Ejecutando backup_external.sh..."
    set +e
    EXTERNAL_OUTPUT=$(bash "$SCRIPT_DIR/backup_external.sh" 2>&1 | tee -a "$LOG_FILE")
    EXTERNAL_RC=$?
    set -e
    log "backup_external rc=$EXTERNAL_RC"
    if [[ $EXTERNAL_RC -ne 0 ]]; then
        err "backup_external fallo (rc=$EXTERNAL_RC). El backup local esta OK pero no se copio al remoto."
        # No fallamos el orquestador: el local esta hecho.
    else
        log "Backup externo exitoso."
    fi
fi

log "=== backup_db COMPLETADO ==="
