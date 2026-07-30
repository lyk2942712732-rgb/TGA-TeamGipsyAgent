#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
config="${TGA_SANDBOX_INTEGRATION_CONFIG:?runner must provide a root-owned integration config}"
work="$(mktemp -d)"
daemon_pid=""
target_id=""

cleanup() {
  set +e
  if [[ -n "$target_id" ]]; then
    docker rm -f "$target_id" >/dev/null 2>&1
  fi
  if [[ -n "$daemon_pid" ]]; then
    sudo kill "$daemon_pid" >/dev/null 2>&1
    wait "$daemon_pid" >/dev/null 2>&1
  fi
  docker ps -aq --filter label=tga.sandbox.managed=true | xargs -r docker rm -f
  docker network ls -q --filter label=tga.sandbox.managed=true | xargs -r docker network rm
  rm -rf "$work"
}
trap cleanup EXIT

cd "$repo_root/sandboxd"
go test -race ./...
go vet ./...
go build -trimpath -o "$work/tga-sandboxd" ./cmd/tga-sandboxd

sudo "$work/tga-sandboxd" --config "$config" >"$work/sandboxd.log" 2>&1 &
daemon_pid=$!

socket="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sandboxd"]["socket_path"])' "$config")"
for _ in $(seq 1 100); do
  [[ -S "$socket" ]] && break
  sleep 0.1
done
test -S "$socket"

target_id="$(docker run -d --rm --label tga.integration.target=true nginx:alpine)"
target_ip="$(docker inspect "$target_id" --format '{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}')"
test -n "$target_ip"

cd "$repo_root"
TGA_SANDBOX_CONFIG_PATH="$config" \
TGA_INTEGRATION_TARGET_IP="$target_ip" \
python3 tests/integration/test_linux_sandbox.py

sudo kill "$daemon_pid"
wait "$daemon_pid" || true
daemon_pid=""

test -z "$(docker ps -aq --filter label=tga.sandbox.managed=true)"
test -z "$(docker network ls -q --filter label=tga.sandbox.managed=true)"
