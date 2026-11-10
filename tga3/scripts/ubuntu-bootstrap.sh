#!/usr/bin/env bash
set -euo pipefail
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
exec python3 "${repo_dir}/tga3/scripts/deploy.py" --source-dir "${repo_dir}" "$@"
