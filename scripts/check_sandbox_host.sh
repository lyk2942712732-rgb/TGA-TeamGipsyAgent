#!/usr/bin/env bash
set -euo pipefail

test "$(uname -s)" = "Linux"
test -c /dev/kvm
test -r /sys/fs/cgroup/cgroup.controllers
test "$(stat -fc %T /sys/fs/cgroup)" = "cgroup2fs"

command -v docker >/dev/null
command -v runsc >/dev/null
command -v nft >/dev/null
command -v sbx >/dev/null
command -v python3 >/dev/null
command -v go >/dev/null

docker info >/dev/null
docker info --format '{{json .Runtimes}}' | grep -q '"runsc"'
docker run --rm --runtime=runsc busybox:1.36.1 true

version="$(sbx version | grep -Eo '[0-9]+\.[0-9]+\.[0-9]+' | head -1)"
test -n "$version"
test "$(printf '%s\n' 0.34.0 "$version" | sort -V | head -1)" = "0.34.0"
test "$(printf '%s\n' "$version" 0.35.0 | sort -V | head -1)" = "$version"
test "$version" != "0.35.0"
sbx diagnose

temporary="$(mktemp)"
trap 'rm -f "$temporary"' EXIT
cat >"$temporary" <<'EOF'
table inet tga_host_check {
  chain forward {
    type filter hook forward priority -5; policy accept;
  }
}
EOF
sudo nft --check --file "$temporary"

test -n "${TGA_SANDBOX_INTEGRATION_CONFIG:-}"
test -r "$TGA_SANDBOX_INTEGRATION_CONFIG"
test "$(stat -c '%U' "$TGA_SANDBOX_INTEGRATION_CONFIG")" = "root"
test "$(stat -c '%a' "$TGA_SANDBOX_INTEGRATION_CONFIG")" = "600"
