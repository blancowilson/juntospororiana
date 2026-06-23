# Respaldos de Juntos por Oriana

Sistema de backups automaticos de la base de datos PostgreSQL con retencion
local y sincronizacion offsite (otra maquina, B2, S3, etc).

## Que hay en este directorio

| Archivo | Que hace |
|---------|----------|
| `backup_local.sh` | `pg_dump` + gzip + SHA256 + verificacion de import + rotacion. |
| `backup_external.sh` | Sincroniza los dumps a un remoto (rclone / scp / s3) y los rota. |
| `backup_db.sh` | Orquestador: ejecuta local, y si sale bien, ejecuta externo. |
| `restore.sh` | Restaura un backup (con confirmacion + safety backup pre-restore). |
| `../vps_deploy/juntospororiana-backup.service` | Unit de systemd (oneshot). |
| `../vps_deploy/juntospororiana-backup.timer` | Timer de systemd (todos los dias 03:30 UTC). |

## Instalacion rapida (Ubuntu 20.04+)

```bash
# 1. Crear directorio de backups
sudo mkdir -p /var/backups/juntospororiana
sudo chown jpoadmin:jpoadmin /var/backups/juntospororiana

# 2. Instalar cliente de rclone (alternativa: aws-cli o solo ssh/scp)
sudo apt install -y rclone   # o awscli, o nada si usas scp

# 3. Configurar el remoto (ejemplos abajo)

# 4. Activar el timer
sudo cp /var/www/juntospororiana/vps_deploy/juntospororiana-backup.service /etc/systemd/system/
sudo cp /var/www/juntospororiana/vps_deploy/juntospororiana-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now juntospororiana-backup.timer

# 5. Probar manualmente
sudo -u jpoadmin bash /var/www/juntospororiana/scripts/backup_db.sh
```

## Configuracion del transporte externo

Edita el `.env` del proyecto y agrega una (o ninguna) de las siguientes
configuraciones. `BACKUP_REMOTE_TYPE` elige el transporte.

### Opcion A: rclone (recomendado, soporta B2, S3, Google Drive, etc.)

```bash
# 1. Configurar el remoto (interactivo)
sudo -u jpoadmin rclone config
#   Nombre: mi-b2  (o lo que quieras)
#   Tipo:   b2 / s3 / drive / etc

# 2. Probar
sudo -u jpoadmin rclone ls mi-b2:

# 3. Agregar al .env
echo "BACKUP_REMOTE_TYPE=rclone" >> /var/www/juntospororiana/.env
echo "BACKUP_REMOTE_PATH=mi-b2:mi-bucket/juntospororiana" >> /var/www/juntospororiana/.env
```

### Opcion B: SCP/SFTP (servidor remoto via SSH)

```bash
# 1. Generar llave SSH dedicada para backups (recomendado, no usar la personal)
sudo -u jpoadmin ssh-keygen -t ed25519 -f /var/www/juntospororiana/.ssh/backup_key -N ''
sudo -u jpoadmin ssh-copy-id -i /var/www/juntospororiana/.ssh/backup_key.pub backup@backup.example.com

# 2. Configurar ~/.ssh/config para usar la llave automaticamente
sudo -u jpoadmin mkdir -p /var/www/juntospororiana/.ssh
sudo -u jpoadmin bash -c 'cat >> /var/www/juntospororiana/.ssh/config <<EOF
Host backup.example.com
    User backup
    IdentityFile /var/www/juntospororiana/.ssh/backup_key
    IdentitiesOnly yes
    StrictHostKeyChecking accept-new
EOF'
chmod 600 /var/www/juntospororiana/.ssh/config

# 3. Agregar al .env
cat >> /var/www/juntospororiana/.env <<EOF
BACKUP_REMOTE_TYPE=scp
BACKUP_REMOTE_HOST=backup.example.com
BACKUP_REMOTE_USER=backup
BACKUP_REMOTE_PATH=/backups/juntospororiana
EOF
```

### Opcion C: S3 (AWS)

```bash
# 1. Crear usuario IAM con permiso s3:PutObject sobre el bucket
# 2. Configurar aws cli
sudo -u jpoadmin aws configure
# 3. Agregar al .env
cat >> /var/www/juntospororiana/.env <<EOF
BACKUP_REMOTE_TYPE=s3
BACKUP_S3_BUCKET=mi-bucket
BACKUP_S3_PREFIX=juntospororiana
EOF
```

### Opcion D: solo local (sin offsite)

```bash
echo "BACKUP_SKIP_EXTERNAL=1" >> /var/www/juntospororiana/.env
```

## Variables de entorno (todas opcionales)

| Variable | Default | Descripcion |
|----------|---------|-------------|
| `PROJECT_DIR` | `/var/www/juntospororiana` | Ruta del proyecto |
| `BACKUP_DIR` | `$PROJECT_DIR/backups` | Donde se guardan los dumps |
| `BACKUP_KEEP_DAYS` | `7` | Dias de retencion local y remota |
| `BACKUP_PREFIX` | `jpo` | Prefijo del nombre de archivo |
| `BACKUP_SKIP_VERIFY` | `0` | Si `1`, no hace import de prueba |
| `BACKUP_SKIP_EXTERNAL` | `0` | Si `1`, no sincroniza al remoto |
| `BACKUP_REMOTE_TYPE` | `rclone` | `rclone` \| `scp` \| `s3` |
| `BACKUP_REMOTE_PATH` | - | Path del remoto (rclone) |
| `BACKUP_REMOTE_HOST` | - | Hostname/IP (scp) |
| `BACKUP_REMOTE_USER` | - | Usuario SSH (scp) |
| `BACKUP_S3_BUCKET` | - | Bucket S3 |
| `BACKUP_S3_PREFIX` | `juntospororiana` | Prefijo dentro del bucket |

## Comandos utiles

```bash
# Listar backups disponibles
bash scripts/restore.sh --list

# Restaurar el mas reciente
bash scripts/restore.sh --latest

# Restaurar uno especifico
bash scripts/restore.sh backups/jpo_host_20260623T033000Z.sql.gz

# Ejecutar backup manualmente (local + offsite)
bash scripts/backup_db.sh

# Solo local
BACKUP_SKIP_EXTERNAL=1 bash scripts/backup_db.sh

# Ver logs
ls -lh /var/backups/juntospororiana/logs/

# Estado del timer
systemctl list-timers juntospororiana-backup.timer

# Ultima corrida del servicio
journalctl -u juntospororiana-backup.service -n 50 --no-pager
```

## Formato del nombre de archivo

```
{prefix}_{hostname}_{timestamp_UTC}.sql.gz
jpo_vps1_20260623T033000Z.sql.gz
```

Cada archivo va acompanado de:
- `.sha256` con el hash para verificacion.
- `.meta` con JSON de metadata (db, hostname, fecha, etc).

## Seguridad

- Los dumps se comprimen con gzip (no se cifran en disco).
- Si necesitas cifrar en reposo, usa el cifrado del transporte offsite
  (B2/S3 con SSE, o rclone crypt con un remote encima).
- `.env` y `BACKUP_DIR` deben tener permisos 700/600 y propietario
  `jpoadmin:jpoadmin`. **Nunca** commitear `.env` al repo.
- El systemd service corre con `ProtectSystem=full` y `NoNewPrivileges=true`.

## Verificacion de integridad

`backup_local.sh` hace lo siguiente para cada dump:

1. Calcula SHA256 y lo guarda en `.sha256`.
2. Verifica que el gzip sea valido (`gzip -t`).
3. **Importa el dump en una BD temporal** (`_verify_<ts>`) y cuenta filas
   en `Aportantes`, `Tickets`, `Rifas`, `AuditLog`. Borra la BD al final.
4. Si la verificacion falla, el script sale con codigo != 0 y el
   orquestador no sincroniza al remoto.

Para deshabilitar la verificacion (e.g. en bases muy grandes):
```bash
BACKUP_SKIP_VERIFY=1 bash scripts/backup_db.sh
```

## Recuperacion ante desastre

Si pierdes la BD (disco muerto, migracion, error humano):

```bash
# 1. Listar backups remotos
bash scripts/restore.sh --list

# 2. Si el archivo esta en el remoto pero no en /var/backups, copiarlo primero
rclone copy mi-b2:mi-bucket/juntospororiana/jpo_vps1_20260623T033000Z.sql.gz /tmp/

# 3. Restaurar (modo interactivo, pide confirmacion y genera safety backup)
bash scripts/restore.sh /tmp/jpo_vps1_20260623T033000Z.sql.gz

# 4. Verificar que la app arranca
sudo systemctl restart juntospororiana
curl -I https://juntospororiana.online/health
```

## Que pasa con los datos de los aportantes?

La reasignacion de boletos (modo mixto, random, manual, original_only_free)
**nunca** elimina aportantes ni tickets. Solo cambia el campo `estado` y
`aportante_id` de los tickets. Los registros de `Aportantes`, `Rifas`,
`Campana` y `LotesConciliacion` permanecen intactos. El flujo de reasignacion
tambien escribe en `AuditLog` para trazabilidad.

Verificado en `tests/test_data_preservation.py`:
- Aportantes: 4 -> 4
- Tickets: 1000 -> 1000
- Suma por estado: 1000 -> 1000
- Pagados: nunca modificados

## Respaldo manual inmediato

Si necesitas un backup antes de un cambio arriesgado (deploy grande,
migracion, etc):

```bash
sudo -u jpoadmin bash /var/www/juntospororiana/scripts/backup_db.sh
ls -lh /var/backups/juntospororiana/*.sql.gz | tail -3
```
