#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "${project_dir}"
docker compose up -d --wait postgres
"${project_dir}/scripts/build-images.sh"
python3 -m venv "${project_dir}/.venv"
"${project_dir}/.venv/bin/pip" install --upgrade pip
"${project_dir}/.venv/bin/pip" install "${project_dir}[control]"

echo "Bootstrap complete. Fill config/models.json, then run:"
echo "  ${project_dir}/.venv/bin/tga3 --config-dir ${project_dir}/config serve"
