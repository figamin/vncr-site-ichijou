#!/usr/bin/env bash
# Compressed PostgreSQL dump with a short retention window.
# Runs as the postgres OS user from vnclub-backup.service.
set -euo pipefail

DEST=${BACKUP_DIR:-/var/backups/vnclub}
RETENTION_DAYS=${RETENTION_DAYS:-7}
STAMP=$(date -u +%Y%m%dT%H%M%SZ)

install -d -m 700 "$DEST"
pg_dump -Fc vndb_stats > "$DEST/vnclub-$STAMP.dump"
find "$DEST" -name 'vnclub-*.dump' -mtime "+$RETENTION_DAYS" -delete
