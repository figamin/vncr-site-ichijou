#!/usr/bin/env bash
# Move the populated Docker database and image cache into the native layout.
# Run on the Docker host as root.
set -euo pipefail

DOCKER_DB_CONTAINER=${DOCKER_DB_CONTAINER:-vndb-stats-backend-db-1}
DUMP_FILE=${DUMP_FILE:-/var/backups/vnclub/vndb_stats.dump}
DOCKER_IMAGES_DIR=${DOCKER_IMAGES_DIR:-public/vndb-images}
NATIVE_IMAGES_DIR=${NATIVE_IMAGES_DIR:-/srv/vnclub/public/vndb-images}

install -d -m 750 -o postgres -g postgres /var/backups/vnclub

echo "Dumping $DOCKER_DB_CONTAINER to $DUMP_FILE..."
docker exec "$DOCKER_DB_CONTAINER" \
  pg_dump -U vndb -Fc vndb_stats > "$DUMP_FILE"
chown postgres:postgres "$DUMP_FILE"

echo "Restoring into native PostgreSQL..."
sudo -u postgres pg_restore --clean --if-exists -d vndb_stats "$DUMP_FILE"

echo "Copying image cache..."
install -d -o vnclub -g vnclub "$NATIVE_IMAGES_DIR"
if command -v rsync >/dev/null; then
  rsync -a "$DOCKER_IMAGES_DIR/" "$NATIVE_IMAGES_DIR/"
else
  cp -a "$DOCKER_IMAGES_DIR/." "$NATIVE_IMAGES_DIR/"
fi
chown -R vnclub:vnclub "$NATIVE_IMAGES_DIR"
chmod -R g+rX "$NATIVE_IMAGES_DIR"

echo "Done. Verify with:"
echo "  sudo -u postgres psql -d vndb_stats -c 'SELECT count(*) FROM visual_novels;'"
echo "  find $NATIVE_IMAGES_DIR -type f | wc -l"
