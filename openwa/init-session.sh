# =========================================================
# Crea la sesion "juntospororiana" en OpenWA y la inicia.
# Muestra el QR para escanear con el WhatsApp del negocio.
# =========================================================
set -e
cd "$(dirname "$0")"

if [ ! -f data/.api-key ]; then
  echo "[ERROR] No se encontro data/.api-key. Arranca el contenedor primero."
  exit 1
fi

API_KEY=$(cat data/.api-key)
BASE=http://127.0.0.1:2785/api

echo "== Listando sesiones existentes =="
SESSIONS=$(curl -s "$BASE/sessions" -H "X-API-Key: $API_KEY")
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
  SESSION_ID=$(echo "$CREATE" | python3 -c "import sys,json;print(json.load(sys.stdin).get('id',''))")
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
echo "== Obteniendo QR =="
QR=$(curl -s "$BASE/sessions/$SESSION_ID/qr" -H "X-API-Key: $API_KEY")
echo "$QR" | python3 -m json.tool 2>/dev/null || echo "$QR"

echo ""
echo "Para ver el QR como imagen abre en el navegador:"
echo "  http://127.0.0.1:2785/  (dashboard)"
echo "O guarda la imagen base64 del campo 'image' de la respuesta anterior."
echo ""
echo "SESSION_ID=$SESSION_ID"
echo ""
echo "Copia este SESSION_ID a tu .env como OPENWA_SESSION_ID"
