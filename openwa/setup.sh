#!/usr/bin/env bash
# =========================================================
# Setup del servidor OpenWA para Juntos por Oriana
# =========================================================
# Clona el repo oficial de OpenWA, configura el .env,
# construye la imagen localmente y arranca el contenedor.
#
# USO:
#   bash openwa/setup.sh
#
# IMPORTANTE:
#   - El repo oficial NO publica imagen en Docker Hub, hay
#     que CONSTRUIRLA localmente (tarda unos minutos la 1ra vez).
#   - Requiere Docker instalado y el usuario actual en el grupo
#     `docker` (o usar sudo).
# =========================================================
set -e

# Resolver la raiz del proyecto (un nivel arriba de /openwa)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
OPENWA_DIR="$SCRIPT_DIR"
OPENWA_REPO="https://github.com/rmyndharis/OpenWA.git"

VERDE="\033[92m"
ROJO="\033[91m"
AMARILLO="\033[93m"
AZUL="\033[94m"
RESET="\033[0m"

_info()  { echo -e "${AZUL}[..]${RESET}  $*"; }
_ok()    { echo -e "${VERDE}[OK]${RESET}  $*"; }
_warn()  { echo -e "${AMARILLO}[WARN]${RESET} $*"; }
_err()   { echo -e "${ROJO}[ERR]${RESET} $*"; }

echo -e "${AZUL}========================================${RESET}"
echo -e "${AZUL}  Setup de OpenWA - Juntos por Oriana${RESET}"
echo -e "${AZUL}========================================${RESET}"
echo

# 1. Verificar Docker
_info "Verificando Docker..."
if ! command -v docker &> /dev/null; then
    _err "Docker no esta instalado."
    echo "    Instalar con: curl -fsSL https://get.docker.com | sudo sh"
    echo "    Luego: sudo usermod -aG docker \$USER  (y volver a iniciar sesion)"
    exit 1
fi
_ok "Docker encontrado: $(docker --version)"

# Verificar que podemos hablar con el daemon
if ! docker info &> /dev/null; then
    _err "No puedes hablar con el daemon de Docker."
    echo "    Causas comunes:"
    echo "    1) Tu usuario no esta en el grupo 'docker' (relogin despues de agregarlo)"
    echo "       sudo usermod -aG docker \$USER"
    echo "    2) Tienes que usar sudo (no recomendado para OpenWA, mejor grupo docker)"
    echo "    3) Docker daemon no esta corriendo"
    echo "       sudo systemctl start docker"
    exit 1
fi
_ok "Permisos de Docker OK"

# 2. Verificar/Clonar el repo de OpenWA dentro de openwa/src
_info "Preparando codigo fuente de OpenWA..."
if [ -d "$OPENWA_DIR/src/.git" ] || [ -d "$OPENWA_DIR/src/package.json" ]; then
    _ok "Repo OpenWA ya esta en $OPENWA_DIR/src"
else
    _info "Clonando OpenWA en $OPENWA_DIR/src (primera vez, puede tardar)..."
    if [ -d "$OPENWA_DIR/src" ]; then
        # Si existe la carpeta pero no es un clone valido, la borramos
        _warn "Borrando $OPENWA_DIR/src previo (no parecia un clone valido)"
        rm -rf "$OPENWA_DIR/src"
    fi
    git clone --depth 1 "$OPENWA_REPO" "$OPENWA_DIR/src"
    _ok "Repo clonado"
fi

# 3. Verificar docker-compose.yml valido
if [ ! -f "$OPENWA_DIR/src/docker-compose.yml" ]; then
    _err "No se encontro docker-compose.yml en $OPENWA_DIR/src"
    exit 1
fi

# 4. Configurar .env de OpenWA (solo si no existe)
OPENWA_ENV="$OPENWA_DIR/.env"
if [ ! -f "$OPENWA_ENV" ]; then
    _info "Creando $OPENWA_ENV desde la plantilla del repo..."
    if [ -f "$OPENWA_DIR/src/.env.minimal" ]; then
        cp "$OPENWA_DIR/src/.env.minimal" "$OPENWA_ENV"
    else
        # .env minimo para empezar
        cat > "$OPENWA_ENV" <<EOF
NODE_ENV=production
PORT=2785
API_PORT=2785
DATABASE_TYPE=sqlite
DATABASE_NAME=/app/data/openwa.sqlite
STORAGE_TYPE=local
ENGINE_TYPE=baileys
PUPPETEER_HEADLESS=true
PUPPETEER_ARGS=--no-sandbox,--disable-setuid-sandbox,--disable-dev-shm-usage
LOG_LEVEL=info
REDIS_ENABLED=false
PLUGINS_ENABLED=true
SESSION_NAME=juntospororiana
EOF
    fi
    _ok "Creado $OPENWA_ENV (revisalo y ajusta si quieres)"
else
    _ok "Ya existe $OPENWA_ENV (no se toca)"
fi

# 5. Construir y arrancar
echo
_info "Cambiando a $OPENWA_DIR/src para construir y arrancar..."
cd "$OPENWA_DIR/src"

_info "Construyendo imagen localmente (primera vez puede tardar varios minutos)..."
docker compose build

_info "Arrancando contenedor en segundo plano..."
docker compose up -d

echo
_ok "OpenWA esta arrancando. Espera 30-60 segundos y verifica con:"
echo "    docker compose -f $OPENWA_DIR/src/docker-compose.yml ps"
echo "    curl -s http://127.0.0.1:2785/api/health"
echo
_info "Cuando este listo, vincula WhatsApp:"
echo "    bash $OPENWA_DIR/init-session.sh"
echo
_info "Despues, copia la API key y SESSION_ID al .env del proyecto principal:"
echo "    cat $OPENWA_DIR/data/.api-key          # -> OPENWA_API_KEY"
echo "    SESSION_ID (lo imprime init-session.sh) # -> OPENWA_SESSION_ID"
echo
_ok "Setup completado"
