#!/usr/bin/env bash
set -euo pipefail
image="${1:-tga3-ctf-base:local}"
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
docker run --rm -i --entrypoint python "${image}" < "${script_dir}/verify-worker-image.py"
