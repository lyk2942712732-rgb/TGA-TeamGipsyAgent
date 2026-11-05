#!/usr/bin/env bash
set -euo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build_network="${DOCKER_BUILD_NETWORK:-host}"
image_tag="${TGA3_IMAGE_TAG:-local}"

docker build --network="${build_network}" \
  --build-arg "PYTHON_IMAGE=${PYTHON_IMAGE:-docker.m.daocloud.io/library/python:3.12-slim-bookworm}" \
  --build-arg "DEBIAN_MIRROR=${DEBIAN_MIRROR-mirrors.aliyun.com}" \
  -f "${project_dir}/docker/Dockerfile.base" -t "tga3-ctf-base:${image_tag}" "${project_dir}"
bash "${project_dir}/scripts/verify-worker-image.sh" "tga3-ctf-base:${image_tag}"

docker build --network="${build_network}" \
  --build-arg "CTF_BASE_IMAGE=tga3-ctf-base:${image_tag}" \
  --build-arg "NODE_DOWNLOAD_BASE=${NODE_DOWNLOAD_BASE:-https://nodejs.org/dist}" \
  -f "${project_dir}/docker/Dockerfile.cli-base" -t "tga3-cli-base:${image_tag}" "${project_dir}"
for worker in openai claude; do
  docker build --network="${build_network}" --build-arg "CLI_BASE_IMAGE=tga3-cli-base:${image_tag}" \
    -f "${project_dir}/docker/Dockerfile.${worker}" -t "tga3-worker-${worker}:${image_tag}" "${project_dir}"
  bash "${project_dir}/scripts/verify-worker-image.sh" "tga3-worker-${worker}:${image_tag}"
  docker run --rm --entrypoint python "tga3-worker-${worker}:${image_tag}" \
    -c 'import tga3.workers.cli_adapter, tga3.workers.hook_service, tga3.workers.runtime'
done
docker run --rm --entrypoint codex "tga3-worker-openai:${image_tag}" --version
docker run --rm --entrypoint claude "tga3-worker-claude:${image_tag}" --version
