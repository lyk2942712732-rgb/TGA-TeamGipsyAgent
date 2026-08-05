#!/usr/bin/env bash
# Provision a bare Linux distribution into a TGA-Runtime.
#
# This runs once, inside the dedicated WSL2 distribution (or on a fresh Linux
# server), and produces the layout every other component assumes:
#
#   /opt/tga/{app,web,bin}   code and the prebuilt frontend bundle
#   /etc/tga/                configuration owned by root
#   /var/lib/tga/runs/       task data, the single TGA_RUN_ROOT
#   /var/log/tga/            component logs
#
# It is idempotent: re-running repairs a partial provision rather than
# starting over, because `tga up` may be interrupted at any point.
set -euo pipefail

TGA_PREFIX="${TGA_PREFIX:-/opt/tga}"
TGA_CONFIG_DIR="${TGA_CONFIG_DIR:-/etc/tga}"
TGA_STATE_DIR="${TGA_STATE_DIR:-/var/lib/tga}"
TGA_LOG_DIR="${TGA_LOG_DIR:-/var/log/tga}"
TGA_USER="${TGA_USER:-tga}"
TGA_GROUP="${TGA_GROUP:-tga}"
TGA_SANDBOX_GROUP="${TGA_SANDBOX_GROUP:-tga-sandbox}"
SOURCE_DIR="${SOURCE_DIR:-}"

log()  { printf '[provision] %s\n' "$*"; }
fail() { printf '[provision] ERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "must run as root"

# --- 1. base packages -------------------------------------------------------
log "installing base packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
  python3 python3-venv python3-pip \
  ca-certificates curl gnupg lsb-release \
  nftables iproute2 procps sudo >/dev/null

python3 - <<'PY' || fail "Python 3.11+ is required"
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY

# --- 2. users and directories ----------------------------------------------
log "creating service account and directories"
getent group "$TGA_GROUP"         >/dev/null || groupadd --system "$TGA_GROUP"
getent group "$TGA_SANDBOX_GROUP" >/dev/null || groupadd --system "$TGA_SANDBOX_GROUP"
getent passwd "$TGA_USER" >/dev/null || useradd --system \
  --gid "$TGA_GROUP" --groups "$TGA_SANDBOX_GROUP" \
  --home-dir "$TGA_STATE_DIR" --shell /usr/sbin/nologin "$TGA_USER"

install -d -m 0755 "$TGA_PREFIX"/{app,web,bin}
install -d -m 0755 "$TGA_CONFIG_DIR"
install -d -m 0750 -o "$TGA_USER" -g "$TGA_GROUP" "$TGA_STATE_DIR"
install -d -m 0750 -o "$TGA_USER" -g "$TGA_GROUP" "$TGA_STATE_DIR/runs"
install -d -m 0750 -o "$TGA_USER" -g "$TGA_GROUP" "$TGA_STATE_DIR/state"
install -d -m 0750 -o "$TGA_USER" -g "$TGA_GROUP" "$TGA_LOG_DIR"

# --- 3. application ---------------------------------------------------------
if [ -n "$SOURCE_DIR" ] && [ -d "$SOURCE_DIR" ]; then
  log "installing application from $SOURCE_DIR"
  # Copy sources without dragging in build and VCS noise.
  tar -C "$SOURCE_DIR" \
      --exclude=.git --exclude=.venv --exclude=node_modules \
      --exclude=runs --exclude=__pycache__ --exclude='*.pyc' \
      -cf - tga apps pyproject.toml config resources \
    | tar -C "$TGA_PREFIX/app" -xf -

  if [ -d "$SOURCE_DIR/apps/web/dist" ]; then
    log "installing prebuilt frontend bundle"
    # Production never builds at startup; the bundle ships prebuilt.
    rm -rf "${TGA_PREFIX:?}/web"
    install -d -m 0755 "$TGA_PREFIX/web"
    tar -C "$SOURCE_DIR/apps/web/dist" -cf - . | tar -C "$TGA_PREFIX/web" -xf -
  else
    log "WARNING: no apps/web/dist found; set TGA_WEB_DIST before serving"
  fi

  log "creating the Python environment"
  python3 -m venv "$TGA_PREFIX/venv"
  "$TGA_PREFIX/venv/bin/pip" install --quiet --upgrade pip
  "$TGA_PREFIX/venv/bin/pip" install --quiet -e "$TGA_PREFIX/app"
fi

# --- 3b. sandboxd ------------------------------------------------------------
# Without this binary the sandbox can never be enforced: the API has nothing to
# talk to, every profile is refused at the execution boundary, and `tga up`
# reports degraded forever. A packaged install ships it prebuilt; a source
# checkout with Go builds it here.
install_sandboxd() {
  local prebuilt="$SOURCE_DIR/dist/tga-sandboxd"
  if [ -f "$prebuilt" ]; then
    log "installing prebuilt tga-sandboxd"
    install -m 0755 "$prebuilt" "$TGA_PREFIX/bin/tga-sandboxd"
    return 0
  fi
  if [ -d "$SOURCE_DIR/sandboxd" ] && command -v go >/dev/null 2>&1; then
    log "building tga-sandboxd from source"
    ( cd "$SOURCE_DIR/sandboxd" \
      && CGO_ENABLED=0 go build -trimpath -o "$TGA_PREFIX/bin/tga-sandboxd" ./cmd/tga-sandboxd )
    chmod 0755 "$TGA_PREFIX/bin/tga-sandboxd"
    return 0
  fi
  return 1
}

if [ -n "$SOURCE_DIR" ] && [ -d "$SOURCE_DIR" ]; then
  if install_sandboxd; then
    log "  sandboxd $TGA_PREFIX/bin/tga-sandboxd"
  else
    log "WARNING: no tga-sandboxd binary and no Go toolchain to build one;"
    log "         the sandbox stays unenforced and 'tga up' will report degraded"
  fi
fi

# --- 4. internal worker shim -----------------------------------------------
log "installing the tga-internal worker"
cat > "$TGA_PREFIX/bin/tga-internal" <<EOF
#!/usr/bin/env bash
# Internal runtime worker. Users run 'tga', never this.
set -euo pipefail
[ -f "$TGA_CONFIG_DIR/tga.env" ] && set -a && . "$TGA_CONFIG_DIR/tga.env" && set +a
exec "$TGA_PREFIX/venv/bin/python" -m tga.cli.internal "\$@"
EOF
chmod 0755 "$TGA_PREFIX/bin/tga-internal"

# --- 5. environment ---------------------------------------------------------
if [ ! -f "$TGA_CONFIG_DIR/tga.env" ]; then
  log "writing $TGA_CONFIG_DIR/tga.env"
  cat > "$TGA_CONFIG_DIR/tga.env" <<EOF
# TGA runtime environment. Managed by provisioning; edit with care.
TGA_RUN_ROOT=$TGA_STATE_DIR/runs
TGA_WEB_DIST=$TGA_PREFIX/web
TGA_STATE_DIR=$TGA_STATE_DIR/state
TGA_LOG_DIR=$TGA_LOG_DIR
TGA_SANDBOX_CONFIG_PATH=$TGA_CONFIG_DIR/sandbox.json
PYTHONUNBUFFERED=1
EOF
  chmod 0644 "$TGA_CONFIG_DIR/tga.env"
fi

# --- 6. sandbox configuration ----------------------------------------------
if [ ! -f "$TGA_CONFIG_DIR/sandbox.json" ] && [ -f "$TGA_PREFIX/app/config/sandbox.json" ]; then
  log "seeding $TGA_CONFIG_DIR/sandbox.json"
  cp "$TGA_PREFIX/app/config/sandbox.json" "$TGA_CONFIG_DIR/sandbox.json"
  chmod 0644 "$TGA_CONFIG_DIR/sandbox.json"
fi
if [ -x "$TGA_PREFIX/venv/bin/python" ]; then
  log "binding sandbox configuration to this host"
  # Writes the real client UID and run root so sandboxd accepts the API
  # process. Never leaves allowed_client_uids empty under enforcement.
  "$TGA_PREFIX/venv/bin/python" -m tga.deployment.config_generator \
      --config "$TGA_CONFIG_DIR/sandbox.json" \
      --run-root "$TGA_STATE_DIR/runs" \
      --client-user "$TGA_USER" || log "WARNING: sandbox config could not be written"
fi

# --- 7. systemd units -------------------------------------------------------
if [ -d /run/systemd/system ] && [ -d "$(dirname "$0")/../systemd" ]; then
  log "installing systemd units"
  install -m 0644 "$(dirname "$0")/../systemd/"*.service /etc/systemd/system/
  systemctl daemon-reload
  systemctl enable tga-api.service >/dev/null 2>&1 || true
  # Enabled only when the binary is actually there: an enabled unit whose
  # ExecStart does not exist fails at every boot and buries the real reason.
  if [ -x "$TGA_PREFIX/bin/tga-sandboxd" ]; then
    systemctl enable tga-sandboxd.service >/dev/null 2>&1 || true
  else
    log "not enabling tga-sandboxd.service: $TGA_PREFIX/bin/tga-sandboxd is missing"
  fi
else
  log "systemd is not active; 'tga up' will supervise the API directly"
fi

log "provisioning complete"
log "  prefix   $TGA_PREFIX"
log "  run root $TGA_STATE_DIR/runs"
log "  web dist $TGA_PREFIX/web"
