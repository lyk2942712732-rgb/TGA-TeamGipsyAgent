#!/usr/bin/env bash
set -euo pipefail

# 兼容旧入口。实际部署编排只维护在仓库根目录“部署手册/deploy.py”中。
project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
repo_dir="$(cd "${project_dir}/.." && pwd)"
manual_entry="${repo_dir}/部署手册/deploy.sh"

if [[ ! -f "${manual_entry}" ]]; then
  echo "未找到统一部署入口: ${manual_entry}" >&2
  echo "请确认源码仓库包含“部署手册”目录，或直接从仓库根目录运行 部署手册/deploy.sh。" >&2
  exit 1
fi

echo "ubuntu-bootstrap.sh 已作为兼容入口，正在转交给部署手册/deploy.sh。" >&2
exec bash "${manual_entry}" --source-dir "${repo_dir}" "$@"
