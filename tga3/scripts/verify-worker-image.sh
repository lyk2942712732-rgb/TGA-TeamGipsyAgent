#!/usr/bin/env bash
set -euo pipefail

image="${1:-tga3-ctf-base:local}"

docker run --rm --entrypoint /bin/bash "${image}" -c '
set -euo pipefail

required_commands=(
  nmap ffuf sqlmap
  gdb gdb-multiarch checksec patchelf strace ltrace qemu-x86_64 afl-fuzz
  ghidra apktool jadx
  tshark ewfinfo foremost plaso-log2timeline yara
  steghide stegseek zbarimg pngcheck convert ffmpeg sox
  jq rg
)

for tool in "${required_commands[@]}"; do
  command -v "${tool}" >/dev/null || {
    echo "missing required worker command: ${tool}" >&2
    exit 1
  }
done

python -c "import angr, bs4, capstone, Crypto, numpy, oletools, PIL, pwn, requests, ropper, sympy, unicorn, volatility3, z3"
echo "Worker tool smoke test passed: ${HOSTNAME}"
'
