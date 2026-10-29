#!/usr/bin/env bash
# Update the native lean core. Run as root.
set -euo pipefail

APP_DIR=${APP_DIR:-/srv/vnclub}

cd "$APP_DIR"
sudo -u vnclub git pull --ff-only
chmod +x "$APP_DIR"/deploy/native/scripts/*.sh "$APP_DIR"/deploy/native/scripts/*.py
sudo -u vnclub bun install --frozen-lockfile
sudo -u vnclub bun run build

# Same installer prepare-app.sh uses, so arm64 hosts get the same wheel
# selection and the same toolchain error message.
bash "$APP_DIR/deploy/native/scripts/install-python-deps.sh"

# Keep the installed units in step with the checkout; a changed ExecStart or
# environment file would otherwise be silently ignored.
for f in "$APP_DIR"/deploy/native/systemd/*.service \
         "$APP_DIR"/deploy/native/systemd/*.timer; do
  install -m 644 "$f" /etc/systemd/system/
done
systemctl daemon-reload

sudo systemctl restart vnclub-api.service
sudo systemctl restart vnclub-web.service

echo "Updated. The API prestart runs schema/data migrations on restart."
