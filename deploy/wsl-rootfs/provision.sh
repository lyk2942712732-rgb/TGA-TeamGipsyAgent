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
TGA_ADMIN_GROUP="${TGA_ADMIN_GROUP:-tga-admin}"
TGA_ADMIN_USER="${TGA_ADMIN_USER:-${SUDO_USER:-}}"
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
  nftables iproute2 procps sudo acl >/dev/null

python3 - <<'PY' || fail "Python 3.11+ is required"
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY

# --- 1b. container engine and gVisor ----------------------------------------
# Without these, sandboxd starts and immediately fails its
# Requires=docker.service; and with Docker but no runsc, a Solver container
# would run straight on the host kernel, which is the one thing this whole
# design exists to prevent.
#
# Both are skippable: a server whose operator already manages Docker should not
# have a second opinion installed underneath them.
TGA_INSTALL_DOCKER="${TGA_INSTALL_DOCKER:-1}"
TGA_INSTALL_RUNSC="${TGA_INSTALL_RUNSC:-1}"

# Pinning the signing key matters more than pinning package versions here: the
# key is stable, while the repository only carries current releases.
DOCKER_KEY_FINGERPRINT="${DOCKER_KEY_FINGERPRINT:-9DC858229FC7DD38854AE2D88D81803C0EBFCD88}"
# A dated release rather than `latest`: an installer that resolves a moving
# pointer cannot be reproduced, and a checksum against it would be a fiction.
GVISOR_RELEASE="${GVISOR_RELEASE:-20260727}"
GVISOR_SHA512_x86_64=ab99ea1b0e2d169ec95473ea6c44abdac9b6b63d9c483f898487fd2b3c32d63bfa9ea104a3d5eed217b90cfc880ceb7a1130a9f7daef6e50656b6e028a8f52e3
GVISOR_SHA512_aarch64=c1a654739a11dadcb6e314ac29f458e7bc8befb5f86259bc55fe090c91cd64ee2254f1b70e3962c3b71a127de612839b0e0915b248d46fcf2a315e995650179b

install_docker() {
  if command -v docker >/dev/null 2>&1; then
    log "docker is already present; leaving it alone"
    return 0
  fi
  local id codename arch
  id="$(. /etc/os-release && printf '%s' "${ID:-}")"
  codename="$(. /etc/os-release && printf '%s' "${VERSION_CODENAME:-}")"
  case "$id" in
    ubuntu|debian) ;;
    *) log "WARNING: no Docker repository is known for '${id:-unknown}'; install Docker yourself"
       return 1 ;;
  esac
  [ -n "$codename" ] || { log "WARNING: /etc/os-release has no VERSION_CODENAME"; return 1; }

  # Every step is checked explicitly. This function is called as
  # `install_docker || log ...`, and inside a `||` list bash suppresses
  # `set -e` for the whole call -- so an unchecked failure here would fall
  # through and still apt-get install from a repository whose key never
  # landed.
  log "installing Docker Engine from download.docker.com"
  curl -fsSL "https://download.docker.com/linux/$id/gpg" -o /tmp/docker.asc \
    || { log "could not fetch the Docker signing key"; return 1; }

  local measured
  measured="$(gpg --show-keys --with-colons /tmp/docker.asc | awk -F: '/^fpr:/{print $10; exit}')"
  if [ "$measured" != "$DOCKER_KEY_FINGERPRINT" ]; then
    rm -f /tmp/docker.asc
    # Not a warning: a repository signing key that is not the expected one is
    # a supply-chain signal, and installing anyway would defeat the check.
    fail "Docker signing key is ${measured:-unreadable}, expected $DOCKER_KEY_FINGERPRINT"
  fi

  gpg --batch --yes --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg /tmp/docker.asc \
    || { rm -f /tmp/docker.asc; log "could not install the Docker keyring"; return 1; }
  rm -f /tmp/docker.asc

  arch="$(dpkg --print-architecture)" || return 1
  printf 'deb [arch=%s signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://download.docker.com/linux/%s %s stable\n' \
    "$arch" "$id" "$codename" > /etc/apt/sources.list.d/docker.list \
    || { log "could not write /etc/apt/sources.list.d/docker.list"; return 1; }

  apt-get update -qq || { log "apt-get update failed after adding the Docker repository"; return 1; }
  apt-get install -y -qq --no-install-recommends \
    docker-ce docker-ce-cli containerd.io >/dev/null \
    || { log "installing docker-ce failed"; return 1; }
}

install_runsc() {
  if command -v runsc >/dev/null 2>&1; then
    log "runsc is already present; leaving it alone"
    return 0
  fi
  local arch expected work
  arch="$(uname -m)"
  case "$arch" in
    x86_64)  expected="$GVISOR_SHA512_x86_64" ;;
    aarch64) expected="$GVISOR_SHA512_aarch64" ;;
    *) log "WARNING: gVisor publishes no build for $arch"; return 1 ;;
  esac

  log "installing gVisor runsc $GVISOR_RELEASE ($arch)"
  work="$(mktemp -d)"
  if ! curl -fsSL \
       "https://storage.googleapis.com/gvisor/releases/release/$GVISOR_RELEASE/$arch/runsc" \
       -o "$work/runsc"; then
    rm -rf "$work"
    log "could not download runsc $GVISOR_RELEASE"
    return 1
  fi
  if ! printf '%s  %s\n' "$expected" "$work/runsc" \
       | sha512sum --check --strict --quiet -; then
    rm -rf "$work"
    # Same reasoning as the Docker key: a binary that is not the pinned one is
    # not something to install with a warning.
    fail "runsc checksum does not match the pinned release $GVISOR_RELEASE"
  fi
  install -m 0755 "$work/runsc" /usr/local/bin/runsc \
    || { rm -rf "$work"; log "could not install runsc"; return 1; }
  rm -rf "$work"
  # Merges the runtime into /etc/docker/daemon.json rather than replacing it.
  /usr/local/bin/runsc install \
    || { log "runsc could not register itself with Docker"; return 1; }
}

if [ "$TGA_INSTALL_DOCKER" = "1" ]; then
  install_docker || log "WARNING: Docker was not installed; the sandbox stays unenforced"
fi
if [ "$TGA_INSTALL_RUNSC" = "1" ] && command -v docker >/dev/null 2>&1; then
  install_runsc || log "WARNING: runsc was not installed; containers would use the host kernel"
fi

if [ -d /run/systemd/system ] && command -v docker >/dev/null 2>&1; then
  log "enabling docker.service"
  systemctl enable docker.service >/dev/null 2>&1 || true
  # Restarted unconditionally: `runsc install` rewrites daemon.json, and the
  # daemon only reads it at start.
  systemctl restart docker.service >/dev/null 2>&1 || log "WARNING: docker.service did not restart"
fi

if command -v docker >/dev/null 2>&1; then
  # Three distinct outcomes, and conflating them produces a scary warning
  # during the rootfs image build -- where there is no daemon by definition.
  if ! docker info >/dev/null 2>&1; then
    log "docker daemon is not reachable here; runsc registration is verified at startup"
  elif docker info --format '{{json .Runtimes}}' 2>/dev/null | grep -q '"runsc"'; then
    log "docker reports the runsc runtime"
  else
    log "WARNING: docker does not report a runsc runtime; enforcement will not engage"
  fi
fi

# --- 2. users and directories ----------------------------------------------
log "creating service account and directories"
getent group "$TGA_GROUP"         >/dev/null || groupadd --system "$TGA_GROUP"
getent group "$TGA_SANDBOX_GROUP" >/dev/null || groupadd --system "$TGA_SANDBOX_GROUP"
getent group "$TGA_ADMIN_GROUP"   >/dev/null || groupadd --system "$TGA_ADMIN_GROUP"
getent passwd "$TGA_USER" >/dev/null || useradd --system \
  --gid "$TGA_GROUP" --groups "$TGA_SANDBOX_GROUP" \
  --home-dir "$TGA_STATE_DIR" --shell /usr/sbin/nologin "$TGA_USER"

if [ -n "$TGA_ADMIN_USER" ]; then
  if id "$TGA_ADMIN_USER" >/dev/null 2>&1 && [ "$TGA_ADMIN_USER" != root ]; then
    usermod -a -G "$TGA_ADMIN_GROUP" "$TGA_ADMIN_USER" \
      || { log "could not add $TGA_ADMIN_USER to $TGA_ADMIN_GROUP"; exit 1; }
    log "granting deployment management to $TGA_ADMIN_USER"
  else
    log "WARNING: TGA_ADMIN_USER '$TGA_ADMIN_USER' is not a non-root local user"
    TGA_ADMIN_USER=""
  fi
fi

install -d -m 0755 "$TGA_PREFIX"/{app,web,bin}
install -d -m 0755 "$TGA_CONFIG_DIR"
install -d -m 2750 -o "$TGA_USER" -g "$TGA_ADMIN_GROUP" "$TGA_STATE_DIR"
install -d -m 2770 -o "$TGA_USER" -g "$TGA_ADMIN_GROUP" "$TGA_STATE_DIR/runs"
install -d -m 2770 -o "$TGA_USER" -g "$TGA_ADMIN_GROUP" "$TGA_STATE_DIR/state"
install -d -m 2770 -o "$TGA_USER" -g "$TGA_ADMIN_GROUP" "$TGA_LOG_DIR"

# Group membership only reaches an already-open login session after the next
# login.  An access ACL lets the user who ran `sudo install.sh` invoke `tga up`
# immediately, while the setgid group remains the durable multi-admin model.
if [ -n "$TGA_ADMIN_USER" ]; then
  setfacl -m "u:$TGA_ADMIN_USER:rx" "$TGA_STATE_DIR" \
    || { log "could not grant access to $TGA_STATE_DIR"; exit 1; }
  for directory in "$TGA_STATE_DIR/runs" "$TGA_STATE_DIR/state" "$TGA_LOG_DIR"; do
    setfacl -m "u:$TGA_ADMIN_USER:rwx" -m "d:u:$TGA_ADMIN_USER:rwx" "$directory" \
      || { log "could not grant access to $directory"; exit 1; }
  done
fi

# Members can manage only TGA's units.  They do not gain Docker access and the
# API service account is deliberately not a member of this group.
SYSTEMCTL_PATH="$(command -v systemctl || true)"
if [ -n "$SYSTEMCTL_PATH" ]; then
  sudoers_tmp="$(mktemp)" || { log "could not create a sudoers staging file"; exit 1; }
  cat > "$sudoers_tmp" <<EOF
%$TGA_ADMIN_GROUP ALL=(root) NOPASSWD: $SYSTEMCTL_PATH start tga-api.service
%$TGA_ADMIN_GROUP ALL=(root) NOPASSWD: $SYSTEMCTL_PATH stop tga-api.service
%$TGA_ADMIN_GROUP ALL=(root) NOPASSWD: $SYSTEMCTL_PATH restart tga-api.service
%$TGA_ADMIN_GROUP ALL=(root) NOPASSWD: $SYSTEMCTL_PATH start tga-sandboxd.service
%$TGA_ADMIN_GROUP ALL=(root) NOPASSWD: $SYSTEMCTL_PATH stop tga-sandboxd.service
EOF
  if [ -n "$TGA_ADMIN_USER" ]; then
    for command in \
      "start tga-api.service" \
      "stop tga-api.service" \
      "restart tga-api.service" \
      "start tga-sandboxd.service" \
      "stop tga-sandboxd.service"; do
      printf '%s ALL=(root) NOPASSWD: %s %s\n' \
        "$TGA_ADMIN_USER" "$SYSTEMCTL_PATH" "$command" >> "$sudoers_tmp"
    done
  fi
  if visudo -cf "$sudoers_tmp" >/dev/null 2>&1; then
    install -m 0440 "$sudoers_tmp" /etc/sudoers.d/tga-admin \
      || { rm -f "$sudoers_tmp"; log "could not install the TGA sudoers policy"; exit 1; }
  else
    rm -f "$sudoers_tmp"
    log "generated TGA sudoers policy is invalid"
    exit 1
  fi
  rm -f "$sudoers_tmp"
fi

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
  local source="$SOURCE_DIR/sandboxd"
  local target="$TGA_PREFIX/bin/tga-sandboxd"
  local work candidate required_go installed_go staged

  work="$(mktemp -d)" \
    || { log "could not create a temporary sandboxd build directory"; return 1; }
  candidate="$work/tga-sandboxd"

  if [ -f "$prebuilt" ]; then
    log "installing prebuilt tga-sandboxd"
    if ! install -m 0755 "$prebuilt" "$candidate"; then
      rm -rf "$work"
      log "installing the prebuilt tga-sandboxd failed"
      return 1
    fi
  elif [ -d "$source" ]; then
    if ! command -v go >/dev/null 2>&1; then
      rm -rf "$work"
      log "no prebuilt dist/tga-sandboxd was provided and Go is not installed"
      return 1
    fi
    required_go="$(awk '$1 == "go" { print $2; exit }' "$source/go.mod")"
    installed_go="$(GOTOOLCHAIN=local go env GOVERSION 2>/dev/null)"
    installed_go="${installed_go#go}"
    if [ -z "$required_go" ] || [ -z "$installed_go" ]; then
      rm -rf "$work"
      log "could not determine the Go version required to build tga-sandboxd"
      return 1
    fi
    if [ "$(printf '%s\n%s\n' "$required_go" "$installed_go" | sort -V | head -n1)" != "$required_go" ]; then
      rm -rf "$work"
      log "tga-sandboxd requires Go $required_go or newer; installed Go is $installed_go"
      log "release packages must include a prebuilt dist/tga-sandboxd"
      return 1
    fi
    log "building tga-sandboxd from source"
    if ! ( cd "$source" \
      && GOTOOLCHAIN=local CGO_ENABLED=0 go build -trimpath -o "$candidate" ./cmd/tga-sandboxd ); then
      rm -rf "$work"
      log "building tga-sandboxd failed with Go $installed_go (requires $required_go+)"
      return 1
    fi
  else
    rm -rf "$work"
    log "no prebuilt dist/tga-sandboxd or sandboxd source tree was found"
    return 1
  fi

  staged="$(mktemp "$TGA_PREFIX/bin/.tga-sandboxd.XXXXXX")" \
    || { rm -rf "$work"; log "could not stage tga-sandboxd in $TGA_PREFIX/bin"; return 1; }
  if ! install -m 0755 "$candidate" "$staged"; then
    rm -f "$staged"
    rm -rf "$work"
    log "staging tga-sandboxd failed"
    return 1
  fi
  if ! mv -f "$staged" "$target"; then
    rm -f "$staged"
    rm -rf "$work"
    log "atomically installing tga-sandboxd at $target failed"
    return 1
  fi
  rm -rf "$work"
  return 0
}

if [ -n "$SOURCE_DIR" ] && [ -d "$SOURCE_DIR" ]; then
  if install_sandboxd; then
    log "  sandboxd $TGA_PREFIX/bin/tga-sandboxd"
  else
    log "WARNING: tga-sandboxd was not installed; see the error above;"
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
# "systemd is running here" and "this system uses systemd" are different
# questions, and conflating them is why the units used to be skipped entirely
# when building the WSL image: /run/systemd/system does not exist inside a
# build container, but the distribution it produces boots with systemd as PID 1.
#
# So: install the units whenever they exist -- an unused unit file harms
# nothing -- and only ask systemd to act when it is actually there.
enable_unit() {
  local unit="$1"
  if [ -d /run/systemd/system ]; then
    systemctl enable "$unit" >/dev/null 2>&1 || true
    return 0
  fi
  # Building an image for a systemd host. `systemctl enable` needs a running
  # systemd, so create exactly the link it would: both units declare
  # WantedBy=multi-user.target.
  install -d -m 0755 /etc/systemd/system/multi-user.target.wants
  ln -sf "/etc/systemd/system/$unit" "/etc/systemd/system/multi-user.target.wants/$unit"
}

UNIT_SOURCE="$(dirname "$0")/../systemd"
if [ -d "$UNIT_SOURCE" ]; then
  log "installing systemd units"
  install -m 0644 "$UNIT_SOURCE/"*.service /etc/systemd/system/
  # An `&&` one-liner here would abort the script under `set -e` on any host
  # without systemd running, which is every image build.
  if [ -d /run/systemd/system ]; then
    systemctl daemon-reload
  fi
  enable_unit tga-api.service
  # Enabled only when the binary is actually there: an enabled unit whose
  # ExecStart does not exist fails at every boot and buries the real reason.
  if [ -x "$TGA_PREFIX/bin/tga-sandboxd" ]; then
    enable_unit tga-sandboxd.service
  else
    log "not enabling tga-sandboxd.service: $TGA_PREFIX/bin/tga-sandboxd is missing"
  fi
  if [ ! -d /run/systemd/system ]; then
    log "systemd is not running here; units installed and linked for first boot"
  fi
else
  log "no systemd units to install; 'tga up' will supervise the API directly"
fi

log "provisioning complete"
log "  prefix   $TGA_PREFIX"
log "  run root $TGA_STATE_DIR/runs"
log "  web dist $TGA_PREFIX/web"
