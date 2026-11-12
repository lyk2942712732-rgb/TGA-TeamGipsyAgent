#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
image="${1:-tga3-hosted:20261112}"
destination="${2:-../release}"
mkdir -p "$destination"
platform="$(docker image inspect --format '{{.Os}}/{{.Architecture}}' "$image")"
[[ "$platform" == linux/amd64 ]] || { echo "Expected linux/amd64, got $platform" >&2; exit 1; }
filename="${image//[:\/]/-}-linux-amd64.tar"
[[ ! -e "$destination/$filename" ]] || { echo "Export exists: $filename" >&2; exit 1; }
trap 'rm -f -- "$destination/$filename.partial"' EXIT
docker image save -o "$destination/$filename.partial" "$image"
mv -- "$destination/$filename.partial" "$destination/$filename"
cp hosted.env.example "$destination/hosted.env.example"
(cd "$destination" && sha256sum "$filename" hosted.env.example > SHA256SUMS)
echo "Exported $destination/$filename"
