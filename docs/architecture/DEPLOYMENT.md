# TGA Deployment Architecture

TGA has one public startup command on every platform:

```bash
tga up
```

Everything else — WSL2, Docker, gVisor, nftables, systemd, sandboxd — is an
implementation detail the launcher owns. Users never run a deployment script.

## Why one entrypoint

The previous `tga go` (native window) and `tga web` (browser) were two startup
code paths that had to be kept correct in parallel. They diverged: `tga go`
built the frontend with `npm` at launch, while the API resolved its run root
from `TGA_RUN_ROOT` and the sandbox lifecycle hardcoded `"runs"`. A deployment
configured with `TGA_RUN_ROOT=/var/lib/tga/runs` therefore wrote tasks to one
tree while sandbox reconciliation scanned another, leaking containers.

One entrypoint, one run-root resolver, one readiness contract.

## Command surface

| Command | Purpose |
| --- | --- |
| `tga up` | Bring the deployment to a serving state and open the interface |
| `tga down` | Stop serving; task data is preserved |
| `tga status` | Report what is running, without changing it |
| `tga doctor` | Diagnose every capability and print remediation |
| `tga logs` | Print a component log tail |

`tga status <task-id>` still reports a single task's snapshot; without an
argument it reports deployment state.

Retired: `tga go`, `tga web`, and any user-facing `serve`. The headless server
survives only as the internal `tga-internal serve`, which systemd supervises.

## Layers

```text
tga / tga.exe                 public launcher (Go, single binary)
  └── tga-internal            internal worker (Python)
        ├── deployment.state  durable phase + file lock (idempotent, resumable)
        ├── deployment.serve  headless FastAPI + prebuilt SPA
        └── deployment.readiness
```

On Windows the launcher forwards through a dedicated WSL2 distribution:

```text
tga.exe up
  └── wsl.exe -d TGA-Runtime -- /opt/tga/bin/tga-internal up --json
```

`TGA_RUNTIME_MODE` overrides surface selection: `wsl` always forwards,
`native` always runs a local checkout. Unset prefers a provisioned
distribution and falls back to a development tree.

A dedicated distribution — rather than the user's own Ubuntu — keeps TGA's
Docker, gVisor and nftables configuration isolated from whatever else they run.

## Readiness is capability-graded

`tga up` waits on `GET /api/v2/system/readiness`, never on `/api/health`. A
listening socket proves a process exists; it does not prove storage is writable
or that tool execution is isolated.

| Status | Meaning | `tga up` |
| --- | --- | --- |
| `ready` | Core serving path works and sandbox isolation is enforced | succeeds |
| `degraded` | Core works; isolation is not enforced | succeeds, prints why |
| `failed` | API or storage unusable | fails |

Degraded is a real state, not a rounding error. A deployment whose
`sandbox.runtime` is `disabled`, or whose profile images are not digest-pinned,
serves the UI but must never claim isolation it does not have.

## Error codes

Every failure carries a stable code from `tga/deployment/errors.py` and a
remediation hint. Codes survive the WSL boundary because the launcher speaks
JSON to the worker rather than screen-scraping.

`WSL_NOT_AVAILABLE`, `WSL_DISTRO_MISSING`, `WSL_IMPORT_FAILED`,
`SANDBOX_RUNTIME_DISABLED`, `SANDBOXD_SOCKET_MISSING`, `SANDBOXD_UID_DENIED`,
`DOCKER_UNAVAILABLE`, `RUNSC_NOT_REGISTERED`, `NFTABLES_UNAVAILABLE`,
`CGROUP_V2_UNAVAILABLE`, `PROFILE_IMAGE_MISSING`, `PROFILE_DIGEST_INVALID`,
`TOOLSET_DIGEST_MISMATCH`, `API_START_FAILED`, `READINESS_TIMEOUT`,
`WEB_BUNDLE_MISSING`, `RUN_ROOT_UNWRITABLE`, `STATE_LOCKED`,
`PORT_UNAVAILABLE`.

## Supervision

Where systemd is present and `tga-api.service` is installed, systemd owns the
process: `tga up` calls `systemctl start`, `tga down` calls `systemctl stop`,
and `tga status` trusts `systemctl` over the state file. Killing the recorded
PID directly would be pointless — systemd would restart it — and an
out-of-band `systemctl stop` must still be visible to `tga status`.

Without systemd (development checkouts, Windows, containers), the launcher
supervises a detached child process itself. Both paths are exercised by
`tests/test_deployment_service_manager.py`.

## Idempotency and interruption

`tga up` takes an exclusive file lock and records completed steps in
`deployment.json`. Running it twice reports "already running" instead of
starting a second server. Interrupting it mid-provision leaves the recorded
steps intact, so the next run resumes rather than restarting.

Lock files are reclaimed when their owning PID is gone. A lock with an
unreadable owner is only reclaimed after `STALE_GRACE_SECONDS`, because
acquisition creates the file and writes the PID as two operations — reclaiming
an empty lock immediately would let two callers hold it at once.

## Configuration generation

Operators never hand-edit `allowed_client_uids` or paste image digests; both
are host facts. `tga.deployment.config_generator` derives them during
provisioning and refuses to certify a configuration that claims enforcement it
cannot deliver:

- empty `allowed_client_uids` under `runtime: enforced`
- `REPLACE_WITH_RELEASE_DIGEST` placeholders
- mutable tags such as `:latest`
- profiles without a toolset digest

Under `runtime: disabled` these become warnings: a disabled deployment is
legitimate, it just has to say so.

## Current state of this installation

`config/sandbox.json` ships with `runtime: disabled` and 22 profiles carrying
`REPLACE_WITH_RELEASE_DIGEST`. Those images are not published, so enforced
isolation cannot be switched on yet. `tga up` therefore reports `degraded`,
which is the honest answer. Publishing real images and re-running provisioning
is what moves this deployment to `ready`.
