#!/usr/bin/env bash
# ============================================================
# restore.sh
# Restaura un backup local de la base de datos PostgreSQL.
#
# ⚠️  OPERACION DESTRUCTIVA: sobreescribe la DB actual.
#     Se hace SIEMPRE un backup de seguridad antes de tocar nada.
#
# Uso:
#   bash scripts/restore.sh                              # menu interactivo
#   bash scripts/restore.sh backups/jpo_host_20260623.sql.gz
#   bash scripts/restore.sh --list                      # lista backups disponibles
#   bash scripts/restore.sh --latest                    # restaura el mas reciente
#
# Variables de entorno (opcionales, toman defaults):
#   PROJECT_DIR, BACKUP_DIR
# ============================================================
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="${PROJECT_DIR:-/var/www/juntospororiana}"
BACKUP_DIR="${BACKUP_DIR:-$PROJECT_DIR/backups}"

# Cargar .env
if [[ ! -f "$PROJECT_DIR/.env" ]]; then
    echo "ERROR: no se encontro $PROJECT_DIR/.env" >&2
    exit 1
fi
get_env() {
    local key="$1"
    local line
    line="$(grep -E "^${key}=" "$PROJECT_DIR/.env" 2>/dev/null | tail -1 || true)"
    if [[ -n "$line" ]]; then
        echo "${line#*=}" | sed -e 's/^"//' -e 's/"$//' -e "s/^'//" -e "s/'$//"
    fi
}
DB_SERVER="${DB_SERVER:-$(get_env DB_SERVER)}"
DB_PORT="${DB_PORT:-$(get_env DB_PORT)}"
DB_USER="${DB_USER:-$(get_env DB_USER)}"
DB_NAME="${DB_NAME:-$(get_env DB_NAME)}"
DB_PASSWORD="${DB_PASSWORD:-$(get_env DB_PASSWORD)}"
DB_SERVER="${DB_SERVER:-127.0.0.1}"
DB_PORT="${DB_PORT:-5432}"
DB_USER="${DB_USER:-postgres}"
DB_NAME="${DB_NAME:-JuntosPorOriana}"

command -v psql >/dev/null 2>&1 || { echo "ERROR: psql no instalado"; exit 2; }
command -v gunzip >/dev/null 2>&1 || { echo "ERROR: gunzip no instalado"; exit 2; }

# ---------- Listar backups ----------
list_backups() {
    echo "Backups disponibles en $BACKUP_DIR:"
    local i=1
    local files=()
    while IFS= read -r f; do
        files+=( "$f" )
    done < <(find "$BACKUP_DIR" -maxdepth 1 -type f -name "*.sql.gz" -printf '%T@ %p\n' | sort -rn | awk '{print $2}')
    if [[ ${#files[@]} -eq 0 ]]; then
        echo "  (ninguno)"
        return 1
    fi
    for f in "${files[@]}"; do
        local size
        size=$(du -h "$f" | awk '{print $1}')
        local mtime
        mtime=$(stat -c '%y' "$f" 2>/dev/null | cut -d. -f1)
        local sha=""
        if [[ -f "$f.sha256" ]]; then
            sha=$(awk '{print $1}' "$f.sha256" | cut -c1-12)
        fi
        echo "  [$i] $(basename "$f")  size=$size  mtime=$mtime  sha=${sha:-N/A}"
        i=$((i+1))
    done
    return 0
}

# ---------- Validar integridad ----------
verify_backup() {
    local f="$1"
    if [[ ! -f "$f" ]]; then
        echo "ERROR: archivo no existe: $f" >&2
        return 1
    fi
    if ! gzip -t "$f" 2>/dev/null; then
        echo "ERROR: el archivo no es gzip valido" >&2
        return 1
    fi
    if [[ -f "$f.sha256" ]]; then
        local expected
        expected=$(awk '{print $1}' "$f.sha256")
        local actual
        actual=$(sha256sum "$f" | awk '{print $1}')
        if [[ "$expected" != "$actual" ]]; then
            echo "ERROR: SHA256 no coincide (esperado=$expected actual=$actual)" >&2
            return 1
        fi
        echo "SHA256 OK: $actual"
    else
        echo "WARN: no hay sidecar .sha256, omito verificacion"
    fi
    return 0
}

# ---------- Hacer backup de seguridad pre-restore ----------
safety_backup() {
    local ts
    ts=$(date -u +%Y%m%dT%H%M%SZ)
    local target="$BACKUP_DIR/PRE_RESTORE_${ts}.sql.gz"
    echo "Generando backup de seguridad pre-restore: $target"
    export PGPASSWORD="$DB_PASSWORD"
    if ! pg_dump \
        -h "$DB_SERVER" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" \
        --no-owner --no-privileges --clean --if-exists \
        2>/dev/null | gzip -c > "$target"; then
        echo "ERROR: no se pudo generar el backup de seguridad" >&2
        return 1
    fi
    unset PGPASSWORD
    sha256sum "$target" | awk '{print $1"  "$2}' > "$target.sha256"
    echo "Backup de seguridad OK: $target"
    return 0
}

# ---------- Restaurar ----------
do_restore() {
    local f="$1"
    echo "=========================================="
    echo "  RESTAURACION DE BASE DE DATOS"
    echo "=========================================="
    echo "  Archivo : $f"
    echo "  Destino : $DB_USER@$DB_SERVER:$DB_PORT/$DB_NAME"
    echo "=========================================="
    echo ""
    echo "⚠️  Esta operacion SOBREESCRIBE la base de datos actual."
    echo "   Se generara un backup de seguridad automaticamente antes."
    echo ""
    read -r -p "Escribe 'RESTAURAR' (en mayusculas) para confirmar: " confirm
    if [[ "$confirm" != "RESTAURAR" ]]; then
        echo "Cancelado por el usuario."
        exit 0
    fi
    safety_backup || { echo "Abortado por fallo en safety_backup"; exit 1; }

    # Drop & recreate
    export PGPASSWORD="$DB_PASSWORD"
    echo "Cerrando conexiones activas a $DB_NAME..."
    psql -h "$DB_SERVER" -p "$DB_PORT" -U "$DB_USER" -d postgres -c "
        SELECT pg_terminate_backend(pid) FROM pg_stat_activity
        WHERE datname = '$DB_NAME' AND pid <> pg_backend_pid();" 2>/dev/null || true

    echo "Eliminando DB $DB_NAME..."
    dropdb -h "$DB_SERVER" -p "$DB_PORT" -U "$DB_USER" --if-exists "$DB_NAME" 2>>/dev/null || true
    echo "Creando DB $DB_NAME..."
    createdb -h "$DB_SERVER" -p "$DB_PORT" -U "$DB_USER" "$DB_NAME" 2>>/dev/null || { echo "ERROR: createdb fallo"; exit 1; }

    echo "Importando dump..."
    if gunzip -c "$f" | psql -h "$DB_SERVER" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -v ON_ERROR_STOP=1 -q 2>&1 | tail -20; then
        echo "RESTAURACION COMPLETADA"
        echo "Tablas y conteo actual:"
        psql -h "$DB_SERVER" -p "$DB_PORT" -U "$DB_USER" -d "$DB_NAME" -tA -c "
            SELECT 'Aportantes: '||count(*) FROM \"Aportantes\"
            UNION ALL SELECT 'Tickets: '||count(*) FROM \"Tickets\"
            UNION ALL SELECT 'Rifas: '||count(*) FROM \"Rifas\"
            UNION ALL SELECT 'AuditLog: '||count(*) FROM \"AuditLog\"
        " 2>/dev/null
    else
        echo "ERROR: la importacion tuvo errores. Revisa arriba."
        exit 1
    fi
    unset PGPASSWORD
}

# ---------- Modo interactivo o argumentos ----------
case "${1:-}" in
    --list)
        list_backups
        ;;
    --latest)
        latest=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name "*.sql.gz" -printf '%T@ %p\n' | sort -rn | head -1 | awk '{print $2}')
        if [[ -z "$latest" ]]; then
            echo "No hay backups disponibles" >&2
            exit 1
        fi
        echo "Restaurando el backup mas reciente: $latest"
        verify_backup "$latest" || exit 1
        do_restore "$latest"
        ;;
    "")
        list_backups || exit 1
        echo ""
        read -r -p "Numero de backup a restaurar (o 'q' para salir): " choice
        if [[ "$choice" == "q" || -z "$choice" ]]; then
            exit 0
        fi
        # Buscar el archivo por numero
        target=$(find "$BACKUP_DIR" -maxdepth 1 -type f -name "*.sql.gz" -printf '%T@ %p\n' | sort -rn | awk -v n="$choice" 'NR==n{print $2}')
        if [[ -z "$target" ]]; then
            echo "Numero invalido" >&2
            exit 1
        fi
        verify_backup "$target" || exit 1
        do_restore "$target"
        ;;
    -*)
        echo "Uso: $0 [--list|--latest|archivo.sql.gz]" >&2
        exit 2
        ;;
    *)
        verify_backup "$1" || exit 1
        do_restore "$1"
        ;;
esac
