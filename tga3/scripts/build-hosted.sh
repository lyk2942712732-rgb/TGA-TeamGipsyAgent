#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
docker build --progress=plain --provenance=false -f docker/Dockerfile.hosted -t "${1:-tga3-hosted-local-knowledge:local}" .
