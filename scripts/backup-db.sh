#!/usr/bin/env bash
# backup-db.sh — daily pg_dump of the fishingposter database.
# Runs ON THE DROPLET via cron (installed at /usr/local/bin/backup-fishingposter.sh).
# Container: o630hdmppejmchbw7gn2qmn2 (postgres:16-alpine), superuser: realms.

set -euo pipefail

CONTAINER="o630hdmppejmchbw7gn2qmn2"
DB_NAME="fishingposter"
BACKUP_DIR="/var/backups/fishingposter"
RETENTION_DAYS=14
TS="$(date +%Y%m%d-%H%M%S)"
OUT="${BACKUP_DIR}/${DB_NAME}-${TS}.sql.gz"

mkdir -p "${BACKUP_DIR}"

# pg_dump runs inside the container as the realms superuser; --no-owner / --no-acl
# keeps the dump portable to a future restore where the app role may not yet exist.
docker exec "${CONTAINER}" pg_dump \
  -U realms \
  --no-owner \
  --no-acl \
  --format=plain \
  "${DB_NAME}" \
  | gzip > "${OUT}"

# Retention: delete dumps older than RETENTION_DAYS.
find "${BACKUP_DIR}" -name "${DB_NAME}-*.sql.gz" -type f -mtime "+${RETENTION_DAYS}" -delete

echo "$(date -Is) backup ok: ${OUT} ($(du -h "${OUT}" | cut -f1))"
