#!/usr/bin/env bash
# Install TGA on a Linux server.
#
# This is an installer resource, not a user entrypoint: it runs once, from the
# distribution package. Afterwards the only supported command is `tga up`.
set -euo pipefail

PREFIX="${TGA_PREFIX:-/opt/tga}"
BIN_DIR="${BIN_DIR:-/usr/local/bin}"
SOURCE_DIR="${SOURCE_DIR:-$(cd "$(dirname "$0")/../.." && pwd)}"

log()  { printf '[install] %s\n' "$*"; }
fail() { printf '[install] ERROR: %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || fail "must run as root (try: sudo $0)"

log "provisioning the runtime from $SOURCE_DIR"
TGA_ADMIN_USER="${TGA_ADMIN_USER:-${SUDO_USER:-}}" \
  SOURCE_DIR="$SOURCE_DIR" bash "$SOURCE_DIR/deploy/wsl-rootfs/provision.sh"

# The launcher is the only thing on the user's PATH. Prefer a compiled Go
# binary; fall back to a shim so a source install still yields `tga up`.
if [ -f "$SOURCE_DIR/tga" ] && [ -x "$SOURCE_DIR/tga" ]; then
  log "installing the compiled launcher"
  install -m 0755 "$SOURCE_DIR/tga" "$BIN_DIR/tga"
else
  log "no compiled launcher found; installing a shim"
  cat > "$BIN_DIR/tga" <<EOF
#!/usr/bin/env bash
# TGA launcher shim. Build launcher/cmd/tga for the single-binary version.
exec $PREFIX/bin/tga-internal "\$@"
EOF
  chmod 0755 "$BIN_DIR/tga"
fi

if command -v systemctl >/dev/null 2>&1 && [ -d /run/systemd/system ]; then
  systemctl daemon-reload
  log "systemd units installed; 'tga up' will manage them"
fi

log "installation complete. Start TGA with: tga up"
