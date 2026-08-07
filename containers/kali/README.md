# Universal Kali Solver image

## Runtime model

Every local Solver still binds its own Kali Profile through `kali.profile_id`.
The Profile in `config/sandbox.json` keeps the executable allowlist, network
policy, resource limits, and runtime capabilities independent, but all 22 local
Profiles now reference the same immutable image:

`ghcr.io/lyk2942712732-rgb/tga-kali-universal@sha256:<release-digest>`

The image is built from `kalilinux/kali-rolling` through the common `base`
stage. `universal/Dockerfile` installs the union of every Solver toolset and
`universal/toolset.json` declares schema version 2, the `universal` image role,
all compatible Profile IDs, and the complete executable inventory. sandboxd
checks that the selected Profile is listed as compatible and that every
allowlisted executable is present before it starts a workload.

The old `solvers/*` contexts are retained as installation provenance and
smaller-image references. They are no longer build-matrix targets and are not
referenced by runtime configuration.

## Source and release configuration

`build-matrix.yaml` is authoritative and contains one Solver target:
`tga-kali-universal`. The checked-in `config/sandbox.json` is a release-input
template and therefore uses `REPLACE_WITH_RELEASE_DIGEST` for that image. It is
not deployable until the image release workflow has:

1. built the base and universal images;
2. validated the universal manifest against all 22 Profiles;
3. scanned and published the image; and
4. broadcast the registry manifest digest to every local Profile.

Digests must not be invented or edited by hand. A `repo@sha256:...` manifest
digest only exists after the registry accepts the image.

## Build and validation

List the matrix and run the source-level contract tests:

```sh
python scripts/kali_build_matrix.py
python scripts/kali_build_matrix.py --format json
python -m pytest -q tests/test_kali_images.py
```

Build the one candidate image:

```sh
docker build --tag tga-kali-base:release containers/kali/base
while read -r image context; do
  docker build \
    --build-arg BASE_IMAGE=tga-kali-base:release \
    --tag "${image}:candidate" \
    "containers/kali/${context}"
done < <(python scripts/kali_build_matrix.py)
```

Validate it against every Profile before scanning or publishing:

```sh
python scripts/validate_kali_image.py \
  --image tga-kali-universal:candidate \
  --all-profiles
```

Validation checks the final UID/GID, manifest digest and compatibility list,
the union of all allowed executable names, absence of sudo, APT cleanup, and
startup with a read-only filesystem, no network, and all Linux capabilities
dropped. CI remains responsible for the full build, Trivy scan, SBOM,
signature, publication, and release digest update.

## Pull and startup

After publication, pull only the shared digest (or let `tga up --pull-images` do it):

```sh
docker pull ghcr.io/lyk2942712732-rgb/tga-kali-universal@sha256:<release-digest>
tga up
```

Image readiness deduplicates identical references, so this produces one Docker
inspect/pull even though 22 Profiles reference the image.

## Sandbox constraints

Installing a tool does not guarantee that the current sandbox backend can run
every feature. `rr`, GDB, LLDB, Frida, tracing, perf events, and similar tools
may require ptrace, kernel facilities, or behavior not exposed by sandboxd or
gVisor. `allow_ptrace` remains Profile/runtime policy. The image does not grant
itself extra capabilities, use privileged mode, start a daemon, or expose a
port.

The malware Profile intentionally retains `pefile` and `lief` as executable
names. The universal image supplies a restricted, read-only CLI that parses a
sample with the corresponding Python library and emits a JSON summary; these
commands are not empty compatibility shims.
