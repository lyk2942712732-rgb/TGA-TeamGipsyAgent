# Sandbox runtime

TGA uses Python as its control plane and a Linux-only Go daemon as the
privileged execution plane.

- `DockerSandboxProvider` remains available for compatibility with offline
  profiles, but it is not the execution backend for `default-kali`.
- `SandboxdProvider` communicates with `tga-sandboxd` over a mode `0660` Unix
  socket. The daemon validates the root-owned profile again and creates only
  `runsc` containers with fixed images, limits and capabilities.
- Task, Solver, profile, configuration digest and fencing token are attached
  to every managed resource. Reconcile only removes resources carrying TGA's
  managed label.
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

Task pause and approval states retain the sandbox. Terminal tasks should mark
the instance released with `destroy_after = terminal time + 15 minutes`; the
cleanup worker passes the current valid instance set to `Reconcile`.

The committed configuration is intentionally `disabled` and contains release
digest placeholders. Enabling enforcement before replacing every selected
profile and tool image with a real `@sha256:<64 hex>` reference is rejected.
Remote MCP configuration remains independent from local Kali image pinning.
# Unified Kali Execution Boundary

`default-kali` is the only local execution profile exposed by the runtime.
Shell, Python, HTTP, network scanners, and local analysis tools are authorized
by `ToolGovernanceGateway` and executed through `KaliSandboxBackend`; the host
does not provide a fallback executor for these capabilities.

The committed `config/sandbox.json` is intentionally disabled. Before setting
`TGA_SANDBOX_RUNTIME=enforced`, operators must:

1. Replace the `default-kali` image placeholder with a digest-pinned image.
2. Replace the Docker Sandbox template placeholder with a pinned release digest.
3. Configure `sandboxd.allowed_client_uids` for the TGA service account.
4. Run `tga-sandboxd` on Linux with gVisor, nftables, cgroup v2, and Docker.

`default-kali` uses `provider=sandboxd` and
`network_mode=target_allowlist`. Each authorized execution replaces the task
network policy with request-scoped CIDR and port grants. An empty grant set is
default-deny, so a prior HTTP or scan action cannot leak network access into a
later Shell or Python action.

Local CLI tools such as nmap, ffuf, nuclei, binwalk, yara, and radare2 belong in
the Kali image and Tool Catalog. Do not add them to `mcp.json`; MCP is reserved
for external systems with an independent remote service lifecycle.
