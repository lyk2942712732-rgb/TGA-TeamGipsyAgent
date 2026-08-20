#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

docker build -f "${project_dir}/docker/Dockerfile.base" -t tga3-ctf-base:local "${project_dir}"
docker build -f "${project_dir}/docker/Dockerfile.openai" -t tga3-worker-openai:local "${project_dir}"
docker build -f "${project_dir}/docker/Dockerfile.claude" -t tga3-worker-claude:local "${project_dir}"
