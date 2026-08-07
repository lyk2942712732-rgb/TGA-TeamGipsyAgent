# Sandbox Runtime

TGA uses Python as its control plane and a Linux-only Go daemon as the
privileged execution plane.

- `DockerSandboxProvider` remains available for compatibility with offline
  profiles. Execution profiles are bound per `SolverDefinition`; there is no
  global `default-kali` fallback.
- `SandboxdProvider` communicates with `tga-sandboxd` over a mode `0660` Unix
  socket. The daemon validates the root-owned profile again and creates only
  `runsc` containers with fixed images, limits and capabilities.
- Task, Solver, SolverRun, profile, configuration digest and fencing token are
  attached to every managed resource. Reconcile only removes resources
  carrying TGA's managed label.
- There is no fallback. `TGA_SANDBOX_RUNTIME=enforced` rejects missing
  profiles, local-process MCP, unavailable providers and mismatched config.
- Docker Sandboxes compatibility is intentionally limited to `sbx` 0.34.x.
  A task-scoped `shell-docker` template owns the VM, while every command runs
  in the Profile's digest-pinned, non-root inner container.
- The sandboxd Unix listener validates both mode `0660` group access and the
  connecting process UID through Linux `SO_PEERCRED`.

Advanced network profiles accept canonical IP/CIDR and port lists only.
Generated nftables rules deny loopback, link-local and metadata ranges before
applying grants. Grants are cleared when each process exits. `NET_ADMIN`,
`SYS_ADMIN`, host networking, devices and the Docker socket are never client
options.

Task pause and approval states retain the sandbox. A terminal Worker destroys
only its own SolverRun sandbox immediately. Task cancellation or terminal Task
completion destroys every active SolverRun sandbox. A transient destroy error
returns the instance to an immediately-due `released` state, and the cleanup
worker retries it while reconciling Docker containers, Docker networks and
nftables policy. API startup also releases containers whose SolverRun is
terminal or whose lease expired after a hard crash; API shutdown drains all
remaining managed sandboxes before sandboxd stops.

The committed competition configuration is enforced and pins every local
Profile to the universal Kali image by immutable registry digest. A missing or
placeholder digest is rejected at the execution boundary.
Remote MCP configuration remains independent from local Kali image pinning.
# SolverRun Execution Boundary

Each execution-capable `SolverDefinition` declares one sandbox profile. The
profile fixes a digest-pinned image, toolset digest, limits, capabilities and
network mode. Shell, Python, HTTP, network scanners, and local analysis tools
are authorized by `ToolGovernanceGateway` and executed through
`KaliSandboxBackend`; the host does not provide a fallback executor.

Every `SolverRun` gets an independent sandbox instance and writable workspace.
The same Run and profile may reuse its instance; a retry creates a new Run and
therefore a new instance. Two Runs in one Task never share a container or
network namespace.

Before using `TGA_SANDBOX_RUNTIME=enforced`, a deployment must:

1. Replace every selected local profile image placeholder with a digest-pinned image.
2. Publish each selected image with its generated `/opt/tga/toolset.json` and replace its `toolset_digest` with the exact SHA256.
3. Replace the Docker Sandbox template placeholder with a pinned release digest.
4. Configure `sandboxd.allowed_client_uids` for the TGA service account.
5. Run `tga-sandboxd` on Linux with gVisor, nftables, cgroup v2, and Docker.

Profiles that use `provider=sandboxd` receive Run-scoped network policy. Each
authorized execution applies request-scoped CIDR and port grants. An empty
grant set is default-deny, so a prior HTTP or scan action cannot leak network
access into a later Shell or Python action.

`kali.exec` accepts a direct executable and argv vector. The executable must
be allowlisted by the profile; absolute paths and `..` path components are
rejected. Typed adapters remain the preferred interface for HTTP and scanner
operations. sandboxd verifies the manifest digest, Profile ID, allowlisted
executable names, and actual executable presence before returning Acquire
success.

Local CLI tools such as nmap, ffuf, nuclei, binwalk, yara, and radare2 belong in
the profile image and Tool Catalog. Do not add them to `mcp.json`; MCP is
reserved for external systems with an independent remote service lifecycle.

# Sandbox Configuration Owns Profiles Only

`config/sandbox.json` declares exactly seven top-level keys: `version`,
`runtime`, `terminal_grace_seconds`, `reconcile_interval_seconds`,
`docker_sandbox`, `sandboxd` and `profiles`. There is no top-level `tools`
mapping, and no configuration layer assigns a per-tool sandbox image or fixed
argument vector. Both planes reject an unknown top-level key: the Python model
uses `extra="forbid"` and sandboxd uses `DisallowUnknownFields`.

The responsibilities are split as follows:

- `SandboxProfile` decides the container image, `toolset_digest`, resource
  limits, network mode, Linux capabilities and `allowed_executables`.
- Local CLI tools are installed in the Kali image that the Profile pins. They
  are not registered individually anywhere in the sandbox configuration.
- `allowed_executables` is the execution allowlist. sandboxd validates the
  Profile and checks `argv[0]` against that allowlist; it resolves no tool
  identity of its own.
- Tool Catalog, Tool Manifest and `ToolGovernanceGateway` own tool semantics
  and decide whether a given tool call is authorized.
- MCP is only for independent external services. It never registers a command
  that lives inside the Kali container, and in-sandbox MCP server images are
  not authorized.

Sandbox configuration therefore keeps no second tool registry. `ProcessSpec`
still carries `tool_id` as an audit, governance and event-record field, which
is unrelated to image or argument resolution.
