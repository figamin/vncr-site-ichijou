# Native lean core

For the complete two-host walkthrough, including the remote worker, start with
[`SETUP.md`](SETUP.md). This file is the shorter reference for the layout and
individual pieces.

This is the Docker-free deployment for a small host. It keeps only the services
needed to serve the site:

- Angie (already on the host)
- Next.js / Bun web
- FastAPI (one Uvicorn worker)
- PostgreSQL 15
- Redis

Umami analytics is not part of the lean core. The site works without it; if you
still want it, run the existing Umami container/service separately and export
its `NEXT_PUBLIC_UMAMI_*` values before building the frontend.

The heavy worker runs on a faster remote machine over SSH. The remote worker
connects back to this host's PostgreSQL and Redis through SSH remote forwards,
so the database is never copied for a normal update.

This layout typically uses about 1.2–1.5 GiB of core memory; the systemd
`MemoryMax` values are ceilings, not reservations. It avoids Docker images and
build cache entirely.

## Layout

```text
/srv/vnclub                         git checkout + .next build
/srv/vnclub/public/vndb-images      image cache (Angie reads it directly)
/srv/vnclub/vndb-stats-backend      FastAPI source
/srv/vnclub/vndb-stats-backend/.venv
/srv/vnclub/dumps                   VNDB dump storage
/etc/vnclub/api.env                 FastAPI environment
/etc/vnclub/web.env                 Next/Bun environment
/etc/vnclub/heavy.env               local SSH/remote-worker settings
/var/backups/vnclub                 PostgreSQL dumps
```

## 1. Packages

Debian 13's default PostgreSQL is newer than the PG15 the application was
developed against. The safest choice is PostgreSQL 15 from PGDG.

```bash
sudo apt-get update
sudo apt-get install -y \
  curl ca-certificates git rsync build-essential libpq-dev libgomp1 \
  postgresql-client redis-server fonts-noto-cjk

# PGDG PostgreSQL 15
sudo install -d /usr/share/postgresql-common/pgdg
curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
  | sudo gpg --dearmor -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg
echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg] https://apt.postgresql.org/pub/repos/apt trixie-pgdg main" \
  | sudo tee /etc/apt/sources.list.d/pgdg.list
sudo apt-get update
sudo apt-get install -y postgresql-15 postgresql-contrib-15
```

If you deliberately test against Debian's PostgreSQL 17, use that cluster
instead and make sure the schema setup and dump restore pass before going live.

Bun is already present on the target host (`/usr/local/bin/bun`). If not:

```bash
curl -fsSL https://bun.sh/install | sudo BUN_INSTALL=/opt/bun bash
sudo ln -sf /opt/bun/bin/bun /usr/local/bin/bun
```

`uv` is used to install a pinned Python 3.11 environment without touching the
system Python:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh
```

## 2. Service user and directories

```bash
sudo useradd --system --user-group --home /srv/vnclub --shell /usr/sbin/nologin vnclub
sudo install -d -o vnclub -g vnclub /srv/vnclub
sudo install -d -m 750 -o root -g vnclub /etc/vnclub
sudo install -d -m 750 -o postgres -g postgres /var/backups/vnclub
```

Check out the repository as `vnclub` **before** creating the runtime
subdirectories, because `git clone` needs an empty target:

```bash
sudo -u vnclub git clone <repo-url> /srv/vnclub
# or, if /srv/vnclub already contains the checkout:
sudo chown -R vnclub:vnclub /srv/vnclub

sudo install -d -o vnclub -g vnclub /srv/vnclub/dumps
sudo install -d -o vnclub -g vnclub /srv/vnclub/public/vndb-images
sudo install -d -o vnclub -g vnclub /srv/vnclub/vndb-stats-backend/logs
```

If `/srv/vnclub` already contains unrelated files, clone elsewhere and copy
the checkout into place rather than trying to clone over them.

Angie must be able to read images. Add its worker group to the `vnclub` group
and make image files group-readable:

```bash
# Use the account Angie actually runs as: `www-data` on Debian, or `angie`.
sudo usermod -aG vnclub www-data
sudo systemctl restart angie
sudo chmod -R g+rX /srv/vnclub/public/vndb-images
sudo find /srv/vnclub/public/vndb-images -type d -exec chmod g+s {} +
```

The setgid bit makes new image directories inherit the `vnclub` group, so Angie
keeps reading files the web process creates.

## 3. PostgreSQL

Create the role and database. Use a strong password and keep it in
`/etc/vnclub/api.env` and the remote worker's environment.

```bash
sudo -u postgres psql <<'SQL'
CREATE ROLE vndb LOGIN PASSWORD 'CHANGE_ME';
CREATE DATABASE vndb_stats OWNER vndb;
SQL
```

Install the native tuning file:

```bash
sudo install -m 644 deploy/native/postgres/tuning.conf \
  /etc/postgresql/15/main/conf.d/vnclub.conf
sudo systemctl restart postgresql
```

The tuning assumes a 2–4 GiB host: `shared_buffers=256MB`, `max_connections=80`
(API, optional bot, and remote worker pools), `work_mem=8MB`. Raise
`shared_buffers` only if you have real free RAM.

Allow the app to connect over loopback with a password. In
`/etc/postgresql/15/main/pg_hba.conf`, before the default `host` lines:

```text
host    vndb_stats    vndb    127.0.0.1/32    scram-sha-256
```

Then reload:

```bash
sudo systemctl reload postgresql
```

## 4. Redis

```bash
sudo install -m 640 -o redis -g redis deploy/native/redis/vnclub.conf \
  /etc/redis/vnclub.conf
echo 'include /etc/redis/vnclub.conf' | sudo tee -a /etc/redis/redis.conf
sudo systemctl restart redis-server
```

Set `requirepass` in `/etc/redis/vnclub.conf` to the same password used in
`REDIS_URL`.

## 5. Python environment

```bash
cd /srv/vnclub/vndb-stats-backend
sudo -u vnclub uv python install 3.11
sudo -u vnclub uv venv --python 3.11 .venv
sudo -u vnclub uv pip install --python .venv/bin/python --require-hashes -r requirements.lock
sudo -u vnclub uv pip install --python .venv/bin/python --no-deps -r requirements-vcs.txt
```

## 6. Frontend build

```bash
cd /srv/vnclub
sudo -u vnclub bun install --frozen-lockfile
sudo -u vnclub bun run build
sudo -u vnclub mkdir -p public/vndb-images
```

`NEXT_PUBLIC_*` values are baked at build time. If you use Umami or a public API
origin, export those before `bun run build`; the browser backend base remains
the same-origin `/api/backend`.

## 7. Environment files

```bash
sudo cp deploy/native/env/api.env.example /etc/vnclub/api.env
sudo cp deploy/native/env/web.env.example /etc/vnclub/web.env
sudo cp deploy/native/env/heavy.env.example /etc/vnclub/heavy.env
sudo chown root:vnclub /etc/vnclub/*.env
sudo chmod 640 /etc/vnclub/*.env
```

Fill in at least:

- `DATABASE_URL`
- `REDIS_URL`
- `VNDB_API_TOKEN`
- `FRONTEND_URL`
- `BLACKLIST_REFRESH_SECRET`
- `HEAVY_SSH_HOST` and the remote settings in `heavy.env`

## 8. Migrate data from Docker

On the host that already has the populated Docker database:

```bash
sudo install -d -m 750 -o postgres -g postgres /var/backups/vnclub
docker exec vndb-stats-backend-db-1 \
  pg_dump -U vndb -Fc vndb_stats > /var/backups/vnclub/vndb_stats.dump

# On the native host, after the role/database exist:
sudo -u postgres pg_restore --clean --if-exists -d vndb_stats \
  /var/backups/vnclub/vndb_stats.dump

# Image cache (transfer the file over SSH if the hosts differ):
sudo rsync -a public/vndb-images/ /srv/vnclub/public/vndb-images/
sudo chown -R vnclub:vnclub /srv/vnclub/public/vndb-images
sudo chmod -R g+rX /srv/vnclub/public/vndb-images
```

The database is roughly 5.3 GiB. Check `df -h /srv /var` before restoring:
the database, image cache, and seven daily dumps all share the host disk. If
you use a newer PostgreSQL than 15, restore with `--no-owner` and verify the
schema setup before starting the API.

### Cutover from Docker

The native services need ports 3000 (web), 8000 (API), 5432 (PostgreSQL), and
6379 (Redis). Stop the compose stack before enabling the native units, or the
new services will fail to bind:

```bash
cd /srv/vnclub
docker compose -f docker-compose.prod.yml --env-file deploy/.env.prod down
```

Keep the database volume until the native restore has been verified. Angie
keeps serving the same `vnclub_app` upstream on `127.0.0.1:3000`; no vhost
change is needed beyond the image alias above.

## 9. systemd

Install the units:

```bash
sudo install -m 644 deploy/native/systemd/vnclub-api.service /etc/systemd/system/
sudo install -m 644 deploy/native/systemd/vnclub-web.service /etc/systemd/system/
sudo install -m 644 deploy/native/systemd/vnclub-heavy.service /etc/systemd/system/
sudo install -m 644 deploy/native/systemd/vnclub-heavy.timer /etc/systemd/system/
sudo install -m 644 deploy/native/systemd/vnclub-bot.service /etc/systemd/system/
sudo install -m 644 deploy/native/systemd/vnclub-backup.service /etc/systemd/system/
sudo install -m 644 deploy/native/systemd/vnclub-backup.timer /etc/systemd/system/
sudo chmod +x deploy/native/scripts/*.sh deploy/native/scripts/*.py
sudo systemctl daemon-reload
```

Enable the core:

```bash
sudo systemctl enable --now vnclub-api.service
sudo systemctl enable --now vnclub-web.service
```

The API unit runs `scripts/prestart.sh` first: schema setup and data migrations.
It is safe to run on every start; the schema setup takes an advisory lock.

The bot is optional:

```bash
sudo systemctl enable --now vnclub-bot.service
```

The remote heavy task is a daily timer. Enable it only after the remote host is
configured and the SSH key works:

```bash
sudo systemctl enable --now vnclub-heavy.timer
```

Backups:

```bash
sudo systemctl enable --now vnclub-backup.timer
```

Smoke-test the core before touching Angie:

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/health/db
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3000/
```

## 10. Angie

The checked-in `39chan.conf` uses the native image path:

```nginx
alias /srv/vnclub/public/vndb-images/$1$vnclub_img_width_suffix.webp;
```

Keep the `X-Served-By: angie-static` header and the
`error_page 404 = @vnclub_app` fallback. The Docker compose layout binds the
same host directory, so this path works for both deployments as long as the
checkout is at `/srv/vnclub`.

If the old deployment kept covers in a separate `/srv/vnclub/vndb-images`
directory, copy them into the checkout before switching the alias:

```bash
sudo rsync -a /srv/vnclub/vndb-images/ /srv/vnclub/public/vndb-images/
sudo chown -R vnclub:vnclub /srv/vnclub/public/vndb-images
sudo chmod -R g+rX /srv/vnclub/public/vndb-images
```

Apply the config change with `sudo angie -t && sudo systemctl reload angie`.

The native web listens on `127.0.0.1:3000`; the API on `127.0.0.1:8000`. If
those ports are already used by the other sites on the host, change the ports
in `web.env`/the API unit, update the Angie upstream, and keep
`API_URL_INTERNAL` in sync with the API port.

## 11. Remote heavy worker

The remote machine needs:

- the same repository checkout at the same path
- the Python venv and the same `requirements.lock`
- `/srv/vnclub/dumps`
- `/srv/vnclub/public/vndb-images` for the worker's own cover cache
- its own `/etc/vnclub/heavy.env` with tunnel addresses:
  - `DATABASE_URL=postgresql+asyncpg://vndb:...@127.0.0.1:15432/vndb_stats`
  - `REDIS_URL=redis://:...@127.0.0.1:16379/0`
  - `DUMP_STORAGE_PATH=/srv/vnclub/dumps`
  - `VNDB_API_TOKEN`, `FRONTEND_URL`, etc.
- an SSH key that can log in from this host without a password

`/etc/vnclub/heavy.env` exists on both machines, but they are different files:
the web host's copy only selects the SSH target and task; the remote copy is
sourced by the remote shell and supplies `DATABASE_URL`, `REDIS_URL`, and the
worker settings.

### 11.1 Create the SSH key on the web host

The heavy unit runs as `vnclub` with `HOME=/srv/vnclub`, so the key must live
there:

```bash
sudo install -d -m 700 -o vnclub -g vnclub /srv/vnclub/.ssh
sudo -u vnclub ssh-keygen -t ed25519 -N '' -f /srv/vnclub/.ssh/id_ed25519
sudo cat /srv/vnclub/.ssh/id_ed25519.pub
```

Copy the printed public key. If the remote account already has a password or a
working key, `ssh-copy-id` is also fine:

```bash
sudo -u vnclub ssh-copy-id -i /srv/vnclub/.ssh/id_ed25519.pub vnclub@heavy.example
```

### 11.2 Prepare the remote host

Log in to the remote machine as root (or with sudo). It needs Python 3.11, the
same checkout path, a venv, and a remote environment file:

```bash
sudo apt-get update
sudo apt-get install -y git build-essential libpq-dev libgomp1

# Same path as the web host; the dispatcher resolves the backend relative to
# deploy/native/scripts/heavy_task.py. This account needs a login shell because
# SSH runs the command non-interactively; the web host's service account can
# remain nologin.
sudo useradd --system --user-group --home /srv/vnclub --shell /bin/bash vnclub
sudo install -d -o vnclub -g vnclub /srv/vnclub
sudo -u vnclub git clone <repo-url> /srv/vnclub
sudo install -d -o vnclub -g vnclub /srv/vnclub/dumps /srv/vnclub/public/vndb-images

# Authorize the public key printed in 11.1 (replace the placeholder line).
sudo install -d -m 700 -o vnclub -g vnclub /srv/vnclub/.ssh
sudo -u vnclub tee /srv/vnclub/.ssh/authorized_keys >/dev/null <<'KEY'
ssh-ed25519 AAAA... vnclub@vnclub-web
KEY
sudo chmod 600 /srv/vnclub/.ssh/authorized_keys

# uv is the same installer as on the web host.
curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh

cd /srv/vnclub/vndb-stats-backend
sudo -u vnclub uv python install 3.11
sudo -u vnclub uv venv --python 3.11 .venv
sudo -u vnclub uv pip install --python .venv/bin/python \
  --require-hashes -r requirements.lock
sudo -u vnclub uv pip install --python .venv/bin/python \
  --no-deps -r requirements-vcs.txt

sudo install -d -m 750 -o root -g vnclub /etc/vnclub
sudo install -m 640 -o root -g vnclub \
  /srv/vnclub/deploy/native/env/heavy.worker.example \
  /etc/vnclub/heavy.env
```

Fill in the remote `/etc/vnclub/heavy.env` with the tunnel addresses:

```text
DATABASE_URL=postgresql+asyncpg://vndb:CHANGE_ME@127.0.0.1:15432/vndb_stats
REDIS_URL=redis://:CHANGE_ME@127.0.0.1:16379/0
DUMP_STORAGE_PATH=/srv/vnclub/dumps
VNDB_API_TOKEN=...
FRONTEND_URL=https://vnclub.figamin.net
```

The remote `sshd` must allow TCP forwarding (the Debian/Ubuntu default is
`AllowTcpForwarding yes`). The forwards listen on the remote loopback only,
which is what the `-R` flags in `run-heavy-remote.sh` request.

The web host's PostgreSQL must accept the password login from `127.0.0.1`
(section 3), and Redis must be bound to `127.0.0.1` with the same password
(section 4). No extra firewall rule is needed because the traffic travels
inside SSH.

### 11.3 Configure the local worker settings

On the web host, `/etc/vnclub/heavy.env` selects the SSH target and task:

```text
HEAVY_SSH_HOST=vnclub@heavy.example
HEAVY_WORKDIR=/srv/vnclub/vndb-stats-backend
HEAVY_PYTHON=.venv/bin/python
HEAVY_TASK=daily
```

The defaults from `deploy/native/env/heavy.env.example` already match the
remote layout; only `HEAVY_SSH_HOST` normally needs editing. Keep the file
mode `640 root:vnclub` so the heavy service can read it.

`run-heavy-remote.sh` opens remote forwards:

```text
remote 15432 -> this host 127.0.0.1:5432
remote 16379 -> this host 127.0.0.1:6379
```

and runs the selected task on the remote host. The database and Redis stay on
this host; the remote machine only needs CPU and dump space.

### 11.4 Test the tunnel

Pre-populate `known_hosts`, verify BatchMode login, then run the lightest task
before scheduling the full daily job:

```bash
ssh-keyscan -H heavy.example | sudo -u vnclub tee -a /srv/vnclub/.ssh/known_hosts
sudo -u vnclub ssh -T -o BatchMode=yes vnclub@heavy.example 'echo ok'

sudo sed -i 's/^HEAVY_TASK=.*/HEAVY_TASK=similarity/' /etc/vnclub/heavy.env
sudo systemctl start --no-block vnclub-heavy.service
journalctl -u vnclub-heavy.service -f
sudo sed -i 's/^HEAVY_TASK=.*/HEAVY_TASK=daily/' /etc/vnclub/heavy.env
```

If the remote run cannot reach PostgreSQL, check the local `pg_hba.conf` rule,
the database password, and whether the SSH session actually opened the
forwards (`ExitOnForwardFailure=yes` aborts when the remote port is busy).

Available tasks:

- `daily`: full import, model/similarity rebuild, leaderboards
- `similarity`: model/similarity rebuild only
- `leaderboards`: leaderboard rebuild only

Run it manually to test:

```bash
sudo systemctl start vnclub-heavy.service
journalctl -u vnclub-heavy.service -f
```

The timer runs it daily at 04:00 with a randomized delay.

Note: the remote worker's `VNDB_CACHE_DIR` is remote. Covers generated there
are not the ones Angie serves. The site's image route fetches missing covers on
demand; if you want the remote news covers on the web host, mount the image
cache over SSHFS or sync it after the run.

## 12. Updates

```bash
cd /srv/vnclub
sudo -u vnclub git pull --ff-only
sudo -u vnclub bun install --frozen-lockfile
sudo -u vnclub bun run build
sudo -u vnclub uv pip install --python vndb-stats-backend/.venv/bin/python \
  --require-hashes -r vndb-stats-backend/requirements.lock
sudo -u vnclub uv pip install --python vndb-stats-backend/.venv/bin/python \
  --no-deps -r vndb-stats-backend/requirements-vcs.txt
sudo systemctl restart vnclub-api.service vnclub-web.service
```

For the remote worker, update its checkout and venv in the same way.
