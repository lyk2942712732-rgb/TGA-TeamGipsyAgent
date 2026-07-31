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
  in a digest-pinned, non-root inner tool container.
- The sandboxd Unix listener validates both mode `0660` group access and the
  connecting process UID through Linux `SO_PEERCRED`.

Advanced network profiles accept canonical IP/CIDR and port lists only.
Generated nftables rules deny loopback, link-local and metadata ranges before
applying grants. Grants are cleared when each process exits. `NET_ADMIN`,
`SYS_ADMIN`, host networking, devices and the Docker socket are never client
options.

Task pause and approval states retain the sandbox. A terminal Worker releases
only its own SolverRun sandbox. Task cancellation or terminal Task completion
releases every active SolverRun sandbox. Terminal instances are marked
released with `destroy_after = terminal time + 15 minutes`; the cleanup worker
passes the current valid instance set to `Reconcile`.

The committed configuration is intentionally `disabled` and contains release
digest placeholders. Enabling enforcement before replacing every selected
profile and tool image with a real `@sha256:<64 hex>` reference is rejected.
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

The committed `config/sandbox.json` is intentionally disabled. Before setting
`TGA_SANDBOX_RUNTIME=enforced`, operators must:

1. Replace every selected local profile image placeholder with a digest-pinned image.
2. Publish each selected image with its generated `/opt/tga/toolset.json` and replace its `toolset_digest` with the exact SHA256.
3. Replace the Docker Sandbox template placeholder with a pinned release digest.
4. Configure `sandboxd.allowed_client_uids` for the TGA service account.
5. Run `tga-sandboxd` on Linux with gVisor, nftables, cgroup v2, and Docker.

Profiles that use `provider=sandboxd` receive Run-scoped network policy. Each
authorized execution applies request-scoped CIDR and port grants. An empty
grant set is default-deny, so a prior HTTP or scan action cannot leak network
access into a later Shell or Python action.

`sandbox.exec` accepts a direct executable and argv vector. The executable must
be allowlisted by the profile; absolute paths and `..` path components are
rejected. Typed adapters remain the preferred interface for HTTP and scanner
operations. sandboxd verifies the manifest digest, Profile ID, allowlisted
executable names, and actual executable presence before returning Acquire
success.

Local CLI tools such as nmap, ffuf, nuclei, binwalk, yara, and radare2 belong in
the profile image and Tool Catalog. Do not add them to `mcp.json`; MCP is
reserved for external systems with an independent remote service lifecycle.
