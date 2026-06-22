# OpenWA - Servidor de WhatsApp para Juntos por Oriana

Esta carpeta contiene la configuración y los scripts para levantar
**OpenWA** (https://www.open-wa.org/), un gateway HTTP self-hosted que
automatiza el envío de mensajes de WhatsApp Web.

## ⚠️ Importante

**OpenWA NO publica una imagen oficial en Docker Hub.** El repo oficial te
obliga a construir la imagen localmente con `docker compose build`. Por eso
no usamos una imagen pre-hecha, sino que clonamos el repo y construimos.

## 🚀 Setup rápido

```bash
cd /var/www/juntospororiana
bash openwa/setup.sh
```

Eso hace todo: clona el repo, configura `.env`, construye la imagen y arranca
el contenedor. Tarda unos minutos la primera vez (build de la imagen).

Después:

```bash
# 1. Verifica que está corriendo
curl -s http://127.0.0.1:2785/api/health

# 2. Vincula WhatsApp (muestra el QR)
bash openwa/init-session.sh
# Escanea el QR con el celular

# 3. Obtén la API key
bash openwa/get-api-key.sh

# 4. Copia las claves al .env del proyecto principal
#    OPENWA_API_KEY=owa_k1_xxxx
#    OPENWA_SESSION_ID=sess_xxxx

# 5. Reinicia FastAPI
sudo systemctl restart juntospororiana
```

## 📂 Estructura después del setup

```
openwa/
├── setup.sh             # script de instalacion (clona repo + build + up)
├── init-session.sh      # crea sesion + muestra QR
├── get-api-key.sh       # muestra la API key
├── README.md            # este archivo
├── .env                 # configuracion de OpenWA (lo crea setup.sh)
├── data/                # sesiones, BD SQLite, API key (NO se sube a git)
└── src/                 # codigo fuente de OpenWA (clonado del repo oficial)
    ├── docker-compose.yml
    ├── Dockerfile
    ├── src/
    └── ...
```

## 🔧 Comandos útiles

```bash
# Ver logs en tiempo real
cd openwa/src && docker compose logs -f

# Ver estado de los contenedores
cd openwa/src && docker compose ps

# Reiniciar OpenWA
cd openwa/src && docker compose restart

# Apagar OpenWA
cd openwa/src && docker compose down

# Actualizar OpenWA a la ultima version
cd openwa/src && git pull && cd .. && bash setup.sh
```

## 🔌 Endpoints que usa la app

| Endpoint OpenWA                              | Lo usa                                  |
|----------------------------------------------|-----------------------------------------|
| `GET /api/sessions`                          | `openwa_admin.estado_sesion()`          |
| `GET /api/sessions/{id}`                     | `openwa_admin.estado_sesion()`          |
| `POST /api/sessions/{id}/messages/send-text` | `whatsapp.enviar_texto()`               |
| `GET /api/sessions/{id}/qr`                  | `openwa_admin.obtener_qr()`             |
| `POST /api/sessions/{id}/start`              | `openwa_admin.iniciar_sesion()`         |

## ❌ Problemas comunes

### "permission denied while trying to connect to the docker API"

Tu usuario no está en el grupo `docker`:
```bash
sudo usermod -aG docker $USER
# IMPORTANTE: cierra sesión y vuelve a entrar (o `exec su -l $USER`)
```

### "pull access denied for rmyndharis/openwa:latest"

Si ves este error es porque estás usando un docker-compose.yml viejo que
intentaba bajar una imagen que no existe. Borra la carpeta `openwa/` y vuelve
a correr `bash openwa/setup.sh` (que clona el repo y construye la imagen).

### "Cannot connect to the Docker daemon"

```bash
sudo systemctl start docker
sudo systemctl enable docker
```

### La sesión se desconecta

WhatsApp Web a veces cierra sesiones inactivas. El contenedor se reinicia
solo (`restart: unless-stopped`). Si sigue fallando, escanea el QR de nuevo:
```bash
bash openwa/init-session.sh
```

### Quiero cambiar de motor (whatsapp-web.js ↔ baileys)

Edita `openwa/.env` y cambia `ENGINE_TYPE`. Para VPS pequeños se recomienda
`baileys` (no usa navegador, consume menos RAM). Después:
```bash
cd openwa/src && docker compose restart
```

## ⚖️ Aviso legal

OpenWA **no está afiliado a Meta ni a WhatsApp**. Es un proyecto open-source
independiente. Úsalo bajo tu responsabilidad respetando los términos de
servicio de WhatsApp.
