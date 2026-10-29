#!/usr/bin/env bash
# Preflight check for a single-host native install.
#
# Read-only: reports the architecture, Debian release, memory, swap, disk and
# port state, and whether the tools the guide installs are present. Run it
# before starting and again after the install if something fails.
#
#   sudo bash deploy/native/scripts/check-host.sh
#
# Exit status is non-zero only for conditions that cannot work (unsupported
# architecture, no systemd, not Debian-derived).
set -uo pipefail

fails=0
ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; }
info() { printf '  \033[36minfo\033[0m  %s\n' "$*"; }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$*"; }
bad()  { printf '  \033[31mfail\033[0m  %s\n' "$*"; fails=$((fails + 1)); }

echo "VN Club host check"

# --- Architecture ------------------------------------------------------------
arch=$(uname -m)
case "$arch" in
  aarch64|arm64)
    ok "architecture: $arch (64-bit ARM)"
    ;;
  x86_64|amd64)
    warn "architecture: $arch - the single-host guide targets arm64, but this layout works on x86-64 too"
    ;;
  *)
    bad "architecture: $arch - 32-bit hosts are not supported (use an arm64 or x86-64 Debian)"
    ;;
esac

# --- OS ----------------------------------------------------------------------
if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  case "${ID:-}" in
    debian|raspbian|ubuntu)
      info "os: ${PRETTY_NAME:-$ID}"
      if [ "${ID:-}" = debian ]; then
        major=${VERSION_ID%%.*}
        if [ "${major:-0}" -lt 12 ] 2>/dev/null; then
          warn "Debian ${VERSION_ID:-unknown}: the guide is written for Debian 12/13"
        fi
      fi
      ;;
    *)
      bad "os: ${PRETTY_NAME:-unknown} - the guide assumes Debian 12 or 13"
      ;;
  esac
else
  bad "os: /etc/os-release is missing"
fi

if [ -d /run/systemd/system ]; then
  ok "systemd: running as PID 1"
else
  bad "systemd: not the init system - the service units will not work"
fi

# --- Memory and swap ---------------------------------------------------------
mem_kb=$(awk '/^MemTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
swap_kb=$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo 2>/dev/null || echo 0)
mem_mib=$((mem_kb / 1024))
swap_mib=$((swap_kb / 1024))
if [ "$mem_mib" -ge 7500 ]; then
  ok "memory: ${mem_mib} MiB"
elif [ "$mem_mib" -ge 3500 ]; then
  if [ "$swap_mib" -ge 2000 ]; then
    ok "memory: ${mem_mib} MiB with ${swap_mib} MiB swap"
  else
    warn "memory: ${mem_mib} MiB and only ${swap_mib} MiB swap - add at least 4 GiB swap"
  fi
else
  warn "memory: ${mem_mib} MiB - below the recommended 8 GiB; the first import and the Next build will be slow, keep swap enabled"
fi

# --- Disk --------------------------------------------------------------------
disk_path=/srv
[ -d "$disk_path" ] || disk_path=/
avail_kb=$(df -Pk "$disk_path" | awk 'NR==2 {print $4}')
avail_gib=$((avail_kb / 1024 / 1024))
if [ "$avail_gib" -ge 35 ]; then
  ok "disk: ${avail_gib} GiB free on $disk_path"
elif [ "$avail_gib" -ge 20 ]; then
  warn "disk: ${avail_gib} GiB free on $disk_path - the database, image cache and dumps share it"
else
  warn "disk: ${avail_gib} GiB free on $disk_path - too little for the database, image cache and dumps"
fi

# --- Tools -------------------------------------------------------------------
check_tool() {
  local name=$1 hint=$2
  if command -v "$name" >/dev/null 2>&1; then
    ok "tool: $name ($(command -v "$name"))"
  else
    warn "tool: $name not found - $hint"
  fi
}
check_tool git "install git"
check_tool curl "install curl"
check_tool bun "install Bun (see the guide)"
check_tool uv "install uv (see the guide)"
check_tool psql "install postgresql-client"
check_tool redis-cli "install redis-tools"
check_tool angie "install Angie, or adapt the vhost for nginx"

python_bin=$(command -v python3 || true)
if [ -n "$python_bin" ]; then
  ok "tool: python3 ($("$python_bin" -V 2>&1))"
else
  warn "tool: python3 not found - the backend venv is created by uv, but some helper scripts need python3"
fi

# --- Ports -------------------------------------------------------------------
if command -v ss >/dev/null 2>&1; then
  echo "  ---- listening on application ports (bound ports are expected after install) ----"
  ss -ltnp 2>/dev/null | grep -E ':(3000|8000|5432|6379)\b' || info "ports 3000, 8000, 5432 and 6379 are free"
fi

# --- Summary -----------------------------------------------------------------
echo
if [ "$fails" -eq 0 ]; then
  echo "Preflight passed with no blocking problems."
else
  echo "$fails blocking problem(s) found."
fi
exit "$fails"
