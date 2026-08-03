# Kali Solver images

## Runtime model

Each `SolverDefinition` may bind one Kali Profile by `kali.profile_id`. The
Profile in `config/sandbox.json` defines the image reference, executable
allowlist, network policy, resource limits, and runtime capabilities. Each
bound Solver has an independent image context under `solvers/<solver-id>` and
inherits the common `base` image. A short-lived `SolverRun` container is
created from that Solver image when the runtime executes work.

The Dockerfiles in this directory mean only that image source is prepared.
They do not make a Profile **Runtime Ready**. An image is Runtime Ready only
after CI has built and validated it, scanned it, published it, and replaced
`REPLACE_WITH_RELEASE_DIGEST` with the verified release digest. The committed
placeholders must never be replaced by invented digests.

## Image matrix

`build-matrix.yaml` is the source of truth for all local Kali images. Every
context contains a Dockerfile and a byte-identical copy of its generated
Profile manifest.

| Solver | Profile | Image | Primary installation sources |
| --- | --- | --- | --- |
| architecture-analyst | architecture-analysis-v1 | tga-kali-architecture-analyst | Kali APT; pinned Semgrep via pipx |
| binary-triage-solver | binary-triage-v1 | tga-kali-binary-triage-solver | Kali APT; pinned flare-capa via pip |
| challenge-classifier | ctf-classifier-v1 | tga-kali-ctf-classifier | Kali APT; pinned zsteg gem; checksummed Didier Stevens pdfid |
| code-audit-solver | code-audit-v1 | tga-kali-code-audit | pinned pipx/npm/Go tools; checksummed CodeQL, Trivy, and Syft releases |
| crash-root-cause-solver | crash-triage-v1 | tga-kali-crash-triage | Kali APT; checksummed CASR release |
| ctf-crypto-solver | ctf-crypto-v1 | tga-kali-ctf-crypto | checksummed Miniforge with pinned conda-forge Sage; Kali APT PARI/GP and OpenSSL |
| ctf-forensics-solver | ctf-forensics-v1 | tga-kali-ctf-forensics | Kali APT; pinned Ruby/Python packages; checksummed Didier Stevens tools |
| ctf-pwn-solver | ctf-pwn-v1 | tga-kali-ctf-pwn | Kali APT; pinned pip and Ruby packages |
| ctf-reverse-solver | ctf-reverse-v1 | tga-kali-ctf-reverse | Kali APT Ghidra/reversing tools; pinned flare-capa |
| ctf-web-solver | ctf-web-v1 | tga-kali-ctf-web | Kali APT; pinned Go tools; verified jwt_tool tag/commit |
| dynamic-analysis-solver | dynamic-analysis-v1 | tga-kali-dynamic-analysis | Kali APT debuggers/tracers; pinned frida-tools |
| dynamic-fuzzing-solver | dynamic-fuzzing-v1 | tga-kali-dynamic-fuzzing | Kali APT AFL++/clang; verified honggfuzz and radamsa tags built in a separate stage |
| evidence-triage-solver | evidence-triage-v1 | tga-kali-evidence-triage | lightweight Kali APT packages only |
| host-network-forensics-solver | network-forensics-v1 | tga-kali-network-forensics | noninteractive Kali APT Wireshark, Zeek, and packet tools |
| logic-config-recovery-solver | logic-recovery-v1 | tga-kali-logic-recovery | Kali APT diffoscope plus base jq/yq |
| malware-solver | malware-analysis-v1 | tga-kali-malware-analysis | Kali APT Ghidra/radare2/YARA; pinned Python analysis libraries |
| poc-reproduction-solver | poc-reproduction-v1 | tga-kali-poc-reproduction | Kali APT compilers, debugger, QEMU, and patchelf |
| static-analysis-solver | static-analysis-v1 | tga-kali-static-analysis | Kali APT Ghidra/reversing tools; pinned flare-capa |
| surface-mapper | pentest-surface-v1 | tga-kali-surface-mapper | Kali APT; pinned Go ProjectDiscovery tools |
| timeline-ioc-solver | timeline-ioc-v1 | tga-kali-timeline-ioc | Kali APT Plaso/Sigma; checksummed Chainsaw, Hayabusa, and evtx releases |
| vulnerability-validator | pentest-validation-v1 | tga-kali-vulnerability-validator | Kali APT; pinned Go tools; verified jwt_tool tag/commit |
| web-api-analyst | pentest-web-api-v1 | tga-kali-web-api-analyst | Kali APT/pipx; pinned Go releases; verified GraphQL Cop and jwt_tool commits |

The malware Profile intentionally retains `pefile` and `lief` as executable
names. Its image supplies a restricted, read-only CLI that parses a sample
with the corresponding real Python library and emits a JSON summary. These
commands are not empty compatibility shims.

## Build and validation

List or consume the matrix without Docker:

```sh
python scripts/kali_build_matrix.py
python scripts/kali_build_matrix.py --format json
python -m pytest -q tests/test_kali_images.py
```

After the base image has a real release digest, CI can build each Solver image
from the repository root:

```sh
while read -r solver profile image context; do
  docker build \
    --build-arg BASE_IMAGE="ghcr.io/team-gipsy/tga-kali-base@sha256:<base-digest>" \
    --tag "${image}:candidate" \
    "containers/kali/${context}"
done < <(python scripts/kali_build_matrix.py)
```

Validate each built candidate before scanning or publishing it:

```sh
while read -r solver profile image context; do
  python scripts/validate_kali_image.py \
    --image "${image}:candidate" \
    --profile "${profile}"
done < <(python scripts/kali_build_matrix.py)
```

`validate_kali_image.py` checks the final UID/GID, manifest digest, Profile
identity, all allowed executable names, absence of sudo, APT cleanup, and
startup with a read-only filesystem, no network, and all Linux capabilities
dropped. Docker CI remains responsible for the real build, Trivy scan, SBOM,
signature, publication, and release digest update.

## Sandbox constraints

Installing a tool does not guarantee that the current sandbox backend can run
every feature. `rr`, GDB, LLDB, Frida, tracing, perf events, and similar tools
may require ptrace, kernel facilities, or behavior not exposed by sandboxd or
gVisor. `allow_ptrace` is Profile/runtime policy. A Dockerfile must not grant
itself extra capabilities, use privileged mode, start a daemon, or expose a
port. Runtime support must be tested separately after the image build.
