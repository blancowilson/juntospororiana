# OpenWA - Servidor de WhatsApp para Juntos por Oriana

Esta carpeta contiene la configuración del servidor **OpenWA**
(https://www.open-wa.org/), un gateway HTTP self-hosted que
automatiza el envío de mensajes de WhatsApp Web.

El servidor corre como un contenedor Docker independiente y expone
un API REST en `http://127.0.0.1:2785`. La aplicación FastAPI
lo consume a través de `app/services/whatsapp.py` para enviar:

| Evento                                                         | Mensaje                                                         |
|----------------------------------------------------------------|-----------------------------------------------------------------|
| Donación directa (con teléfono)                                | Agradecimiento personalizado con monto                         |
| Compra de tickets de rifa                                      | Aviso de revisión manual y números provisionales                |
| Confirmación manual de tickets por el admin                    | Confirmación definitiva con los tickets oficiales               |

---

## 1. Estructura

```
openwa/
├── .env.example           # Variables de entorno (copialo a .env)
├── docker-compose.yml     # Levanta el contenedor OpenWA
├── data/                  # Sesiones, BD SQLite, API key (NO subir a git)
├── get-api-key.sh         # Muestra la API key generada
└── init-session.sh        # Crea la sesion y muestra el QR para vincular
```

---

## 2. Arranque local (Windows / Mac / Linux con Docker)

```bash
cd openwa
cp .env.example .env

# Levantar el contenedor
docker compose up -d

# Ver logs (espera a ver "Nest application successfully started")
docker compose logs -f openwa
```

Cuando veas `Nest application successfully started` el API ya está
corriendo en `http://127.0.0.1:2785`.

- **Dashboard web:** http://127.0.0.1:2785
- **Swagger API:** http://127.0.0.1:2785/api/docs

---

## 3. Vincular tu número de WhatsApp

### 3.1. Obtener la API key

```bash
cat data/.api-key
```

Copia ese valor a tu `.env` del proyecto principal como `OPENWA_API_KEY`.

### 3.2. Crear la sesión y escanear el QR

```bash
bash init-session.sh
```

El script:
1. Crea una sesión llamada `juntospororiana`.
2. La inicia.
3. Imprime el QR en formato JSON (campo `image` es base64 PNG).

**Para escanear el QR tienes dos opciones:**

- **A) Dashboard web:** abre http://127.0.0.1:2785, ve a la sesión y
  escanea el QR que se muestra ahí.
- **B) Directo desde el celular:** abre WhatsApp en tu teléfono → ⋮ →
  *Dispositivos vinculados* → *Vincular un dispositivo* → escanea el
  QR de la terminal (puedes decodificar el base64 a imagen con cualquier
  visor online).

Al final del script se imprime algo como:

```
SESSION_ID=sess_xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
```

Copia ese ID a tu `.env` del proyecto principal como `OPENWA_SESSION_ID`.

### 3.3. Reinicia FastAPI

```bash
# En el proyecto principal
python main.py
# o si usas uvicorn
uvicorn main:app --reload
```

---

## 4. Configuración en el proyecto principal

En tu archivo `.env` (raíz de JuntosporOriana):

```ini
OPENWA_BASE_URL=http://127.0.0.1:2785/api
OPENWA_API_KEY=owa_k1_xxxxxxxxxxxx
OPENWA_SESSION_ID=sess_xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx
OPENWA_ENABLED=true
OPENWA_DEFAULT_COUNTRY_CODE=58
```

Si `OPENWA_ENABLED=false` los envíos se omiten (útil en desarrollo
cuando aún no has vinculado WhatsApp).

---

## 5. Despliegue en VPS (Ubuntu)

El script `vps_deploy/deploy.sh` ya instala Docker y deja preparado
el servicio systemd. Después de correrlo:

```bash
cd /var/www/juntospororiana/openwa
cp .env.example .env
nano .env   # ajustar valores si quieres

# Arrancar el contenedor
sudo systemctl start openwa
sudo systemctl status openwa

# Esperar 20-30s a que genere la API key
sleep 30
cat data/.api-key

# Crear sesion y mostrar QR (ejecutar una sola vez)
sudo -u jpoadmin bash init-session.sh
```

Para mantener la sesión viva entre reinicios del servidor, asegúrate
de que el contenedor Docker **no se elimine** (en `docker-compose.yml`
ya está `restart: unless-stopped`).

---

## 6. Probar el envío desde la línea de comandos

```bash
API_KEY=$(cat openwa/data/.api-key)
SESSION_ID=$(grep OPENWA_SESSION_ID .env | cut -d= -f2)

curl -X POST "http://127.0.0.1:2785/api/sessions/${SESSION_ID}/messages/send-text" \
  -H "X-API-Key: ${API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"chatId":"584141234567@c.us","text":"Hola desde OpenWA!"}'
```

(Sustituye `584141234567` por tu número con código de país.)

---

## 7. Troubleshooting

### La sesión se desconecta
WhatsApp Web a veces cierra sesiones inactivas. Para reconectarla:

```bash
# El contenedor se reinicia solo (restart: unless-stopped)
docker compose restart openwa
# Espera 30s y vuelve a escanear el QR
bash init-session.sh
```

### OpenWA no responde
```bash
docker compose ps
docker compose logs --tail 100 openwa
```

### Quiero cambiar de motor (whatsapp-web.js ↔ baileys)
Edita `.env` y cambia `ENGINE_TYPE`. Para VPS pequeños se recomienda
`baileys` (no usa navegador, consume menos RAM).

### El número se banea
WhatsApp puede suspender números si se comportan como bot. Revisa:
- ¿Estás enviando mensajes masivos a gente que no te ha contactado?
- ¿Hay un volumen muy alto en poco tiempo?
- OpenWA ya incluye rate limiting y simulación de tecleo, pero el
  comportamiento humano sigue siendo responsabilidad del operador.

---

## 8. Aviso legal

OpenWA **no está afiliado a Meta ni a WhatsApp**. Es un proyecto
open-source independiente. Úsalo bajo tu responsabilidad respetando
los términos de servicio de WhatsApp.
