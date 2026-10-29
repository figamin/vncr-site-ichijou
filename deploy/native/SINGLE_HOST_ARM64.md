# Single-host install on arm64 Debian (no Docker)

This guide installs VN Club on **one 64-bit ARM machine** running Debian 12
(bookworm) or 13 (trixie), with no Docker and no second host:

- Angie (or nginx) as the public reverse proxy
- Next.js served by Bun on `127.0.0.1:3000`
- FastAPI on `127.0.0.1:8000`
- PostgreSQL 15 on `127.0.0.1:5432`
- Redis on `127.0.0.1:6379`
- The heavy import and nightly model rebuild run locally through
  `vnclub-heavy.service`

```text
Internet
   |
   | 80/443
   v
+---------------------------------------------------------------+
| arm64 Debian host                                             |
|                                                               |
| Angie            443  ->  127.0.0.1:3000                      |
|                          -> /srv/vnclub/public/vndb-images    |
| Next.js / Bun    127.0.0.1:3000                               |
| FastAPI          127.0.0.1:8000                               |
| PostgreSQL 15    127.0.0.1:5432   <- API + local heavy job    |
| Redis            127.0.0.1:6379   <- API + local heavy job    |
|                                                               |
| vnclub-heavy.timer (04:00) -> import, models, leaderboards    |
+---------------------------------------------------------------+
```

Only ports 22, 80 and 443 are exposed. Everything else stays on loopback.

If you later want the import to run on a faster machine, keep this host as is
and switch `HEAVY_MODE` to `remote`; see the end of this guide.

---

## 0. Assumptions and sizing

Set these in your shell or replace them in the commands below:

```bash
DOMAIN=vnclub.example.com
REPO_URL=<repo-url>
```

Requirements:

| Item | Minimum | Recommended |
|---|---|---|
| CPU | 2 cores arm64 | 4+ cores |
| RAM | 3.5 GiB + 4 GiB swap | 8 GiB |
| Disk for `/srv` | 20 GiB free | 40 GiB+ |
| Debian | 12 or 13, arm64 | 13 |

The PostgreSQL data directory is roughly 5–8 GiB after the first import. The
cover cache is separate and grows with traffic (and prefetching). Database
dumps used by the import live in `/srv/vnclub/dumps`; they are large and are
re-downloaded when stale, so leave room for them. Backups default to seven
daily dumps in `/var/backups/vnclub`.

A 4 GiB board (Raspberry Pi 5, Rock 5, similar) works: the first import takes
many hours and the Next.js build needs swap, but steady-state serving is light.
An 8 GiB machine halves the import time and removes most of the build risk.

---

## 1. Preflight

Log in as root (or use `sudo`), then run the read-only check:

```bash
uname -m                      # must print aarch64
cat /etc/os-release
sudo bash /srv/vnclub/deploy/native/scripts/check-host.sh
```

The script has to exist before this step. Clone the repository first if it is
not there yet (section 6), then come back. It reports the architecture, Debian
release, memory, swap, free disk, listening ports, and which tools are already
installed. Nothing is changed.

Enable a firewall early, but allow SSH before turning it on:

```bash
sudo apt-get install -y ufw
sudo ufw allow 22/tcp
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
```

Do **not** open 3000, 8000, 5432 or 6379.

---

## 2. Base packages

```bash
sudo apt-get update
sudo apt-get upgrade -y
sudo apt-get install -y \
  curl ca-certificates gnupg git rsync unzip openssl htop \
  build-essential gfortran libopenblas-dev libgomp1 libpq-dev \
  redis-server redis-tools fonts-noto-cjk certbot

sudo timedatectl set-timezone UTC
```

Why the compiler packages on arm64: most dependencies ship `aarch64` wheels,
and the backend pins are hashed in `requirements.lock`, so `uv` normally
installs binaries. If a wheel is unavailable for a pinned version, `uv` builds
from source. `implicit` (used only by the nightly model rebuild) is the most
likely candidate; it needs a C++ compiler with OpenMP (`libgomp1`), Fortran for
BLAS (`gfortran`), and `libopenblas-dev`. Installing them up front costs a few
hundred MB and avoids a failed install at the end of the guide.

Do not install a desktop, Docker, MariaDB, or a second web stack.

### Swap

Even with 8 GiB, a swap file protects the Next build and the import from the
OOM killer. If `swapon --show` is empty:

```bash
sudo fallocate -l 4G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab

echo 'vm.swappiness=10' | sudo tee /etc/sysctl.d/99-vnclub.conf
sudo sysctl --system
```

On a 4 GiB host use 8 GiB of swap instead, and lower `vm.swappiness` to `10`
as above so the kernel prefers dropping page cache over swapping the API.

---

## 3. PostgreSQL 15 from PGDG

Debian 13 ships a newer major version. The application’s migrations and
tooling are developed against PostgreSQL 15, and the PGDG repository publishes
arm64 packages for both bookworm and trixie.

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
apt-cache policy postgresql-15
sudo apt-get install -y postgresql-15 postgresql-contrib-15
```

`apt-cache policy` must show a candidate and an arm64 download URL. If the
repository does not offer PostgreSQL 15 for your release, two options:

- install Debian’s `postgresql` (16 or 17) and use its cluster path
  (`/etc/postgresql/<version>/main`) everywhere this guide says `15`; or
- run a PostgreSQL container elsewhere and point `DATABASE_URL` at it.

Everything below assumes `/etc/postgresql/15/main`.

### Role and database

Generate a URL-safe password (hex never needs escaping):

```bash
DB_PASSWORD=$(openssl rand -hex 24)
REDIS_PASSWORD=$(openssl rand -hex 24)
printf 'db=%s\nredis=%s\n' "$DB_PASSWORD" "$REDIS_PASSWORD"
```

Keep both values; they go into `/etc/vnclub/api.env` and the Redis fragment.

```bash
sudo -u postgres psql <<SQL
CREATE ROLE vndb LOGIN PASSWORD '$DB_PASSWORD';
CREATE DATABASE vndb_stats OWNER vndb;
SQL
```

### Tuning

A single host runs the API, the cache, the site and the nightly import on one
PostgreSQL instance, so use the single-host profile rather than the small
two-host one:

```bash
sudo install -m 644 \
  /srv/vnclub/deploy/native/postgres/tuning-single-host.conf \
  /etc/postgresql/15/main/conf.d/vnclub.conf
sudo systemctl restart postgresql
```

On a 6 GiB host, lower `shared_buffers` to `768MB` and
`effective_cache_size` to `2GB` in that file. On a 4 GiB host, use
`deploy/native/postgres/tuning.conf` instead and keep the heavy job off hours.

### Password login over loopback

In `/etc/postgresql/15/main/pg_hba.conf`, add this line **before** the default
`host` lines:

```text
host    vndb_stats    vndb    127.0.0.1/32    scram-sha-256
```

```bash
sudo systemctl reload postgresql
psql "postgresql://vndb:$DB_PASSWORD@127.0.0.1:5432/vndb_stats" -c 'SELECT 1;'
```

---

## 4. Redis

The checked-in fragment binds Redis to loopback, sets a password, caps memory
and enables AOF persistence. It is written for a shared host, so 128 MB is
plenty for the caches this site uses.

```bash
sudo install -m 640 -o redis -g redis \
  /srv/vnclub/deploy/native/redis/vnclub.conf \
  /etc/redis/vnclub.conf

echo 'include /etc/redis/vnclub.conf' \
  | sudo tee -a /etc/redis/redis.conf

sudo sed -i "s/^requirepass .*/requirepass $REDIS_PASSWORD/" \
  /etc/redis/vnclub.conf

sudo systemctl enable --now redis-server
redis-cli -a "$REDIS_PASSWORD" --no-auth-warning ping
```

The last command must print `PONG`.

On arm64, Debian’s `redis-server` package is compiled natively; no extra steps
are needed.

---

## 5. Bun and uv

Both publish arm64 Linux builds. Bun runs the Next.js server; uv provides a
pinned Python 3.11 without touching the system interpreter.

```bash
curl -fsSL https://bun.sh/install | sudo BUN_INSTALL=/opt/bun bash
sudo ln -sf /opt/bun/bin/bun /usr/local/bin/bun

# The install script ships bunx next to bun; link it explicitly in case an
# older installer left only bun.
BUN_REAL=$(readlink -f /opt/bun/bin/bun)
sudo ln -sf "$(dirname "$BUN_REAL")/bunx" /usr/local/bin/bunx

curl -LsSf https://astral.sh/uv/install.sh \
  | sudo env UV_INSTALL_DIR=/usr/local/bin sh

bun --version
command -v bunx
uv --version
```

`bun --version` must not fail with “Illegal instruction” (that would mean the
wrong architecture binary); `uv --version` must work on arm64.

---

## 6. Application tree

The systemd units hard-code `/srv/vnclub`.

```bash
sudo install -d -o root -g root /srv/vnclub
sudo git clone "$REPO_URL" /srv/vnclub
cd /srv/vnclub
sudo bash deploy/native/scripts/prepare-app.sh
```

`prepare-app.sh` does **not** install apt packages and does **not** start
PostgreSQL, Redis or Angie. It:

- creates the `vnclub` service user and the runtime directories
  (`dumps`, `public/vndb-images`, `vndb-stats-backend/logs`);
- adds the Angie/`www-data` account to the `vnclub` group;
- runs `bun install --frozen-lockfile` and `bun run build`;
- creates the backend venv and installs `requirements.lock`
  (`install-python-deps.sh`, which verifies the imports afterwards);
- installs the systemd units;
- copies the environment examples to `/etc/vnclub/`.

The frontend build is the most memory-hungry step. On a 4 GiB host, stop
everything else first and keep swap enabled. If it is OOM-killed:

```bash
sudo systemctl stop vnclub-api vnclub-web 2>/dev/null || true
cd /srv/vnclub
sudo -u vnclub bun run build
```

The arm64 binaries are already pinned in `bun.lock`
(`@next/swc-linux-arm64-gnu`, `@img/sharp-linux-arm64`,
`@tailwindcss/oxide-linux-arm64-gnu`), so no extra package installation is
needed for the frontend.

Make the image directory readable by the web server. Use the account Angie
actually runs as (`ps -o user= -C angie`); on Debian it is usually `www-data`:

```bash
sudo usermod -aG vnclub www-data
sudo chmod -R g+rX /srv/vnclub/public/vndb-images
sudo find /srv/vnclub/public/vndb-images -type d -exec chmod g+s {} +
```

The setgid bit makes new image directories inherit the `vnclub` group, so
covers written by the site can still be read by Angie.

---

## 7. Environment files

Edit these three files:

```bash
sudo editor /etc/vnclub/api.env
sudo editor /etc/vnclub/web.env
sudo editor /etc/vnclub/heavy.env
```

### `/etc/vnclub/api.env`

Required values:

```dotenv
DATABASE_URL=postgresql+asyncpg://vndb:DB_PASSWORD@127.0.0.1:5432/vndb_stats
DATABASE_POOL_SIZE=5
DATABASE_MAX_OVERFLOW=10
REDIS_URL=redis://:REDIS_PASSWORD@127.0.0.1:6379/0
FRONTEND_URL=https://vnclub.example.com
CORS_ORIGINS=https://vnclub.example.com
VNDB_API_TOKEN=...
DUMP_STORAGE_PATH=/srv/vnclub/dumps
VNDB_CACHE_DIR=/srv/vnclub/public/vndb-images
BLACKLIST_REFRESH_SECRET=...
ADMIN_API_KEY=...
DEV_MODE=false
```

`VNDB_API_TOKEN` is required for `/stats/<username>` lookups. Use the hex
passwords from section 3 so they need no URL escaping. `CORS_ORIGINS` is a
comma-separated list; a JSON array also works, but systemd can strip the
quotes inside one. Set it to the exact public origin — a trailing slash makes
browser calls fail.

### `/etc/vnclub/web.env`

```dotenv
NODE_ENV=production
PORT=3000
HOSTNAME=127.0.0.1
NEXT_TELEMETRY_DISABLED=1
API_URL_INTERNAL=http://127.0.0.1:8000
VNDB_CACHE_DIR=/srv/vnclub/public/vndb-images
BLACKLIST_REFRESH_SECRET=...
```

`BLACKLIST_REFRESH_SECRET` must be identical to the API value. `NEXT_PUBLIC_*`
values are compiled into the client bundle; this layout uses the same-origin
`/api/backend` proxy, so you normally set none of them. If you add Umami later,
set its `NEXT_PUBLIC_UMAMI_*` values and rebuild the frontend.

### `/etc/vnclub/heavy.env`

The copied example is already correct for a single host:

```dotenv
HEAVY_MODE=local
HEAVY_TASK=daily
HEAVY_WORKDIR=/srv/vnclub/vndb-stats-backend
HEAVY_PYTHON=.venv/bin/python
HEAVY_TASK_SCRIPT=/srv/vnclub/deploy/native/scripts/heavy_task.py
```

`HEAVY_MODE=local` makes `vnclub-heavy.service` run the job in this host’s
venv, using `DATABASE_URL` and `REDIS_URL` from `api.env`. Set
`HEAVY_MODE=remote` and `HEAVY_SSH_HOST` only if you later move the job to
another machine (section 13).

Lock the files down:

```bash
sudo chown root:vnclub /etc/vnclub/*.env
sudo chmod 640 /etc/vnclub/*.env
```

---

## 8. First schema, then the first import

The API’s `ExecStartPre` creates the schema and applies data migrations. That
must happen before the heavy job writes anything, so start the API first:

```bash
sudo systemctl daemon-reload
sudo systemctl enable vnclub-api.service
sudo systemctl start vnclub-api.service
sudo systemctl status vnclub-api.service --no-pager

curl -fsS http://127.0.0.1:8000/health
```

`/health` should answer and `/health/db` should respond with `has_data: false`.
If the API fails, see section 12 before continuing.

Now run the full import through the heavy unit. It downloads the VNDB dumps into
`/srv/vnclub/dumps`, imports them, trains the recommendation model, rebuilds
similarity tables and leaderboards.

```bash
sudo systemctl start --no-block vnclub-heavy.service
journalctl -u vnclub-heavy.service -f
```

This is the long step. Expect roughly 1–3 hours on a 4-core arm64 host with an
SSD, longer on eMMC or a 2-core board. Phase 1 is network and disk; phase 2
(model training) is CPU-bound and will use all cores.

Watch memory and disk in another shell:

```bash
free -h
df -h /srv
systemd-cgtop -1
```

If it fails, the journal names the phase. Re-running the unit is safe: the
import uses an advisory lock and replaces staging tables atomically.

Alternatively restore a dump from another instance (see section 11) and skip
the download. If you restore data, run the model rebuild once:

```bash
sudo sed -i 's/^HEAVY_TASK=.*/HEAVY_TASK=similarity/' /etc/vnclub/heavy.env
sudo systemctl start vnclub-heavy.service
sudo sed -i 's/^HEAVY_TASK=.*/HEAVY_TASK=daily/' /etc/vnclub/heavy.env
```

When it succeeds, verify:

```bash
sudo -u postgres psql -d vndb_stats \
  -c 'SELECT count(*) FROM visual_novels;'
curl -fsS http://127.0.0.1:8000/health/db
```

`visual_novels` should hold tens of thousands of rows, and `/health/db`
should now report `has_data: true` and a recent `last_import`.

Then start the front end:

```bash
sudo systemctl enable --now vnclub-web.service
curl -fsS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:3000/
```

---

## 9. Angie

### Install

```bash
sudo curl -o /etc/apt/trusted.gpg.d/angie-signing.gpg \
  https://angie.software/keys/angie-signing.gpg

echo "deb https://download.en.angie.software/angie/$(. /etc/os-release && echo "$ID/$VERSION_ID $VERSION_CODENAME") main" \
  | sudo tee /etc/apt/sources.list.d/angie.list

sudo apt-get update
sudo apt-get install -y angie
sudo systemctl enable angie
```

Angie publishes arm64 packages for Debian 12 and 13. If your release has no
`angie` candidate, install `nginx` instead and adjust the copied vhost:
remove the `status_zone` lines, the `listen ... quic` lines and `http3 on`, and
keep ordinary `listen 443 ssl` / `http2 on` (nginx 1.25.1 and newer understand
`http2 on`).

### TLS

The vhost expects:

```text
/etc/letsencrypt/live/$DOMAIN/fullchain.pem
/etc/letsencrypt/live/$DOMAIN/privkey.pem
```

Issue the certificate before the final vhost is enabled, while port 80 is free:

```bash
sudo systemctl stop angie 2>/dev/null || true
sudo certbot certonly --standalone -d "$DOMAIN" \
  --agree-tos -m "admin@$DOMAIN" --non-interactive
sudo certbot renew --dry-run
```

### vhost

```bash
sudo install -m 644 /srv/vnclub/39chan.conf /etc/angie/conf.d/39chan.conf
sudo editor /etc/angie/conf.d/39chan.conf
```

Change:

- `server_name` (both the port-80 and port-443 blocks) to `$DOMAIN`;
- `ssl_certificate` and `ssl_certificate_key` paths;
- keep `alias /srv/vnclub/public/vndb-images/...` as written.

```bash
sudo angie -t
sudo systemctl restart angie
curl -I "https://$DOMAIN/"
```

### Image serving

Angie serves an existing WebP cover directly and falls back to Next for a
missing one. The first GET for a cover generates the resized file:

```bash
IMAGE_PATH='/img/cv/12/79412.webp?w=128'
curl -sS -o /dev/null -D - "https://$DOMAIN$IMAGE_PATH" \
  | grep -iE '^(HTTP|cache-control|x-served-by|etag)'
```

Repeat the request; the second response should include:

```text
x-served-by: angie-static
cache-control: public, max-age=31536000, immutable
```

If it keeps returning `max-age=2592000`, the file was not created or Angie
cannot read it. Check `ls -l /srv/vnclub/public/vndb-images/cv/12/`, the
`VNDB_CACHE_DIR` value in `web.env`, and the Angie user’s group membership.

Optional: pre-generate covers for the most-voted titles while the API is up
(this queries the local database, not the VNDB API). Load `web.env` so the
script writes into the directory Angie serves, rather than its fallback under
the service user's home:

```bash
sudo -u vnclub bash -c '
  set -a; . /etc/vnclub/web.env; set +a
  cd /srv/vnclub
  exec bun scripts/cache-vndb-images.ts --top 500
'
```

---

## 10. Timers and services

```bash
# Nightly import at 04:00 with a 15 minute random delay
sudo systemctl enable --now vnclub-heavy.timer

# PostgreSQL dump at 03:30, seven days retained
sudo systemctl enable --now vnclub-backup.timer

systemctl list-timers 'vnclub-*'
```

The backup timer runs before the import on purpose: the dump captures the
previous day’s consistent state, and does not compete with the heavy job for
I/O on a small disk.

Optional Discord bot:

```bash
sudo systemctl enable --now vnclub-bot.service
```

Do not enable the bot on a 4 GiB host unless you have memory headroom; its unit
caps it at 384 MiB, but the extra process competes with the import.

### Resource limits

Each unit has a `MemoryMax`. On a larger arm64 host you can raise the web and
API ceilings without editing the checked-in files:

```bash
sudo systemctl edit vnclub-web.service
```

```ini
[Service]
MemoryHigh=1536M
MemoryMax=2G
```

```bash
sudo systemctl edit vnclub-api.service
```

```ini
[Service]
MemoryHigh=1G
MemoryMax=1536M
```

Leave the heavy unit uncapped: the model rebuild legitimately needs several GiB
on a large catalogue. If the import is OOM-killed, add RAM or move the job to a
worker (section 13); a `MemoryMax` on the heavy unit only turns a slow import
into a failed one.

---

## 11. Backups and restore

```bash
sudo systemctl start vnclub-backup.service
sudo ls -lh /var/backups/vnclub
```

Retention is seven dumps by default. On a 40 GiB disk that is a significant
share, especially with dumps and covers on the same volume. Either lower
`RETENTION_DAYS` in `deploy/native/scripts/backup.sh` or copy dumps off-host.

Test a restore periodically:

```bash
sudo -u postgres createdb vndb_stats_restore
sudo -u postgres pg_restore -d vndb_stats_restore \
  /var/backups/vnclub/vnclub-YYYYMMDDTHHMMSSZ.dump
sudo -u postgres psql -d vndb_stats_restore \
  -c 'SELECT count(*) FROM visual_novels;'
sudo -u postgres dropdb vndb_stats_restore
```

Restoring into the live database:

```bash
sudo systemctl stop vnclub-web vnclub-api
sudo -u postgres pg_restore --clean --if-exists -d vndb_stats \
  /var/backups/vnclub/vnclub-YYYYMMDDTHHMMSSZ.dump
sudo systemctl start vnclub-api vnclub-web
```

A dump carries no cover files. After restoring, `/srv/vnclub/public/vndb-images`
is either empty or whatever was already there; the image route regenerates each
missing WebP on the first request, and the optional prefetch above fills the
popular ones in advance.

`prestart.sh` runs migrations on API start, so a dump from an older revision is
brought forward automatically.

---

## 12. Operations and troubleshooting

### Updates

```bash
sudo bash /srv/vnclub/deploy/native/scripts/update.sh
```

This pulls the repository, rebuilds the frontend, refreshes the Python venv,
reinstalls the systemd units, reloads systemd, and restarts the API and web
services. The API’s prestart applies migrations. The heavy job picks up the new
code on its next timer run.

### Service status

```bash
systemctl status vnclub-api vnclub-web --no-pager
journalctl -u vnclub-api -n 100 --no-pager
journalctl -u vnclub-web -n 100 --no-pager
journalctl -u vnclub-heavy -n 100 --no-pager
```

### API health fails

```bash
curl -i http://127.0.0.1:8000/health
curl -i http://127.0.0.1:8000/health/db
```

Run the prestart by hand to see the migration error:

```bash
sudo -u vnclub bash -c \
  'set -a; . /etc/vnclub/api.env; set +a; exec /srv/vnclub/deploy/native/scripts/prestart.sh'
```

Common causes: wrong database password, missing `pg_hba.conf` line, Redis
password mismatch, or PostgreSQL not listening on loopback.

### Public site returns 502

```bash
curl -v http://127.0.0.1:3000/
curl -v http://127.0.0.1:8000/health
sudo tail -n 50 /var/log/angie/error.log
```

If 3000 answers but Angie does not, check the upstream and `angie -t`.

### Python install fails on arm64

Run the installer directly for the full error:

```bash
sudo bash /srv/vnclub/deploy/native/scripts/install-python-deps.sh
```

If the failure names a package building from source:

```bash
sudo apt-get install -y build-essential gfortran libopenblas-dev libgomp1 libpq-dev
sudo bash /srv/vnclub/deploy/native/scripts/install-python-deps.sh
```

`implicit` ships `manylinux2014_aarch64` wheels for CPython 3.11, and so does
`onnxruntime`; a source build means the interpreter is not the pinned 3.11
(check `.venv/bin/python -V`) or the index is unreachable. Verify the venv:

```bash
/srv/vnclub/vndb-stats-backend/.venv/bin/python -V
/srv/vnclub/vndb-stats-backend/.venv/bin/python -c \
  'import implicit, onnxruntime; print(implicit.__version__, onnxruntime.__version__)'
```

Do not “fix” this by installing the system `python3` packages: the application
expects the uv-managed 3.11 environment.

### `bunx: command not found`

The web unit’s `PATH` is `/usr/local/bin:/usr/bin:/bin`. Link the missing
entry point:

```bash
BUN_REAL=$(readlink -f "$(command -v bun)")
sudo ln -sf "$(dirname "$BUN_REAL")/bunx" /usr/local/bin/bunx
command -v bunx
```

### Frontend build or import is OOM-killed

```bash
journalctl -k | grep -i -E 'oom|killed process'
systemctl show vnclub-api -p MemoryCurrent
free -h
```

- Add swap (section 2).
- Stop the API and web while building.
- On a 4 GiB host, run the import alone: `sudo systemctl stop vnclub-web`.
- Consider moving the import to a worker with more RAM (section 13).

### Disk fills during the import

```bash
du -sh /srv/vnclub/* /var/backups/vnclub/* 2>/dev/null | sort -h
df -h /srv
```

The usual causes are old database dumps, the cover cache and consecutive
`.next` builds. Lower `RETENTION_DAYS`, remove old dumps in
`/srv/vnclub/dumps`, or move backups off-host.

### Temperature and clock (single-board computers)

A sustained 100% load for hours will throttle an SBC with a passive heatsink;
the import still completes, just slowly. If you see `thermal` messages in
`dmesg`, improve cooling. Keep NTP enabled (`timedatectl status`) — TLS and the
daily timer both depend on a correct clock.

---

## 13. Moving the heavy job to another machine later

The single-host layout is the default, but the same checkout supports the
two-host layout. On a worker with more RAM:

1. Install the same repository at `/srv/vnclub`, the same `uv` and Python
   environment (`install-python-deps.sh`), and `/srv/vnclub/dumps`.
2. Install `/srv/vnclub/deploy/native/env/heavy.worker.example` as the
   worker’s `/etc/vnclub/heavy.env` and fill in the tunnel addresses
   (`127.0.0.1:15432` for PostgreSQL, `127.0.0.1:16379` for Redis).
3. Create an SSH key for `vnclub` on the core host and authorize it on the
   worker; see `deploy/native/SETUP.md` sections 10–11.
4. On the core host set in `/etc/vnclub/heavy.env`:

   ```dotenv
   HEAVY_MODE=remote
   HEAVY_SSH_HOST=vnclub@worker.example.com
   ```

5. Test the lightest task before the timer runs:

   ```bash
   sudo sed -i 's/^HEAVY_TASK=.*/HEAVY_TASK=leaderboards/' /etc/vnclub/heavy.env
   sudo systemctl start --no-block vnclub-heavy.service
   journalctl -u vnclub-heavy.service -f
   sudo sed -i 's/^HEAVY_TASK=.*/HEAVY_TASK=daily/' /etc/vnclub/heavy.env
   ```

The core keeps PostgreSQL and Redis on loopback; the worker reaches them
through the SSH remote forwards opened by `run-heavy-remote.sh`. The full
two-host walkthrough remains in `deploy/native/SETUP.md`.

---

## 14. Final checklist

- [ ] `uname -m` prints `aarch64`
- [ ] `check-host.sh` reports no blocking problems
- [ ] swap active (`swapon --show`)
- [ ] PostgreSQL 15 running on `127.0.0.1:5432` with the arm64 package
- [ ] `psql "postgresql://vndb:...@127.0.0.1:5432/vndb_stats" -c 'SELECT 1'`
- [ ] Redis answers `PING` with a password
- [ ] `install-python-deps.sh` prints `all backend imports OK`
- [ ] `/etc/vnclub/api.env`, `web.env`, `heavy.env` mode `640 root:vnclub`
- [ ] `curl http://127.0.0.1:8000/health/db` succeeds
- [ ] `visual_novels` count is non-zero after the first import
- [ ] `curl http://127.0.0.1:3000/` answers
- [ ] `angie -t` passes (or `nginx -t`)
- [ ] `https://$DOMAIN/` loads with a valid certificate
- [ ] a second cover request returns `X-Served-By: angie-static`
- [ ] `systemctl list-timers 'vnclub-*'` shows the heavy and backup timers
- [ ] a backup dump exists and a test restore succeeded
- [ ] `ss -ltnp` shows 3000, 8000, 5432 and 6379 bound to loopback only
