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

`config/sandbox.json` declares `runtime: enforced` and all 22 profiles are
pinned to published digests under `ghcr.io/lyk2942712732-rgb`, signed and
scanned by the `sandbox-v0.1.1` release. `resolve_sandbox_digests.py --check`
reports `22/22 pinned`.

The file is still a template in one respect: `allowed_client_uids` is empty,
because it is a host fact that provisioning fills in. So validating the
repository copy on its own fails — deliberately. That is what stops a green
provision log from being read as proof of isolation.

## Reset

`up` resumes from the steps it recorded, which is what makes an interrupted
provision safe to retry — and also what leaves a deployment wedged when one
step keeps failing. `tga reset` clears that record so the next `up` starts
over. It never touches the run root: losing a competition's evidence to a
troubleshooting command is not a trade this offers, at any flag.

`tga reset --runtime` is the exception, and the only destructive command here.
Unregistering a WSL distribution deletes its disk, and the run root lives
inside it, so it states that before asking and accepts nothing but the word
`yes`. It runs on the Windows side without resolving a worker — the worker is
inside the thing being removed, and the usual reason to reach for this is that
it stopped answering. `--yes` skips the prompt for scripted use.

## Container engine and gVisor

Provisioning installs Docker Engine and `runsc`, because without them sandboxd
fails its `Requires=docker.service`, and with Docker but no `runsc` a Solver
container would run straight on the host kernel — the one thing this design
exists to prevent.

Both are pinned, and both refusals abort rather than warn:

- Docker comes from `download.docker.com` with its signing key compared against
  a pinned fingerprint. Pinning the key rather than package versions is
  deliberate: the key is stable, the repository only carries current releases.
- `runsc` is a dated gVisor release with a pinned sha512, never `latest`. A
  checksum pinned against a moving pointer would be decorative.

Each step is checked explicitly rather than left to `set -e`. Both installers
are called as `install_x || log …`, and inside a `||` list bash suppresses
`set -e` for everything the function calls — so an unchecked failure would fall
through and, in the first version of this, added an apt repository whose key
had failed to install and then ran `apt-get install` against it.

`runsc install` merges the runtime into `/etc/docker/daemon.json` rather than
replacing it, and provisioning then asks `docker info` whether the runtime is
really registered. Installing the binary is not the same as Docker knowing
about it.

`TGA_INSTALL_DOCKER=0` and `TGA_INSTALL_RUNSC=0` decline both. An operator who
already manages Docker should not get a second opinion installed underneath
them; the installers also no-op when the commands are already present.

## sandboxd

`tga-sandboxd` refuses to run as anyone but root: it creates the socket, hands
it to the `tga-sandbox` group, and drives Docker and nftables. So it is a
systemd unit, never a launcher-supervised child.

Provisioning installs the binary at `/opt/tga/bin/tga-sandboxd` — from a
prebuilt one in the payload if there is one, otherwise built from source when a
Go toolchain is present — and enables the unit only if that binary is really
there. An enabled unit whose `ExecStart` does not exist fails at every boot and
buries the actual reason under a restart loop.

`tga up` starts the unit and then waits for the socket to answer, because an
active unit is not the same as a listening one. `tga down` stops it: `up`
started it, so leaving a privileged runtime behind would be a surprise.

Where no unit is installed, the step only checks the socket. That is the
development case, and it must not fail for the absence of systemd.

## Images

`tga.deployment.image_manager` asks, for every profile that names one, whether
that image is on this host. A pinned reference makes "is it here?" and "is it
the right one?" the same question, so a successful `docker image inspect` on
the digest answers both, and nothing re-hashes the image afterwards.

`tga up` checks but does not pull. The shared universal Solver image can run to
gigabytes; a first run that silently spent an hour downloading — inside a
ninety-second readiness budget — would be worse than one that says what is
missing and how to get it. `tga up --pull-images` fetches them. This is a
deliberate departure from section 12 of the design, which specifies pulling
automatically on first run: that section was written before anyone measured
the images.

Absence never fails a startup. Sandbox capability is graded, so a host with no
images still serves the interface and reports `degraded` along with the count.
Readiness inspection itself never touches a registry, and anything still
missing is fetched by `docker create` the first time a profile is used.
