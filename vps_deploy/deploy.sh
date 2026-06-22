#!/usr/bin/env bash
# =========================================================
# Script de instalacion para Juntos por Oriana
# Ubuntu Server 20.04 / 22.04 / 24.04
# Ejecutar como root o con sudo
# =========================================================

set -e

APP_USER="jpoadmin"
APP_DIR="/var/www/juntospororiana"
REPO_URL="https://github.com/blancowilson/juntospororiana.git"

echo "=== 1. Actualizando sistema ==="
apt-get update && apt-get upgrade -y

echo "=== 2. Instalando dependencias base ==="
apt-get install -y python3 python3-pip python3-venv nginx curl git \
                   build-essential libssl-dev libffi-dev python3-dev \
                   ufw fail2ban certbot python3-certbot-nginx \
                   ca-certificates gnupg

echo "=== 2.1. Instalando Docker (necesario para OpenWA) ==="
if ! command -v docker &> /dev/null; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg
    chmod a+r /etc/apt/keyrings/docker.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update
    apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
    systemctl enable --now docker
fi
# Agregar al usuario de la app al grupo docker para usar docker sin sudo
usermod -aG docker ${APP_USER} 2>/dev/null || true

echo "=== 3. Instalando PostgreSQL ==="
apt-get install -y postgresql postgresql-contrib
systemctl enable --now postgresql

echo "=== 4. Creando usuario de sistema ${APP_USER} ==="
if ! id "${APP_USER}" &>/dev/null; then
    adduser --disabled-password --gecos "" ${APP_USER}
    usermod -aG sudo ${APP_USER}
fi

echo "=== 5. Configurando firewall UFW ==="
ufw allow OpenSSH
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable

echo "=== 6. Creando estructura de la aplicacion ==="
mkdir -p "${APP_DIR}"
mkdir -p /var/log/juntospororiana
chown -R ${APP_USER}:${APP_USER} "${APP_DIR}"
chown -R ${APP_USER}:${APP_USER} /var/log/juntospororiana

echo ""
echo "================================================================"
echo "  PASOS MANUALES RESTANTES"
echo "================================================================"
echo ""
echo "A. Crear la base de datos PostgreSQL (ejecutar como postgres):"
echo "   sudo -u postgres psql"
echo "   CREATE USER jpo WITH PASSWORD 'TU_PASSWORD_SEGURA';"
echo "   CREATE DATABASE \"JuntosPorOriana\" OWNER jpo;"
echo "   GRANT ALL PRIVILEGES ON DATABASE \"JuntosPorOriana\" TO jpo;"
echo "   \\q"
echo ""
echo "B. Clonar el repositorio y preparar el entorno:"
echo "   sudo -u ${APP_USER} bash -c 'cd ${APP_DIR} && \\"
echo "       git clone ${REPO_URL} . && \\"
echo "       # Si es instalacion nueva, copiar .env.example a .env:"
echo "       [ -f .env ] || cp .env.example .env && \\"
echo "       # Si .env ya existe, sincronizar SOLO variables nuevas (preserva tus credenciales):"
echo "       python scripts/sync_env.py && \\"
echo "       nano .env   # editar credenciales reales'"
echo ""
echo "C. Crear venv e instalar dependencias:"
echo "   sudo -u ${APP_USER} bash -c 'cd ${APP_DIR} && \\"
echo "       python3 -m venv .venv && \\"
echo "       source .venv/bin/activate && \\"
echo "       pip install --upgrade pip && \\"
echo "       pip install -r requirements.txt'"
echo ""
echo "D. Sembrar la base de datos (primera vez):"
echo "   sudo -u ${APP_USER} bash -c 'cd ${APP_DIR} && \\"
echo "       source .venv/bin/activate && \\"
echo "       python seed.py'"
echo ""
echo "E. Instalar el servicio systemd y Nginx:"
echo "   sudo cp ${APP_DIR}/vps_deploy/juntospororiana.service /etc/systemd/system/"
echo "   sudo systemctl daemon-reload"
echo "   sudo systemctl enable --now juntospororiana"
echo "   sudo cp ${APP_DIR}/vps_deploy/nginx.conf /etc/nginx/sites-available/juntospororiana"
echo "   sudo ln -sf /etc/nginx/sites-available/juntospororiana /etc/nginx/sites-enabled/"
echo "   sudo rm -f /etc/nginx/sites-enabled/default"
echo "   sudo nginx -t && sudo systemctl reload nginx"
echo ""
echo "E.1 Instalar OpenWA (WhatsApp gateway):"
echo "   sudo cp ${APP_DIR}/openwa/.env.example ${APP_DIR}/openwa/.env"
echo "   sudo -u ${APP_USER} bash -c 'cd ${APP_DIR}/openwa && nano .env   # ajustar valores'"
echo "   sudo cp ${APP_DIR}/vps_deploy/openwa.service /etc/systemd/system/"
echo "   sudo systemctl daemon-reload"
echo "   sudo systemctl enable --now openwa"
echo "   # Esperar a que arranque y luego obtener la API key:"
echo "   sleep 20 && sudo cat ${APP_DIR}/openwa/data/.api-key"
echo "   # Copiar esa API key al .env del proyecto FastAPI como OPENWA_API_KEY"
echo "   # Reiniciar FastAPI para que tome la nueva config:"
echo "   sudo systemctl restart juntospororiana"
echo ""
echo "E.2 Crear la sesion de WhatsApp y vincular el numero:"
echo "   sudo -u ${APP_USER} bash ${APP_DIR}/openwa/init-session.sh"
echo "   # Escanear el QR con el WhatsApp del negocio"
echo "   # El SESSION_ID se imprime al final del script, agregalo al .env"
echo ""
echo "F. Activar HTTPS con Let's Encrypt:"
echo "   sudo certbot --nginx -d juntospororiana.online -d www.juntospororiana.online"
echo ""
echo "G. Asegurate de que el DNS A en Namecheap apunte a 144.126.149.59"
echo "================================================================"
