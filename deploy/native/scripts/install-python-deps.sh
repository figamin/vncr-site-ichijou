#!/usr/bin/env bash
# Create or refresh the backend Python environment for a native host.
#
# Works on arm64 and x86-64. Prebuilt aarch64 wheels exist for the packages the
# backend pins; if pip/uv has to fall back to a source build (implicit, for
# example), it needs a compiler with OpenMP support:
#
#   apt-get install -y build-essential gfortran libopenblas-dev libgomp1 libpq-dev
#
# Usage:
#   sudo bash deploy/native/scripts/install-python-deps.sh
#
# The repository must already be at APP_DIR (default /srv/vnclub) and the
# vnclub service user must exist.
set -euo pipefail

APP_DIR=${APP_DIR:-/srv/vnclub}
BACKEND_DIR="$APP_DIR/vndb-stats-backend"
PYTHON_VERSION=${PYTHON_VERSION:-3.11}
VENV_DIR="$BACKEND_DIR/.venv"
# Keep the uv-managed interpreter inside the application tree. Relying on the
# invoking user's HOME would put it under /root when this script runs as root,
# and the service account cannot execute a python that lives behind /root.
UV_PYTHON_DIR="$APP_DIR/.uv/python"

if [ ! -f "$BACKEND_DIR/requirements.lock" ]; then
  echo "No backend at $BACKEND_DIR (missing requirements.lock)." >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is missing. Install it first:" >&2
  echo "  curl -LsSf https://astral.sh/uv/install.sh | sudo env UV_INSTALL_DIR=/usr/local/bin sh" >&2
  exit 1
fi

if [ "$(id -u)" -eq 0 ] && ! id vnclub >/dev/null 2>&1; then
  echo "The vnclub service user does not exist." >&2
  echo "Run deploy/native/scripts/prepare-app.sh, or create the account first." >&2
  exit 1
fi

# Run uv as the service user when we are root, so the venv is not root-owned,
# and pin HOME and the interpreter directory so both invocations agree on where
# Python 3.11 lives.
as_service_user() {
  if [ "$(id -u)" -eq 0 ] && id vnclub >/dev/null 2>&1; then
    sudo -u vnclub env \
      HOME="$APP_DIR" \
      UV_PYTHON_INSTALL_DIR="$UV_PYTHON_DIR" \
      "$@"
  else
    UV_PYTHON_INSTALL_DIR="$UV_PYTHON_DIR" "$@"
  fi
}

cd "$BACKEND_DIR"

# A venv created by an older run may point at an interpreter the service user
# cannot reach (for example one under /root). Recreate it rather than failing
# later with a permission error inside the service.
if [ -e "$VENV_DIR" ] && ! as_service_user "$VENV_DIR/bin/python" -V >/dev/null 2>&1; then
  echo "Existing venv interpreter is not runnable; recreating $VENV_DIR..."
  as_service_user rm -rf "$VENV_DIR"
fi

echo "Installing CPython $PYTHON_VERSION for the backend venv..."
as_service_user uv python install "$PYTHON_VERSION"

if [ ! -x "$VENV_DIR/bin/python" ]; then
  echo "Creating $VENV_DIR..."
  as_service_user uv venv --python "$PYTHON_VERSION" "$VENV_DIR"
fi

echo "Installing locked requirements..."
if ! as_service_user uv pip install \
  --python "$VENV_DIR/bin/python" \
  --require-hashes -r requirements.lock; then
  cat >&2 <<EOF

Dependency installation failed.

On arm64 this is almost always one of:
  * a missing compiler for a package that has no aarch64 wheel
    (install build-essential gfortran libopenblas-dev libgomp1 libpq-dev)
  * not enough disk or memory for a source build of implicit/scipy

Retry after installing the toolchain:
  sudo apt-get install -y build-essential gfortran libopenblas-dev libgomp1 libpq-dev
  sudo bash $APP_DIR/deploy/native/scripts/install-python-deps.sh
EOF
  exit 1
fi

# requirements-vcs.txt normally contains only comments (the VCS entry set is
# empty); skip it rather than handing uv an effectively empty file.
if grep -qE '^[^#[:space:]].*@[[:space:]]*git\+' requirements-vcs.txt; then
  echo "Installing VCS requirements..."
  as_service_user uv pip install \
    --python "$VENV_DIR/bin/python" \
    --no-deps -r requirements-vcs.txt
fi

echo "Verifying imports..."
as_service_user "$VENV_DIR/bin/python" - <<'PY'
import sys

missing = []
for name in (
    "fastapi",
    "uvicorn",
    "asyncpg",
    "sqlalchemy",
    "alembic",
    "numpy",
    "scipy",
    "sklearn",
    "implicit",
    "onnxruntime",
    "tokenizers",
):
    try:
        __import__(name)
    except Exception as exc:  # noqa: BLE001 - report, do not abort the install
        missing.append(f"{name}: {exc}")

print(f"python: {sys.version.split()[0]} ({sys.platform}, {sys.maxsize > 2**32 and '64-bit' or '32-bit'})")
if missing:
    print("Missing or broken imports:")
    for line in missing:
        print(f"  - {line}")
    raise SystemExit(1)
print("all backend imports OK")
PY

echo "Python environment ready: $VENV_DIR"
