#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_dir="$(cd "${project_dir}/.." && pwd)"

command -v docker >/dev/null || { echo "docker is required" >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 is required" >&2; exit 1; }
command -v npm >/dev/null || { echo "Node.js/npm is required to build apps/web" >&2; exit 1; }

cd "${project_dir}"
docker compose up -d --wait postgres
"${project_dir}/scripts/build-images.sh"
python3 -m venv "${project_dir}/.venv"
"${project_dir}/.venv/bin/pip" install --upgrade pip
"${project_dir}/.venv/bin/pip" install "${project_dir}[control]"
npm --prefix "${repo_dir}/apps/web" ci
npm --prefix "${repo_dir}/apps/web" run build

echo "Bootstrap complete. Fill config/models.json, install deploy/tga3.service and deploy/nginx-tga3.conf."
