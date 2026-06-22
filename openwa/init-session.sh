#!/usr/bin/env bash
# =========================================================
# Crea la sesion "juntospororiana" en OpenWA y la inicia.
# Muestra el QR para escanear con el WhatsApp del negocio.
# =========================================================
set -e

# Este script vive en /openwa, los fuentes de OpenWA en /openwa/src
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPENWA_SRC="$SCRIPT_DIR/src"

if [ ! -d "$OPENWA_SRC" ]; then
    echo "[ERROR] No se encontro $OPENWA_SRC"
    echo "        Ejecuta primero: bash $SCRIPT_DIR/setup.sh"
    exit 1
fi

# Buscar el .api-key generado por OpenWA (puede estar en /openwa/data o en /openwa/src/data)
API_KEY=""
for CANDIDATE in "$SCRIPT_DIR/data/.api-key" "$OPENWA_SRC/data/.api-key"; do
    if [ -f "$CANDIDATE" ]; then
        API_KEY=$(cat "$CANDIDATE")
        break
    fi
done

if [ -z "$API_KEY" ]; then
    echo "[ERROR] No se encontro el archivo .api-key de OpenWA."
    echo "        Asegurate de que el contenedor este corriendo:"
    echo "          cd $OPENWA_SRC && docker compose ps"
    echo "        Y que ya haya pasado el primer arranque (espera 30-60s)."
    exit 1
fi

BASE=http://127.0.0.1:2785/api

echo "== Listando sesiones existentes =="
SESSIONS=$(curl -s "$BASE/sessions" -H "X-API-Key: $API_KEY" 2>/dev/null || echo "[]")
echo "$SESSIONS" | python3 -m json.tool 2>/dev/null || echo "$SESSIONS"

# Tomar la primera sesion (si ya existe) o crear una nueva
SESSION_ID=$(echo "$SESSIONS" | python3 -c "import sys,json;d=json.load(sys.stdin);print(d[0]['id'] if d else '')" 2>/dev/null || echo "")

if [ -z "$SESSION_ID" ]; then
    echo ""
    echo "== Creando sesion 'juntospororiana' =="
    CREATE=$(curl -s -X POST "$BASE/sessions" \
        -H "X-API-Key: $API_KEY" \
        -H "Content-Type: application/json" \
        -d '{"name":"juntospororiana"}')
    echo "$CREATE" | python3 -m json.tool 2>/dev/null || echo "$CREATE"
    SESSION_ID=$(echo "$CREATE" | python3 -c "import sys,json;print(json.load(sys.stdin).get('id',''))" 2>/dev/null || echo "")
fi

if [ -z "$SESSION_ID" ]; then
    echo "[ERROR] No se pudo obtener el ID de la sesion"
    exit 1
fi

echo ""
echo "== Iniciando sesion $SESSION_ID =="
curl -s -X POST "$BASE/sessions/$SESSION_ID/start" \
    -H "X-API-Key: $API_KEY" | python3 -m json.tool 2>/dev/null || true

echo ""
echo "== Esperando QR (10 segundos) =="
sleep 10

echo ""
echo "== Obteniendo QR =="
QR=$(curl -s "$BASE/sessions/$SESSION_ID/qr" -H "X-API-Key: $API_KEY")
echo "$QR" | python3 -m json.tool 2>/dev/null || echo "$QR"

echo ""
echo "Para ver el QR como imagen abre en el navegador:"
echo "  http://127.0.0.1:2785/  (dashboard)"
echo "O decodifica el campo 'image' (base64) con un visor online."
echo ""
echo "Una vez vinculado, copia este SESSION_ID al .env del proyecto principal:"
echo "  OPENWA_SESSION_ID=$SESSION_ID"
echo
echo "Tambien necesitas la API key (esta en $(ls -1 "$SCRIPT_DIR/data/.api-key" 2>/dev/null || ls -1 "$OPENWA_SRC/data/.api-key" 2>/dev/null))"
