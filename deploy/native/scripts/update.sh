#!/usr/bin/env bash
# Update the native lean core. Run as root.
set -euo pipefail

APP_DIR=${APP_DIR:-/srv/vnclub}
BACKEND_DIR="$APP_DIR/vndb-stats-backend"

cd "$APP_DIR"
sudo -u vnclub git pull --ff-only
sudo -u vnclub bun install --frozen-lockfile
sudo -u vnclub bun run build

sudo -u vnclub uv pip install \
  --python "$BACKEND_DIR/.venv/bin/python" \
  --require-hashes -r "$BACKEND_DIR/requirements.lock"
sudo -u vnclub uv pip install \
  --python "$BACKEND_DIR/.venv/bin/python" \
  --no-deps -r "$BACKEND_DIR/requirements-vcs.txt"

sudo systemctl restart vnclub-api.service
sudo systemctl restart vnclub-web.service

echo "Updated. The API prestart runs schema/data migrations on restart."
