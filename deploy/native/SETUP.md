# Two-host setup: lean production core + heavy worker

This is the end-to-end guide for running VN Club without Docker:

- **Core host** — public Angie, Next/Bun, FastAPI, PostgreSQL 15, Redis.
- **Worker host** — no public services; runs the heavy Python jobs over SSH.

The database and Redis stay on the core host. The core opens SSH **remote
forwards** on the worker, so the worker reaches the core's loopback PostgreSQL
and Redis without exposing either service to the network.

```text
Internet
   |
   | 80/443
   v
+--------------------------+          SSH + remote forwards          +----------------------+
| Core host                |  core -> worker:22                      | Worker host          |
|                          |  worker:15432 -> core:127.0.0.1:5432   |                      |
| Angie                    |  worker:16379 -> core:127.0.0.1:6379   | Python 3.11 venv     |
| Next/Bun   127.0.0.1:3000|  ------------------------------------>  | heavy_task.py        |
| FastAPI    127.0.0.1:8000|                                         | /srv/vnclub/dumps    |
| PostgreSQL 127.0.0.1:5432|                                         | no Postgres/Redis    |
| Redis      127.0.0.1:6379|                                         | no Bun               |
+--------------------------+                                         +----------------------+
```

## Assumptions and placeholders

- Both machines run Debian 12 or 13 (x86-64).
- You have root or passwordless sudo.
- The repository is checked out at `/srv/vnclub` on both hosts.
- The public domain below is a placeholder:

```bash
DOMAIN=vnclub.example.com
CORE_IP=<core-public-ip>
WORKER_HOST=vnclub@worker.example.com
REPO_URL=<repo-url>
```

Set these in your shell (or replace the placeholders in each command) before
running the examples. The worker SSH account is `vnclub`; if you use a
different account, adjust `WORKER_HOST`, the worker user, and group ownership.

Sizing:

| Service | Practical minimum |
|---|---|
| Core | 2 vCPU, 2.4 GiB RAM + swap, 35+ GiB free |
| Worker | 4+ vCPU, 8 GiB RAM, disk for dumps and the Python venv |

The worker is the only host that needs enough RAM for the similarity/model
rebuild. Do not run `vnclub-heavy.service` on the core host.

On a 39 GiB core disk, do not keep seven full database dumps locally. Move
backups off-host or reduce the retention after the first successful backup.

---

## 0. Before wiping the old host

If you are reinstalling the machine that currently runs the Docker deployment,
copy everything you need off the host first.

```bash
# On the old host, from the repo root.
sudo install -d -m 750 -o postgres -g postgres /var/backups/vnclub

# Database (adjust the container name if yours differs)
docker exec vndb-stats-backend-db-1 \
  pg_dump -U vndb -Fc vndb_stats > /var/backups/vnclub/vndb_stats.dump

# Images and configuration. Set IMAGE_DIR to whatever the current compose
# mounts; it may be public/vndb-images or a separate vndb-images directory.
IMAGE_DIR=${IMAGE_DIR:-public/vndb-images}
sudo rsync -a "$IMAGE_DIR/" /var/backups/vnclub/vndb-images/

sudo tar -C / -czf /var/backups/vnclub/host-config.tgz \
  etc/angie etc/letsencrypt \
  srv/vnclub/deploy/.env.prod \
  srv/vnclub/39chan.conf
```

Copy `/var/backups/vnclub` to another machine before reinstalling. Verify the
dump and the image directory before wiping anything.

If the old host is not being wiped, keep the Docker stack stopped once the
native core is ready; both cannot bind ports 3000 and 8000.

---

## 1. Core host: base Debian

Start from a minimal Debian installation with SSH enabled. Do not install a
desktop, Docker, MariaDB, or a second web stack.

```bash
sudo apt-get update
sudo apt-get upgrade -y
sudo apt-get install -y \
  curl ca-certificates gnupg git rsync unzip openssl \
  build-essential libpq-dev libgomp1 \
  redis-server fonts-noto-cjk certbot

sudo timedatectl set-timezone UTC
```

### Swap

On a 2.4 GiB core host, make sure swap exists. If `swapon --show` is empty:

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-vnclub.conf
sudo sysctl --system
```

Swap is a safety net for build and query spikes, not a substitute for RAM.

### PostgreSQL 15 from PGDG

Debian 13 ships a newer PostgreSQL by default. Use PGDG PostgreSQL 15:

```bash
. /etc/os-release
PGDG_SUITE="${VERSION_CODENAME}-pgdg"

sudo install -d /usr/share/postgresql-common/pgdg
curl -fsSL https://www.postgresql.org/media/keys/ACCC4CF8.asc \
  | sudo gpg --dearmor \
      -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg

echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.gpg] \
https://apt.postgresql.org/pub/repos/apt ${PGDG_SUITE} main" \
  | sudo tee /etc/apt/sources.list.d/pgdg.list

sudo apt-get update
sudo apt-get install -y postgresql-15 postgresql-contrib-15
```

If you deliberately use a different PostgreSQL version, adjust the cluster path
`/etc/postgresql/15/main` throughout this guide and test the dump restore first.

### Bun and uv

```bash
curl -fsSL https://bun.sh/install | sudo BUN_INSTALL=/opt/bun bash
sudo ln -sf /opt/bun/bin/bun /usr/local/bin/bun

curl -LsSf https://astral.sh/uv/install.sh \
  | sudo env UV_INSTALL_DIR=/usr/local/bin sh
```

Verify:

```bash
bun --version
uv --version
```

### Angie

If Angie is already installed, skip this block. Otherwise add the official
Debian repository:

```bash
sudo curl -o /etc/apt/trusted.gpg.d/angie-signing.gpg \
  https://angie.software/keys/angie-signing.gpg

echo "deb https://download.en.angie.software/angie/$(. /etc/os-release && echo "$ID/$VERSION_ID $VERSION_CODENAME") main" \
  | sudo tee /etc/apt/sources.list.d/angie.list

sudo apt-get update
sudo apt-get install -y angie

sudo systemctl enable angie
```

### TLS certificates

The vhost expects certificates at:

```text
/etc/letsencrypt/live/$DOMAIN/fullchain.pem
/etc/letsencrypt/live/$DOMAIN/privkey.pem
```

Restore the existing `/etc/letsencrypt` directory from the old host when
possible. If you are issuing certificates from scratch, use your preferred
ACME method, then verify renewal:

```bash
sudo certbot renew --dry-run
```

Do not start Angie with the final TLS vhost until the certificate paths exist.

### Firewall

Allow only SSH and the web ports. Example with ufw; adapt if you use
nftables directly:

```bash
sudo apt-get install -y ufw
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

Make sure your SSH port is allowed before enabling the firewall, or you will
lock yourself out.

Do **not** expose 3000, 8000, 5432, or 6379. They stay on loopback and are
reached by Angie/Next or by the SSH tunnel.

---

## 2. Core host: application tree

The repository must live at `/srv/vnclub` because the systemd units hard-code
that path.

```bash
sudo install -d -o root -g root /srv/vnclub
sudo git clone "$REPO_URL" /srv/vnclub
```

If the checkout already exists, skip the clone. `prepare-app.sh` creates the
service user and chowns the tree.

Then create the service user, directories, frontend build, Python venv, and
systemd units:

```bash
sudo bash /srv/vnclub/deploy/native/scripts/prepare-app.sh
```

`prepare-app.sh` does **not** install apt packages or start PostgreSQL, Redis, or
Angie. It:

- creates the `vnclub` service user and `/srv/vnclub` runtime directories;
- adds the Angie user to the `vnclub` group;
- runs `bun install --frozen-lockfile` and `bun run build`;
- creates `vndb-stats-backend/.venv` with Python 3.11;
- installs the systemd units;
- copies the env examples if they do not exist.

On a 2.4 GiB core host, the frontend build is the most likely place to hit the
RAM ceiling. Keep the 4 GiB swap enabled, or build on a larger machine and copy
`.next` and `node_modules` over. A failed build can be retried without
recreating the tree:

```bash
cd /srv/vnclub
sudo -u vnclub bun install --frozen-lockfile
sudo -u vnclub bun run build
```

Make the image directory readable by Angie:

```bash
sudo usermod -aG vnclub www-data     # or the account Angie actually runs as
sudo chmod -R g+rX /srv/vnclub/public/vndb-images
sudo find /srv/vnclub/public/vndb-images -type d -exec chmod g+s {} +
```

Check the Angie account with:

```bash
ps -o user= -C angie
```

Restart Angie later, after the vhost is installed, so it picks up the new
supplementary group.

---

## 3. Core host: PostgreSQL

Create the role and database. Use a long random password:

```bash
openssl rand -hex 24
```

```bash
sudo -u postgres psql
```

```sql
CREATE ROLE vndb LOGIN PASSWORD 'PASTE_PASSWORD_HERE';
CREATE DATABASE vndb_stats OWNER vndb;
\q
```

Install the tuning file:

```bash
sudo install -m 644 /srv/vnclub/deploy/native/postgres/tuning.conf \
  /etc/postgresql/15/main/conf.d/vnclub.conf
sudo systemctl restart postgresql
```

Add the loopback password rule before the default `host` lines in
`/etc/postgresql/15/main/pg_hba.conf`:

```text
host    vndb_stats    vndb    127.0.0.1/32    scram-sha-256
```

Reload and verify:

```bash
sudo systemctl reload postgresql
pg_isready -h 127.0.0.1 -p 5432
```

The tuning sets `listen_addresses = '127.0.0.1'` and
`max_connections = 80`. The API, optional bot, and remote worker pools fit
inside that budget.

---

## 4. Core host: Redis

Install the native Redis fragment:

```bash
sudo install -m 640 -o redis -g redis \
  /srv/vnclub/deploy/native/redis/vnclub.conf \
  /etc/redis/vnclub.conf

echo 'include /etc/redis/vnclub.conf' \
  | sudo tee -a /etc/redis/redis.conf

sudo systemctl enable --now redis-server
```

Edit `/etc/redis/vnclub.conf` and replace `requirepass CHANGE_ME` with the same
password used in `REDIS_URL`:

```bash
sudo editor /etc/redis/vnclub.conf
sudo systemctl restart redis-server
```

Verify:

```bash
redis-cli -a 'PASTE_REDIS_PASSWORD' ping
```

The fragment binds Redis to `127.0.0.1`, sets a 128 MB LRU limit, and enables
AOF persistence.

---

## 5. Core host: environment files

`prepare-app.sh` copied the examples to `/etc/vnclub/`. Edit them:

```bash
sudo editor /etc/vnclub/api.env
sudo editor /etc/vnclub/web.env
```

### `/etc/vnclub/api.env`

Fill in at least:

```dotenv
DATABASE_URL=postgresql+asyncpg://vndb:DB_PASSWORD@127.0.0.1:5432/vndb_stats
REDIS_URL=redis://:REDIS_PASSWORD@127.0.0.1:6379/0
FRONTEND_URL=https://vnclub.example.com
CORS_ORIGINS=["https://vnclub.example.com"]
VNDB_API_TOKEN=...
DUMP_STORAGE_PATH=/srv/vnclub/dumps
VNDB_CACHE_DIR=/srv/vnclub/public/vndb-images
BLACKLIST_REFRESH_SECRET=...
ADMIN_API_KEY=...
```

`VNDB_API_TOKEN` is required for `/stats/<username>` lookups. Use hex passwords
from `openssl rand -hex 24` so they do not need URL escaping.

### `/etc/vnclub/web.env`

```dotenv
NODE_ENV=production
PORT=3000
HOSTNAME=127.0.0.1
API_URL_INTERNAL=http://127.0.0.1:8000
VNDB_CACHE_DIR=/srv/vnclub/public/vndb-images
BLACKLIST_REFRESH_SECRET=...
NEXT_TELEMETRY_DISABLED=1
```

`BLACKLIST_REFRESH_SECRET` must match the API value.

If you use Umami, export its `NEXT_PUBLIC_UMAMI_*` values and rebuild the
frontend after editing `web.env`; they are baked into the client bundle. Umami
itself is not part of the lean core.

Lock down the files:

```bash
sudo chown root:vnclub /etc/vnclub/*.env
sudo chmod 640 /etc/vnclub/*.env
```

---

## 6. Core host: restore the database and images

Copy the old dump and image directory to the core host, then:

```bash
sudo -u postgres pg_restore --clean --if-exists \
  -d vndb_stats /path/to/vndb_stats.dump

sudo rsync -a /path/to/vndb-images/ /srv/vnclub/public/vndb-images/
sudo chown -R vnclub:vnclub /srv/vnclub/public/vndb-images
sudo chmod -R g+rX /srv/vnclub/public/vndb-images
sudo find /srv/vnclub/public/vndb-images -type d -exec chmod g+s {} +
```

Verify:

```bash
sudo -u postgres psql -d vndb_stats \
  -c 'SELECT count(*) FROM visual_novels;'
find /srv/vnclub/public/vndb-images -type f | wc -l
df -h /srv
```

If this is a fresh deployment, skip the restore. The API's prestart step will
create the schema, and the image route will populate covers on demand.

Keep at least 5–8 GiB free on `/srv`. If you keep database dumps on the same
disk, reduce the backup retention or send them off-host.

---

## 7. Core host: start the services

If the Docker stack is still running on this host, stop it first; native and
Docker cannot both bind 3000, 8000, 5432, and 6379:

```bash
cd /srv/vnclub
docker compose -f docker-compose.prod.yml --env-file deploy/.env.prod down
```

Keep the volumes until the native restore and smoke tests pass. After that, old
Docker images and volumes can be removed to reclaim disk.

Then start the native units:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now vnclub-api.service
sudo systemctl enable --now vnclub-web.service

sudo systemctl status vnclub-api.service --no-pager
sudo systemctl status vnclub-web.service --no-pager
```

The first API start runs `prestart.sh`: schema setup and data migrations. It has
a 600-second start timeout.

Smoke-test before touching Angie:

```bash
curl -fsS http://127.0.0.1:8000/health
curl -fsS http://127.0.0.1:8000/health/db
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3000/
```

All four ports should be loopback-only:

```bash
sudo ss -ltnp | grep -E ':(3000|8000|5432|6379)\b'
```

---

## 8. Core host: Angie

Install the checked-in vhost into your Angie vhost directory, typically:

```bash
sudo install -m 644 /srv/vnclub/39chan.conf \
  /etc/angie/conf.d/39chan.conf
```

Update these values for the real host:

- `server_name`
- certificate paths
- the `alias` path, which should be
  `/srv/vnclub/public/vndb-images/$1$vnclub_img_width_suffix.webp`

Test and reload:

```bash
sudo angie -t
sudo systemctl restart angie
```

Verify the public site:

```bash
curl -I "https://$DOMAIN/"
curl -I "https://$DOMAIN/img/cv/00/1.webp"
```

Once the file exists in `/srv/vnclub/public/vndb-images`, the image response
should include:

```text
X-Served-By: angie-static
```

If the image is missing, Angie falls back to the Next image route, which
generates it on demand; the first request may therefore be served by Next, and
the second by Angie.

Enable the backup timer:

```bash
sudo systemctl enable --now vnclub-backup.timer
```

The bot is optional:

```bash
sudo systemctl enable --now vnclub-bot.service
```

Do **not** enable the heavy timer before the worker is ready.

---

## 9. Worker host: base Debian and account

The worker does not need PostgreSQL, Redis, Angie, Docker, or Bun. It needs
Python 3.11, git, build tools, and enough RAM/disk for the import.

```bash
sudo apt-get update
sudo apt-get upgrade -y
sudo apt-get install -y \
  curl ca-certificates git rsync build-essential \
  libpq-dev libgomp1 fonts-noto-cjk

sudo timedatectl set-timezone UTC
```

Create a login-capable service account. It needs a real shell because SSH runs
the dispatcher non-interactively:

```bash
sudo useradd --system --user-group \
  --home /srv/vnclub --shell /bin/bash vnclub

# If your sshd/PAM policy rejects system accounts, create this as a normal
# login user instead; keep the same home and shell.
```

```bash
sudo install -d -o vnclub -g vnclub /srv/vnclub
sudo -u vnclub git clone "$REPO_URL" /srv/vnclub

sudo install -d -o vnclub -g vnclub \
  /srv/vnclub/dumps \
  /srv/vnclub/public/vndb-images \
  /srv/vnclub/vndb-stats-backend/logs
```

The repository must be at the same path as on the core host. The dispatcher
resolves the backend from `deploy/native/scripts/heavy_task.py`.

Install `uv`:

```bash
curl -LsSf https://astral.sh/uv/install.sh \
  | sudo env UV_INSTALL_DIR=/usr/local/bin sh
```

---

## 10. Worker host: SSH access from the core

On the **core** host, create the key as `vnclub`:

```bash
sudo install -d -m 700 -o vnclub -g vnclub /srv/vnclub/.ssh
sudo -u vnclub ssh-keygen -t ed25519 -N '' \
  -f /srv/vnclub/.ssh/id_ed25519
sudo cat /srv/vnclub/.ssh/id_ed25519.pub
```

Copy the public key output.

On the **worker** host, authorize it:

```bash
sudo install -d -m 700 -o vnclub -g vnclub /srv/vnclub/.ssh
sudo -u vnclub tee /srv/vnclub/.ssh/authorized_keys >/dev/null <<'KEY'
ssh-ed25519 AAAA... vnclub@vnclub-core
KEY
sudo chmod 600 /srv/vnclub/.ssh/authorized_keys
```

Back on the **core** host, record the worker's host key and test BatchMode:

```bash
ssh-keyscan -H "${WORKER_HOST#*@}" \
  | sudo -u vnclub tee -a /srv/vnclub/.ssh/known_hosts

sudo -H -u vnclub ssh -T -o BatchMode=yes "$WORKER_HOST" 'echo ok'
```

If you use a nonstandard SSH port, add `-p PORT` to `ssh-keyscan` and configure
the port in `/srv/vnclub/.ssh/config`.

The worker's `sshd` must allow TCP forwarding; the Debian default is
`AllowTcpForwarding yes`. Confirm it and keep `GatewayPorts no` so the forwarded
ports stay on the worker loopback:

```bash
grep -E '^(AllowTcpForwarding|GatewayPorts)' /etc/ssh/sshd_config || true
sudo systemctl reload ssh
```

Allow SSH from the core host only. With ufw, that is something like:

```bash
sudo ufw allow from CORE_IP to any port 22 proto tcp
sudo ufw enable
```

Do not open PostgreSQL or Redis on the worker; neither is installed there.

---

## 11. Worker host: Python environment

```bash
cd /srv/vnclub/vndb-stats-backend

sudo -u vnclub uv python install 3.11
sudo -u vnclub uv venv --python 3.11 .venv
sudo -u vnclub uv pip install \
  --python .venv/bin/python --require-hashes -r requirements.lock
sudo -u vnclub uv pip install \
  --python .venv/bin/python --no-deps -r requirements-vcs.txt
```

Verify:

```bash
/srv/vnclub/vndb-stats-backend/.venv/bin/python -V
```

The worker must be updated with the same `requirements.lock` as the core.

---

## 12. Worker host: `/etc/vnclub/heavy.env`

```bash
sudo install -d -m 750 -o root -g vnclub /etc/vnclub
sudo install -m 640 -o root -g vnclub \
  /srv/vnclub/deploy/native/env/heavy.worker.example \
  /etc/vnclub/heavy.env

sudo editor /etc/vnclub/heavy.env
```

Set:

```dotenv
DATABASE_URL=postgresql+asyncpg://vndb:DB_PASSWORD@127.0.0.1:15432/vndb_stats
REDIS_URL=redis://:REDIS_PASSWORD@127.0.0.1:16379/0
DUMP_STORAGE_PATH=/srv/vnclub/dumps
VNDB_CACHE_DIR=/srv/vnclub/public/vndb-images
VNDB_API_TOKEN=...
FRONTEND_URL=https://vnclub.example.com
```

The `15432` and `16379` ports are **on the worker** and only exist while the
core's SSH session is open. The core's `run-heavy-remote.sh` creates them with:

```text
remote 15432 -> core 127.0.0.1:5432
remote 16379 -> core 127.0.0.1:6379
```

The worker's cover cache is local to the worker. Covers generated there are not
the files Angie serves on the core; the web image route generates missing
covers on demand.

---

## 13. Core host: point at the worker

On the **core** host, edit `/etc/vnclub/heavy.env`:

```dotenv
HEAVY_SSH_HOST=vnclub@worker.example.com
HEAVY_WORKDIR=/srv/vnclub/vndb-stats-backend
HEAVY_PYTHON=.venv/bin/python
HEAVY_TASK=daily
```

The defaults match the layout above; normally only `HEAVY_SSH_HOST` changes.
Keep the file mode at `640 root:vnclub`.

Test the lightest task first:

```bash
sudo sed -i 's/^HEAVY_TASK=.*/HEAVY_TASK=similarity/' \
  /etc/vnclub/heavy.env

sudo systemctl start --no-block vnclub-heavy.service
journalctl -u vnclub-heavy.service -f
```

When it succeeds, restore the daily task and enable the timer:

```bash
sudo sed -i 's/^HEAVY_TASK=.*/HEAVY_TASK=daily/' \
  /etc/vnclub/heavy.env
sudo systemctl enable --now vnclub-heavy.timer
```

Available tasks:

- `daily` — full import, model/similarity rebuild, leaderboards
- `similarity` — model/similarity rebuild only
- `leaderboards` — leaderboard rebuild only

The timer runs daily at 04:00 with up to 15 minutes of randomized delay.

---

## 14. Operations

### Updates

On the core:

```bash
sudo bash /srv/vnclub/deploy/native/scripts/update.sh
```

This pulls the repo, installs Bun dependencies, rebuilds the frontend, updates
the Python venv, and restarts the API and web services. The API's `prestart.sh`
runs migrations on restart.

If the update changes systemd units:

```bash
sudo install -m 644 /srv/vnclub/deploy/native/systemd/*.service \
  /etc/systemd/system/
sudo install -m 644 /srv/vnclub/deploy/native/systemd/*.timer \
  /etc/systemd/system/
sudo systemctl daemon-reload
```

On the worker, update the checkout and venv:

```bash
cd /srv/vnclub
sudo -u vnclub git pull --ff-only

cd /srv/vnclub/vndb-stats-backend
sudo -u vnclub uv pip install \
  --python .venv/bin/python --require-hashes -r requirements.lock
sudo -u vnclub uv pip install \
  --python .venv/bin/python --no-deps -r requirements-vcs.txt
```

### Backups

```bash
systemctl list-timers 'vnclub-*'
sudo systemctl start vnclub-backup.service
ls -lh /var/backups/vnclub
```

The default retention is seven days. On a 39 GB core disk, either reduce the
retention in `deploy/native/scripts/backup.sh` or copy dumps off-host after
each run. Test a restore periodically:

```bash
sudo -u postgres createdb vndb_stats_restore
sudo -u postgres pg_restore -d vndb_stats_restore \
  /var/backups/vnclub/vnclub-YYYYMMDDTHHMMSSZ.dump
sudo -u postgres psql -d vndb_stats_restore \
  -c 'SELECT count(*) FROM visual_novels;'
sudo -u postgres dropdb vndb_stats_restore
```

### Monitoring

```bash
systemctl status vnclub-api vnclub-web
journalctl -u vnclub-api -u vnclub-web -f
systemd-cgtop
free -h
df -h /srv /var
```

PostgreSQL:

```bash
sudo -u postgres psql -c \
  'SELECT state, count(*) FROM pg_stat_activity GROUP BY state;'
```

Redis:

```bash
redis-cli -a 'REDIS_PASSWORD' --no-auth-warning info memory
```

If the API or web service is OOM-killed, check:

```bash
journalctl -k | grep -i -E 'oom|killed process'
systemctl show vnclub-api -p MemoryCurrent
systemctl show vnclub-web -p MemoryCurrent
```

The `MemoryMax` values are ceilings. On a 2.4 GiB host, keep the bot and Umami
off and keep swap enabled.

---

## 15. Troubleshooting

### Public site returns 502

Angie cannot reach `127.0.0.1:3000`.

```bash
curl -v http://127.0.0.1:3000/
sudo systemctl status vnclub-web --no-pager
journalctl -u vnclub-web -n 100 --no-pager
```

Check that `.next` and `node_modules` exist and that the build completed.

### API health fails

```bash
curl -i http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8000/health/db
journalctl -u vnclub-api -n 100 --no-pager
```

Common causes:

- `DATABASE_URL` password mismatch
- missing `pg_hba.conf` loopback rule
- Redis password mismatch
- schema migration failure during `prestart.sh`

Run the prestart script manually as the service user to see the error:

```bash
sudo -u vnclub bash -c \
  'set -a; . /etc/vnclub/api.env; set +a; exec /srv/vnclub/deploy/native/scripts/prestart.sh'
```

### Images return 404 or Angie falls back to Next

```bash
namei -l /srv/vnclub/public/vndb-images/cv/00/1.webp
ps -o user= -C angie
```

Ensure the image directory is group-readable, the setgid bit is set, Angie was
restarted after `usermod`, and the alias in `39chan.conf` points at
`/srv/vnclub/public/vndb-images`.

### Worker cannot reach PostgreSQL or Redis

Check, in order:

1. `sudo -u vnclub ssh -T -o BatchMode=yes vnclub@worker 'echo ok'`
2. `sudo ss -ltnp` on the worker while the heavy service is running; `15432`
   and `16379` should be listening.
3. The worker's `/etc/vnclub/heavy.env` points at `127.0.0.1:15432` and
   `127.0.0.1:16379`.
4. The core's `pg_hba.conf` allows `127.0.0.1/32` for `vndb_stats`.
5. Redis on the core is bound to `127.0.0.1` and uses the same password.
6. `ExitOnForwardFailure=yes` will abort if either remote port is already in
   use.

### Frontend build is OOM-killed

Use the 4 GiB swap, stop the bot, and retry:

```bash
cd /srv/vnclub
sudo -u vnclub bun install --frozen-lockfile
sudo -u vnclub bun run build
```

If it still fails, build on a larger host and copy `.next` and `node_modules`
to the core.

### Disk is filling up

```bash
du -sh /srv/vnclub/* /var/backups/vnclub/* | sort -h
df -h /srv
```

The usual causes are local database dumps, the image cache, and old `.next`
builds. Move backups off-host or lower `RETENTION_DAYS`.

---

## 16. Final checklist

Core host:

- [ ] PostgreSQL 15 running on `127.0.0.1:5432`
- [ ] Redis running on `127.0.0.1:6379` with a password
- [ ] `/etc/vnclub/api.env` and `web.env` filled in and mode `640`
- [ ] database restored and `visual_novels` count checked
- [ ] `curl http://127.0.0.1:8000/health/db` succeeds
- [ ] `curl http://127.0.0.1:3000/` returns a response
- [ ] `angie -t` passes
- [ ] `https://DOMAIN/` works
- [ ] `X-Served-By: angie-static` on a cover image
- [ ] backup timer enabled and a test dump exists

Worker host:

- [ ] SSH BatchMode login from the core works
- [ ] worker venv uses Python 3.11 and the same lock file
- [ ] `/etc/vnclub/heavy.env` points at the tunnel ports
- [ ] `/srv/vnclub/dumps` and `/srv/vnclub/public/vndb-images` exist
- [ ] `similarity` task succeeds
- [ ] `vnclub-heavy.timer` enabled only after the test succeeds
