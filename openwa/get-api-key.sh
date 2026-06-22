#!/usr/bin/env bash
# =========================================================
# Muestra la API key generada por OpenWA en el primer arranque.
# Copiala al archivo .env del proyecto FastAPI como OPENWA_API_KEY
# =========================================================
set -e
cd "$(dirname "$0")"

if [ ! -f data/.api-key ]; then
  echo "[ERROR] No se encontro data/.api-key"
  echo "        Arranca el contenedor primero: docker compose up -d"
  exit 1
fi

echo "API key de OpenWA (copiala a tu .env como OPENWA_API_KEY):"
echo ""
cat data/.api-key
echo ""
echo ""
echo "Tambien puedes obtener la sessionId con:"
echo "  curl -s http://127.0.0.1:2785/api/sessions -H \"X-API-Key: \$(cat data/.api-key)\""
