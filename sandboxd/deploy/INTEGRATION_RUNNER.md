# Dedicated sandbox integration runner

Use a disposable, repository-owned Linux VM. Never attach the privileged
runner to a public fork workflow.

Required GitHub labels:

```text
self-hosted
linux
x64
tga-sandbox-integration
```

Required host capabilities:

- KVM and `/dev/kvm`
- Docker Engine with the `runsc` runtime
- nftables and cgroup v2
- Docker Sandboxes `sbx` 0.34.x, authenticated for headless use
- Go 1.26.5 and Python 3.11 with the project development dependencies
- a `tga-sandbox` group containing the runner account
- passwordless sudo only on this disposable runner

Create a root-owned enforced configuration dedicated to the runner. It must
use release or integration-registry image digests and set
`sandboxd.allowed_client_uids` to the numeric runner UID:

```bash
id -u
sudo install -o root -g root -m 0600 sandbox.integration.json \
  /etc/tga/sandbox.integration.json
```

Expose its location to the runner service:

```text
TGA_SANDBOX_INTEGRATION_CONFIG=/etc/tga/sandbox.integration.json
```

Protect the GitHub environment named `sandbox-integration` with required
reviewers. Configure the runner as ephemeral and re-image it after every job.
Run `scripts/check_sandbox_host.sh` before registering it.

The workflow refuses fork pull requests. The final cleanup step fails if any
TGA-labelled container, network or nftables table remains.
