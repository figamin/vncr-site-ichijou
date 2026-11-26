#!/usr/bin/env bash
# Migrate VN Club's primary app DB (vndb_stats) and preexisting Jiten data
# from localhost to the remote PostgreSQL server at 11.11.11.4:5432,
# run Alembic migrations, populate external ratings, and verify health.
#
# Run as root on the VN Club host.
set -euo pipefail

REMOTE_HOST=${REMOTE_HOST:-11.11.11.4}
REMOTE_PORT=${REMOTE_PORT:-5432}
REMOTE_USER=${REMOTE_USER:-vnuser}
REMOTE_PASSWORD=${REMOTE_PASSWORD:-supermio}

LOCAL_APP_DB=${LOCAL_APP_DB:-vndb_stats}
REMOTE_APP_DB=${REMOTE_APP_DB:-vndb_stats}
REMOTE_JITEN_DB=${REMOTE_JITEN_DB:-jiten}

DUMP_DIR=${DUMP_DIR:-/var/backups/vnclub}
DUMP_FILE=${DUMP_FILE:-${DUMP_DIR}/${LOCAL_APP_DB}_to_remote.dump}
BACKEND_DIR=${BACKEND_DIR:-/srv/vnclub/vndb-stats-backend}
PYTHON_BIN=${PYTHON_BIN:-${BACKEND_DIR}/.venv/bin/python}
ALEMBIC_BIN=${ALEMBIC_BIN:-${BACKEND_DIR}/.venv/bin/alembic}
API_ENV_FILE=${API_ENV_FILE:-/etc/vnclub/api.env}

export PGPASSWORD="$REMOTE_PASSWORD"

echo "=== 1. Checking connectivity to ${REMOTE_HOST}:${REMOTE_PORT} ==="
pg_isready -h "$REMOTE_HOST" -p "$REMOTE_PORT"

echo "=== 2. Verifying target database '${REMOTE_APP_DB}' exists on ${REMOTE_HOST} ==="
if ! psql -h "$REMOTE_HOST" -p "$REMOTE_PORT" -U "$REMOTE_USER" -d "$REMOTE_APP_DB" -c "SELECT 1;" >/dev/null 2>&1; then
  cat <<EOF
ERROR: Cannot connect to database '${REMOTE_APP_DB}' on ${REMOTE_HOST}:${REMOTE_PORT} as '${REMOTE_USER}'.
If '${REMOTE_APP_DB}' does not exist yet on ${REMOTE_HOST}, run as PostgreSQL superuser on ${REMOTE_HOST}:

  CREATE DATABASE ${REMOTE_APP_DB} OWNER ${REMOTE_USER};
  \\c ${REMOTE_APP_DB}
  CREATE EXTENSION IF NOT EXISTS pg_trgm;

Then re-run this script.
EOF
  exit 1
fi

echo "=== 3. Checking pg_trgm extension on ${REMOTE_HOST}/${REMOTE_APP_DB} ==="
if ! psql -h "$REMOTE_HOST" -p "$REMOTE_PORT" -U "$REMOTE_USER" -d "$REMOTE_APP_DB" -tAc "SELECT 1 FROM pg_extension WHERE extname = 'pg_trgm';" | grep -q 1; then
  psql -h "$REMOTE_HOST" -p "$REMOTE_PORT" -U "$REMOTE_USER" -d "$REMOTE_APP_DB" -c "CREATE EXTENSION IF NOT EXISTS pg_trgm;" || {
    echo "NOTE: If CREATE EXTENSION pg_trgm failed due to permissions, run on ${REMOTE_HOST} as superuser:"
    echo "  psql -d ${REMOTE_APP_DB} -c 'CREATE EXTENSION IF NOT EXISTS pg_trgm;'"
    exit 1
  }
fi

echo "=== 4. Migrating preexisting Jiten tables (vn_difficulty + word_of_the_day) to ${REMOTE_HOST}/${REMOTE_JITEN_DB} ==="
LOCAL_DB_URL=$(grep -E '^DATABASE_URL=' "$API_ENV_FILE" 2>/dev/null | head -n1 | cut -d= -f2- || true)
if [ -n "$LOCAL_DB_URL" ] && [[ "$LOCAL_DB_URL" != *"@${REMOTE_HOST}:"* ]]; then
  "$PYTHON_BIN" "${BACKEND_DIR}/scripts/migrate_jiten_to_remote.py" --from-db-url "$LOCAL_DB_URL" --sweep
else
  echo "Source DATABASE_URL not local or already remote; seeding ${REMOTE_JITEN_DB} via --sweep..."
  "$PYTHON_BIN" "${BACKEND_DIR}/scripts/migrate_jiten_to_remote.py" --sweep
fi

echo "=== 5. Dumping local '${LOCAL_APP_DB}' to ${DUMP_FILE} ==="
install -d -m 750 -o postgres -g postgres "$DUMP_DIR"
sudo -u postgres pg_dump -d "$LOCAL_APP_DB" --no-owner --no-privileges -Fc -f "$DUMP_FILE"

echo "=== 6. Restoring into ${REMOTE_HOST}/${REMOTE_APP_DB} ==="
pg_restore \
  -h "$REMOTE_HOST" \
  -p "$REMOTE_PORT" \
  -U "$REMOTE_USER" \
  -d "$REMOTE_APP_DB" \
  --clean \
  --if-exists \
  --no-owner \
  --no-privileges \
  "$DUMP_FILE"

REMOTE_APP_URL="postgresql+asyncpg://${REMOTE_USER}:${REMOTE_PASSWORD}@${REMOTE_HOST}:${REMOTE_PORT}/${REMOTE_APP_DB}"
REMOTE_VNDB_URL="postgresql+asyncpg://${REMOTE_USER}:${REMOTE_PASSWORD}@${REMOTE_HOST}:${REMOTE_PORT}/vndb"
REMOTE_EGS_URL="postgresql+asyncpg://${REMOTE_USER}:${REMOTE_PASSWORD}@${REMOTE_HOST}:${REMOTE_PORT}/egs"
REMOTE_BGM_URL="postgresql+asyncpg://${REMOTE_USER}:${REMOTE_PASSWORD}@${REMOTE_HOST}:${REMOTE_PORT}/bangumi"
REMOTE_JITEN_URL="postgresql+asyncpg://${REMOTE_USER}:${REMOTE_PASSWORD}@${REMOTE_HOST}:${REMOTE_PORT}/${REMOTE_JITEN_DB}"

echo "=== 7. Running Alembic migrations against ${REMOTE_HOST}/${REMOTE_APP_DB} ==="
(
  cd "$BACKEND_DIR"
  DATABASE_URL="$REMOTE_APP_URL" \
  VNDB_SOURCE_DATABASE_URL="$REMOTE_VNDB_URL" \
  EGS_DATABASE_URL="$REMOTE_EGS_URL" \
  BANGUMI_DATABASE_URL="$REMOTE_BGM_URL" \
  JITEN_DATABASE_URL="$REMOTE_JITEN_URL" \
    "$ALEMBIC_BIN" upgrade head
)

echo "=== 8. Syncing external ErogameScape & Bangumi ratings mirror ==="
(
  cd "$BACKEND_DIR"
  DATABASE_URL="$REMOTE_APP_URL" \
  VNDB_SOURCE_DATABASE_URL="$REMOTE_VNDB_URL" \
  EGS_DATABASE_URL="$REMOTE_EGS_URL" \
  BANGUMI_DATABASE_URL="$REMOTE_BGM_URL" \
  JITEN_DATABASE_URL="$REMOTE_JITEN_URL" \
    "$PYTHON_BIN" ../deploy/native/scripts/heavy_task.py external
)

cat <<EOF

=== Migration complete! Next step: update ${API_ENV_FILE} ===

Set these database lines in ${API_ENV_FILE}:

  DATABASE_URL=${REMOTE_APP_URL}
  VNDB_SOURCE_DATABASE_URL=${REMOTE_VNDB_URL}
  EGS_DATABASE_URL=${REMOTE_EGS_URL}
  BANGUMI_DATABASE_URL=${REMOTE_BGM_URL}
  JITEN_DATABASE_URL=${REMOTE_JITEN_URL}

Then restart services:

  sudo systemctl restart vnclub-api vnclub-bot vnclub-web
  curl -s http://127.0.0.1:8000/health/db | python3 -m json.tool
EOF
