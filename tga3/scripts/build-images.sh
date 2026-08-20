#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build_network="${DOCKER_BUILD_NETWORK:-host}"
kali_mirror="${KALI_MIRROR:-https://kali.download/kali}"

docker build --network="${build_network}" --build-arg "KALI_MIRROR=${kali_mirror}" -f "${project_dir}/docker/Dockerfile.base" -t tga3-ctf-base:local "${project_dir}"
bash "${project_dir}/scripts/verify-worker-image.sh" tga3-ctf-base:local
docker build --network="${build_network}" -f "${project_dir}/docker/Dockerfile.openai" -t tga3-worker-openai:local "${project_dir}"
docker build --network="${build_network}" -f "${project_dir}/docker/Dockerfile.claude" -t tga3-worker-claude:local "${project_dir}"
