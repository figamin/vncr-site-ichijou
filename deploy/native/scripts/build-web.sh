#!/usr/bin/env bash
# Build the Next.js frontend with a strict memory cap (6 GiB max by default).
# Run as root on native deployments (invoked by prepare-app.sh and update.sh).
set -euo pipefail

APP_DIR=${APP_DIR:-/srv/vnclub}
BUILD_USER=${BUILD_USER:-vnclub}
BUILD_MEMORY_HIGH=${BUILD_MEMORY_HIGH:-5G}
BUILD_MEMORY_MAX=${BUILD_MEMORY_MAX:-6G}

cd "$APP_DIR"

if command -v systemd-run >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  exec systemd-run --scope --quiet \
    -p "MemoryHigh=${BUILD_MEMORY_HIGH}" \
    -p "MemoryMax=${BUILD_MEMORY_MAX}" \
    -- sudo -u "$BUILD_USER" /usr/local/bin/bun run build "$@"
fi

exec sudo -u "$BUILD_USER" /usr/local/bin/bun run build "$@"
