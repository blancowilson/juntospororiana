#!/usr/bin/env bash
# =========================================================
# Aplica el pin de WWEBJS_WEB_VERSION al contenedor OpenWA.
# =========================================================
# Soluciona el bug conocido de whatsapp-web.js 1.34.x donde la
# sesion se queda en "authenticating" indefinidamente tras escanear
# el QR (la version auto-seleccionada de WA Web es incompatible).
# Referencia: openwa/src/docs/12-troubleshooting-faq.md, seccion
# "Session stuck at authenticating, never reaches ready".
#
# El pin debe ir en openwa/src/.env (es donde docker compose lee
# las vars de entorno del stack). Si esa carpeta no existe, corre
# primero:  bash openwa/setup.sh
#
# Uso:
#   bash scripts/fix_openwa_version.sh            # solo escribe el .env
#   bash scripts/fix_openwa_version.sh --restart  # ademas recrea el contenedor
# =========================================================
set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
OPENWA_SRC="$PROJECT_ROOT/openwa/src"
ENV_FILE="$OPENWA_SRC/.env"

# Version de WA Web probada y funcional con wwebjs 1.34.7
# (la 2.3000.1023204257 de los docs viejos devuelve 404 en el repo actual)
WA_VERSION="2.3000.1038507070-alpha"
WA_AUTH_TIMEOUT_MS="90000"

echo "=========================================="
echo "  Fix: pin WWEBJS_WEB_VERSION para OpenWA"
echo "=========================================="
echo

if [ ! -d "$OPENWA_SRC" ]; then
    echo "[ERROR] No se encontro $OPENWA_SRC"
    echo "        El codigo de OpenWA no esta clonado. Ejecuta primero:"
    echo "          bash openwa/setup.sh"
    exit 1
fi

# Crear el archivo .env si no existe
mkdir -p "$OPENWA_SRC"
[ -f "$ENV_FILE" ] || : > "$ENV_FILE"

# set_env <KEY> <VALUE> <FILE>
# Crea o actualiza una linea KEY=VALUE preservando el resto del archivo.
set_env() {
    local key="$1"
    local value="$2"
    local file="$3"
    if grep -qE "^${key}=" "$file"; then
        # Update in-place via sed (BSD/GNU compatible)
        local tmp
        tmp=$(mktemp)
        sed "s|^${key}=.*|${key}=${value}|" "$file" > "$tmp"
        mv "$tmp" "$file"
        echo "  [updated] $key=$value"
    else
        # Append al final
        printf '%s=%s\n' "$key" "$value" >> "$file"
        echo "  [added]   $key=$value"
    fi
}

echo ">> Aplicando pin en $ENV_FILE"
set_env "WWEBJS_WEB_VERSION"     "$WA_VERSION"        "$ENV_FILE"
set_env "WWEBJS_AUTH_TIMEOUT_MS" "$WA_AUTH_TIMEOUT_MS" "$ENV_FILE"

echo
echo ">> Contenido final relevante:"
grep -E '^WWEBJS_' "$ENV_FILE" || echo "  (vacio)"

if [ "${1:-}" = "--restart" ]; then
    echo
    echo ">> Recreando contenedor openwa-api..."
    cd "$OPENWA_SRC"
    sudo docker compose up -d openwa-api
    echo
    sleep 5
    echo ">> Env efectiva dentro del contenedor:"
    sudo docker exec openwa-api env | grep -E '^WWEBJS_WEB_VERSION='
fi

echo
echo "[OK] Pin aplicado."
if [ "${1:-}" != "--restart" ]; then
    echo "     Para que tome efecto, recrea el contenedor:"
    echo "       cd $OPENWA_SRC && sudo docker compose up -d openwa-api"
fi
echo
echo "Nota: la OPENWA_SESSION_ID del .env del proyecto FastAPI NO se"
echo "      toca aca (es runtime: cambia cada vez que se recrea la sesion)."
