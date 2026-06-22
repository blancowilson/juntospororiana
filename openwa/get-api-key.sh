#!/usr/bin/env bash
# =========================================================
# Muestra la API key generada por OpenWA en el primer arranque.
# Copiala al .env del proyecto principal como OPENWA_API_KEY
# =========================================================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

CANDIDATOS=(
    "$SCRIPT_DIR/data/.api-key"
    "$SCRIPT_DIR/src/data/.api-key"
)

API_KEY_FILE=""
for c in "${CANDIDATOS[@]}"; do
    if [ -f "$c" ]; then
        API_KEY_FILE="$c"
        break
    fi
done

if [ -z "$API_KEY_FILE" ]; then
    echo "[ERROR] No se encontro data/.api-key"
    echo "        Arranca el contenedor primero: bash $SCRIPT_DIR/setup.sh"
    exit 1
fi

echo "API key de OpenWA (ubicacion: $API_KEY_FILE)"
echo "Copiala a tu .env como OPENWA_API_KEY:"
echo ""
cat "$API_KEY_FILE"
echo ""
echo ""
echo "Para listar las sesiones: bash $SCRIPT_DIR/init-session.sh"
