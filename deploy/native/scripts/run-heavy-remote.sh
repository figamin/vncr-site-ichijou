#!/usr/bin/env bash
# Run a heavy backend task on a remote machine.
#
# The remote machine reaches this host's PostgreSQL and Redis through SSH
# remote forwards, so no database copy is needed for a normal update.
set -euo pipefail

: "${HEAVY_SSH_HOST:?HEAVY_SSH_HOST is not set in /etc/vnclub/heavy.env}"
: "${HEAVY_WORKDIR:=/srv/vnclub/vndb-stats-backend}"
: "${HEAVY_PYTHON:=.venv/bin/python}"
: "${HEAVY_TASK:=daily}"
: "${HEAVY_TASK_SCRIPT:=/srv/vnclub/deploy/native/scripts/heavy_task.py}"

case "$HEAVY_TASK" in
  daily|similarity|leaderboards) ;;
  *)
    echo "HEAVY_TASK must be daily, similarity or leaderboards" >&2
    exit 2
    ;;
esac

# -R opens the ports on the remote loopback. The remote heavy.env must point
# DATABASE_URL and REDIS_URL at 127.0.0.1:15432 and 127.0.0.1:16379.
exec ssh -T \
  -o BatchMode=yes \
  -o ConnectTimeout=15 \
  -o ExitOnForwardFailure=yes \
  -o ServerAliveInterval=30 \
  -o ServerAliveCountMax=4 \
  -R 15432:127.0.0.1:5432 \
  -R 16379:127.0.0.1:6379 \
  "$HEAVY_SSH_HOST" \
  "cd '$HEAVY_WORKDIR' && set -a && . /etc/vnclub/heavy.env && set +a && exec '$HEAVY_PYTHON' '$HEAVY_TASK_SCRIPT' '$HEAVY_TASK'"
