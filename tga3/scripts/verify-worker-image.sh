#!/usr/bin/env bash
set -euo pipefail
image="${1:-tga3-ctf-base:local}"
docker run --rm --entrypoint /bin/bash "${image}" -c '
set -euo pipefail
required_commands=(
  curl wget git jq rg vim.tiny less ps ip ping gcc g++ make nasm pkg-config
  unzip 7z xz file xxd strings readelf strace ltrace tmux openssl
  nmap socat nc gdb php ruby mysql redis-cli psql smbclient exiftool
)
for tool in "${required_commands[@]}"; do
  command -v "${tool}" >/dev/null || { echo "missing required worker command: ${tool}" >&2; exit 1; }
done
python -c "import sys, pwn, Crypto, gmpy2, z3, ropper; assert sys.version_info[:2] == (3, 12); assert gmpy2.invert(3, 7) == 5; assert z3.simplify(z3.IntVal(1)+2).as_long() == 3"
gdb -q -batch -ex "show architecture" >/dev/null
cat /opt/tga3/tool-install-status.tsv
echo "Worker tool smoke test passed: ${HOSTNAME}"
'
