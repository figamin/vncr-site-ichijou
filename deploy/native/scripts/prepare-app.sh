#!/usr/bin/env bash
# Prepare the application tree for the native lean core.
#
# This does NOT install apt packages and does NOT start/restart PostgreSQL,
# Redis or Angie. On a host that already serves other sites, those steps should
# be reviewed manually. It creates the service user, directories, Python venv,
# frontend build and installs the systemd unit files.
set -euo pipefail

APP_DIR=${APP_DIR:-/srv/vnclub}
BACKEND_DIR="$APP_DIR/vndb-stats-backend"
REPO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root." >&2
  exit 1
fi

# The installed systemd units hard-code /srv/vnclub. Keep APP_DIR in sync with
# them rather than silently building a tree the units will never use.
if [ "$APP_DIR" != "/srv/vnclub" ]; then
  echo "The systemd units assume APP_DIR=/srv/vnclub; got $APP_DIR." >&2
  echo "Edit deploy/native/systemd/*.service first if you need another path." >&2
  exit 1
fi

if [ ! -f "$REPO_DIR/vndb-stats-backend/requirements.lock" ]; then
  echo "Repository not found at $REPO_DIR" >&2
  echo "Clone it to $APP_DIR first, or set APP_DIR/REPO_DIR." >&2
  exit 1
fi

if ! id vnclub >/dev/null 2>&1; then
  useradd --system --user-group --home "$APP_DIR" --shell /usr/sbin/nologin vnclub
fi

install -d -o vnclub -g vnclub "$APP_DIR"
install -d -o vnclub -g vnclub "$APP_DIR/dumps"
install -d -o vnclub -g vnclub "$APP_DIR/public/vndb-images"
install -d -o vnclub -g vnclub "$BACKEND_DIR/logs"
install -d -m 750 -o root -g vnclub /etc/vnclub
if id postgres >/dev/null 2>&1; then
  install -d -m 750 -o postgres -g postgres /var/backups/vnclub
fi

chown -R vnclub:vnclub "$APP_DIR"

for u in www-data angie; do
  if id "$u" >/dev/null 2>&1; then
    usermod -aG vnclub "$u"
  fi
done
chmod -R g+rX "$APP_DIR/public/vndb-images"
find "$APP_DIR/public/vndb-images" -type d -exec chmod g+s {} +

if ! command -v bun >/dev/null; then
  echo "Bun is missing. Install it first:" >&2
  echo "  curl -fsSL https://bun.sh/install | BUN_INSTALL=/opt/bun bash" >&2
  echo "  ln -sf /opt/bun/bin/bun /usr/local/bin/bun" >&2
  echo "  ln -sf /opt/bun/bin/bunx /usr/local/bin/bunx" >&2
  exit 1
fi

# The site's package scripts call bunx, and the web unit's PATH only includes
# /usr/local/bin. Make sure both entry points exist there.
BUN_REAL=$(readlink -f "$(command -v bun)")
if [ ! -x /usr/local/bin/bun ]; then
  ln -sf "$BUN_REAL" /usr/local/bin/bun
fi
if [ ! -x /usr/local/bin/bunx ]; then
  BUNX_REAL="$(dirname "$BUN_REAL")/bunx"
  if [ -x "$BUNX_REAL" ]; then
    ln -sf "$BUNX_REAL" /usr/local/bin/bunx
  else
    # bunx is normally a symlink to the bun binary; keep that behavior if the
    # installer omitted the second symlink.
    ln -sf "$BUN_REAL" /usr/local/bin/bunx
  fi
fi

if ! command -v uv >/dev/null; then
  echo "uv is missing. Install it first:" >&2
  echo "  curl -LsSf https://astral.sh/uv/install.sh | UV_INSTALL_DIR=/usr/local/bin sh" >&2
  exit 1
fi

cd "$APP_DIR"
sudo -u vnclub bun install --frozen-lockfile
sudo -u vnclub bun run build
sudo -u vnclub mkdir -p public/vndb-images

cd "$BACKEND_DIR"
sudo -u vnclub uv python install 3.11
sudo -u vnclub uv venv --python 3.11 .venv
sudo -u vnclub uv pip install \
  --python .venv/bin/python --require-hashes -r requirements.lock
sudo -u vnclub uv pip install \
  --python .venv/bin/python --no-deps -r requirements-vcs.txt

for f in "$REPO_DIR"/deploy/native/systemd/*.service \
         "$REPO_DIR"/deploy/native/systemd/*.timer; do
  install -m 644 "$f" /etc/systemd/system/
done
chmod +x "$REPO_DIR"/deploy/native/scripts/*.sh
chmod +x "$REPO_DIR"/deploy/native/scripts/*.py
systemctl daemon-reload

for name in api.env web.env heavy.env; do
  if [ ! -e "/etc/vnclub/$name" ]; then
    install -m 640 -o root -g vnclub \
      "$REPO_DIR/deploy/native/env/$name.example" "/etc/vnclub/$name"
  fi
done

cat <<EOF

Prepared. Remaining manual steps:
  1. Install PostgreSQL 15 and Redis (see deploy/native/SETUP.md).
  2. Edit /etc/vnclub/api.env, web.env and heavy.env.
  3. Create the vndb role/database and apply the pg_hba rule.
  4. Set the Redis requirepass and include /etc/redis/vnclub.conf.
  5. Restore the database and image cache.
  6. Restart Angie so its worker picks up the vnclub supplementary group.
  7. systemctl enable --now vnclub-api.service vnclub-web.service
EOF
