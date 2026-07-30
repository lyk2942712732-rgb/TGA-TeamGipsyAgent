# tga-sandboxd deployment

`tga-sandboxd` is Linux-only and must run on a dedicated host with Docker,
gVisor `runsc`, nftables and cgroup v2. It never opens a TCP listener.

1. Create the `tga-sandbox` system group with the supplied sysusers file.
2. Install `tga-sandboxd` at `/usr/local/libexec/tga-sandboxd`.
3. Install an enforced, root-owned configuration at `/etc/tga/sandbox.json`
   (`root:root`, mode `0600`) and set `sandboxd.run_root` to an absolute path.
   Set `sandboxd.allowed_client_uids` to the numeric Python API service UID.
4. Configure Docker's `runsc` runtime and verify `docker info` reports it.
5. Install and enable the supplied systemd service.
6. Add only the Python API service account to `tga-sandbox`; do not add Agent
   or tool-container users to that group.

Release image builds must pass a digest-pinned `KALI_BASE`, for example:

```text
docker build --build-arg KALI_BASE=kalilinux/kali-rolling@sha256:<digest> --target offline .
```

CI/release automation must reject an unpinned base, generate an SBOM with
Syft, scan with Trivy, and sign the pushed digest with Cosign. Runtime package
installation is intentionally absent.
