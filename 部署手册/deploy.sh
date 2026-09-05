#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python_bin=""
for candidate in python3 python3.13 python3.12 python3.11; do
  if command -v "${candidate}" >/dev/null 2>&1 \
    && "${candidate}" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
    python_bin="${candidate}"
    break
  fi
done

if [[ -z "${python_bin}" ]]; then
  echo "未找到 Python 3.11 或更高版本。" >&2
  exit 1
fi

exec "${python_bin}" "${script_dir}/deploy.py" "$@"
