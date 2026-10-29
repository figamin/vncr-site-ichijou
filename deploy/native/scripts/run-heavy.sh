#!/usr/bin/env bash
# Run a heavy backend task, locally or on a remote worker.
#
# vnclub-heavy.service loads /etc/vnclub/api.env and /etc/vnclub/heavy.env and
# then executes this script. HEAVY_MODE decides where the work happens:
#
#   local  - in this host's backend venv (single-host installs)
#   remote - on HEAVY_SSH_HOST over SSH, with PostgreSQL and Redis reached
#            through remote port forwards (two-host installs)
#   auto   - remote when HEAVY_SSH_HOST is set, otherwise local
#
# Local mode needs DATABASE_URL, which normally comes from api.env. Remote mode
# needs HEAVY_SSH_HOST and is handled by run-heavy-remote.sh.
set -euo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

: "${HEAVY_TASK:=daily}"
: "${HEAVY_MODE:=auto}"
: "${HEAVY_WORKDIR:=/srv/vnclub/vndb-stats-backend}"
: "${HEAVY_PYTHON:=.venv/bin/python}"
: "${HEAVY_TASK_SCRIPT:=$SCRIPT_DIR/heavy_task.py}"

case "$HEAVY_TASK" in
  daily|similarity|leaderboards) ;;
  *)
    echo "HEAVY_TASK must be daily, similarity or leaderboards (got '$HEAVY_TASK')" >&2
    exit 2
    ;;
esac

mode=$HEAVY_MODE
if [ "$mode" = auto ]; then
  if [ -n "${HEAVY_SSH_HOST:-}" ]; then
    mode=remote
  else
    mode=local
  fi
fi

case "$mode" in
  remote)
    exec "$SCRIPT_DIR/run-heavy-remote.sh"
    ;;
  local)
    : "${DATABASE_URL:?DATABASE_URL is not set; load /etc/vnclub/api.env before running the heavy task}"
    if [ ! -x "$HEAVY_WORKDIR/$HEAVY_PYTHON" ] && [ ! -x "$HEAVY_PYTHON" ]; then
      echo "Python interpreter not found: $HEAVY_PYTHON" >&2
      echo "Create the venv with deploy/native/scripts/install-python-deps.sh" >&2
      exit 1
    fi
    cd "$HEAVY_WORKDIR"
    echo "Running heavy task '$HEAVY_TASK' locally in $HEAVY_WORKDIR"
    exec "$HEAVY_PYTHON" "$HEAVY_TASK_SCRIPT" "$HEAVY_TASK"
    ;;
  *)
    echo "HEAVY_MODE must be local, remote or auto (got '$mode')" >&2
    exit 2
    ;;
esac
