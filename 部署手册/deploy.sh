#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if ! command -v python3 >/dev/null 2>&1; then
  echo "未找到 python3；TGA3 需要 Python 3.11 或更高版本。" >&2
  exit 1
fi

exec python3 "${script_dir}/deploy.py" "$@"
