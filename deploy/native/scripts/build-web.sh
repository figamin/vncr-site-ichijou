#!/usr/bin/env bash
# Build the Next.js frontend with an 8 GiB RAM and 4-core CPU cap by default.
# Run as root on native deployments (invoked by prepare-app.sh and update.sh).
set -euo pipefail

APP_DIR=${APP_DIR:-/srv/vnclub}
BUILD_USER=${BUILD_USER:-vnclub}
BUILD_MEMORY_HIGH=${BUILD_MEMORY_HIGH:-7G}
BUILD_MEMORY_MAX=${BUILD_MEMORY_MAX:-8G}
BUILD_CPU_QUOTA=${BUILD_CPU_QUOTA:-400%}

cd "$APP_DIR"

if command -v systemd-run >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  exec systemd-run --scope --quiet \
    -p "MemoryHigh=${BUILD_MEMORY_HIGH}" \
    -p "MemoryMax=${BUILD_MEMORY_MAX}" \
    -p "CPUQuota=${BUILD_CPU_QUOTA}" \
    -- sudo -u "$BUILD_USER" /usr/local/bin/bun run build "$@"
fi

exec sudo -u "$BUILD_USER" /usr/local/bin/bun run build "$@"
