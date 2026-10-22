#!/usr/bin/env bash
# Schema setup and data migrations, run before Uvicorn.
# systemd starts this as vnclub with /etc/vnclub/api.env loaded.
set -euo pipefail

cd /srv/vnclub/vndb-stats-backend
export PATH="$PWD/.venv/bin:/usr/local/bin:/usr/bin:/bin"

echo "Running native schema setup..."
.venv/bin/python scripts/schema_setup.py

echo "Running native data migrations..."
.venv/bin/python - <<'PY'
import asyncio
import logging

logging.basicConfig(level=logging.INFO)
try:
    from app.ingestion.data_migrations import load_migrations, run_pending_migrations
    load_migrations()
    asyncio.run(run_pending_migrations())
except Exception as exc:
    print(f"Data migration warning: {exc}")
    print("Data migrations can be run manually later with: bun run api:data-migrate")
PY
